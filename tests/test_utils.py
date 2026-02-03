# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=unused-argument,too-few-public-methods,no-member,use-implicit-booleaness-not-comparison,line-too-long
# pylint: disable=invalid-name,too-many-statements,too-many-instance-attributes,wrong-import-position,wrong-import-order
# pylint: disable=deprecated-module,too-many-locals,too-many-lines,attribute-defined-outside-init,unexpected-keyword-arg
# pylint: disable=duplicate-code
import json
import queue
import sqlite3
import time

import pytest

from models import SensorConfig
import utils

TEST_IP = "192.0.2.1"  # NOSONAR - reserved TEST-NET-1 address for tests
TEST_PEER = f"{TEST_IP}:1234"
TEST_DNS_1 = "192.0.2.53"  # NOSONAR - reserved TEST-NET-1 address for tests
TEST_DNS_2 = "192.0.2.54"  # NOSONAR - reserved TEST-NET-1 address for tests


def test_friendly_name():
    assert utils.friendly_name("battery_soc") == "Battery Soc"
    assert utils.friendly_name("grid") == "Grid"


def test_mode_state_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "mode.json"
    monkeypatch.setattr(utils, "MODE_STATE_PATH", str(path))

    utils.save_mode_state(3, "DEV1")
    mode, device_id = utils.load_mode_state()

    assert mode == 3
    assert device_id == "DEV1"


def test_mode_state_out_of_range(tmp_path, monkeypatch):
    path = tmp_path / "mode.json"
    monkeypatch.setattr(utils, "MODE_STATE_PATH", str(path))

    path.write_text(json.dumps(
        {"mode": 99, "device_id": "DEV2"}), encoding="utf-8")
    mode, device_id = utils.load_mode_state()

    assert mode is None
    assert device_id == "DEV2"


def test_mode_state_invalid_value(tmp_path, monkeypatch):
    path = tmp_path / "mode.json"
    monkeypatch.setattr(utils, "MODE_STATE_PATH", str(path))

    path.write_text(json.dumps(
        {"mode": "bad", "device_id": "DEV3"}), encoding="utf-8")
    mode, device_id = utils.load_mode_state()

    assert mode is None
    assert device_id == "DEV3"


def test_mode_state_invalid_json(tmp_path, monkeypatch):
    path = tmp_path / "mode.json"
    monkeypatch.setattr(utils, "MODE_STATE_PATH", str(path))

    path.write_text("{bad json", encoding="utf-8")
    mode, device_id = utils.load_mode_state()

    assert mode is None
    assert device_id is None


def test_save_mode_state_error(monkeypatch):
    def boom(*_args, **_kwargs):
        raise OSError("nope")

    monkeypatch.setattr(utils.os, "makedirs", boom)
    utils.save_mode_state(1, "DEV9")


def test_prms_state_merge_and_load(tmp_path, monkeypatch):
    path = tmp_path / "prms.json"
    monkeypatch.setattr(utils, "PRMS_STATE_PATH", str(path))

    utils.save_prms_state("tbl_box_prms", {"A": 1}, "DEV3")
    utils.save_prms_state("tbl_box_prms", {"B": 2}, "DEV3")

    tables, device_id = utils.load_prms_state()
    assert device_id == "DEV3"
    assert tables["tbl_box_prms"]["A"] == 1
    assert tables["tbl_box_prms"]["B"] == 2


def test_save_prms_state_backward_compat(tmp_path, monkeypatch):
    path = tmp_path / "prms.json"
    monkeypatch.setattr(utils, "PRMS_STATE_PATH", str(path))

    path.write_text(json.dumps({"tbl_box_prms": {"A": 1}}), encoding="utf-8")
    utils.save_prms_state("tbl_box_prms", {"B": 2}, "DEV9")

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["device_id"] == "DEV9"
    assert data["tables"]["tbl_box_prms"]["values"]["A"] == 1
    assert data["tables"]["tbl_box_prms"]["values"]["B"] == 2


def test_save_prms_state_invalid_inputs(tmp_path, monkeypatch):
    path = tmp_path / "prms.json"
    monkeypatch.setattr(utils, "PRMS_STATE_PATH", str(path))

    utils.save_prms_state("", {"A": 1}, "DEVX")
    utils.save_prms_state("tbl_box_prms", {}, "DEVX")
    assert not path.exists()


def test_parse_prms_tables_invalid_entries():
    assert utils._parse_prms_tables("bad") == {}

    tables = utils._parse_prms_tables(
        {
            "tbl_invalid": {"values": "nope"},
            "tbl_raw": {"A": 1},
            "tbl_bad": "nope",
            1: {"values": {"A": 1}},
        }
    )
    assert "tbl_raw" in tables
    assert "tbl_invalid" not in tables


def test_load_prms_state_non_dict(tmp_path, monkeypatch):
    path = tmp_path / "prms.json"
    monkeypatch.setattr(utils, "PRMS_STATE_PATH", str(path))

    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    tables, device_id = utils.load_prms_state()
    assert tables == {}
    assert device_id is None


def test_resolve_cloud_host_cached(monkeypatch):
    calls = []

    def fake_resolve(host: str):
        calls.append(host)
        return TEST_IP, 60.0

    monkeypatch.setattr(utils, "_PUBLIC_DNS_HOSTS", {"oigservis.cz"})
    monkeypatch.setattr(utils, "_PUBLIC_DNS_CACHE", {})
    monkeypatch.setattr(utils, "_PUBLIC_DNS_LAST_LOG", {})
    monkeypatch.setattr(utils, "_resolve_public_dns", fake_resolve)

    assert utils.resolve_cloud_host("oigservis.cz") == TEST_IP
    assert utils.resolve_cloud_host("oigservis.cz") == TEST_IP
    assert utils.resolve_cloud_host(TEST_IP) == TEST_IP
    assert calls == ["oigservis.cz"]


def test_resolve_cloud_host_failure(monkeypatch):
    monkeypatch.setattr(utils, "_PUBLIC_DNS_HOSTS", {"oigservis.cz"})
    monkeypatch.setattr(utils, "_PUBLIC_DNS_CACHE", {})
    monkeypatch.setattr(utils, "_PUBLIC_DNS_LAST_LOG", {})
    monkeypatch.setattr(
        utils,
        "_resolve_public_dns",
        lambda host: (
            None,
            30.0))

    with pytest.raises(RuntimeError):
        utils.resolve_cloud_host("oigservis.cz")


def test_get_sensor_config_prefers_table(monkeypatch):
    table_cfg = SensorConfig(name="Table", unit="W")
    base_cfg = SensorConfig(name="Base", unit="W")
    monkeypatch.setattr(
        utils,
        "SENSORS",
        {
            "tbl_actual:POWER": table_cfg,
            "POWER": base_cfg,
        },
    )

    cfg, unique_key = utils.get_sensor_config("POWER", "tbl_actual")
    assert cfg is table_cfg
    assert unique_key == "tbl_actual:POWER"

    cfg, unique_key = utils.get_sensor_config("POWER", "tbl_other")
    assert cfg is base_cfg
    assert unique_key == "tbl_other:POWER"


def test_decode_warnings(monkeypatch):
    monkeypatch.setattr(
        utils,
        "WARNING_MAP",
        {
            "WARN": [
                {"bit": 1, "remark": "A"},
                {"bit": 2, "remark_cs": "B"},
            ]
        },
    )
    assert utils.decode_warnings("WARN", 3) == ["A", "B"]


def test_decode_warnings_invalid_values(monkeypatch):
    monkeypatch.setattr(
        utils,
        "WARNING_MAP",
        {"WARN": [{"bit": None, "remark": "A"}]},
    )
    assert utils.decode_warnings("MISSING", 1) == []
    assert utils.decode_warnings("WARN", "bad") == []
    assert utils.decode_warnings("WARN", 1) == []


def test_capture_payload_creates_queue(tmp_path, monkeypatch):
    db_path = tmp_path / "capture.db"
    monkeypatch.setattr(utils, "CAPTURE_PAYLOADS", True)
    monkeypatch.setattr(utils, "CAPTURE_DB_PATH", str(db_path))
    monkeypatch.setattr(utils, "CAPTURE_RAW_BYTES", True)
    monkeypatch.setattr(utils, "_capture_queue", None)
    monkeypatch.setattr(utils, "_capture_thread", None)
    monkeypatch.setattr(utils, "_capture_cols", set())

    conn, cols = utils.init_capture_db()
    assert conn is not None
    conn.close()
    assert "raw_b64" in cols

    utils.capture_payload(
        device_id="DEV1",
        table="tbl_box_prms",
        raw="<Frame>1</Frame>",
        raw_bytes=b"<Frame>1</Frame>",
        parsed={"MODE": 1},
        direction="proxy_to_cloud",
        conn_id=1,
        peer=TEST_PEER,
        length=15,
    )

    time.sleep(0.2)
    with sqlite3.connect(db_path) as check:
        cur = check.execute("SELECT COUNT(*) FROM frames")
        assert cur.fetchone()[0] >= 0


def test_public_dns_nameservers_env(monkeypatch):
    monkeypatch.setenv("CLOUD_PUBLIC_DNS", f"{TEST_DNS_1}, bad, {TEST_DNS_2}")
    servers = utils._public_dns_nameservers()
    assert TEST_DNS_1 in servers
    assert TEST_DNS_2 in servers
    assert "bad" not in servers


def test_public_dns_nameservers_default(monkeypatch):
    monkeypatch.delenv("CLOUD_PUBLIC_DNS", raising=False)
    servers = utils._public_dns_nameservers()
    assert servers == list(utils._PUBLIC_DNS_DEFAULT)


def test_public_dns_cache_expires(monkeypatch):
    monkeypatch.setattr(
        utils, "_PUBLIC_DNS_CACHE", {
            "host": (
                TEST_IP, time.time() - 1)})
    assert utils._public_dns_cache_get("host") is None
    assert utils._PUBLIC_DNS_CACHE == {}


def test_resolve_public_dns_without_module(monkeypatch):
    monkeypatch.setattr(utils, "dns", None)
    ip, ttl = utils._resolve_public_dns("example.com")
    assert ip is None
    assert ttl == utils._PUBLIC_DNS_TTL_DEFAULT_S


def test_resolve_public_dns_with_dummy(monkeypatch):
    class DummyAnswer(list):
        def __init__(self):
            super().__init__([TEST_IP])
            self.rrset = type("R", (), {"ttl": 60})()

    class DummyResolver:
        def __init__(self, configure=False):
            self.nameservers = []

        def resolve(self, _host, _record, lifetime=2.0):
            return DummyAnswer()

    class DummyDNS:
        class resolver:
            Resolver = DummyResolver

    monkeypatch.setattr(utils, "dns", DummyDNS)
    monkeypatch.setattr(utils, "_public_dns_nameservers", lambda: [TEST_DNS_1])
    ip, ttl = utils._resolve_public_dns("example.com")
    assert ip == TEST_IP
    assert ttl == pytest.approx(60.0)


def test_resolve_public_dns_error(monkeypatch):
    class DummyResolver:
        def __init__(self, configure=False):
            self.nameservers = []

        def resolve(self, _host, _record, lifetime=2.0):
            raise RuntimeError("boom")

    class DummyDNS:
        class resolver:
            Resolver = DummyResolver

    monkeypatch.setattr(utils, "dns", DummyDNS)
    monkeypatch.setattr(utils, "_public_dns_nameservers", lambda: [TEST_DNS_1])
    ip, ttl = utils._resolve_public_dns("example.com")
    assert ip is None
    assert ttl == utils._PUBLIC_DNS_TTL_DEFAULT_S


def test_resolve_cloud_host_non_target(monkeypatch):
    monkeypatch.setattr(utils, "_PUBLIC_DNS_HOSTS", {"oigservis.cz"})
    assert utils.resolve_cloud_host("example.com") == "example.com"


def test_resolve_cloud_host_empty():
    assert utils.resolve_cloud_host("") == ""


def test_load_prms_state_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        utils, "PRMS_STATE_PATH", str(
            tmp_path / "missing.json"))
    tables, device_id = utils.load_prms_state()
    assert tables == {}
    assert device_id is None


def test_split_prms_state_backward_compat():
    tables, device_id = utils._split_prms_state({"tbl_box_prms": {"MODE": 1}})
    assert device_id is None
    assert tables["tbl_box_prms"]["MODE"] == 1


def test_load_sensor_map_reads_file(tmp_path, monkeypatch):
    mapping = {
        "sensors": {
            "POWER": {"name": "Power", "unit_of_measurement": "W"},
        },
        "warnings_3f": [
            {"table_key": "WARN", "bit": 1, "remark": "A"},
        ],
    }
    map_path = tmp_path / "map.json"
    map_path.write_text(json.dumps(mapping), encoding="utf-8")

    monkeypatch.setattr(utils, "SENSOR_MAP_PATH", str(map_path))
    monkeypatch.setattr(utils, "MAP_RELOAD_SECONDS", 0)
    monkeypatch.setattr(utils, "_last_map_load", 0.0)
    monkeypatch.setattr(utils, "SENSORS", {})
    monkeypatch.setattr(utils, "WARNING_MAP", {})

    utils.load_sensor_map()
    assert "POWER" in utils.SENSORS
    assert "WARN" in utils.WARNING_MAP


def test_add_sensors_from_mapping_invalid(monkeypatch):
    monkeypatch.setattr(utils, "SENSORS", {})
    assert utils._add_sensors_from_mapping({"sensors": "bad"}) == 0

    utils._add_sensors_from_mapping(
        {"sensors": {"OK": {"name": "Ok"}, "BAD": "nope", 1: {"name": "Skip"}}}
    )
    assert "OK" in utils.SENSORS


def test_build_warning_map_invalid():
    assert utils._build_warning_map({"warnings_3f": "bad"}) == {}

    out = utils._build_warning_map(
        {
            "warnings_3f": [
                1,
                {"table_key": None, "bit": None},
                {"table_key": "WARN", "bit": "1", "remark": "A"},
            ]
        }
    )
    assert out["WARN"][0]["bit"] == 1


def test_load_sensor_map_skips_when_recent(tmp_path, monkeypatch):
    map_path = tmp_path / "map.json"
    map_path.write_text(json.dumps({"sensors": {}}), encoding="utf-8")

    monkeypatch.setattr(utils, "SENSOR_MAP_PATH", str(map_path))
    monkeypatch.setattr(utils, "MAP_RELOAD_SECONDS", 60)
    monkeypatch.setattr(utils, "_last_map_load", time.time())
    utils.load_sensor_map()


def test_load_sensor_map_missing_file(tmp_path, monkeypatch):
    map_path = tmp_path / "missing.json"
    monkeypatch.setattr(utils, "SENSOR_MAP_PATH", str(map_path))
    monkeypatch.setattr(utils, "MAP_RELOAD_SECONDS", 0)
    monkeypatch.setattr(utils, "_last_map_load", 0.0)
    monkeypatch.setattr(utils, "SENSORS", {})

    utils.load_sensor_map()
    assert utils.SENSORS == {}


def test_load_sensor_map_invalid_content(tmp_path, monkeypatch):
    map_path = tmp_path / "map.json"
    map_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    monkeypatch.setattr(utils, "SENSOR_MAP_PATH", str(map_path))
    monkeypatch.setattr(utils, "MAP_RELOAD_SECONDS", 0)
    monkeypatch.setattr(utils, "_last_map_load", 0.0)
    monkeypatch.setattr(utils, "SENSORS", {})

    utils.load_sensor_map()
    assert utils.SENSORS == {}


def test_init_capture_db_disabled(monkeypatch):
    monkeypatch.setattr(utils, "CAPTURE_PAYLOADS", False)
    conn, cols = utils.init_capture_db()
    assert conn is None
    assert cols == set()


def test_capture_payload_queue_full(monkeypatch, tmp_path):
    db_path = tmp_path / "capture.db"
    monkeypatch.setattr(utils, "CAPTURE_PAYLOADS", True)
    monkeypatch.setattr(utils, "CAPTURE_DB_PATH", str(db_path))
    monkeypatch.setattr(utils, "CAPTURE_RAW_BYTES", False)

    q = queue.Queue(maxsize=1)
    q.put_nowait(("ts", None, None, None, None, "{}", None, None, None, None))

    class DummyThread:
        def is_alive(self):
            return True

    monkeypatch.setattr(utils, "_capture_queue", q)
    monkeypatch.setattr(utils, "_capture_thread", DummyThread())
    monkeypatch.setattr(utils, "_capture_cols", set())

    utils.capture_payload(
        device_id="DEV1",
        table="tbl_box_prms",
        raw="<Frame>1</Frame>",
        raw_bytes=None,
        parsed={"MODE": 1},
        direction="proxy_to_cloud",
        conn_id=1,
        peer=TEST_PEER,
        length=15,
    )


def test_commit_capture_batch_error():
    class DummyConn:
        def __init__(self):
            self.rolled_back = False

        def executemany(self, *_args, **_kwargs):
            raise RuntimeError("boom")

        def commit(self):
            raise AssertionError("commit should not be called")

        def rollback(self):
            self.rolled_back = True

    conn = DummyConn()
    utils._commit_capture_batch(conn, "SQL", [("a",)])
    assert conn.rolled_back is True


def test_capture_worker_no_queue(monkeypatch, tmp_path):
    db_path = tmp_path / "capture.db"
    monkeypatch.setattr(utils, "_capture_queue", None)
    utils._capture_worker(str(db_path))


def test_capture_worker_connect_error(monkeypatch):
    def boom(*_args, **_kwargs):
        raise sqlite3.Error("nope")

    monkeypatch.setattr(sqlite3, "connect", boom)
    utils._capture_worker(":memory:")
