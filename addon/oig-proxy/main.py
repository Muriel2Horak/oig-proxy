#!/usr/bin/env python3
"""
OIG Proxy - vstupní bod aplikace.
"""

import asyncio
import logging
import os
import sys

from config import (
    LOG_LEVEL,
    MQTT_AVAILABLE,
    PROXY_LISTEN_HOST,
    PROXY_LISTEN_PORT,
    TARGET_PORT,
    TARGET_SERVER,
    DATA_DIR,
    CLEAR_CLOUD_QUEUE_ON_START,
    CLOUD_QUEUE_ENABLED,
)
from utils import load_sensor_map
from proxy import OIGProxy
from cloud_manager import CloudQueue


def _sanitize_log_value(value: object) -> object:
    """Očistí logované hodnoty od řídicích znaků a zachová strukturu."""
    if isinstance(value, str):
        return value.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
    if isinstance(value, dict):
        return {key: _sanitize_log_value(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_sanitize_log_value(val) for val in value)
    return value


class LogSanitizerFilter(logging.Filter):  # pylint: disable=too-few-public-methods
    """Sanitizuje log recordy před zapsáním do logu."""
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _sanitize_log_value(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    key: _sanitize_log_value(val) for key, val in record.args.items()
                }
            else:
                record.args = tuple(_sanitize_log_value(arg) for arg in record.args)
        return True


# Logging setup
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_root_logger = logging.getLogger()
for _handler in _root_logger.handlers:
    _handler.addFilter(LogSanitizerFilter())

logger = logging.getLogger(__name__)


def check_requirements():
    """Zkontroluje základní požadavky."""
    if not MQTT_AVAILABLE:
        logger.warning(
            "⚠️ MQTT knihovna paho-mqtt není nainstalována. "
            "MQTT funkcionalita nebude dostupná."
        )


async def main():
    """Hlavní funkce."""
    logger.info("=" * 60)
    logger.info("OIG Proxy - Multi-mode Cloud & MQTT Proxy")
    logger.info("=" * 60)

    # Check requirements
    check_requirements()

    # Načti sensor mapu (DŮLEŽITÉ pro MQTT entity)
    load_sensor_map()
    logger.debug("✅ Sensor map loaded")

    # DEVICE_ID je optional - detekuje se z komunikace
    device_id = os.getenv('DEVICE_ID')
    if device_id:
        logger.info("Using configured DEVICE_ID: %s", device_id)
    else:
        logger.info("DEVICE_ID not set - will be detected from communication")
        device_id = "AUTO"  # Placeholder - bude aktualizováno z prvního framu

    # Konfigurace
    logger.info("📋 Configuration:")
    logger.info("   Device ID: %s", device_id)
    logger.info("   Listen: %s:%s", PROXY_LISTEN_HOST, PROXY_LISTEN_PORT)
    logger.info("   Cloud target: %s:%s", TARGET_SERVER, TARGET_PORT)
    logger.info("   Data directory: %s", DATA_DIR)
    logger.info("   Log level: %s", LOG_LEVEL)
    logger.info("   MQTT: %s", "Enabled" if MQTT_AVAILABLE else "Disabled")
    logger.info(
        "   Cloud queue: %s",
        "Enabled" if CLOUD_QUEUE_ENABLED else "Disabled (offline frames dropped)",
    )

    if not CLOUD_QUEUE_ENABLED:
        cleanup_queue = CloudQueue()
        if cleanup_queue.size() > 0:
            logger.warning("🧹 Clearing cloud queue on startup")
            cleanup_queue.clear()

    # Vytvoř a spusť proxy
    proxy = OIGProxy(device_id)
    if CLEAR_CLOUD_QUEUE_ON_START:
        logger.warning("🧹 Clearing cloud queue on startup (config)")
        proxy.cloud_queue.clear()

    try:
        await proxy.start()
    except KeyboardInterrupt:
        logger.info("\n👋 Shutting down...")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("❌ Fatal error: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
