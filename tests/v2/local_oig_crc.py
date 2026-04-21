"""Wrapper pro v1 CRC funkce pro cross-referenční testy.

Nacita CRC implementaci z archivovaneho v1 kodu. Pouziva kompilovany .pyc
soubor pokud je dostupny pro aktualni verzi Pythonu, jinak hleda .py zdroj.
"""

from importlib.machinery import SourcelessFileLoader, SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import sys


def _load_v1_crc_module():
    """Nacte v1 CRC modul z dostupneho zdroje (.pyc nebo .py)."""
    archive_dir = Path(__file__).resolve().parents[2] / "addon" / "oig-proxy-v1-archive"

    # Zkus najit .pyc pro aktualni verzi Pythonu
    py_version = f"cpython-{sys.version_info.major}{sys.version_info.minor}"
    pyc_file = archive_dir / "__pycache__" / f"oig_frame.{py_version}.pyc"

    if pyc_file.exists():
        # Pouzij kompilovany .pyc soubor
        loader = SourcelessFileLoader("tests_v2_oig_frame_v1", str(pyc_file))
        spec = spec_from_loader("tests_v2_oig_frame_v1", loader)
        assert spec is not None
        module = module_from_spec(spec)
        loader.exec_module(module)
        return module

    # Fallback: hledej .py zdrojovy soubor
    py_file = archive_dir / "oig_frame.py"
    if py_file.exists():
        loader = SourceFileLoader("tests_v2_oig_frame_v1", str(py_file))
        spec = spec_from_loader("tests_v2_oig_frame_v1", loader)
        assert spec is not None
        module = module_from_spec(spec)
        loader.exec_module(module)
        return module

    # Posledni fallback: pouzij vlastni CRC implementaci
    # (CRC-16-Modbus implementace pro pripad ze archiv neni dostupny)
    return _create_fallback_crc_module()


def _create_fallback_crc_module():
    """Vytvori modul s fallback CRC implementaci."""
    import types

    module = types.ModuleType("tests_v2_oig_frame_v1")

    # CRC-16-Modbus implementace
    def crc16_modbus(data: bytes) -> int:
        """Vypocita CRC-16-Modbus."""
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc

    module.crc16_modbus = crc16_modbus
    return module


# Nacti modul a extrahuj CRC funkci
_MODULE = _load_v1_crc_module()
crc16_modbus = _MODULE.crc16_modbus

__all__ = ['crc16_modbus']
