"""ProxyStatusReporter – periodické publikování stavu proxy do MQTT."""

# pylint: disable=broad-exception-caught,protected-access

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from config import PROXY_STATUS_INTERVAL
from control_pipeline import ControlPipeline

if TYPE_CHECKING:
    from proxy import OIGProxy

logger = logging.getLogger(__name__)


class ProxyStatusReporter:
    """Builds and publishes proxy status payloads + periodic heartbeat."""

    def __init__(self, proxy: OIGProxy) -> None:
        self._proxy = proxy
        self.mqtt_was_ready: bool = False
        self.last_hb_ts: float = 0.0
        self.hb_interval_s: float = max(60.0, float(PROXY_STATUS_INTERVAL))
        self.status_attrs_topic: str = str(
            proxy._proxy_status_attrs_topic
            if hasattr(proxy, "_proxy_status_attrs_topic")
            else "oig_local/oig_proxy/proxy_status/attrs"
        )

    # ------------------------------------------------------------------

    def build_status_payload(self) -> dict[str, Any]:
        """Vytvoří payload pro proxy_status MQTT sensor."""
        p = self._proxy
        inflight = p._ctrl.inflight
        inflight_str = ControlPipeline.format_tx(inflight) if inflight else ""
        last_result_str = ControlPipeline.format_result(p._ctrl.last_result)
        inflight_key = str(inflight.get("request_key")
                           or "") if inflight else ""
        queue_keys = [str(tx.get("request_key") or "")
                      for tx in p._ctrl.queue]
        return {
            "status": p._hm.mode.value,
            "mode": p._hm.mode.value,
            "configured_mode": p._hm.configured_mode,
            "control_session_id": p._ctrl.session_id,
            "box_device_id": p.device_id if p.device_id != "AUTO" else None,
            "cloud_online": int(not p._hm.in_offline),
            "hybrid_fail_count": p._hm.fail_count,
            "cloud_connects": p._cf.connects,
            "cloud_disconnects": p._cf.disconnects,
            "cloud_timeouts": p._cf.timeouts,
            "cloud_errors": p._cf.errors,
            "cloud_session_connected": int(p._cf.session_connected),
            "cloud_session_active": int(p._cf.session_connected),
            "mqtt_queue": p.mqtt_publisher.queue.size(),
            "box_connected": int(p.box_connected),
            "box_connections": p.box_connections,
            "box_connections_active": int(p.box_connected),
            "box_data_recent": int(
                p._last_data_epoch is not None
                and (time.time() - p._last_data_epoch) <= 90
            ),
            "last_data": p._last_data_iso,
            "isnewset_polls": p._isnew_polls,
            "isnewset_last_poll": p._isnew_last_poll_iso,
            "isnewset_last_response": p._isnew_last_response,
            "isnewset_last_rtt_ms": p._isnew_last_rtt_ms,
            "control_queue_len": len(p._ctrl.queue),
            "control_inflight": inflight_str,
            "control_inflight_key": inflight_key,
            "control_queue_keys": [k for k in queue_keys if k],
            "control_last_result": last_result_str,
        }

    def build_status_attrs_payload(self) -> dict[str, Any]:
        """Builds the smaller attrs-only payload."""
        p = self._proxy
        if p._ctrl.inflight:
            inflight_key = str(p._ctrl.inflight.get("request_key") or "")
        else:
            inflight_key = ""
        queue_keys = [
            str(tx.get("request_key") or "")
            for tx in p._ctrl.queue
        ]
        return {
            "control_inflight_key": inflight_key,
            "control_queue_keys": [k for k in queue_keys if k],
        }

    async def publish(self) -> None:
        """Publikuje stav proxy."""
        p = self._proxy
        payload = self.build_status_payload()
        try:
            await p.mqtt_publisher.publish_proxy_status(payload)
        except Exception as e:
            logger.debug("Proxy status publish failed: %s", e)
        try:
            await p.mqtt_publisher.publish_raw(
                topic=self.status_attrs_topic,
                payload=json.dumps(
                    self.build_status_attrs_payload(), ensure_ascii=True),
                qos=p._ctrl.qos,
                retain=True,
            )
        except Exception as e:
            logger.debug("Proxy status attrs publish failed: %s", e)

    def note_mqtt_ready_transition(self, mqtt_ready: bool) -> None:
        """Uloží změnu MQTT readiness."""
        self.mqtt_was_ready = mqtt_ready

    def log_heartbeat(self) -> None:
        """Periodický heartbeat log."""
        if self.hb_interval_s <= 0:
            return
        now = time.time()
        if (now - self.last_hb_ts) < self.hb_interval_s:
            return
        self.last_hb_ts = now

        p = self._proxy

        last_data_age = "n/a"
        if p._last_data_epoch is not None:
            last_data_age = f"{int(now - p._last_data_epoch)}s"

        box_uptime = "n/a"
        if p._box_connected_since_epoch is not None:
            box_uptime = f"{int(now - p._box_connected_since_epoch)}s"

        logger.info(
            "💓 HB: mode=%s box=%s cloud=%s cloud_sess=%s mqtt=%s q_mqtt=%s "
            "frames_rx=%s tx=%s ack=%s/%s last_data_age=%s box_uptime=%s",
            p._hm.mode.value,
            "on" if p.box_connected else "off",
            "off" if p._hm.in_offline else "on",
            "on" if p._cf.session_connected else "off",
            "on" if p.mqtt_publisher.is_ready() else "off",
            p.mqtt_publisher.queue.size(),
            p.stats["frames_received"],
            p.stats["frames_forwarded"],
            p.stats["acks_local"],
            p.stats["acks_cloud"],
            last_data_age,
            box_uptime,
        )

    async def status_loop(self) -> None:
        """Periodicky publikuje proxy_status do MQTT."""
        if PROXY_STATUS_INTERVAL <= 0:
            logger.info("Proxy status loop disabled (interval <= 0)")
            return

        logger.info(
            "Proxy status: periodic publish every %ss",
            PROXY_STATUS_INTERVAL,
        )
        while True:
            await asyncio.sleep(PROXY_STATUS_INTERVAL)
            try:
                mqtt_ready = self._proxy.mqtt_publisher.is_ready()
                self.note_mqtt_ready_transition(mqtt_ready)
                await self.publish()
                self.log_heartbeat()
            except Exception as e:
                logger.debug("Proxy status loop publish failed: %s", e)

    def get_stats(self) -> dict[str, Any]:
        """Vrátí statistiky proxy."""
        p = self._proxy
        return {
            "mode": p._hm.mode.value,
            "configured_mode": p._hm.configured_mode,
            "cloud_online": not p._hm.in_offline,
            "hybrid_fail_count": p._hm.fail_count,
            "mqtt_queue_size": p.mqtt_publisher.queue.size(),
            "mqtt_connected": p.mqtt_publisher.connected,
            **p.stats
        }
