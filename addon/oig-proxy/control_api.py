#!/usr/bin/env python3
"""
Minimal HTTP control API for prototyping "Setting" writes to BOX.

Intentionally:
- no auth (prototype)
- minimal validation (only checks that BOX is connected and sending data)
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class _Handler(BaseHTTPRequestHandler):  # pylint: disable=invalid-name
    """HTTP handler pro Control API."""
    server_version = "OIGProxyControlAPI/0.1"

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        """Odešle JSON odpověď se zadaným HTTP statusem."""
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # pylint: disable=invalid-name
        """Zpracuje GET requesty Control API."""
        if self.path.rstrip("/") == "/api/health":
            proxy = self.server.proxy  # type: ignore[attr-defined]
            payload = proxy.get_control_api_health()
            self._send_json(200, payload)
            return

        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # pylint: disable=invalid-name
        """Zpracuje POST /api/setting pro odeslání Setting do BOXu."""
        if self.path.rstrip("/") != "/api/setting":
            self._send_json(404, {"error": "not_found"})
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

        proxy = self.server.proxy  # type: ignore[attr-defined]
        res = proxy.control_api_send_setting(
            tbl_name=str(tbl_name),
            tbl_item=str(tbl_item),
            new_value=str(new_value),
            confirm=str(confirm),
        )
        status = 200 if res.get("ok") else 409
        self._send_json(status, res)

    def log_message(self, _fmt: str, *args: Any) -> None:  # pylint: disable=arguments-differ
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

    def start(self) -> None:
        """Spustí HTTP server v background threadu."""
        httpd = ThreadingHTTPServer((self.host, self.port), _Handler)
        httpd.proxy = self.proxy  # type: ignore[attr-defined]
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
