"""ControlPipeline – MQTT control command lifecycle for OIG Proxy."""

# pylint: disable=too-many-instance-attributes,protected-access
# pylint: disable=missing-function-docstring,too-many-lines,too-many-return-statements
# pylint: disable=too-many-arguments,too-many-locals,too-many-public-methods
# pylint: disable=broad-exception-caught

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from config import (
    CONTROL_MQTT_ACK_TIMEOUT_S,
    CONTROL_MQTT_APPLIED_TIMEOUT_S,
    CONTROL_MQTT_BOX_READY_SECONDS,
    CONTROL_MQTT_ENABLED,
    CONTROL_MQTT_LOG_ENABLED,
    CONTROL_MQTT_LOG_PATH,
    CONTROL_MQTT_MODE_QUIET_SECONDS,
    CONTROL_MQTT_PENDING_PATH,
    CONTROL_MQTT_QOS,
    CONTROL_MQTT_RETAIN,
    CONTROL_MQTT_RESULT_TOPIC,
    CONTROL_MQTT_SET_TOPIC,
    CONTROL_MQTT_STATUS_PREFIX,
    CONTROL_MQTT_STATUS_RETAIN,
    CONTROL_WRITE_WHITELIST,
    MQTT_PUBLISH_QOS,
    MQTT_STATE_RETAIN,
)
from utils import get_sensor_config

if TYPE_CHECKING:
    from proxy import OIGProxy

logger = logging.getLogger("oig_proxy")


def _get_current_timestamp() -> str:
    """Get current timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ControlPipeline:
    """MQTT control command lifecycle manager for OIG Proxy."""

    _POST_DRAIN_SA_KEY = "post_drain_sa_refresh"

    def __init__(self, proxy: OIGProxy) -> None:
        self._proxy = proxy

        self.mqtt_enabled: bool = bool(CONTROL_MQTT_ENABLED)
        self.set_topic: str = CONTROL_MQTT_SET_TOPIC
        self.result_topic: str = CONTROL_MQTT_RESULT_TOPIC
        self.status_prefix: str = CONTROL_MQTT_STATUS_PREFIX
        self.qos: int = int(CONTROL_MQTT_QOS)
        self.retain: bool = bool(CONTROL_MQTT_RETAIN)
        self.status_retain: bool = bool(CONTROL_MQTT_STATUS_RETAIN)
        self.log_enabled: bool = bool(CONTROL_MQTT_LOG_ENABLED)
        self.log_path: str = str(CONTROL_MQTT_LOG_PATH)
        self.box_ready_s: float = float(CONTROL_MQTT_BOX_READY_SECONDS)
        self.ack_timeout_s: float = float(CONTROL_MQTT_ACK_TIMEOUT_S)
        self.applied_timeout_s: float = float(CONTROL_MQTT_APPLIED_TIMEOUT_S)
        self.mode_quiet_s: float = float(CONTROL_MQTT_MODE_QUIET_SECONDS)
        self.whitelist: dict[str, set[str]] = CONTROL_WRITE_WHITELIST
        self.max_attempts: int = 5
        self.retry_delay_s: float = 120.0
        self.session_id: str = uuid.uuid4().hex
        self.pending_path: str = str(CONTROL_MQTT_PENDING_PATH)
        self.pending_keys: set[str] = self.load_pending_keys()

        self.queue: deque[dict[str, Any]] = deque()
        self.inflight: dict[str, Any] | None = None
        self.lock = asyncio.Lock()
        self.ack_task: asyncio.Task[Any] | None = None
        self.applied_task: asyncio.Task[Any] | None = None
        self.quiet_task: asyncio.Task[Any] | None = None
        self.retry_task: asyncio.Task[Any] | None = None
        self.last_result: dict[str, Any] | None = None
        self.key_state: dict[str, dict[str, Any]] = {}
        self.post_drain_refresh_pending: bool = False

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def format_tx(tx: dict[str, Any] | None) -> str:
        if not tx:
            return ""
        tbl = str(tx.get("tbl_name") or "")
        item = str(tx.get("tbl_item") or "")
        val = str(tx.get("new_value") or "")
        stage = str(tx.get("stage") or "")
        attempts = tx.get("_attempts")
        tx_id = str(tx.get("tx_id") or "")
        if attempts is None:
            return f"{tbl}/{item}={val} ({stage}) tx={tx_id}".strip()
        return f"{tbl}/{item}={val} ({stage} {attempts}) tx={tx_id}".strip()

    @staticmethod
    def format_result(result: dict[str, Any] | None) -> str:
        if not result:
            return ""
        status = str(result.get("status") or "")
        tbl = str(result.get("tbl_name") or "")
        item = str(result.get("tbl_item") or "")
        val = str(result.get("new_value") or "")
        err = result.get("error")
        tx_id = str(result.get("tx_id") or "")
        if err:
            return f"{status} {tbl}/{item}={val} err={err} tx={tx_id}".strip()
        return f"{status} {tbl}/{item}={val} tx={tx_id}".strip()

    @staticmethod
    def build_request_key(
        *, tbl_name: str, tbl_item: str, canon_value: str
    ) -> str:
        return f"{tbl_name}/{tbl_item}/{canon_value}"

    @staticmethod
    def result_key_state(
            status: str,
            detail: str | None) -> str | None:
        if status == "completed" and detail in (
                "duplicate_ignored", "noop_already_set"):
            return None
        mapping = {
            "accepted": "queued",
            "deferred": "queued",
            "sent_to_box": "sent",
            "box_ack": "acked",
            "applied": "applied",
            "completed": "done",
            "error": "error",
        }
        return mapping.get(status)

    @staticmethod
    def coerce_value(value: Any) -> Any:
        if value is None or isinstance(value, (int, float, bool)):
            return value
        text = str(value).strip()
        if text.lower() in ("true", "false"):
            return text.lower() == "true"
        if re.fullmatch(r"-?\d+", text):
            try:
                return int(text)
            except Exception:
                return value
        if re.fullmatch(r"-?\d+\.\d+", text):
            try:
                return float(text)
            except Exception:
                return value
        return value

    # ------------------------------------------------------------------
    # Instance methods
    # ------------------------------------------------------------------

    def normalize_value(
        self, *, tbl_name: str, tbl_item: str, new_value: Any
    ) -> tuple[str, str] | tuple[None, str]:
        raw = new_value
        if isinstance(raw, (int, float)):
            raw_str = str(raw)
        else:
            raw_str = str(raw).strip()

        key = (tbl_name, tbl_item)
        if key == ("tbl_box_prms", "MODE"):
            try:
                mode_int = int(float(raw_str))
            except Exception:
                return None, "bad_value"
            if mode_int < 0 or mode_int > 5:
                return None, "bad_value"
            v = str(mode_int)
            return v, v

        if key in (("tbl_invertor_prm1", "AAC_MAX_CHRG"),
                   ("tbl_invertor_prm1", "A_MAX_CHRG")):
            try:
                f = float(raw_str)
            except Exception:
                return None, "bad_value"
            v = f"{f:.1f}"
            return v, v

        return raw_str, raw_str

    def setup_mqtt(self) -> None:
        if self._proxy._loop is None:
            return

        def _handler(
                topic: str,
                payload: bytes,
                _qos: int,
                retain: bool) -> None:
            if self._proxy._loop is None:
                return
            asyncio.run_coroutine_threadsafe(self.on_mqtt_message(
                topic=topic, payload=payload, retain=retain), self._proxy._loop, )

        self._proxy.mqtt_publisher.add_message_handler(
            topic=self.set_topic,
            handler=_handler,
            qos=self.qos,
        )
        logger.info(
            "CONTROL: MQTT enabled (set=%s result=%s)",
            self.set_topic,
            self.result_topic,
        )

    async def publish_result(
        self,
        *,
        tx: dict[str, Any],
        status: str,
        error: str | None = None,
        detail: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "tx_id": tx.get("tx_id"),
            "request_key": tx.get("request_key"),
            "device_id": None if self._proxy.device_id == "AUTO" else self._proxy.device_id,
            "tbl_name": tx.get("tbl_name"),
            "tbl_item": tx.get("tbl_item"),
            "new_value": tx.get("new_value"),
            "status": status,
            "error": error,
            "detail": detail,
            "ts": _get_current_timestamp(),
        }
        if extra:
            payload.update(extra)
        self.last_result = payload
        await self._proxy.mqtt_publisher.publish_raw(
            topic=self.result_topic,
            payload=json.dumps(payload, ensure_ascii=True),
            qos=self.qos,
            retain=self.retain,
        )
        if self.log_enabled:
            try:
                log_entry = json.dumps(payload, ensure_ascii=True) + "\n"
                await asyncio.to_thread(
                    lambda: self.append_to_log(log_entry)
                )
            except Exception as e:
                logger.debug("CONTROL: Log write failed: %s", e)
        try:
            await self._proxy.publish_proxy_status()
        except Exception as e:
            logger.debug("CONTROL: Status publish failed: %s", e)

        key_state = self.result_key_state(
            status=status, detail=detail)
        if key_state:
            try:
                await self.publish_key_status(tx=tx, state=key_state, detail=detail)
            except Exception as e:
                logger.debug("CONTROL: Key status publish failed: %s", e)

        # SA only after real applied/completed change (avoid noop/duplicate)
        if status in ("applied", "completed") and not error:
            if detail not in ("noop_already_set", "duplicate_ignored"):
                if (tx.get("tbl_name"), tx.get("tbl_item")) != (
                        "tbl_box_prms", "SA"):
                    self.post_drain_refresh_pending = True

    def drop_post_drain_sa_locked(self) -> list[dict[str, Any]]:
        """Drop queued post-drain SA refresh so new commands can proceed."""
        removed: list[dict[str, Any]] = []
        if not self.queue:
            return removed
        kept: deque[dict[str, Any]] = deque()
        for tx in self.queue:  # noqa: PLW0640
            if tx.get("tx_key") != self._POST_DRAIN_SA_KEY:
                kept.append(tx)
            else:
                removed.append(tx)
        self.queue = kept
        return removed

    def append_to_log(self, log_entry: str) -> None:
        """Append entry to control log file (synchronous, called via to_thread)."""
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(log_entry)

    def cancel_post_drain_sa_inflight_locked(
            self) -> dict[str, Any] | None:
        """Cancel inflight post-drain SA refresh so new commands can proceed."""
        tx = self.inflight
        if (not tx or (tx.get("tbl_name"), tx.get("tbl_item")) != (
                "tbl_box_prms", "SA") or tx.get("_internal") != "post_drain_sa"):
            return None
        self.inflight = None
        for task in (
                self.ack_task,
                self.applied_task,
                self.quiet_task):
            if task and not task.done():
                task.cancel()
        self.ack_task = None
        self.applied_task = None
        self.quiet_task = None
        return tx

    def status_topic(self, request_key: str) -> str:
        return f"{self.status_prefix}/{request_key}"

    async def publish_key_status(
        self, *, tx: dict[str, Any], state: str, detail: str | None = None
    ) -> None:
        request_key = str(tx.get("request_key") or "").strip()
        if not request_key:
            return
        payload: dict[str, Any] = {
            "request_key": request_key,
            "state": state,
            "tx_id": tx.get("tx_id"),
            "tbl_name": tx.get("tbl_name"),
            "tbl_item": tx.get("tbl_item"),
            "new_value": tx.get("new_value"),
            "detail": detail,
            "ts": _get_current_timestamp(),
        }
        self.key_state[request_key] = payload
        self.update_pending_keys(request_key=request_key, state=state)
        await self._proxy.mqtt_publisher.publish_raw(
            topic=self.status_topic(request_key),
            payload=json.dumps(payload, ensure_ascii=True),
            qos=self.qos,
            retain=self.status_retain,
        )

    def load_pending_keys(self) -> set[str]:
        try:
            with open(self.pending_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return set()
        except Exception as e:
            logger.debug("CONTROL: Pending load failed: %s", e)
            return set()
        if isinstance(data, list):
            return {str(item) for item in data if item}
        return set()

    def store_pending_keys(self) -> None:
        try:
            with open(self.pending_path, "w", encoding="utf-8") as fh:
                json.dump(
                    sorted(
                        self.pending_keys),
                    fh,
                    ensure_ascii=True)
        except Exception as e:
            logger.debug("CONTROL: Pending save failed: %s", e)

    def update_pending_keys(
            self, *, request_key: str, state: str) -> None:
        if state in ("queued", "sent", "acked", "applied"):
            if request_key not in self.pending_keys:
                self.pending_keys.add(request_key)
                self.store_pending_keys()
            return
        if state in ("done", "error"):
            if request_key in self.pending_keys:
                self.pending_keys.discard(request_key)
                self.store_pending_keys()

    async def publish_restart_errors(self) -> None:
        if not self.pending_keys:
            return
        for request_key in sorted(self.pending_keys):
            tbl_name = ""
            tbl_item = ""
            new_value = ""
            parts = request_key.split("/", 2)
            if len(parts) == 3:
                tbl_name, tbl_item, new_value = parts
            tx = {
                "tx_id": None,
                "request_key": request_key,
                "tbl_name": tbl_name,
                "tbl_item": tbl_item,
                "new_value": new_value,
            }
            await self.publish_result(
                tx=tx, status="error", error="proxy_restart", detail="proxy_restart"
            )
        self.pending_keys.clear()
        self.store_pending_keys()

    def is_box_ready(self) -> tuple[bool, str | None]:
        if not self._proxy.box_connected:
            return False, "box_not_connected"
        if self._proxy.device_id == "AUTO":
            return False, "device_id_unknown"
        if self._proxy._box_connected_since_epoch is None:
            return False, "box_not_ready"
        if (time.time() - self._proxy._box_connected_since_epoch) < self.box_ready_s:
            return False, "box_not_ready"
        if self._proxy._last_data_epoch is None:
            return False, "box_not_sending_data"
        if (time.time() - self._proxy._last_data_epoch) > 30:
            return False, "box_not_sending_data"
        return True, None

    async def validate_request(
        self,
        payload: bytes,
    ) -> dict[str, Any] | None:
        try:
            data = json.loads(payload.decode("utf-8", errors="strict"))
        except Exception:
            await self._proxy.mqtt_publisher.publish_raw(
                topic=self.result_topic,
                payload=json.dumps(
                    {
                        "tx_id": None,
                        "status": "error",
                        "error": "bad_json",
                        "ts": _get_current_timestamp(),
                    }
                ),
                qos=self.qos,
                retain=self.retain,
            )
            return None
        return data

    def build_tx(
        self,
        data: dict[str, Any],
    ) -> dict[str, Any] | None:
        tx_id = str(data.get("tx_id") or "").strip()
        tbl_name = str(data.get("tbl_name") or "").strip()
        tbl_item = str(data.get("tbl_item") or "").strip()
        if not tx_id or not tbl_name or not tbl_item or "new_value" not in data:
            return None

        return {
            "tx_id": tx_id,
            "tbl_name": tbl_name,
            "tbl_item": tbl_item,
            "new_value": data.get("new_value"),
            "confirm": str(data.get("confirm") or "New"),
            "received_at": _get_current_timestamp(),
            "_attempts": 0,
        }

    async def check_whitelist_and_normalize(
        self,
        tx: dict[str, Any],
    ) -> tuple[bool, str | None]:
        tbl_name = tx["tbl_name"]
        tbl_item = tx["tbl_item"]

        allowed = tbl_item in self.whitelist.get(tbl_name, set())
        if not allowed:
            await self.publish_result(tx=tx, status="error", error="not_allowed")
            return False, "not_allowed"

        send_value, err = self.normalize_value(
            tbl_name=tbl_name, tbl_item=tbl_item, new_value=tx["new_value"]
        )
        if send_value is None:
            await self.publish_result(tx=tx, status="error", error=err)
            return False, err

        tx["new_value"] = send_value
        tx["_canon"] = send_value
        return True, None

    async def handle_duplicate_or_noop(
        self,
        tx: dict[str, Any],
        request_key: str,
    ) -> bool:
        tbl_name = tx["tbl_name"]
        tbl_item = tx["tbl_item"]
        send_value = tx["_canon"]

        async with self.lock:
            active_state = (
                self.key_state.get(request_key, {}).get("state")
                if request_key
                else None
            )

            if active_state in ("queued", "sent", "acked", "applied"):
                await self.publish_result(
                    tx=tx, status="completed", detail="duplicate_ignored"
                )
                return True

            current = self._proxy._last_values.get((tbl_name, tbl_item))
            if current is not None:
                current_norm, _ = self.normalize_value(
                    tbl_name=tbl_name, tbl_item=tbl_item, new_value=current
                )
                if (
                    current_norm is not None
                    and str(current_norm) == str(send_value)
                    and self.inflight is None
                    and not self.queue
                ):
                    await self.publish_result(
                        tx=tx, status="completed", detail="noop_already_set"
                    )
                    return True

        return False

    async def enqueue_tx(
        self,
        tx: dict[str, Any],
        request_key: str,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        canceled_sa = None
        dropped_sa: list[dict[str, Any]] = []

        async with self.lock:
            canceled_sa = self.cancel_post_drain_sa_inflight_locked()
            dropped_sa = self.drop_post_drain_sa_locked()
            self.queue.append(tx)

        return canceled_sa, dropped_sa

    async def defer_inflight(self, *, reason: str) -> None:
        """Requeue inflight command for retry; stop after max attempts."""
        async with self.lock:
            tx = self.inflight
            if tx is None:
                return
            attempts = int(tx.get("_attempts") or 0)
            if attempts >= self.max_attempts:
                self.inflight = None
            else:
                tx["stage"] = "deferred"
                tx["deferred_reason"] = reason
                tx["next_attempt_at"] = time.monotonic() + \
                    self.retry_delay_s
                self.queue.appendleft(tx)
                self.inflight = None
            for task in (
                self.ack_task,
                self.applied_task,
                self.quiet_task,
            ):
                if task and not task.done():
                    task.cancel()
            self.ack_task = None
            self.applied_task = None
            self.quiet_task = None

        if attempts >= self.max_attempts:
            await self.publish_result(
                tx=tx,
                status="error",
                error="max_attempts_exceeded",
                detail=reason,
                extra={"attempts": attempts, "max_attempts": self.max_attempts},
            )
            await self.maybe_start_next()
            await self.maybe_queue_post_drain_refresh(last_tx=tx)
            return

        await self.publish_result(
            tx=tx,
            status="deferred",
            detail=reason,
            extra={
                "attempts": attempts,
                "max_attempts": self.max_attempts,
                "retry_in_s": self.retry_delay_s,
            },
        )
        await self.maybe_start_next()

    async def on_mqtt_message(
        self, *, topic: str, payload: bytes, retain: bool
    ) -> None:
        _ = topic
        _ = retain
        data = await self.validate_request(payload)
        if data is None:
            return

        tx = self.build_tx(data)
        if tx is None:
            tx_id = str(data.get("tx_id") or "").strip()
            tbl_name = str(data.get("tbl_name") or "").strip()
            tbl_item = str(data.get("tbl_item") or "").strip()
            tx_payload = {
                "tx_id": tx_id or None,
                "tbl_name": tbl_name,
                "tbl_item": tbl_item,
                "new_value": data.get("new_value"),
            }
            await self.publish_result(
                tx=tx_payload,
                status="error",
                error="missing_fields",
            )
            return

        allowed, _err = await self.check_whitelist_and_normalize(tx)
        if not allowed:
            return

        request_key_raw = str(data.get("request_key") or "").strip()
        request_key = self.build_request_key(
            tbl_name=tx["tbl_name"],
            tbl_item=tx["tbl_item"],
            canon_value=tx["_canon"]
        )
        if request_key_raw and request_key_raw != request_key:
            tx["request_key_raw"] = request_key_raw
        tx["request_key"] = request_key

        if await self.handle_duplicate_or_noop(tx, request_key):
            return

        canceled_sa, dropped_sa = await self.enqueue_tx(tx, request_key)

        if canceled_sa:
            logger.info(
                "CONTROL: Canceling inflight post-drain SA to allow new command (%s/%s)",
                tx.get("tbl_name"),
                tx.get("tbl_item"),
            )
            await self.publish_result(
                tx=canceled_sa,
                status="completed",
                detail="canceled_by_new_command",
            )

        for sa_tx in dropped_sa:
            logger.info(
                "CONTROL: Dropping post-drain SA to allow new command (%s/%s)",
                tx.get("tbl_name"),
                tx.get("tbl_item"),
            )
            await self.publish_result(
                tx=sa_tx,
                status="completed",
                detail="canceled_by_new_command",
            )

        await self.publish_result(tx=tx, status="accepted")
        await self.maybe_start_next()

    async def maybe_start_next(self) -> None:
        ok, _ = self.is_box_ready()
        if not ok:
            return

        schedule_delay: float | None = None
        async with self.lock:
            if self.inflight is not None:
                return
            if not self.queue:
                return
            tx_dict: dict[str, Any] = self.queue[0]
            next_at = float(tx_dict.get("next_attempt_at") or 0.0)
            now = time.monotonic()
            tx: dict[str, Any] | None
            if next_at and now < next_at:
                schedule_delay = next_at - now
                tx = None
            else:
                tx = self.queue.popleft()
            self.inflight = tx
            if tx is not None:
                tx["stage"] = "accepted"

        if schedule_delay is not None:
            await self.schedule_retry(schedule_delay)
            return
        if tx is None:
            return
        await self.start_inflight()

    async def schedule_retry(self, delay_s: float) -> None:
        if delay_s <= 0:
            await self.maybe_start_next()
            return
        if self.retry_task and not self.retry_task.done():
            return

        async def _wait_and_retry() -> None:
            await asyncio.sleep(delay_s)
            await self.maybe_start_next()

        self.retry_task = asyncio.create_task(_wait_and_retry())

    async def start_inflight(self) -> None:
        async with self.lock:
            tx = self.inflight
            if tx is None:
                return
            attempts = int(tx.get("_attempts") or 0)
            too_many = attempts >= self.max_attempts

        if too_many:
            await self.publish_result(
                tx=tx,
                status="error",
                error="max_attempts_exceeded",
                extra={"attempts": attempts, "max_attempts": self.max_attempts},
            )
            await self.finish_inflight()
            return

        result = await self._proxy._send_setting_to_box(
            tbl_name=str(tx["tbl_name"]),
            tbl_item=str(tx["tbl_item"]),
            new_value=str(tx["new_value"]),
            confirm=str(tx.get("confirm") or "New"),
            tx_id=str(tx["tx_id"]),
        )
        if not result.get("ok"):
            err = str(result.get("error") or "send_failed")
            if err in (
                "box_not_connected",
                "box_not_sending_data",
                    "no_active_box_writer"):
                await self.defer_inflight(reason=err)
                return
            await self.publish_result(tx=tx, status="error", error=err)
            await self.finish_inflight()
            return

        tx["attempts"] = attempts + 1
        tx["_attempts"] = attempts + 1
        tx["stage"] = "sent_to_box"
        tx["id"] = result.get("id")
        tx["id_set"] = result.get("id_set")
        tx["sent_at_mono"] = time.monotonic()
        tx["disconnected"] = False

        await self.publish_result(
            tx=tx,
            status="sent_to_box",
            extra={
                "id": tx.get("id"),
                "id_set": tx.get("id_set"),
                "attempts": tx.get("_attempts"),
                "max_attempts": self.max_attempts,
            },
        )

        if self.ack_task and not self.ack_task.done():
            self.ack_task.cancel()
        self.ack_task = asyncio.create_task(
            self.ack_timeout())

    async def on_box_setting_ack(
            self, *, tx_id: str | None, ack: bool) -> None:
        if not tx_id:
            return
        async with self.lock:
            tx = self.inflight
            if tx is None or str(tx.get("tx_id")) != str(tx_id):
                return
            tx["stage"] = "box_ack" if ack else "error"

        if self.ack_task and not self.ack_task.done():
            self.ack_task.cancel()
        self.ack_task = None

        if not ack:
            await self.publish_result(tx=tx, status="error", error="box_nack")
            await self.finish_inflight()
            return

        await self.publish_result(tx=tx, status="box_ack")

        if self.applied_task and not self.applied_task.done():
            self.applied_task.cancel()
        self.applied_task = asyncio.create_task(
            self.applied_timeout())

    def map_optimistic_value(
            self,
            *,
            tbl_name: str,
            tbl_item: str,
            value: Any) -> Any:
        cfg, _ = get_sensor_config(tbl_item, tbl_name)
        if cfg and cfg.options:
            text = str(value).strip()
            if re.fullmatch(r"-?\d+", text):
                idx = int(text)
                if 0 <= idx < len(cfg.options):
                    return cfg.options[idx]
        return self.coerce_value(value)

    async def publish_setting_event_state(
        self,
        *,
        tbl_name: str,
        tbl_item: str,
        new_value: Any,
        device_id: str | None,
        source: str,
    ) -> None:
        if not tbl_name or not tbl_item:
            return

        raw_value = self.coerce_value(new_value)
        target_device_id = device_id
        if not target_device_id:
            target_device_id = (
                self._proxy.device_id if self._proxy.device_id != "AUTO" else self._proxy.mqtt_publisher.device_id)
        if not target_device_id:
            return
        topic = self._proxy.mqtt_publisher.state_topic(target_device_id, tbl_name)
        cached = self._proxy.mqtt_publisher.get_cached_payload(topic)
        payload: dict[str, Any] = {}
        try:
            if cached:
                payload = json.loads(cached)
        except Exception:
            payload = {}

        if not payload:
            table_values = self._proxy._table_cache.get(tbl_name)
            if not table_values:
                table_values = self._proxy._prms_tables.get(tbl_name)
            if isinstance(table_values, dict) and table_values:
                raw_payload = dict(table_values)
                raw_payload[tbl_item] = raw_value
                payload, _ = self._proxy.mqtt_publisher.map_data_for_publish(
                    {"_table": tbl_name, **raw_payload},
                    table=tbl_name,
                    target_device_id=target_device_id,
                )

        payload[tbl_item] = self.map_optimistic_value(
            tbl_name=tbl_name,
            tbl_item=tbl_item,
            value=new_value,
        )

        updated = json.dumps(payload, ensure_ascii=True)
        self._proxy.mqtt_publisher.set_cached_payload(topic, updated)
        await self._proxy.mqtt_publisher.publish_raw(
            topic=topic,
            payload=updated,
            qos=MQTT_PUBLISH_QOS,
            retain=MQTT_STATE_RETAIN,
        )
        logger.info(
            "SETTING: State publish %s/%s=%s (source=%s)",
            tbl_name,
            tbl_item,
            payload.get(tbl_item),
            source,
        )

    async def ack_timeout(self) -> None:
        await asyncio.sleep(self.ack_timeout_s)
        async with self.lock:
            tx = self.inflight
            if tx is None:
                return
            if tx.get("stage") not in ("sent_to_box", "accepted"):
                return
        if not self._proxy.box_connected or tx.get("disconnected"):
            await self.defer_inflight(reason="box_not_connected")
            return
        await self.defer_inflight(reason="timeout_waiting_ack")

    async def applied_timeout(self) -> None:
        await asyncio.sleep(self.applied_timeout_s)
        async with self.lock:
            tx = self.inflight
            if tx is None:
                return
            if tx.get("stage") in ("applied", "completed", "error"):
                return
        await self.publish_result(
            tx=tx, status="error", error="timeout_waiting_applied"
        )
        await self.finish_inflight()

    async def quiet_wait(self) -> None:
        while True:
            async with self.lock:
                tx = self.inflight
                if tx is None:
                    return
                if tx.get("stage") not in ("applied",):
                    return
                last = float(tx.get("last_inv_ack_mono")
                             or tx.get("applied_at_mono") or 0.0)
                wait_s = max(
                    0.0, (last + self.mode_quiet_s) - time.monotonic())
            if wait_s > 0:
                await asyncio.sleep(wait_s)
                continue
            break

        async with self.lock:
            tx = self.inflight
            if tx is None:
                return
            if tx.get("stage") != "applied":
                return
        await self.publish_result(tx=tx, status="completed", detail="quiet_window")
        await self.finish_inflight()

    async def finish_inflight(self) -> None:
        async with self.lock:
            tx = self.inflight
            self.inflight = None
            for task in (
                self.ack_task,
                self.applied_task,
                self.quiet_task,
            ):
                if task and not task.done():
                    task.cancel()
            self.ack_task = None
            self.applied_task = None
            self.quiet_task = None
        await self.maybe_start_next()
        await self.maybe_queue_post_drain_refresh(last_tx=tx)

    async def maybe_queue_post_drain_refresh(
        self,
        *,
        last_tx: dict[str, Any] | None,
    ) -> None:
        if not last_tx:
            return
        if (last_tx.get("tbl_name"), last_tx.get(
                "tbl_item")) == ("tbl_box_prms", "SA"):
            return

        async with self.lock:
            if self.inflight is not None or self.queue:
                return
            if not self.post_drain_refresh_pending:
                return
            self.post_drain_refresh_pending = False

        await self.enqueue_internal_sa(reason="queue_drained")

    async def enqueue_internal_sa(self, *, reason: str) -> None:
        request_key = self.build_request_key(
            tbl_name="tbl_box_prms",
            tbl_item="SA",
            canon_value="1",
        )
        tx = {
            "tx_id": f"internal_sa_{int(time.time() * 1000)}",
            "tbl_name": "tbl_box_prms",
            "tbl_item": "SA",
            "new_value": "1",
            "confirm": "New",
            "received_at": _get_current_timestamp(),
            "_attempts": 0,
            "_canon": "1",
            "request_key": request_key,
            "_internal": "post_drain_sa",
        }

        async with self.lock:
            if self.inflight and self.inflight.get(
                    "request_key") == request_key:
                return
            for queued in self.queue:
                if queued.get("request_key") == request_key:
                    return
            self.queue.append(tx)

        await self.publish_result(tx=tx, status="accepted", detail=f"internal_sa:{reason}")
        await self.maybe_start_next()

    async def get_valid_tx_with_lock(
        self,
        tx_id: str,
    ) -> dict[str, Any] | None:
        async with self.lock:
            tx = self.inflight
            if tx is None or tx.get("tx_id") != tx_id:
                return None
            return tx

    async def handle_marker_frames(
        self,
        tx: dict[str, Any],
        table_name: str,
    ) -> None:
        tx_id = tx.get("tx_id")
        if tx_id is None:
            return
        tx2 = await self.get_valid_tx_with_lock(tx_id)
        if tx2 is None:
            return
        stage = tx2.get("stage")
        if stage in ("box_ack", "applied"):
            await self.publish_result(
                tx=tx, status="completed", detail=f"box_marker:{table_name}"
            )
            await self.finish_inflight()

    async def handle_setting_event_control(
        self,
        tx: dict[str, Any],
        content: str,
    ) -> None:
        ev = self._proxy._parse_setting_event(content)
        if not ev:
            return
        ev_tbl, ev_item, old_v, new_v = ev
        if ev_tbl != tx.get("tbl_name") or ev_item != tx.get("tbl_item"):
            return
        desired = str(tx.get("new_value"))
        if str(new_v) != desired:
            return
        tx_id = tx.get("tx_id")
        if tx_id is None:
            return
        async with self.lock:
            tx2 = self.inflight
            if tx2 is None or tx2.get("tx_id") != tx_id:
                return
            tx2["stage"] = "applied"
            tx2["applied_at_mono"] = time.monotonic()
            tx2["last_inv_ack_mono"] = tx2["applied_at_mono"]
        await self.publish_result(
            tx=tx,
            status="applied",
            extra={
                "old_value": old_v,
                "observed_new_value": new_v,
            },
        )

        if (tx.get("tbl_name"), tx.get("tbl_item")) != (
                "tbl_box_prms", "MODE"):
            await self.publish_result(tx=tx, status="completed", detail="applied")
            await self.finish_inflight()
            return

        if self.quiet_task and not self.quiet_task.done():
            self.quiet_task.cancel()
        self.quiet_task = asyncio.create_task(
            self.quiet_wait())

    async def handle_invertor_ack(
        self,
        tx: dict[str, Any],
        content: str,
    ) -> None:
        if ("Invertor ACK" not in content or
                (tx.get("tbl_name"), tx.get("tbl_item")) != ("tbl_box_prms", "MODE")):
            return
        tx_id = tx.get("tx_id")
        if tx_id is None:
            return
        async with self.lock:
            tx2 = self.inflight
            if tx2 is None or tx2.get("tx_id") != tx_id:
                return
            if tx2.get("stage") != "applied":
                return
            tx2["last_inv_ack_mono"] = time.monotonic()
        if self.quiet_task and not self.quiet_task.done():
            self.quiet_task.cancel()
        self.quiet_task = asyncio.create_task(
            self.quiet_wait())

    async def observe_box_frame(
        self, parsed: dict[str, Any], table_name: str | None, _frame: str
    ) -> None:
        async with self.lock:
            tx = self.inflight
        if tx is None or not parsed or not table_name:
            return

        if table_name in ("IsNewSet", "IsNewWeather", "IsNewFW", "END"):
            await self.handle_marker_frames(tx, table_name)
            return

        if table_name != "tbl_events":
            return

        content = parsed.get("Content")
        typ = parsed.get("Type")
        if not content or not isinstance(content, str):
            return

        if typ == "Setting":
            await self.handle_setting_event_control(tx, content)
            return

        await self.handle_invertor_ack(tx, content)

    async def note_box_disconnect(self) -> None:
        """Mark inflight control command as interrupted by box disconnect."""
        async with self.lock:
            tx = self.inflight
            if tx is None:
                return
            if tx.get("stage") in ("sent_to_box", "accepted"):
                tx["disconnected"] = True
