import http.client
import json

from control_api import ControlAPIServer


class DummyProxy:
    def __init__(self) -> None:
        self.last_setting = None

    def get_control_api_health(self):
        return {"ok": True, "status": "ready"}

    def control_api_send_setting(self, *, tbl_name, tbl_item, new_value, confirm):
        self.last_setting = {
            "tbl_name": tbl_name,
            "tbl_item": tbl_item,
            "new_value": new_value,
            "confirm": confirm,
        }
        return {"ok": True, "tbl_name": tbl_name, "tbl_item": tbl_item}


def _request(port: int, method: str, path: str, body: bytes | None, headers: dict[str, str] | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, data


def test_control_api_health_and_setting():
    proxy = DummyProxy()
    server = ControlAPIServer(host="127.0.0.1", port=0, proxy=proxy)
    server.start()
    try:
        port = server._httpd.server_address[1]  # type: ignore[union-attr]

        status, data = _request(port, "GET", "/api/health", None)
        payload = json.loads(data.decode("utf-8"))
        assert status == 200
        assert payload["ok"] is True

        body = json.dumps(
            {"tbl_name": "tbl_box_prms", "tbl_item": "MODE", "new_value": "3"}
        ).encode("utf-8")
        status, data = _request(
            port,
            "POST",
            "/api/setting",
            body,
            {"Content-Type": "application/json"},
        )
        assert status == 200
        assert proxy.last_setting["tbl_name"] == "tbl_box_prms"
        assert proxy.last_setting["tbl_item"] == "MODE"
    finally:
        server.stop()


def test_control_api_missing_fields():
    proxy = DummyProxy()
    server = ControlAPIServer(host="127.0.0.1", port=0, proxy=proxy)
    server.start()
    try:
        port = server._httpd.server_address[1]  # type: ignore[union-attr]
        body = json.dumps({"tbl_name": "tbl_box_prms"}).encode("utf-8")
        status, data = _request(
            port,
            "POST",
            "/api/setting",
            body,
            {"Content-Type": "application/json"},
        )
        payload = json.loads(data.decode("utf-8"))
        assert status == 400
        assert payload["error"] == "missing_fields"
    finally:
        server.stop()


def test_control_api_not_found_paths():
    proxy = DummyProxy()
    server = ControlAPIServer(host="127.0.0.1", port=0, proxy=proxy)
    server.start()
    try:
        port = server._httpd.server_address[1]  # type: ignore[union-attr]
        status, _data = _request(port, "GET", "/nope", None)
        assert status == 404

        status, _data = _request(port, "POST", "/nope", b"{}", {"Content-Type": "application/json"})
        assert status == 404
    finally:
        server.stop()


def test_control_api_xml_fallback():
    proxy = DummyProxy()
    server = ControlAPIServer(host="127.0.0.1", port=0, proxy=proxy)
    server.start()
    try:
        port = server._httpd.server_address[1]  # type: ignore[union-attr]
        body = (
            "<TblName>tbl_box_prms</TblName>"
            "<TblItem>MODE</TblItem>"
            "<NewValue>2</NewValue>"
        ).encode("utf-8")
        status, data = _request(port, "POST", "/api/setting", body, {"Content-Type": "text/plain"})
        payload = json.loads(data.decode("utf-8"))
        assert status == 200
        assert payload["ok"] is True
    finally:
        server.stop()
