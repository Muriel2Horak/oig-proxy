"""
Cloud Forwarder – řeší veškerou komunikaci s oigservis.cz cloudem.

Extracted from proxy.py (Phase 3f).
"""

# pylint: disable=too-many-instance-attributes,protected-access
# pylint: disable=missing-function-docstring,too-many-return-statements
# pylint: disable=too-many-arguments,too-many-positional-arguments,broad-exception-caught

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from contextlib import suppress
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from config import (
    CLOUD_ACK_TIMEOUT,
    TARGET_PORT,
    TARGET_SERVER,
)
from oig_frame import (
    build_end_time_frame,
    build_frame,
    extract_one_xml_frame,
)
from utils import capture_payload, resolve_cloud_host

if TYPE_CHECKING:
    from proxy import OIGProxy

logger = logging.getLogger(__name__)

_CLOUD_ACK_MAX_BYTES = 4096


class CloudForwarder:
    """Manages cloud (oigservis.cz) TCP forwarding sessions."""

    def __init__(self, proxy: OIGProxy) -> None:
        self._proxy = proxy
        # Connection counters
        self.connects: int = 0
        self.disconnects: int = 0
        self.timeouts: int = 0
        self.errors: int = 0
        # Session state
        self.session_connected: bool = False
        self.connected_since_epoch: float | None = None
        self.peer: str | None = None
        self.rx_buf: bytearray = bytearray()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _close_writer(
            self, writer: asyncio.StreamWriter | None) -> None:
        if writer is None:
            return
        with suppress(Exception):
            writer.close()
            await writer.wait_closed()

    # ------------------------------------------------------------------
    # Cloud failure recording
    # ------------------------------------------------------------------

    async def note_failure(
            self,
            *,
            reason: str,
            local_ack: bool | None = None) -> None:
        """Record a cloud failure.  In HYBRID mode may switch to offline."""
        logger.debug("☁️ Cloud failure noted: %s", reason)
        self._proxy._hm.record_failure(reason=reason, local_ack=local_ack)

    # ------------------------------------------------------------------
    # Offline fallback (shared by several error handlers)
    # ------------------------------------------------------------------

    async def fallback_offline(
        self,
        *,
        reason: str,
        frame_bytes: bytes,
        table_name: str | None,
        device_id: str | None,
        box_writer: asyncio.StreamWriter,
        cloud_writer: asyncio.StreamWriter | None,
        note_cloud_failure: bool = True,
        send_box_ack: bool = True,
        conn_id: int | None = None,
    ) -> tuple[None, None]:
        if self.session_connected:
            self._proxy._tc.record_cloud_session_end(reason=reason)
        self.session_connected = False
        await self._close_writer(cloud_writer)
        await self._proxy._process_frame_offline(
            frame_bytes,
            table_name,
            device_id,
            box_writer,
            send_ack=send_box_ack,
            conn_id=conn_id,
        )
        if note_cloud_failure:
            await self.note_failure(reason=reason, local_ack=send_box_ack)
        return None, None

    # ------------------------------------------------------------------
    # Cloud connection management
    # ------------------------------------------------------------------

    async def ensure_connected(
        self,
        cloud_reader: asyncio.StreamReader | None,
        cloud_writer: asyncio.StreamWriter | None,
        *,
        conn_id: int,
        table_name: str | None,
        connect_timeout_s: float,
    ) -> tuple[asyncio.StreamReader | None, asyncio.StreamWriter | None, bool]:
        if not self._proxy._hm.should_try_cloud():
            await self._close_writer(cloud_writer)
            if self.session_connected:
                self._proxy._tc.record_cloud_session_end(
                    reason="manual_offline")
            self.session_connected = False
            return None, None, False
        if cloud_writer is not None and not cloud_writer.is_closing():
            return cloud_reader, cloud_writer, False
        try:
            target_host = resolve_cloud_host(TARGET_SERVER)
            cloud_reader, cloud_writer = await asyncio.wait_for(
                asyncio.open_connection(target_host, TARGET_PORT),
                timeout=connect_timeout_s,
            )
            self.connects += 1
            was_connected = self.session_connected
            self.session_connected = True
            if not was_connected:
                self.connected_since_epoch = time.time()
                self.peer = f"{target_host}:{TARGET_PORT}"
            if not was_connected:
                logger.info(
                    "☁️ Cloud session connected (%s:%s, conn=%s, table=%s)",
                    TARGET_SERVER,
                    TARGET_PORT,
                    conn_id,
                    table_name or "-",
                )
            return cloud_reader, cloud_writer, True
        except Exception as e:
            logger.warning(
                "⚠️ Cloud unavailable: %s (conn=%s, table=%s)",
                e,
                conn_id,
                table_name,
            )
            self.errors += 1
            self.session_connected = False
            await self._close_writer(cloud_writer)
            return None, None, True

    # ------------------------------------------------------------------
    # Error handlers
    # ------------------------------------------------------------------

    async def handle_connection_failed(
        self,
        *,
        conn_id: int,
        table_name: str | None,
        frame_bytes: bytes,
        device_id: str | None,
        box_writer: asyncio.StreamWriter,
        cloud_writer: asyncio.StreamWriter | None,
        cloud_attempted: bool,
    ) -> tuple[None, None]:
        self._proxy._tc.cloud_failed_in_window = True
        if cloud_attempted:
            self._proxy._tc.fire_event(
                "error_cloud_connect",
                cloud_host=TARGET_SERVER,
                reason="connect_failed",
            )
        if self._proxy._hm.is_hybrid_mode():
            self._proxy._hm.record_failure(
                reason="connect_failed", local_ack=True)
            return await self.fallback_offline(
                reason="connect_failed",
                frame_bytes=frame_bytes,
                table_name=table_name,
                device_id=device_id,
                box_writer=box_writer,
                cloud_writer=cloud_writer,
                note_cloud_failure=False,
                conn_id=conn_id,
            )
        if self.rx_buf:
            self.rx_buf.clear()
        self._proxy._tc.record_timeout(conn_id=conn_id)
        return None, None

    async def handle_eof(
        self,
        *,
        conn_id: int,
        table_name: str | None,
        frame_bytes: bytes,
        device_id: str | None,
        box_writer: asyncio.StreamWriter,
        cloud_writer: asyncio.StreamWriter | None,
    ) -> tuple[None, None]:
        self._proxy._tc.cloud_failed_in_window = True
        logger.warning(
            "⚠️ Cloud closed connection (conn=%s, table=%s)",
            conn_id,
            table_name,
        )
        self.disconnects += 1
        if self.session_connected:
            self._proxy._tc.record_cloud_session_end(reason="eof")
        self._proxy._tc.fire_event(
            "error_cloud_disconnect", reason="eof")
        self._proxy._tc.record_error_context(
            event_type="cloud_eof",
            details={"table_name": table_name, "conn_id": conn_id},
        )
        self._proxy._hm.record_failure(
            reason="cloud_eof",
            local_ack=self._proxy._hm.is_hybrid_mode(),
        )
        await self._close_writer(cloud_writer)
        self.rx_buf.clear()
        if self._proxy._hm.is_hybrid_mode():
            return await self.fallback_offline(
                reason="cloud_eof",
                frame_bytes=frame_bytes,
                table_name=table_name,
                device_id=device_id,
                box_writer=box_writer,
                cloud_writer=None,
                note_cloud_failure=False,
                conn_id=conn_id,
            )
        self._proxy._tc.record_timeout(conn_id=conn_id)
        return None, None

    async def handle_timeout(
        self,
        *,
        conn_id: int,
        table_name: str | None,
        frame_bytes: bytes,
        device_id: str | None,
        box_writer: asyncio.StreamWriter,
        cloud_reader: asyncio.StreamReader,
        cloud_writer: asyncio.StreamWriter | None,
    ) -> tuple[asyncio.StreamReader | None, asyncio.StreamWriter | None]:
        self._proxy._tc.cloud_failed_in_window = True
        self.timeouts += 1
        self._proxy._tc.fire_event(
            "error_cloud_timeout",
            cloud_host=TARGET_SERVER,
            timeout_s=CLOUD_ACK_TIMEOUT,
        )
        self._proxy._tc.record_error_context(
            event_type="cloud_timeout",
            details={"table_name": table_name, "conn_id": conn_id, "timeout_s": CLOUD_ACK_TIMEOUT},
        )
        self._proxy._hm.record_failure(
            reason="ack_timeout",
            local_ack=self._proxy._hm.is_hybrid_mode(),
        )
        logger.warning(
            "⏱️ Cloud ACK timeout (%.1fs) (conn=%s, table=%s)",
            CLOUD_ACK_TIMEOUT,
            conn_id,
            table_name,
        )
        if self._proxy._hm.is_hybrid_mode():
            if table_name == "END":
                logger.info(
                    "📤 HYBRID: Sending local END (conn=%s)",
                    conn_id,
                )
                end_frame = build_end_time_frame()
                self._proxy._tc.record_response(
                    end_frame.decode("utf-8", errors="replace"),
                    source="local",
                    conn_id=conn_id,
                )
                box_writer.write(end_frame)
                await box_writer.drain()
                self._proxy.stats["acks_local"] += 1
                return cloud_reader, cloud_writer
            return await self.fallback_offline(
                reason="ack_timeout",
                frame_bytes=frame_bytes,
                table_name=table_name,
                device_id=device_id,
                box_writer=box_writer,
                cloud_writer=cloud_writer,
                note_cloud_failure=False,
                conn_id=conn_id,
            )
        if self.session_connected:
            self._proxy._tc.record_cloud_session_end(reason="timeout")
        await self._close_writer(cloud_writer)
        self.rx_buf.clear()
        self._proxy._tc.record_timeout(conn_id=conn_id)
        return None, None

    async def handle_error(
        self,
        *,
        error: Exception,
        conn_id: int,
        table_name: str | None,
        frame_bytes: bytes,
        device_id: str | None,
        box_writer: asyncio.StreamWriter,
        cloud_writer: asyncio.StreamWriter | None,
    ) -> tuple[None, None]:
        self._proxy._tc.cloud_failed_in_window = True
        logger.warning(
            "⚠️ Cloud error: %s (conn=%s, table=%s)",
            error,
            conn_id,
            table_name,
        )
        self.errors += 1
        if self.session_connected:
            self._proxy._tc.record_cloud_session_end(reason="cloud_error")
        self._proxy._tc.record_error_context(
            event_type="cloud_error",
            details={"table_name": table_name, "conn_id": conn_id, "error": str(error)},
        )
        self._proxy._hm.record_failure(
            reason="cloud_error",
            local_ack=self._proxy._hm.is_hybrid_mode(),
        )
        await self._close_writer(cloud_writer)
        self.rx_buf.clear()
        if self._proxy._hm.is_hybrid_mode():
            return await self.fallback_offline(
                reason="cloud_error",
                frame_bytes=frame_bytes,
                table_name=table_name,
                device_id=device_id,
                box_writer=box_writer,
                cloud_writer=None,
                note_cloud_failure=False,
                conn_id=conn_id,
            )
        self._proxy._tc.record_timeout(conn_id=conn_id)
        return None, None

    # ------------------------------------------------------------------
    # Frame send / ACK read
    # ------------------------------------------------------------------

    async def send_frame(
        self,
        *,
        frame_bytes: bytes,
        cloud_writer: asyncio.StreamWriter,
        cloud_reader: asyncio.StreamReader,
        table_name: str | None,  # pylint: disable=unused-argument
        conn_id: int,  # pylint: disable=unused-argument
    ) -> tuple[bytes | None, None]:
        cloud_writer.write(frame_bytes)
        await cloud_writer.drain()
        self._proxy.stats["frames_forwarded"] += 1
        self._proxy._tc.cloud_ok_in_window = True
        self._proxy._tc.record_frame_direction("proxy_to_box")
        ack_data = await self.read_ack(
            cloud_reader=cloud_reader,
            ack_timeout_s=CLOUD_ACK_TIMEOUT,
            ack_max_bytes=_CLOUD_ACK_MAX_BYTES,
        )
        if not ack_data:
            raise EOFError("Cloud closed connection")
        return ack_data, None

    async def read_ack(
        self,
        *,
        cloud_reader: asyncio.StreamReader,
        ack_timeout_s: float,
        ack_max_bytes: int,
    ) -> bytes:
        existing = extract_one_xml_frame(self.rx_buf)
        if existing is not None:
            return existing

        async def _read_until_frame() -> bytes:
            while True:
                chunk = await cloud_reader.read(4096)
                if not chunk:
                    return b""
                self.rx_buf.extend(chunk)
                frame = extract_one_xml_frame(self.rx_buf)
                if frame is not None:
                    return frame
                if len(self.rx_buf) > ack_max_bytes:
                    data = bytes(self.rx_buf)
                    self.rx_buf.clear()
                    return data

        return await asyncio.wait_for(
            _read_until_frame(), timeout=ack_timeout_s)

    # ------------------------------------------------------------------
    # ACK forwarding to BOX
    # ------------------------------------------------------------------

    async def forward_ack_to_box(
        self,
        *,
        ack_data: bytes,
        table_name: str | None,
        box_writer: asyncio.StreamWriter,
        conn_id: int,
    ) -> None:
        self._proxy._hm.record_success()
        ack_str = ack_data.decode("utf-8", errors="replace")
        self._proxy._tc.cloud_ok_in_window = True
        capture_payload(
            None,
            table_name,
            ack_str,
            ack_data,
            {},
            direction="cloud_to_proxy",
            length=len(ack_data),
            conn_id=conn_id,
            peer=self._proxy._active_box_peer,
        )
        self._proxy._tc.record_response(
            ack_str, source="cloud", conn_id=conn_id
        )
        self._proxy._tc.record_frame_direction("cloud_to_proxy")
        if table_name in ("ACK", "END", "NACK"):
            self._proxy._tc.record_signal_class(table_name)
            if table_name == "END":
                self._proxy._tc.record_end_frame(sent=True)
        if table_name in ("IsNewSet", "IsNewWeather", "IsNewFW"):
            self._proxy._isnew_last_response = self._proxy._last_data_iso
            if self._proxy._isnew_last_poll_epoch:
                self._proxy._isnew_last_rtt_ms = round(
                    (time.time() - self._proxy._isnew_last_poll_epoch) * 1000,
                    1,
                )
        box_writer.write(ack_data)
        await box_writer.drain()
        self._proxy.stats["acks_cloud"] += 1

    # ------------------------------------------------------------------
    # Main entry point – forward a single frame online
    # ------------------------------------------------------------------

    async def forward_frame(
        self,
        *,
        frame_bytes: bytes,
        table_name: str | None,
        device_id: str | None,
        conn_id: int,
        box_writer: asyncio.StreamWriter,
        cloud_reader: asyncio.StreamReader | None,
        cloud_writer: asyncio.StreamWriter | None,
        connect_timeout_s: float,
    ) -> tuple[asyncio.StreamReader | None, asyncio.StreamWriter | None]:
        if table_name in ("IsNewSet", "IsNewFW", "IsNewWeather") and self._proxy._cs.pending_frame is not None:
            pending = self._proxy._cs.pending
            if pending is not None:
                now_local = datetime.now()
                now_utc = datetime.now(timezone.utc)
                inner = (
                    f"<ID>{pending['id']}</ID>"
                    f"<ID_Device>{self._proxy.device_id}</ID_Device>"
                    f"<ID_Set>{pending['id_set']}</ID_Set>"
                    "<ID_SubD>0</ID_SubD>"
                    f"<DT>{now_local.strftime('%d.%m.%Y %H:%M:%S')}</DT>"
                    f"<NewValue>{pending['new_value']}</NewValue>"
                    f"<Confirm>{pending['confirm']}</Confirm>"
                    f"<TblName>{pending['tbl_name']}</TblName>"
                    f"<TblItem>{pending['tbl_item']}</TblItem>"
                    "<ID_Server>5</ID_Server>"
                    "<mytimediff>0</mytimediff>"
                    "<Reason>Setting</Reason>"
                    f"<TSec>{now_utc.strftime('%Y-%m-%d %H:%M:%S')}</TSec>"
                    f"<ver>{secrets.randbelow(90000) + 10000:05d}</ver>"
                )
                setting_frame = build_frame(inner, add_crlf=True).encode("utf-8", errors="strict")
            else:
                setting_frame = self._proxy._cs.pending_frame
            self._proxy._cs.pending_frame = None
            if self._proxy._cs.pending is not None:
                self._proxy._cs.pending["sent_at"] = time.monotonic()
                self._proxy._cs.pending["delivered_conn_id"] = conn_id
            self._proxy._tc.record_response(
                setting_frame.decode("utf-8", errors="replace"),
                source="local",
                conn_id=conn_id,
            )
            capture_payload(
                None,
                "IsNewSet",
                setting_frame.decode("utf-8", errors="replace"),
                setting_frame,
                {},
                direction="proxy_to_box",
                length=len(setting_frame),
                conn_id=conn_id,
                peer=self._proxy._active_box_peer,
            )
            box_writer.write(setting_frame)
            await box_writer.drain()
            self._proxy.stats["acks_local"] += 1
            logger.info(
                "CONTROL: Delivered pending Setting as any poll type response "
                "(online/hybrid, %s/%s=%s, conn=%s)",
                self._proxy._cs.pending.get("tbl_name") if self._proxy._cs.pending else "?",
                self._proxy._cs.pending.get("tbl_item") if self._proxy._cs.pending else "?",
                self._proxy._cs.pending.get("new_value") if self._proxy._cs.pending else "?",
                conn_id,
            )
            return cloud_reader, cloud_writer

        if self._proxy._hm.force_offline_enabled():
            return await self.handle_frame_offline_mode(
                frame_bytes=frame_bytes,
                table_name=table_name,
                device_id=device_id,
                conn_id=conn_id,
                box_writer=box_writer,
                cloud_writer=cloud_writer,
            )

        cloud_reader, cloud_writer, cloud_attempted = (
            await self.ensure_connected(
                cloud_reader,
                cloud_writer,
                conn_id=conn_id,
                table_name=table_name,
                connect_timeout_s=connect_timeout_s,
            )
        )

        if cloud_writer is None or cloud_reader is None:
            return await self.handle_connection_failed(
                conn_id=conn_id,
                table_name=table_name,
                frame_bytes=frame_bytes,
                device_id=device_id,
                box_writer=box_writer,
                cloud_writer=cloud_writer,
                cloud_attempted=cloud_attempted,
            )

        try:
            ack_data, _ = await self.send_frame(
                frame_bytes=frame_bytes,
                cloud_writer=cloud_writer,
                cloud_reader=cloud_reader,
                table_name=table_name,
                conn_id=conn_id,
            )
            if not ack_data:
                return await self.handle_eof(
                    conn_id=conn_id,
                    table_name=table_name,
                    frame_bytes=frame_bytes,
                    device_id=device_id,
                    box_writer=box_writer,
                    cloud_writer=cloud_writer,
                )

            await self.forward_ack_to_box(
                ack_data=ack_data,
                table_name=table_name,
                box_writer=box_writer,
                conn_id=conn_id,
            )
            return cloud_reader, cloud_writer

        except asyncio.TimeoutError:
            return await self.handle_timeout(
                conn_id=conn_id,
                table_name=table_name,
                frame_bytes=frame_bytes,
                device_id=device_id,
                box_writer=box_writer,
                cloud_reader=cloud_reader,
                cloud_writer=cloud_writer,
            )
        except EOFError:
            return await self.handle_eof(
                conn_id=conn_id,
                table_name=table_name,
                frame_bytes=frame_bytes,
                device_id=device_id,
                box_writer=box_writer,
                cloud_writer=cloud_writer,
            )
        except Exception as e:
            return await self.handle_error(
                error=e,
                conn_id=conn_id,
                table_name=table_name,
                frame_bytes=frame_bytes,
                device_id=device_id,
                box_writer=box_writer,
                cloud_writer=cloud_writer,
            )

    # ------------------------------------------------------------------
    # Offline mode frame handler
    # ------------------------------------------------------------------

    async def handle_frame_offline_mode(
        self,
        *,
        frame_bytes: bytes,
        table_name: str | None,
        device_id: str | None,
        conn_id: int,
        box_writer: asyncio.StreamWriter,
        cloud_writer: asyncio.StreamWriter | None,
    ) -> tuple[None, None]:
        await self._close_writer(cloud_writer)
        if self.session_connected:
            self._proxy._tc.record_cloud_session_end(
                reason="manual_offline")
        self.session_connected = False
        await self._proxy._process_frame_offline(
            frame_bytes,
            table_name,
            device_id,
            box_writer,
            conn_id=conn_id,
        )
        return None, None
