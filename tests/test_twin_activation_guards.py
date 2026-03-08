# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring
# pylint: disable=wrong-import-position,wrong-import-order,duplicate-code
# pylint: disable=too-few-public-methods
import importlib
import os

import pytest


def _reload_config_with_env(monkeypatch, env_overrides: dict):
    for key, value in env_overrides.items():
        monkeypatch.setenv(key, value)
    for key in (
        "TWIN_ENABLED", "TWIN_KILL_SWITCH", "LOCAL_CONTROL_ROUTING",
        "TWIN_CLOUD_ALIGNED", "TWIN_ACK_DEADLINE_SECONDS",
        "TWIN_APPLIED_DEADLINE_SECONDS", "TWIN_VERBOSE_LOGGING",
    ):
        if key not in env_overrides:
            monkeypatch.delenv(key, raising=False)

    import config
    return importlib.reload(config)


# ============================================================================
# QA Scenario 1: Valid config matrix
# ============================================================================

class TestValidConfigMatrix:
    VALID_COMBOS = [
        # (#1) Defaults — legacy mode
        {},
        # (#2) Twin enabled, auto routing
        {"TWIN_ENABLED": "true"},
        # (#3) Twin enabled, force_twin
        {"TWIN_ENABLED": "true", "LOCAL_CONTROL_ROUTING": "force_twin"},
        # (#4) Twin enabled, force_cloud
        {"TWIN_ENABLED": "true", "LOCAL_CONTROL_ROUTING": "force_cloud"},
        # (#5) Kill switch without force_twin (noop)
        {"TWIN_ENABLED": "true", "TWIN_KILL_SWITCH": "true"},
        # (#6) Cloud-aligned twin
        {"TWIN_ENABLED": "true", "TWIN_CLOUD_ALIGNED": "true"},
        # (#10) Kill switch on disabled twin (noop)
        {"TWIN_ENABLED": "false", "TWIN_KILL_SWITCH": "true"},
        # (#11) Cloud-aligned + force_twin
        {
            "TWIN_ENABLED": "true",
            "TWIN_CLOUD_ALIGNED": "true",
            "LOCAL_CONTROL_ROUTING": "force_twin",
        },
        # (#12) Kill switch + force_cloud (twin disabled, cloud forced)
        {
            "TWIN_ENABLED": "true",
            "TWIN_KILL_SWITCH": "true",
            "LOCAL_CONTROL_ROUTING": "force_cloud",
        },
    ]

    @pytest.mark.parametrize("env", VALID_COMBOS, ids=[
        "defaults", "twin_on", "force_twin", "force_cloud",
        "kill_switch", "cloud_aligned", "kill_noop",
        "cloud_aligned_force_twin", "kill_force_cloud",
    ])
    def test_valid_combos_pass_guard(self, monkeypatch, env):
        cfg = _reload_config_with_env(monkeypatch, env)
        cfg.validate_startup_guards()


# ============================================================================
# QA Scenario 2: Invalid config rejection
# ============================================================================

class TestInvalidConfigRejection:

    def test_g1_force_twin_with_twin_disabled(self, monkeypatch):
        cfg = _reload_config_with_env(monkeypatch, {
            "TWIN_ENABLED": "false",
            "LOCAL_CONTROL_ROUTING": "force_twin",
        })
        with pytest.raises(ValueError, match="force_twin requires TWIN_ENABLED=true"):
            cfg.validate_startup_guards()

    def test_g1_force_twin_with_kill_switch(self, monkeypatch):
        cfg = _reload_config_with_env(monkeypatch, {
            "TWIN_ENABLED": "true",
            "TWIN_KILL_SWITCH": "true",
            "LOCAL_CONTROL_ROUTING": "force_twin",
        })
        with pytest.raises(ValueError, match="force_twin requires TWIN_ENABLED=true"):
            cfg.validate_startup_guards()

    def test_g2_cloud_aligned_without_twin(self, monkeypatch):
        cfg = _reload_config_with_env(monkeypatch, {
            "TWIN_ENABLED": "false",
            "TWIN_CLOUD_ALIGNED": "true",
        })
        with pytest.raises(ValueError, match="TWIN_CLOUD_ALIGNED=true requires TWIN_ENABLED=true"):
            cfg.validate_startup_guards()

    def test_g3_cloud_aligned_with_kill_switch(self, monkeypatch):
        cfg = _reload_config_with_env(monkeypatch, {
            "TWIN_ENABLED": "true",
            "TWIN_KILL_SWITCH": "true",
            "TWIN_CLOUD_ALIGNED": "true",
        })
        with pytest.raises(ValueError, match="TWIN_CLOUD_ALIGNED=true is incompatible with TWIN_KILL_SWITCH"):
            cfg.validate_startup_guards()

    def test_g4_ack_deadline_zero(self, monkeypatch):
        cfg = _reload_config_with_env(monkeypatch, {
            "TWIN_ACK_DEADLINE_SECONDS": "0",
        })
        with pytest.raises(ValueError, match="TWIN_ACK_DEADLINE_SECONDS must be > 0"):
            cfg.validate_startup_guards()

    def test_g5_applied_deadline_negative(self, monkeypatch):
        cfg = _reload_config_with_env(monkeypatch, {
            "TWIN_APPLIED_DEADLINE_SECONDS": "-1",
        })
        with pytest.raises(ValueError, match="TWIN_APPLIED_DEADLINE_SECONDS must be > 0"):
            cfg.validate_startup_guards()

    def test_g6_applied_less_than_ack(self, monkeypatch):
        cfg = _reload_config_with_env(monkeypatch, {
            "TWIN_ACK_DEADLINE_SECONDS": "60",
            "TWIN_APPLIED_DEADLINE_SECONDS": "30",
        })
        with pytest.raises(ValueError, match="TWIN_APPLIED_DEADLINE_SECONDS must be >= TWIN_ACK_DEADLINE_SECONDS"):
            cfg.validate_startup_guards()

    def test_multiple_errors_collected(self, monkeypatch):
        cfg = _reload_config_with_env(monkeypatch, {
            "TWIN_ENABLED": "false",
            "LOCAL_CONTROL_ROUTING": "force_twin",
            "TWIN_CLOUD_ALIGNED": "true",
            "TWIN_ACK_DEADLINE_SECONDS": "0",
        })
        with pytest.raises(ValueError) as exc_info:
            cfg.validate_startup_guards()
        msg = str(exc_info.value)
        assert "TWIN_ACK_DEADLINE_SECONDS" in msg
        assert "force_twin" in msg
        assert "TWIN_CLOUD_ALIGNED" in msg
