import importlib
from typing import Any

ControlPipeline = importlib.import_module("control_pipeline").ControlPipeline


def test_format_tx_with_and_without_attempts():
    tx: dict[str, Any] = {
        "tbl_name": "tbl_box_prms",
        "tbl_item": "SA",
        "new_value": "1",
        "stage": "queued",
        "tx_id": "x",
    }
    formatted = ControlPipeline.format_tx(tx)
    assert "tbl_box_prms/SA=1" in formatted
    assert "tx=x" in formatted

    tx["_attempts"] = 3
    formatted_with_attempts = ControlPipeline.format_tx(tx)
    assert "queued 3" in formatted_with_attempts
