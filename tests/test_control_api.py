# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=unused-argument,too-few-public-methods,no-member,use-implicit-booleaness-not-comparison,line-too-long
# pylint: disable=invalid-name,too-many-statements,too-many-instance-attributes,wrong-import-position,wrong-import-order
# pylint: disable=deprecated-module,too-many-locals,too-many-lines,attribute-defined-outside-init,unexpected-keyword-arg
# pylint: disable=duplicate-code
import json
import io
from types import SimpleNamespace
from unittest.mock import patch

from control_api import ControlAPIServer, _Handler


class DummyProxy:
    def __init__(self) -> None:
        self.last_setting = None

    def get_control_api_health(self):
        return {"ok": True, "status": "ready"}

    def control_api_send_setting(
            self,
            *,
            tbl_name,
            tbl_item,
            new_value,
            confirm):
        self.last_setting = {
            "tbl_name": tbl_name,
            "tbl_item": tbl_item,
            "new_value": new_value,
            "confirm": confirm,
        }
        return {"ok": True, "tbl_name": tbl_name, "tbl_item": tbl_item}


class _TestHandler(_Handler):
    def __init__(self, request_bytes: bytes, server):
        # pylint: disable=super-init-not-called
        # BaseRequestHandler.__init__ requires a real socket; tests use in-memory streams.
        self.rfile = io.BytesIO(request_bytes)
        self.wfile = io.BytesIO()
        self.raw_requestline = self.rfile.readline()
        self.error_code = self.error_message = None
        self.server = server
        self.request_version = "HTTP/1.1"
        self.close_connection = True
        if not self.parse_request():
            return
        if self.command == "GET":
            self.do_GET()
        elif self.command == "POST":
            self.do_POST()


def _request(method: str, path: str, body: bytes | None,
             headers: dict[str, str] | None = None):
    headers = headers or {}
    if body is None:
        body = b""
    headers.setdefault("Content-Length", str(len(body)))
    raw = (
        f"{method} {path} HTTP/1.1\r\n"
        + "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        + "\r\n"
    ).encode("utf-8") + body
    server = SimpleNamespace(proxy=DummyProxy())
    handler = _TestHandler(raw, server)
    response = handler.wfile.getvalue()
    header_part, body_part = response.split(b"\r\n\r\n", 1)
    status_line = header_part.split(b"\r\n", 1)[0].decode("utf-8")
    status = int(status_line.split(" ")[1])
    return status, body_part, server.proxy


def test_control_api_health_and_setting():
    status, data, proxy = _request("GET", "/api/health", None)
    payload = json.loads(data.decode("utf-8"))
    assert status == 200
    assert payload["ok"] is True

    body = json.dumps(
        {"tbl_name": "tbl_box_prms", "tbl_item": "MODE", "new_value": "3"}
    ).encode("utf-8")
    status, data, proxy = _request(
        "POST",
        "/api/setting",
        body,
        {"Content-Type": "application/json"},
    )
    assert status == 200
    assert proxy.last_setting["tbl_name"] == "tbl_box_prms"
    assert proxy.last_setting["tbl_item"] == "MODE"


def test_control_api_missing_fields():
    body = json.dumps({"tbl_name": "tbl_box_prms"}).encode("utf-8")
    status, data, _proxy = _request(
        "POST",
        "/api/setting",
        body,
        {"Content-Type": "application/json"},
    )
    payload = json.loads(data.decode("utf-8"))
    assert status == 400
    assert payload["error"] == "missing_fields"


def test_control_api_not_found_paths():
    status, _data, _proxy = _request("GET", "/nope", None)
    assert status == 404

    status, _data, _proxy = _request(
        "POST", "/nope", b"{}", {"Content-Type": "application/json"})
    assert status == 404


def test_control_api_xml_fallback():
    body = (
        "<TblName>tbl_box_prms</TblName>"
        "<TblItem>MODE</TblItem>"
        "<NewValue>2</NewValue>"
    ).encode("utf-8")
    status, data, _proxy = _request(
        "POST", "/api/setting", body, {"Content-Type": "text/plain"})
    payload = json.loads(data.decode("utf-8"))
    assert status == 200
    assert payload["ok"] is True


def test_control_api_stop_without_start():
    proxy = DummyProxy()
    server = ControlAPIServer(host="127.0.0.1", port=0, proxy=proxy)
    server.stop()


def test_control_api_start_and_stop():
    proxy = DummyProxy()
    server = ControlAPIServer(host="127.0.0.1", port=0, proxy=proxy)

    class DummyHTTPD:
        def __init__(self, *_args, **_kwargs):
            self.proxy = None
            self._serve_called = False

        def serve_forever(self):
            self._serve_called = True

        def shutdown(self):
            return None

        def server_close(self):
            return None

    with patch("control_api.ThreadingHTTPServer", DummyHTTPD):
        server.start()
        assert server._httpd is not None
        assert server._httpd.proxy is proxy
        server.stop()
