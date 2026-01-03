import os
import sys


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ADDON_DIR = os.path.join(ROOT_DIR, "addon", "oig-proxy")
if ADDON_DIR not in sys.path:
    sys.path.insert(0, ADDON_DIR)
