"""Test implementations for state processing components."""

from tests.state_processor import (
    StateMessageProcessor,
    ValueTransformer,
    StatePersistence,
)


class DummyTransformer(ValueTransformer):
    """Dummy transformer for testing - returns values unchanged."""

    def transform(self, tbl_name: str, tbl_item: str, value: Any) -> str:
        return str(value)


class DummyPersistence(StatePersistence):
    """Dummy persistence for testing - saves to memory."""

    def __init__(self):
        self.storage = {}

    def save(self, table_name: str, device_id: str, values: dict) -> None:
        self.storage[table_name] = values

    def load(self, table_name: str, device_id: str) -> dict:
        return self.storage.get(table_name, {})


def test_process_message_basic():
    """Test basic message processing."""
    processor = StateMessageProcessor(
        transformer=DummyTransformer(),
        persistence=DummyPersistence()
    )

    result = processor.process(
        topic="tele/DEV1/tbl_box_prms/SA",
        payload_text='{"SA": 1, "SB": 0}',
        device_id="DEV1",
        table_name="tbl_box_prms",
    )

    assert result["status"] == "ok"
    assert "SA" in result["saved"]
    assert "SB" in result["saved"]


def test_process_message_invalid_device_id():
    """Test message processing with AUTO device ID."""
    processor = StateMessageProcessor(
        transformer=DummyTransformer(),
        persistence=DummyPersistence()
    )

    result = processor.process(
        topic="tele/AUTO/tbl_box_prms/SA",
        payload_text='{"SA": 1}',
        device_id="AUTO",
        table_name="tbl_box_prms",
    )

    assert result["status"] == "error"
    assert result["error"] == "device_id_unknown"


def test_process_message_invalid_table():
    """Test message processing with non-tbl_ table name."""
    processor = StateMessageProcessor(
        transformer=DummyTransformer(),
        persistence=DummyPersistence()
    )

    result = processor.process(
        topic="tele/DEV1/status",
        payload_text='{"value": 1}',
        device_id="DEV1",
        table_name="status",
    )

    assert result["status"] == "error"
    assert result["error"] == "invalid_table_name"


def test_process_message_malformed_json():
    """Test message processing with malformed JSON."""
    processor = StateMessageProcessor(
        transformer=DummyTransformer(),
        persistence=DummyPersistence()
    )

    result = processor.process(
        topic="tele/DEV1/tbl_box_prms/SA",
        payload_text='invalid json',
        device_id="DEV1",
        table_name="tbl_box_prms",
    )

    assert result["status"] == "error"
    assert "json_parse_error" in result["error"]


def test_value_transformer():
    """Test value transformer interface."""
    transformer = DummyTransformer()
    
    assert transformer.transform("tbl_box_prms", "SA", 1) == "1"
    assert transformer.transform("tbl_box_prms", "SB", 0) == "0"


def test_state_persistence():
    """Test state persistence interface."""
    persistence = DummyPersistence()
    
    persistence.save("tbl_box_prms", "DEV1", {"SA": 1})
    loaded = persistence.load("tbl_box_prms", "DEV1")
    
    assert loaded["SA"] == 1
    assert persistence.load("tbl_box_prms", "OTHER") == {}
