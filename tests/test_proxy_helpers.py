import re

from oig_frame import compute_frame_checksum
import proxy as proxy_module
from config import MQTT_NAMESPACE


def _assert_crc(frame: str) -> None:
    match = re.search(r"<CRC>(\d+)</CRC>", frame)
    assert match is not None
    crc = int(match.group(1))
    assert compute_frame_checksum(frame.encode("utf-8")) == crc


def test_format_control_tx_and_result():
    tx = {
        "tbl_name": "tbl_box_prms",
        "tbl_item": "MODE",
        "new_value": 3,
        "stage": "sent",
        "_attempts": 2,
        "tx_id": "abc",
    }
    assert proxy_module.OIGProxy._format_control_tx(tx) == (
        "tbl_box_prms/MODE=3 (sent 2) tx=abc"
    )

    result = {
        "status": "error",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "MODE",
        "new_value": 3,
        "error": "fail",
        "tx_id": "abc",
    }
    assert proxy_module.OIGProxy._format_control_result(result) == (
        "error tbl_box_prms/MODE=3 err=fail tx=abc"
    )


def test_build_frames_have_crc_and_crlf():
    frame = proxy_module.OIGProxy._build_getactual_frame().decode("utf-8")
    assert frame.endswith("\r\n")
    assert "<Result>ACK</Result>" in frame
    assert "<ToDo>GetActual</ToDo>" in frame
    _assert_crc(frame)

    ack = proxy_module.OIGProxy._build_ack_only_frame().decode("utf-8")
    assert ack.endswith("\r\n")
    assert "<Result>ACK</Result>" in ack
    _assert_crc(ack)

    end = proxy_module.OIGProxy._build_end_time_frame().decode("utf-8")
    assert "<Result>END</Result>" in end
    assert "<Time>" in end and "<UTCTime>" in end
    _assert_crc(end)


def test_infer_table_and_device_id():
    frame = "<Frame><TblName>tbl_box</TblName><ID_Device>123</ID_Device></Frame>"
    assert proxy_module.OIGProxy._infer_table_name(frame) == "tbl_box"
    assert proxy_module.OIGProxy._infer_device_id(frame) == "123"

    result_frame = "<Frame><Result>END</Result></Frame>"
    assert proxy_module.OIGProxy._infer_table_name(result_frame) == "END"


def test_mqtt_state_topic_parse():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    ok_topic = f"{MQTT_NAMESPACE}/DEV1/tbl_actual/state"
    device_id, table_name = proxy._parse_mqtt_state_topic(ok_topic)
    assert device_id == "DEV1"
    assert table_name == "tbl_actual"

    bad = proxy._parse_mqtt_state_topic("invalid/topic")
    assert bad == (None, None)


def test_control_helpers_and_setting_event():
    request_key = proxy_module.OIGProxy._control_build_request_key(
        tbl_name="tbl_box_prms",
        tbl_item="MODE",
        canon_value="3",
    )
    assert request_key == "tbl_box_prms/MODE/3"

    assert proxy_module.OIGProxy._control_result_key_state("accepted", None) == "queued"
    assert proxy_module.OIGProxy._control_result_key_state("completed", "noop_already_set") is None

    event = "Remotely : tbl_invertor_prm1 / AAC_MAX_CHRG: [50.0]->[120.0]"
    parsed = proxy_module.OIGProxy._parse_setting_event(event)
    assert parsed == ("tbl_invertor_prm1", "AAC_MAX_CHRG", "50.0", "120.0")


def test_should_persist_table():
    assert proxy_module.OIGProxy._should_persist_table(None) is False
    assert proxy_module.OIGProxy._should_persist_table("tbl_actual") is False
    assert proxy_module.OIGProxy._should_persist_table("tbl_box_prms") is True
    assert proxy_module.OIGProxy._should_persist_table("other") is False


def test_drop_replay_frame_detection():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    end_frame = (
        "<Frame><Result>END</Result><Reason>All data sent</Reason></Frame>"
    ).encode("utf-8")
    other_end = "<Frame><Result>END</Result></Frame>".encode("utf-8")

    assert proxy._should_drop_replay_frame("END", end_frame) is True
    assert proxy._should_drop_replay_frame("END", other_end) is False
    assert proxy._should_drop_replay_frame("tbl_actual", end_frame) is False


def test_control_normalize_value():
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)

    ok = proxy._control_normalize_value(
        tbl_name="tbl_box_prms",
        tbl_item="MODE",
        new_value="3",
    )
    assert ok == ("3", "3")

    bad = proxy._control_normalize_value(
        tbl_name="tbl_box_prms",
        tbl_item="MODE",
        new_value="9",
    )
    assert bad == (None, "bad_value")

    charge = proxy._control_normalize_value(
        tbl_name="tbl_invertor_prm1",
        tbl_item="AAC_MAX_CHRG",
        new_value="50",
    )
    assert charge == ("50.0", "50.0")


def test_control_coerce_value():
    coerce = proxy_module.OIGProxy._control_coerce_value
    assert coerce("true") is True
    assert coerce("false") is False
    assert coerce("12") == 12
    assert coerce("12.5") == 12.5
    assert coerce(" text ") == " text "
