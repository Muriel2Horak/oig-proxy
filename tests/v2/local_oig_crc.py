"""Wrapper pro v1 CRC funkce pro cross-referenční testy."""
import sys
import os

# Uložíme si aktuální path
original_path = sys.path.copy()

# Přidáme v1 addon do path na konec (aby v2 mělo přednost)
v1_addon = os.path.join(os.path.dirname(__file__), '..', '..', 'addon', 'oig-proxy')
if v1_addon not in sys.path:
    sys.path.append(v1_addon)

from oig_frame import crc16_modbus

# Obnovíme path, aby v1 neovlivňovalo další importy
sys.path = original_path

__all__ = ['crc16_modbus']
