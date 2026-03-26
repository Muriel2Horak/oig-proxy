import importlib


decode_warnings = importlib.import_module("sensor.warnings").decode_warnings
decode_warning_details = importlib.import_module("sensor.warnings").decode_warning_details


def test_decode_warnings_single_bit() -> None:
    warnings_list = [{"bit": 8, "key": "ERR_PV"}]
    result = decode_warnings(256, warnings_list)
    assert result == ["ERR_PV"]


def test_decode_warnings_zero_value() -> None:
    warnings_list = [{"bit": 8, "key": "ERR_PV"}, {"bit": 4, "key": "ERR_BATT"}]
    result = decode_warnings(0, warnings_list)
    assert result == []


def test_decode_warnings_multiple_bits() -> None:
    warnings_list = [
        {"bit": 8, "key": "ERR_PV"},
        {"bit": 4, "key": "ERR_BATT"},
        {"bit": 2, "key": "ERR_GRID"},
    ]
    result = decode_warnings(272, warnings_list)
    assert set(result) == {"ERR_PV", "ERR_BATT"}


def test_decode_warning_details_returns_texts_and_codes() -> None:
    warnings_list = [
        {
            "bit": 8,
            "key": "ERR_PV",
            "warning_code": 17,
            "remark": "Solar input 1 voltage too higher",
            "remark_cs": "Příliš vysoké napětí FV1",
        },
        {
            "bit": 4,
            "key": "ERR_BATT",
            "warning_code": 14,
            "remark": "Battery under",
            "remark_cs": "Příliš nízké napětí baterie",
        },
    ]
    result = decode_warning_details(272, warnings_list)
    assert len(result) == 2
    assert result[0]["key"] == "ERR_PV"
    assert result[0]["warning_code"] == 17
    assert result[0]["remark_cs"] == "Příliš vysoké napětí FV1"
