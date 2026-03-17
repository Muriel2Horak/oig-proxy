"""Warning bits decoder for OIG Proxy v2."""

from typing import Any


def decode_warnings(field_value: int, warnings_list: list[dict[str, Any]]) -> list[str]:
    """
    Decode warning bits from a bit field.

    Args:
        field_value: Integer containing the bit field value.
        warnings_list: List of dicts with "bit" and "key" keys, e.g.:
            [{"bit": 8, "key": "ERR_PV", ...}, {"bit": 4, "key": "ERR_BATT", ...}]

    Returns:
        List of keys for warnings that are set in the bit field.
        Returns [w["key"] for w in warnings_list if field_value & (1 << w["bit"])]
    """
    if not warnings_list:
        return []

    result: list[str] = []
    for w in warnings_list:
        bit = w.get("bit")
        key = w.get("key")
        if bit is None or key is None:
            continue
        if field_value & (1 << bit):
            result.append(key)

    return result


def decode_warning_details(field_value: int, warnings_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not warnings_list:
        return []

    result: list[dict[str, Any]] = []
    for w in warnings_list:
        bit = w.get("bit")
        key = w.get("key")
        if bit is None or key is None:
            continue
        if field_value & (1 << bit):
            result.append(
                {
                    "key": key,
                    "warning_code": w.get("warning_code"),
                    "remark": w.get("remark"),
                    "remark_cs": w.get("remark_cs"),
                }
            )

    return result
