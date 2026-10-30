import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from arthera_finance import doctor
from arthera_finance.brokers import config


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BROKERS_CONFIG_PATH", tmp_path / "brokers.json")
    monkeypatch.setenv("FINANCIAL_SERVICE_BROKERS_ENABLED", "1")
    monkeypatch.setattr(doctor, "find_spec", lambda module: object())
    def save(rows):
        path = tmp_path / "brokers.json"
        path.write_text(json.dumps({"brokers": rows}))
        path.chmod(0o600)
        return path
    return save


def codes(report):
    return {issue["code"] for row in report["connections"] for issue in row["issues"]}


def test_paper_check_never_instantiates_adapter_or_creates_ledger(profiles, monkeypatch, tmp_path):
    from arthera_finance.brokers import registry
    profiles([{"id": "practice", "type": "paper"}])
    monkeypatch.setattr(registry, "get_broker_class", lambda *a: pytest.fail("adapter invoked"))
    before = set(tmp_path.iterdir())
    report = doctor.diagnose()
    assert report["success"] and report["live_connection_tested"] is False
    assert set(tmp_path.iterdir()) == before


def test_auth_diagnostics_reveal_neither_values_nor_environment_names(profiles, monkeypatch):
    profiles([{"id": "mine", "type": "alpaca", "credentials": {"access_token": "PRIVATE_TOKEN_REF"}}])
    monkeypatch.setenv("FINANCIAL_SERVICE_NETWORK", "1")
    assert codes(doctor.diagnose()) == {"credentials_unavailable"}
    monkeypatch.setenv("PRIVATE_TOKEN_REF", "secret-must-never-appear")
    report = doctor.diagnose()
    assert report["success"]
    assert "PRIVATE_TOKEN_REF" not in json.dumps(report)
    assert "secret-must-never-appear" not in json.dumps(report)
    assert report["live_connection_tested"] is False


@pytest.mark.parametrize("kind", sorted(doctor.ADAPTERS))
def test_every_adapter_has_offline_diagnostics(profiles, kind):
    profiles([{"id": "mine", "type": kind}])
    assert doctor.diagnose()["connections"][0]["type"] == kind


def test_gateway_selection_missing_sdk_and_permission_are_distinct(profiles, monkeypatch):
    profiles([{"id": "mine", "type": "ibkr"}])
    monkeypatch.setenv("FINANCIAL_SERVICE_BROKERS_ENABLED", "0")
    monkeypatch.setattr(doctor, "find_spec", lambda module: None)
    assert codes(doctor.diagnose()) == {
        "broker_access_disabled", "network_disabled", "broker_sdk_missing", "required_settings_missing"}


def test_alpaca_mixed_or_incomplete_auth_is_rejected(profiles):
    for refs in ({"api_key": "MY_KEY"}, {"api_key": "MY_KEY", "access_token": "MY_TOKEN"}):
        profiles([{"id": "mine", "type": "alpaca", "credentials": refs}])
        assert "invalid_auth_mode" in codes(doctor.diagnose())


def test_only_selected_profile_is_diagnosed_and_unknown_never_falls_back(profiles):
    profiles([{"id": "practice", "type": "paper"}, {"id": "other", "type": "ibkr"}])
    assert doctor.diagnose("practice")["success"]
    report = doctor.diagnose("missing")
    assert report["error_code"] == "connection_unavailable" and not report["connections"]


def test_invalid_profiles_and_sdk_exceptions_are_redacted(profiles, monkeypatch):
    path = profiles([{"id": "mine", "type": "paper", "password": "sensitive"}])
    report = doctor.diagnose()
    assert report["error_code"] == "broker_configuration_invalid"
    assert "sensitive" not in json.dumps(report)
    path.write_text("not json with sensitive")
    assert "sensitive" not in json.dumps(doctor.diagnose())
    profiles([{"id": "mine", "type": "ibkr", "account_id": "PRIVATE_ACCOUNT"}])
    def fail(*args):
        raise ValueError("PRIVATE_ACCOUNT sensitive SDK error")
    monkeypatch.setattr(doctor, "find_spec", fail)
    report = doctor.diagnose()
    assert "sdk_detection_failed" in codes(report)
    assert "PRIVATE_ACCOUNT" not in json.dumps(report)


def test_desktop_platform_and_communication_password_reference(profiles, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    profiles([{"id": "mine", "type": "easytrader", "credentials": {"comm_password": "MY_COMM_PASSWORD"}}])
    assert codes(doctor.diagnose()) == {"unsupported_platform", "credentials_unavailable", "network_disabled"}
    monkeypatch.setenv("MY_COMM_PASSWORD", "secret")
    assert config.resolve_profile(config.load_profiles()[0])["comm_password"] == "secret"
    profiles([{"id": "mine", "type": "easytrader", "comm_password": "inline-secret"}])
    assert doctor.diagnose()["error_code"] == "broker_configuration_invalid"


def test_cli_exit_codes_and_json_do_not_claim_live_success(profiles, capsys):
    profiles([{"id": "practice", "type": "paper"}])
    assert doctor.main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["live_connection_tested"] is False
    assert doctor.main(["--connection", "missing"]) == 1
    assert "connection_unavailable" in capsys.readouterr().out


def test_no_profiles_is_actionable_not_a_success(profiles):
    profiles([])
    assert doctor.diagnose()["error_code"] == "no_connections"


def test_module_command_runs_without_mcp_or_broker_sdks(profiles):
    path = profiles([{"id": "practice", "type": "paper"}])
    env = {**os.environ, "FINANCIAL_SERVICE_HOME": str(path.parent),
           "PYTHONPATH": str(Path(doctor.__file__).parents[1])}
    result = subprocess.run([sys.executable, "-S", "-m", "arthera_finance.doctor", "--json"],
                            env=env, capture_output=True, timeout=10, check=False)
    assert result.returncode == 0
    assert json.loads(result.stdout)["success"] is True
    assert result.stderr == b""
