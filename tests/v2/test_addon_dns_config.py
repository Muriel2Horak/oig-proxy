# pylint: disable=missing-module-docstring,missing-function-docstring
from __future__ import annotations

import json
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ADDON_DIR = ROOT / "addon" / "oig-proxy"
CONFIG_PATH = ADDON_DIR / "config.py"
DOCS_PATH = ROOT / "docs" / "v2" / "configuration.md"

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


def test_config_defaults_local_getactual_disabled(monkeypatch) -> None:
    monkeypatch.delenv("LOCAL_GETACTUAL_ENABLED", raising=False)
    monkeypatch.delenv("LOCAL_GETACTUAL_INTERVAL_S", raising=False)

    config = Config()

    assert config.local_getactual_enabled is False
    assert config.local_getactual_interval_s == 10


def test_config_reads_local_getactual_env(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_GETACTUAL_ENABLED", "true")
    monkeypatch.setenv("LOCAL_GETACTUAL_INTERVAL_S", "15")

    config = Config()

    assert config.local_getactual_enabled is True
    assert config.local_getactual_interval_s == 15


def test_config_clamps_local_getactual_interval(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_GETACTUAL_ENABLED", "1")
    monkeypatch.setenv("LOCAL_GETACTUAL_INTERVAL_S", "3")

    config = Config()

    assert config.local_getactual_enabled is True
    assert config.local_getactual_interval_s == 10


def test_addon_config_exposes_local_getactual_options() -> None:
    addon_config = json.loads((ADDON_DIR / "config.json").read_text(encoding="utf-8"))

    assert addon_config["version"] == "2.2.0"
    assert addon_config["options"]["local_getactual_enabled"] is False
    assert addon_config["options"]["local_getactual_interval_s"] == 10
    assert addon_config["schema"]["local_getactual_enabled"] == "bool?"
    assert addon_config["schema"]["local_getactual_interval_s"] == "int?"


def test_addon_config_exposes_control_transaction_options() -> None:
    addon_config = json.loads((ADDON_DIR / "config.json").read_text(encoding="utf-8"))

    assert addon_config["options"]["control_mqtt_enabled"] is False
    assert addon_config["options"]["control_ack_timeout_s"] == 30.0
    assert addon_config["options"]["control_event_timeout_s"] == 300.0
    assert addon_config["options"]["control_command_ttl_s"] == 900.0
    assert addon_config["options"]["control_max_attempts"] == 8
    assert addon_config["schema"]["control_ack_timeout_s"] == "float"
    assert addon_config["schema"]["control_event_timeout_s"] == "float"
    assert addon_config["schema"]["control_command_ttl_s"] == "float"
    assert addon_config["schema"]["control_max_attempts"] == "int"


def test_configuration_docs_parameter_table_matches_addon_config() -> None:
    addon_config = json.loads((ADDON_DIR / "config.json").read_text(encoding="utf-8"))
    docs = DOCS_PATH.read_text(encoding="utf-8").splitlines()

    parameter_names: list[str] = []
    in_parameter_table = False
    for line in docs:
        if line.strip() == "## Parameter Table":
            in_parameter_table = True
            continue
        if in_parameter_table and line.startswith("## "):
            break
        if in_parameter_table and line.startswith("| `"):
            parameter_names.append(line.split("|")[1].strip().strip("`"))

    documented_options = [
        name
        for name in addon_config["options"]
        if name
        not in {
            "control_ack_timeout_s",
            "control_event_timeout_s",
            "control_command_ttl_s",
            "control_max_attempts",
        }
    ]
    assert parameter_names == documented_options


def test_run_exports_local_getactual_options() -> None:
    run_script = (ADDON_DIR / "run").read_text(encoding="utf-8")

    assert "LOCAL_GETACTUAL_ENABLED_RAW=$(bashio::config 'local_getactual_enabled')" in run_script
    assert 'export LOCAL_GETACTUAL_ENABLED="true"' in run_script
    assert 'export LOCAL_GETACTUAL_ENABLED="false"' in run_script
    assert "LOCAL_GETACTUAL_INTERVAL_RAW=$(bashio::config 'local_getactual_interval_s')" in run_script
    assert "LOCAL_GETACTUAL_INTERVAL_RAW=10" in run_script
    assert "export LOCAL_GETACTUAL_INTERVAL_S=$LOCAL_GETACTUAL_INTERVAL_RAW" in run_script


def test_run_exports_max_concurrent_connections_option() -> None:
    run_script = (ADDON_DIR / "run").read_text(encoding="utf-8")

    assert "MAX_CONCURRENT_CONNECTIONS_RAW=$(bashio::config 'max_concurrent_connections')" in run_script
    assert "MAX_CONCURRENT_CONNECTIONS_RAW=5" in run_script
    assert "export MAX_CONCURRENT_CONNECTIONS=$MAX_CONCURRENT_CONNECTIONS_RAW" in run_script


def test_run_exports_control_transaction_options() -> None:
    run_script = (ADDON_DIR / "run").read_text(encoding="utf-8")

    assert "CONTROL_ACK_TIMEOUT_S_RAW=$(bashio::config 'control_ack_timeout_s')" in run_script
    assert "CONTROL_ACK_TIMEOUT_S_RAW=30" in run_script
    assert "export CONTROL_ACK_TIMEOUT_S=$CONTROL_ACK_TIMEOUT_S_RAW" in run_script
    assert "CONTROL_EVENT_TIMEOUT_S_RAW=$(bashio::config 'control_event_timeout_s')" in run_script
    assert "CONTROL_EVENT_TIMEOUT_S_RAW=300" in run_script
    assert "export CONTROL_EVENT_TIMEOUT_S=$CONTROL_EVENT_TIMEOUT_S_RAW" in run_script
    assert "CONTROL_COMMAND_TTL_S_RAW=$(bashio::config 'control_command_ttl_s')" in run_script
    assert "CONTROL_COMMAND_TTL_S_RAW=900" in run_script
    assert "export CONTROL_COMMAND_TTL_S=$CONTROL_COMMAND_TTL_S_RAW" in run_script
    assert "CONTROL_MAX_ATTEMPTS_RAW=$(bashio::config 'control_max_attempts')" in run_script
    assert "CONTROL_MAX_ATTEMPTS_RAW=8" in run_script
    assert "export CONTROL_MAX_ATTEMPTS=$CONTROL_MAX_ATTEMPTS_RAW" in run_script
