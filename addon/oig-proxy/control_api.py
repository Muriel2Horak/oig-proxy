#!/usr/bin/env python3
"""Minimal HTTP control API for prototyping "Setting" writes to BOX.

.. deprecated::
    The HTTP control API is a **legacy entrypoint**.  The preferred control
    path is MQTT-driven twin routing via :class:`digital_twin.TwinMQTTHandler`.
    This API remains available as a fallback but will be removed once MQTT
    twin-first path is validated in production.

Intentionally:
- minimal validation (only checks that BOX is connected and sending data)
"""

# pylint: disable=too-many-locals,too-many-return-statements

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from config import CONTROL_WRITE_WHITELIST

logger = logging.getLogger(__name__)

TOKEN_FILE_PATH = "/data/control_api_token"  # nosec: B105 - this is a file path, not a password


def _get_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = str(raw).strip().lower()
    if raw in ("true", "1", "yes", "on"):
        return True
    if raw in ("false", "0", "no", "off"):
        return False
    return default


def _control_api_fallback_only_enabled() -> bool:
    twin_first = _get_bool_env("CONTROL_TWIN_FIRST_ENABLED", False)
    return _get_bool_env("CONTROL_API_FALLBACK_ONLY", twin_first)


class _Handler(BaseHTTPRequestHandler): # pylint: disable=invalid-name
    """HTTP handler pro Control API."""
    server_version = "OIGProxyControlAPI/0.1"

    def _validate_token(self) -> bool:
        """Validate Authorization header against stored token."""
        auth_header = self.headers.get("Authorization", "")
        expected_token = self.server.control_api_token  # type: ignore[attr-defined]

        if not auth_header.startswith("Bearer "):
            return False

        token = auth_header[7:]  # Remove "Bearer " prefix
        return token == expected_token

    def _send_unauthorized(self) -> None:
        """Send 401 Unauthorized response."""
        self._send_json(401, {"error": "unauthorized"})

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        """Odešle JSON odpověď se zadaným HTTP statusem.
        
        SECURITY NOTE: This is a prototype API with minimal validation.
        In production, sanitize payload to prevent XSS and reflection attacks."""
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None: # pylint: disable=invalid-name
        """Zpracuje GET requesty Control API."""
        if not self._validate_token():
            self._send_unauthorized()
            return

        if self.path.rstrip("/") == "/api/health":
            proxy = self.server.proxy # type: ignore[attr-defined]
            payload = proxy._cs.get_health() # pylint: disable=protected-access
            self._send_json(200, payload)
            return

        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None: # pylint: disable=invalid-name
        """Zpracuje POST /api/setting pro odeslání Setting do BOXu."""
        if not self._validate_token():
            self._send_unauthorized()
            return

        if self.path.rstrip("/") != "/api/setting":
            self._send_json(404, {"error": "not_found"})
            return

        if _get_bool_env("CONTROL_API_KILL_SWITCH", False):
            self._send_json(
                410,
                {
                    "error": "control_api_killed",
                    "detail": "CONTROL_API_KILL_SWITCH=true",
                },
            )
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length > 0 else b""

        # JSON preferred, but allow minimal XML snippet containing just the
        # tags.
        try:
            data = json.loads(body.decode("utf-8") if body else "{}")
        except json.JSONDecodeError:
            text = body.decode("utf-8", errors="ignore")

            def _tag(name: str) -> str | None:
                m = re.search(rf"<{name}>([^<]+)</{name}>", text)
                return m.group(1) if m else None

            data = {
                "TblName": _tag("TblName"),
                "TblItem": _tag("TblItem"),
                "NewValue": _tag("NewValue"),
                "Confirm": _tag("Confirm") or "New",
            }

        tbl_name = data.get("tbl_name") or data.get("TblName")
        tbl_item = data.get("tbl_item") or data.get("TblItem")
        new_value = data.get("new_value") or data.get("NewValue")
        confirm = data.get("confirm") or data.get("Confirm") or "New"

        if not tbl_name or not tbl_item or new_value is None:
            self._send_json(
                400,
                {
                    "error": "missing_fields",
                    "required": ["tbl_name", "tbl_item", "new_value"],
                },
            )
            return

        # Whitelist validation (deny-by-default)
        if tbl_name not in CONTROL_WRITE_WHITELIST:
            client = self.client_address[0] if self.client_address else "unknown"
            logger.warning(
                "Whitelist rejection: tbl_name not in whitelist (client=%s)",
                client,
            )
            self._send_json(400, {"error": "tbl_name not in whitelist"})
            return

        allowed_items = CONTROL_WRITE_WHITELIST[tbl_name]
        if tbl_item not in allowed_items:
            client = self.client_address[0] if self.client_address else "unknown"
            logger.warning(
                "Whitelist rejection: tbl_item not in whitelist for tbl_name=%s (client=%s)",
                tbl_name,
                client,
            )
            self._send_json(400, {"error": "tbl_item not in whitelist"})
            return

        proxy = self.server.proxy  # type: ignore[attr-defined]

        if _control_api_fallback_only_enabled():
            route: str | None = None
            try:
                cs = getattr(proxy, "_cs", None)
                if cs is not None and hasattr(cs, "resolve_control_route"):
                    route = cs.resolve_control_route()
            except Exception:  # pylint: disable=broad-exception-caught
                route = None
            if route == "twin":
                self._send_json(
                    409,
                    {
                        "error": "deprecated_entrypoint_fallback_only",
                        "detail": "Use MQTT/twin-first control path; HTTP control API is fallback-only",
                    },
                )
                return

        logger.info(
            "DEPRECATED_ENTRYPOINT_MARKER: control_api HTTP /api/setting tbl=%s item=%s",
            tbl_name,
            tbl_item,
        )
        res = proxy._cs.send_setting(  # pylint: disable=protected-access
            tbl_name=str(tbl_name),
            tbl_item=str(tbl_item),
            new_value=str(new_value),
            confirm=str(confirm),
        )
        status = 200 if res.get("ok") else 409
        self._send_json(status, res)

    def log_message(self, format: str, *args: Any) -> None:  # pylint: disable=redefined-builtin,arguments-differ
        """Potlačí výpisy do stdout."""
        # Keep stdout clean; proxy logs are elsewhere.


class ControlAPIServer:
    """Thin wrapper pro ThreadingHTTPServer s control API handlerem."""

    def __init__(self, *, host: str, port: int, proxy: Any):
        """Inicializuje Control API server (bez spuštění)."""
        self.host = host
        self.port = port
        self.proxy = proxy
        self._thread: threading.Thread | None = None
        self._httpd: ThreadingHTTPServer | None = None
        self._control_api_token = self._load_or_generate_token()

    def _load_or_generate_token(self) -> str:
        """Load existing token from file or generate a new one."""
        if os.path.exists(TOKEN_FILE_PATH):
            with open(TOKEN_FILE_PATH, "r", encoding="utf-8") as f:
                token = f.read().strip()
                if token:
                    return token

        token = secrets.token_urlsafe(32)
        os.makedirs(os.path.dirname(TOKEN_FILE_PATH), exist_ok=True)
        with open(TOKEN_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(token)
        logger.info("Control API token generated and saved to %s", TOKEN_FILE_PATH)
        return token

    def start(self) -> None:
        """Spustí HTTP server v background threadu."""
        httpd = ThreadingHTTPServer((self.host, self.port), _Handler)
        httpd.proxy = self.proxy # type: ignore[attr-defined]
        httpd.control_api_token = self._control_api_token # type: ignore[attr-defined]
        self._httpd = httpd

        t = threading.Thread(
            target=httpd.serve_forever,
            name="oig-control-api",
            daemon=True)
        t.start()
        self._thread = t

    def stop(self) -> None:
        """Bezpečně zastaví server."""
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
