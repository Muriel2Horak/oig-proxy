#!/usr/bin/env python3
"""
OIG Proxy - vstupní bod aplikace.
"""

import asyncio
import logging
import os
import sys

from config import (
    DEVICE_ID,
    LOG_LEVEL,
    MQTT_AVAILABLE,
    PROXY_LISTEN_HOST,
    PROXY_LISTEN_PORT,
    TARGET_PORT,
    TARGET_SERVER,
)
from proxy import OIGProxy

# Logging setup
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

logger = logging.getLogger(__name__)


def check_requirements():
    """Zkontroluje základní požadavky."""
    if not MQTT_AVAILABLE:
        logger.warning(
            "⚠️ MQTT knihovna paho-mqtt není nainstalována. "
            "MQTT funkcionalita nebude dostupná."
        )
    
    if not DEVICE_ID:
        logger.error("❌ DEVICE_ID není nastaveno v proměnných prostředí")
        sys.exit(1)
    
    logger.info("✅ Základní požadavky splněny")


async def main():
    """Hlavní funkce."""
    logger.info("=" * 60)
    logger.info("OIG Proxy - Multi-mode Cloud & MQTT Proxy")
    logger.info("=" * 60)
    
    # Check requirements
    check_requirements()
    
    # Konfigurace
    logger.info(f"📋 Konfigurace:")
    logger.info(f"   Device ID: {DEVICE_ID}")
    logger.info(f"   Listen: {PROXY_LISTEN_HOST}:{PROXY_LISTEN_PORT}")
    logger.info(f"   Cloud target: {TARGET_SERVER}:{TARGET_PORT}")
    logger.info(f"   Log level: {LOG_LEVEL}")
    logger.info(f"   MQTT: {'Enabled' if MQTT_AVAILABLE else 'Disabled'}")
    
    # Vytvoř a spusť proxy
    proxy = OIGProxy(DEVICE_ID)
    
    try:
        await proxy.start()
    except KeyboardInterrupt:
        logger.info("\n👋 Shutting down...")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
