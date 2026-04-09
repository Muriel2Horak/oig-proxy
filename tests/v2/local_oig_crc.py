"""Wrapper pro v1 CRC funkce pro cross-referenční testy."""

from importlib.machinery import SourcelessFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path


_V1_PYC = (
    Path(__file__).resolve().parents[2]
    / "addon"
    / "oig-proxy-v1-archive"
    / "__pycache__"
    / "oig_frame.cpython-312.pyc"
)
_LOADER = SourcelessFileLoader("tests_v2_oig_frame_v1", str(_V1_PYC))
_SPEC = spec_from_loader("tests_v2_oig_frame_v1", _LOADER)
assert _SPEC is not None
_MODULE = module_from_spec(_SPEC)
_LOADER.exec_module(_MODULE)

crc16_modbus = _MODULE.crc16_modbus

__all__ = ['crc16_modbus']
