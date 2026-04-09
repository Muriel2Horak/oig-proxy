# pylint: disable=missing-module-docstring,missing-function-docstring
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ADDON_DIR = ROOT / "addon" / "oig-proxy"
CONFIG_PATH = ADDON_DIR / "config.py"

spec = importlib.util.spec_from_file_location("addon_oig_proxy_config", CONFIG_PATH)
assert spec is not None
assert spec.loader is not None
config_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config_module)
Config = config_module.Config


def test_config_default_target_server_is_bridge(monkeypatch) -> None:
    monkeypatch.delenv("TARGET_SERVER", raising=False)

    config = Config()

    assert config.cloud_host == "bridge.oigpower.cz"


def test_run_uses_target_server_for_dns_override() -> None:
    run_script = (ADDON_DIR / "run").read_text(encoding="utf-8")

    assert "TARGET_SERVER_RAW=$(bashio::config 'target_server'" in run_script
    assert 'echo "address=/$TARGET_SERVER_RAW/$DNS_TARGET_IP" > /etc/dnsmasq.d/oig.conf' in run_script
    assert 'if [ "$TARGET_SERVER_RAW" = "bridge.oigpower.cz" ]; then' in run_script
    assert 'echo "address=/oigservis.cz/$DNS_TARGET_IP" >> /etc/dnsmasq.d/oig.conf' in run_script
