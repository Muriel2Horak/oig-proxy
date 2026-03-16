"""Tests for sensor/processor.py — FrameProcessor."""
from __future__ import annotations

# pyright: reportMissingImports=false

import json
from unittest.mock import MagicMock, patch

import pytest

from sensor.processor import FrameProcessor
from sensor.loader import SensorMapLoader
from mqtt.client import MQTTClient


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

@pytest.fixture
def mock_mqtt() -> MagicMock:
    """Create a mock MQTTClient."""
    mqtt = MagicMock(spec=MQTTClient)
    mqtt.send_discovery.return_value = True
    mqtt.publish_state.return_value = True
    return mqtt


@pytest.fixture
def mock_loader() -> MagicMock:
    """Create a mock SensorMapLoader."""
    loader = MagicMock(spec=SensorMapLoader)
    loader.iter_sensors.return_value = []
    return loader


@pytest.fixture
def processor(mock_mqtt: MagicMock, mock_loader: MagicMock) -> FrameProcessor:
    """Create a FrameProcessor with mocked dependencies."""
    return FrameProcessor(mqtt=mock_mqtt, sensor_loader=mock_loader)


@pytest.fixture
def processor_with_proxy(mock_mqtt: MagicMock, mock_loader: MagicMock) -> FrameProcessor:
    return FrameProcessor(
        mqtt=mock_mqtt,
        sensor_loader=mock_loader,
        proxy_device_id="oig_proxy",
    )


# -----------------------------------------------------------------------------
# Basic processing tests
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_empty_data(processor: FrameProcessor, mock_mqtt: MagicMock) -> None:
    """Empty data should not publish anything."""
    await processor.process("DEV01", "tbl_actual", {})
    mock_mqtt.publish_state.assert_not_called()


def test_publish_all_discovery_uses_sensor_map_entries(processor: FrameProcessor, mock_mqtt: MagicMock, mock_loader: MagicMock) -> None:
    mock_loader.iter_sensors.return_value = [
        ("tbl_actual", "Temp", {"name_cs": "Teplota", "device_mapping": "inverter"}),
        ("tbl_batt", "BAT_V", {"name_cs": "Baterie - Napeti", "device_mapping": "battery"}),
    ]

    processor.publish_all_discovery("DEV01")

    assert mock_mqtt.send_discovery.call_count == 2


def test_publish_all_discovery_skips_isnew_tables(processor: FrameProcessor, mock_mqtt: MagicMock, mock_loader: MagicMock) -> None:
    mock_loader.iter_sensors.return_value = [
        ("IsNewFW", "BAT_C", {"name_cs": "Stav nabití"}),
        ("IsNewSet", "BAT_C", {"name_cs": "Stav nabití"}),
        ("IsNewWeather", "BAT_C", {"name_cs": "Stav nabití"}),
        ("proxy_control", "PROXY_MODE", {"name_cs": "Proxy - Režim"}),
        ("tbl_actual", "BAT_C", {"name_cs": "Stav nabití"}),
    ]

    processor.publish_all_discovery("DEV01")

    assert mock_mqtt.send_discovery.call_count == 1
    assert mock_mqtt.send_discovery.call_args.kwargs["table"] == "tbl_actual"


def test_publish_all_discovery_skips_batt_prm2_for_single_bank(processor: FrameProcessor, mock_mqtt: MagicMock, mock_loader: MagicMock) -> None:
    mock_loader.iter_sensors.return_value = [
        ("tbl_batt_prm1", "BAT_CH_HI", {"name_cs": "Baterie - Horní mez nabíjení"}),
        ("tbl_batt_prm2", "BAT_CH_HI", {"name_cs": "Baterie - Horní mez nabíjení"}),
    ]

    processor.publish_all_discovery("DEV01")

    assert mock_mqtt.send_discovery.call_count == 1
    assert mock_mqtt.send_discovery.call_args.kwargs["table"] == "tbl_batt_prm1"


@pytest.mark.asyncio
async def test_tbl_events_publishes_to_proxy_device(processor_with_proxy: FrameProcessor, mock_mqtt: MagicMock, mock_loader: MagicMock) -> None:
    mock_loader.lookup.return_value = {
        "name_cs": "Typ udalosti",
        "device_mapping": "proxy",
    }

    await processor_with_proxy.process("DEV01", "tbl_events", {"Type": "Setting"})

    args = mock_mqtt.publish_state.call_args[0]
    assert args[0] == "oig_proxy"
    assert args[1] == "tbl_events"


@pytest.mark.asyncio
async def test_process_skips_internal_keys(processor: FrameProcessor, mock_mqtt: MagicMock) -> None:
    """Keys starting with _ should be skipped."""
    await processor.process("DEV01", "tbl_actual", {"_raw": "data", "_internal": True, "Temp": 25})
    
    # publish_state should be called with only non-internal keys
    mock_mqtt.publish_state.assert_called_once()
    pub_data = mock_mqtt.publish_state.call_args[0][2]
    assert "_raw" not in pub_data
    assert "_internal" not in pub_data
    assert "Temp" in pub_data


@pytest.mark.asyncio
async def test_process_looks_up_sensor_metadata(processor: FrameProcessor, mock_loader: MagicMock) -> None:
    """Should look up sensor metadata from loader."""
    mock_loader.lookup.return_value = {
        "name_cs": "Teplota",
        "unit_of_measurement": "°C",
        "device_class": "temperature",
        "state_class": "measurement",
        "device_mapping": "inverter",
    }
    
    await processor.process("DEV01", "tbl_actual", {"Temp": 25.5})
    
    mock_loader.lookup.assert_called_once_with("tbl_actual", "Temp")


@pytest.mark.asyncio
async def test_process_normalizes_isnew_to_tbl_actual(processor: FrameProcessor, mock_loader: MagicMock) -> None:
    mock_loader.lookup.return_value = {
        "name_cs": "Baterie - Stav nabití",
        "unit_of_measurement": "%",
        "device_mapping": "battery",
    }

    await processor.process("DEV01", "IsNewFW", {"BAT_C": 91})

    mock_loader.lookup.assert_called_once_with("tbl_actual", "BAT_C")


@pytest.mark.asyncio
async def test_process_skips_batt_prm2_when_bat_n_is_single_bank(processor: FrameProcessor, mock_mqtt: MagicMock, mock_loader: MagicMock) -> None:
    mock_loader.lookup.return_value = {
        "name_cs": "Baterie - Horní mez nabíjení",
        "device_mapping": "battery",
    }

    await processor.process("DEV01", "tbl_batt_prms", {"BAT_N": 1})
    await processor.process("DEV01", "tbl_batt_prm2", {"BAT_CH_HI": 0})

    called_tables = [c[0][1] for c in mock_mqtt.publish_state.call_args_list]
    assert "tbl_batt_prms" in called_tables
    assert "tbl_batt_prm2" not in called_tables


@pytest.mark.asyncio
async def test_process_allows_batt_prm2_when_bat_n_is_two_or_more(processor: FrameProcessor, mock_mqtt: MagicMock, mock_loader: MagicMock) -> None:
    mock_loader.lookup.return_value = {
        "name_cs": "Baterie - Horní mez nabíjení",
        "device_mapping": "battery",
    }

    await processor.process("DEV01", "tbl_batt_prms", {"BAT_N": 2})
    await processor.process("DEV01", "tbl_batt_prm2", {"BAT_CH_HI": 0})

    called_tables = [c[0][1] for c in mock_mqtt.publish_state.call_args_list]
    assert "tbl_batt_prm2" in called_tables


@pytest.mark.asyncio
async def test_process_skips_batt_prm2_when_values_equal_prm1(processor: FrameProcessor, mock_mqtt: MagicMock, mock_loader: MagicMock) -> None:
    mock_loader.lookup.return_value = {
        "name_cs": "Baterie - Horní mez nabíjení",
        "device_mapping": "battery",
    }

    await processor.process("DEV01", "tbl_batt_prms", {"BAT_N": 2})
    await processor.process("DEV01", "tbl_batt_prm1", {"BAT_CH_HI": 0, "BAT_DI_HI": 0})
    await processor.process("DEV01", "tbl_batt_prm2", {"BAT_CH_HI": 0, "BAT_DI_HI": 0})

    called_tables = [c[0][1] for c in mock_mqtt.publish_state.call_args_list]
    assert called_tables.count("tbl_batt_prm1") == 1
    assert "tbl_batt_prm2" not in called_tables


@pytest.mark.asyncio
async def test_process_keeps_batt_prm2_when_values_differ_from_prm1(processor: FrameProcessor, mock_mqtt: MagicMock, mock_loader: MagicMock) -> None:
    mock_loader.lookup.return_value = {
        "name_cs": "Baterie - Horní mez nabíjení",
        "device_mapping": "battery",
    }

    await processor.process("DEV01", "tbl_batt_prms", {"BAT_N": 2})
    await processor.process("DEV01", "tbl_batt_prm1", {"BAT_CH_HI": 0, "BAT_DI_HI": 0})
    await processor.process("DEV01", "tbl_batt_prm2", {"BAT_CH_HI": 10, "BAT_DI_HI": 0})

    prm2_calls = [c for c in mock_mqtt.publish_state.call_args_list if c[0][1] == "tbl_batt_prm2"]
    assert len(prm2_calls) == 1
    assert prm2_calls[0][0][2] == {"BAT_CH_HI": 10}


@pytest.mark.asyncio
async def test_process_sends_discovery_with_metadata(processor: FrameProcessor, mock_mqtt: MagicMock, mock_loader: MagicMock) -> None:
    """Should send HA discovery with metadata from sensor_map."""
    mock_loader.lookup.return_value = {
        "name_cs": "Teplota",
        "unit_of_measurement": "°C",
        "device_class": "temperature",
        "state_class": "measurement",
        "device_mapping": "inverter",
    }
    
    await processor.process("DEV01", "tbl_actual", {"Temp": 25.5})
    
    mock_mqtt.send_discovery.assert_called_once()
    call_kwargs = mock_mqtt.send_discovery.call_args.kwargs
    assert call_kwargs["device_id"] == "DEV01"
    assert call_kwargs["table"] == "tbl_actual"
    assert call_kwargs["sensor_key"] == "Temp"
    assert call_kwargs["sensor_name"] == "Teplota"
    assert call_kwargs["unit"] == "°C"
    assert call_kwargs["device_class"] == "temperature"
    assert call_kwargs["state_class"] == "measurement"
    assert call_kwargs["device_mapping"] == "inverter"


@pytest.mark.asyncio
async def test_process_publishes_state(processor: FrameProcessor, mock_mqtt: MagicMock, mock_loader: MagicMock) -> None:
    """Should publish state data to MQTT."""
    mock_loader.lookup.return_value = None # No metadata
    
    await processor.process("DEV01", "tbl_actual", {"Temp": 25.5, "Humid": 60})
    
    mock_mqtt.publish_state.assert_called_once()
    call_args = mock_mqtt.publish_state.call_args[0]
    assert call_args[0] == "DEV01"
    assert call_args[1] == "tbl_actual"
    pub_data = call_args[2]
    assert pub_data["Temp"] == 25.5
    assert pub_data["Humid"] == 60


# -----------------------------------------------------------------------------
# Discovery metadata tests
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_discovery_uses_name_cs_when_available(processor: FrameProcessor, mock_mqtt: MagicMock, mock_loader: MagicMock) -> None:
    """Should prefer name_cs over name for sensor name."""
    mock_loader.lookup.return_value = {
        "name": "Temperature",
        "name_cs": "Teplota",
        "unit_of_measurement": "°C",
    }
    
    await processor.process("DEV01", "tbl_actual", {"Temp": 25})
    
    call_kwargs = mock_mqtt.send_discovery.call_args.kwargs
    assert call_kwargs["sensor_name"] == "Teplota"


@pytest.mark.asyncio
async def test_send_discovery_falls_back_to_name_when_name_cs_missing(processor: FrameProcessor, mock_mqtt: MagicMock, mock_loader: MagicMock) -> None:
    """Should fall back to name when name_cs is not available."""
    mock_loader.lookup.return_value = {
        "name": "Temperature",
        "unit_of_measurement": "°C",
    }
    
    await processor.process("DEV01", "tbl_actual", {"Temp": 25})
    
    call_kwargs = mock_mqtt.send_discovery.call_args.kwargs
    assert call_kwargs["sensor_name"] == "Temperature"


@pytest.mark.asyncio
async def test_send_discovery_falls_back_to_key_when_no_name(processor: FrameProcessor, mock_mqtt: MagicMock, mock_loader: MagicMock) -> None:
    """Should fall back to key when neither name nor name_cs is available."""
    mock_loader.lookup.return_value = {
        "unit_of_measurement": "°C",
    }
    
    await processor.process("DEV01", "tbl_actual", {"Temp": 25})
    
    call_kwargs = mock_mqtt.send_discovery.call_args.kwargs
    assert call_kwargs["sensor_name"] == "Temp"


@pytest.mark.asyncio
async def test_send_discovery_with_binary_sensor(processor: FrameProcessor, mock_mqtt: MagicMock, mock_loader: MagicMock) -> None:
    """Should handle binary sensor metadata."""
    mock_loader.lookup.return_value = {
        "name_cs": "Nabíjení aktivní",
        "is_binary": True,
        "device_class": "battery_charging",
        "device_mapping": "battery",
    }
    
    await processor.process("DEV01", "tbl_actual", {"CHARGE": 1})
    
    call_kwargs = mock_mqtt.send_discovery.call_args.kwargs
    assert call_kwargs["sensor_name"] == "Nabíjení aktivní"
    assert call_kwargs["device_class"] == "battery_charging"
    assert call_kwargs["is_binary"] is True


@pytest.mark.asyncio
async def test_send_discovery_with_entity_category(processor: FrameProcessor, mock_mqtt: MagicMock, mock_loader: MagicMock) -> None:
    """Should handle entity_category metadata."""
    mock_loader.lookup.return_value = {
        "name_cs": "Stav systému",
        "entity_category": "diagnostic",
        "device_mapping": "inverter",
    }
    
    await processor.process("DEV01", "tbl_actual", {"Status": "OK"})
    
    mock_mqtt.send_discovery.assert_called_once()
    call_kwargs = mock_mqtt.send_discovery.call_args.kwargs
    assert call_kwargs["entity_category"] == "diagnostic"


# -----------------------------------------------------------------------------
# Warning decoding tests
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_decodes_warnings_when_warnings_3f_present(processor: FrameProcessor, mock_mqtt: MagicMock, mock_loader: MagicMock) -> None:
    """Should decode warnings when field has warnings_3f definition."""
    mock_loader.lookup.return_value = {
        "name_cs": "Stav FVE",
        "warnings_3f": [
            {"bit": 8, "key": "ERR_PV", "remark_cs": "Výpadek FV vstupu 1"},
            {"bit": 4, "key": "ERR_PV", "remark_cs": "Výpadek FV vstupu 2"},
        ],
    }
    
    # Value with bit 8 set (0b100000000 = 256)
    await processor.process("DEV01", "tbl_actual", {"ERR_PV": 256})
    
    pub_data = mock_mqtt.publish_state.call_args[0][2]
    assert "ERR_PV_warnings" in pub_data
    assert "ERR_PV" in pub_data["ERR_PV_warnings"]
    assert "ERR_PV_warnings_cs" in pub_data
    assert "Výpadek FV vstupu 1" in pub_data["ERR_PV_warnings_cs"]


@pytest.mark.asyncio
async def test_process_decodes_multiple_warnings(processor: FrameProcessor, mock_mqtt: MagicMock, mock_loader: MagicMock) -> None:
    """Should decode multiple warning bits."""
    mock_loader.lookup.return_value = {
        "name_cs": "Chyby baterie",
        "warnings_3f": [
            {"bit": 4, "key": "ERR_BATT_LOW", "remark_cs": "Baterie nízká"},
            {"bit": 3, "key": "ERR_BATT_UNDER", "remark_cs": "Baterie pod napětím"},
        ],
    }
    
    # Value with bits 4 and 3 set (1<<4 | 1<<3 = 16 + 8 = 24)
    await processor.process("DEV01", "tbl_actual", {"ERR_BATT": 24})
    
    pub_data = mock_mqtt.publish_state.call_args[0][2]
    assert "ERR_BATT_warnings" in pub_data
    warnings = pub_data["ERR_BATT_warnings"]
    assert "ERR_BATT_LOW" in warnings
    assert "ERR_BATT_UNDER" in warnings
    assert "ERR_BATT_warnings_cs" in pub_data


@pytest.mark.asyncio
async def test_process_no_warnings_when_value_is_zero(processor: FrameProcessor, mock_mqtt: MagicMock, mock_loader: MagicMock) -> None:
    """Should not add warnings field when no warnings are set."""
    mock_loader.lookup.return_value = {
        "name_cs": "Stav FVE",
        "warnings_3f": [
            {"bit": 8, "key": "ERR_PV", "remark_cs": "Výpadek FV vstupu 1"},
        ],
    }
    
    await processor.process("DEV01", "tbl_actual", {"ERR_PV": 0})
    
    pub_data = mock_mqtt.publish_state.call_args[0][2]
    # When no warnings, the _warnings key should not be present
    assert "ERR_PV_warnings" not in pub_data


@pytest.mark.asyncio
async def test_process_no_warnings_when_warnings_3f_empty(processor: FrameProcessor, mock_mqtt: MagicMock, mock_loader: MagicMock) -> None:
    """Should not add warnings field when warnings_3f is empty."""
    mock_loader.lookup.return_value = {
        "name_cs": "Teplota",
        "warnings_3f": [],
    }
    
    await processor.process("DEV01", "tbl_actual", {"Temp": 25})
    
    pub_data = mock_mqtt.publish_state.call_args[0][2]
    assert "Temp_warnings" not in pub_data


@pytest.mark.asyncio
async def test_process_no_warnings_when_warnings_3f_missing(processor: FrameProcessor, mock_mqtt: MagicMock, mock_loader: MagicMock) -> None:
    """Should not add warnings field when warnings_3f is not present."""
    mock_loader.lookup.return_value = {
        "name_cs": "Teplota",
    }
    
    await processor.process("DEV01", "tbl_actual", {"Temp": 25})
    
    pub_data = mock_mqtt.publish_state.call_args[0][2]
    assert "Temp_warnings" not in pub_data


# -----------------------------------------------------------------------------
# Edge cases
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_handles_none_metadata(processor: FrameProcessor, mock_mqtt: MagicMock, mock_loader: MagicMock) -> None:
    """Should handle missing sensor_map entry gracefully."""
    mock_loader.lookup.return_value = None
    
    await processor.process("DEV01", "tbl_actual", {"UnknownSensor": 42})
    
    # Should still publish the value even without metadata
    mock_mqtt.publish_state.assert_called_once()
    pub_data = mock_mqtt.publish_state.call_args[0][2]
    assert pub_data["UnknownSensor"] == 42
    mock_mqtt.send_discovery.assert_not_called()


@pytest.mark.asyncio
async def test_process_handles_null_values_in_metadata(processor: FrameProcessor, mock_mqtt: MagicMock, mock_loader: MagicMock) -> None:
    """Should handle null values in metadata gracefully."""
    mock_loader.lookup.return_value = {
        "name_cs": None,
        "name": "Temperature",
        "unit_of_measurement": None,
        "device_class": None,
        "state_class": None,
    }
    
    await processor.process("DEV01", "tbl_actual", {"Temp": 25})
    
    call_kwargs = mock_mqtt.send_discovery.call_args.kwargs
    assert call_kwargs["sensor_name"] == "Temperature"  # Falls back to name
    assert call_kwargs["unit"] == ""  # Empty string for None
    assert call_kwargs["device_class"] == ""  # Empty string for None


@pytest.mark.asyncio
async def test_tbl_actual_mirrors_values_to_core_table(processor: FrameProcessor, mock_mqtt: MagicMock, mock_loader: MagicMock) -> None:
    mock_loader.iter_sensors.return_value = [
        ("tbl_actual", "FV_P1", {"name_cs": "Aktuální FV1"}),
        ("tbl_dc_in", "FV_P1", {"name_cs": "FVE string 1"}),
    ]

    fresh_processor = FrameProcessor(mqtt=mock_mqtt, sensor_loader=mock_loader)

    def _lookup(table: str, key: str):
        if (table, key) == ("tbl_actual", "FV_P1"):
            return {"name_cs": "Aktuální FV1", "device_mapping": "pv"}
        if (table, key) == ("tbl_dc_in", "FV_P1"):
            return {"name_cs": "FVE string 1", "device_mapping": "pv"}
        return None

    mock_loader.lookup.side_effect = _lookup

    await fresh_processor.process("DEV01", "tbl_actual", {"FV_P1": 321})

    assert mock_mqtt.publish_state.call_count == 2
    first = mock_mqtt.publish_state.call_args_list[0][0]
    second = mock_mqtt.publish_state.call_args_list[1][0]
    assert first[1] == "tbl_actual"
    assert first[2]["FV_P1"] == 321
    assert second[1] == "tbl_dc_in"
    assert second[2]["FV_P1"] == 321


@pytest.mark.asyncio
async def test_process_preserves_original_value_in_pub_data(processor: FrameProcessor, mock_mqtt: MagicMock, mock_loader: MagicMock) -> None:
    """Should preserve original values in published data."""
    mock_loader.lookup.return_value = {
        "name_cs": "Teplota",
        "unit_of_measurement": "°C",
    }
    
    await processor.process("DEV01", "tbl_actual", {"Temp": 25.5, "Count": 42})
    
    pub_data = mock_mqtt.publish_state.call_args[0][2]
    assert pub_data["Temp"] == 25.5
    assert pub_data["Count"] == 42


@pytest.mark.asyncio
async def test_process_with_multiple_sensors(processor: FrameProcessor, mock_mqtt: MagicMock, mock_loader: MagicMock) -> None:
    """Should handle multiple sensors in single frame."""
    def lookup_side_effect(table: str, key: str):
        lookups = {
            "Temp": {"name_cs": "Teplota", "unit_of_measurement": "°C", "device_class": "temperature"},
            "Humid": {"name_cs": "Vlhkost", "unit_of_measurement": "%", "device_class": "humidity"},
            "Pressure": {"name_cs": "Tlak", "unit_of_measurement": "hPa"},
        }
        return lookups.get(key)
    
    mock_loader.lookup.side_effect = lookup_side_effect
    
    await processor.process("DEV01", "tbl_actual", {
        "Temp": 25.5,
        "Humid": 60,
        "Pressure": 1013,
    })
    
    # Should send discovery for each sensor
    assert mock_mqtt.send_discovery.call_count == 3
    
    # Should publish all values
    pub_data = mock_mqtt.publish_state.call_args[0][2]
    assert pub_data["Temp"] == 25.5
    assert pub_data["Humid"] == 60
    assert pub_data["Pressure"] == 1013


# -----------------------------------------------------------------------------
# Integration-style tests with real loader
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_integration_with_real_loader_and_temp_sensor(tmp_path) -> None:
    """Integration test with real SensorMapLoader and Temp sensor."""
    # Create a minimal sensor_map.json
    sensor_map = {
        "sensors": {
            "tbl_actual:Temp": {
                "name": "Box Temperature (live)",
                "name_cs": "Střídač - Teplota v boxu (live)",
                "unit_of_measurement": "°C",
                "device_class": "temperature",
                "state_class": "measurement",
                "device_mapping": "inverter",
            }
        }
    }
    
    map_file = tmp_path / "sensor_map.json"
    map_file.write_text(json.dumps(sensor_map))
    
    loader = SensorMapLoader(str(map_file))
    loader.load()
    
    mock_mqtt = MagicMock(spec=MQTTClient)
    mock_mqtt.send_discovery.return_value = True
    mock_mqtt.publish_state.return_value = True
    
    processor = FrameProcessor(mqtt=mock_mqtt, sensor_loader=loader)
    
    await processor.process("DEV01", "tbl_actual", {"Temp": 25.5})
    
    # Verify discovery was sent with correct unit
    call_kwargs = mock_mqtt.send_discovery.call_args.kwargs
    assert call_kwargs["unit"] == "°C"
    assert call_kwargs["device_class"] == "temperature"
    assert call_kwargs["state_class"] == "measurement"
    
    # Verify state was published
    pub_data = mock_mqtt.publish_state.call_args[0][2]
    assert pub_data["Temp"] == 25.5
