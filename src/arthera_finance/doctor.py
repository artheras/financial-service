"""Offline broker setup checks; never import SDKs, connect or print credentials."""
import argparse
from importlib.util import find_spec
import json
import os
import sys

from .brokers.config import load_profiles
from .brokers.registry import ADAPTERS


SDK_MODULES = {
    "alpaca": "alpaca", "ibkr": "ib_insync", "futu": "futu",
    "tiger": "tigeropen", "longbridge": "longbridge", "webull": "webull",
    "trading212": "requests", "xtquant": "xtquant", "easytrader": "easytrader",
}
REQUIRED_FIELDS = {
    "ibkr": ("account_id",), "futu": ("account_id",),
    "tiger": ("tiger_id", "account", "private_key_path"),
    "longbridge": ("app_key", "app_secret", "access_token"),
    "webull": ("username", "password"), "trading212": ("api_key",),
    "xtquant": ("account_id", "path"),
}
HINTS = {
    "broker_access_disabled": "Set FINANCIAL_SERVICE_BROKERS_ENABLED=1 in the service host environment.",
    "network_disabled": "Set FINANCIAL_SERVICE_NETWORK=1 to permit this broker's account queries.",
    "adapter_unavailable": "Choose a supported broker adapter type.",
    "broker_sdk_missing": "Install the adapter's optional SDK in this interpreter; see the connection guide.",
    "sdk_detection_failed": "Check this interpreter's package installation.",
    "credentials_unavailable": "Set the selected profile's credential environment variables in the service host.",
    "required_settings_missing": "Complete the adapter's required host settings in brokers.json.",
    "invalid_auth_mode": "Choose either an Alpaca OAuth token or a complete API key/secret pair.",
    "unsupported_platform": "Run this desktop adapter on Windows with its supported trading client.",
}


def _issues(profile):
    kind = profile["type"]
    issues = []
    if os.getenv("FINANCIAL_SERVICE_BROKERS_ENABLED") != "1":
        issues.append("broker_access_disabled")
    if kind not in ADAPTERS:
        return issues + ["adapter_unavailable"]
    if kind != "paper" and os.getenv("FINANCIAL_SERVICE_NETWORK") != "1":
        issues.append("network_disabled")
    module = SDK_MODULES.get(kind)
    if module:
        try:
            if find_spec(module) is None:
                issues.append("broker_sdk_missing")
        except Exception:
            issues.append("sdk_detection_failed")
    refs = profile.get("credentials", {})
    if any(not os.environ.get(name) for name in refs.values()):
        issues.append("credentials_unavailable")
    # Check references and non-secret settings, never resolve or echo secret values.
    provided = {key for key, value in profile.items() if value}
    provided.update(refs)
    if any(key not in provided for key in REQUIRED_FIELDS.get(kind, ())):
        issues.append("required_settings_missing")
    if kind == "alpaca":
        token = "access_token" in provided
        keys = {"api_key", "api_secret"}.intersection(provided)
        if (token and keys) or (not token and len(keys) != 2):
            issues.append("invalid_auth_mode")
    if kind in {"xtquant", "easytrader"} and sys.platform != "win32":
        issues.append("unsupported_platform")
    return issues


def diagnose(connection_id=None):
    report = {"check": "local_configuration", "live_connection_tested": False,
              "read_only": True, "connections": []}
    try:
        profiles = load_profiles()
    except Exception:
        return {**report, "success": False, "error_code": "broker_configuration_invalid",
                "hint": "Check brokers.json syntax, credential references and owner-only file permissions."}
    if connection_id is not None:
        profiles = [p for p in profiles if p["id"] == connection_id]
    if not profiles:
        return {**report, "success": False,
                "error_code": "connection_unavailable" if connection_id is not None else "no_connections",
                "hint": "Configure the selected user's private broker profiles; see docs/broker-connections.md."}
    for profile in profiles:
        issues = _issues(profile)
        report["connections"].append({
            "connection_id": profile["id"],
            "type": profile["type"] if profile["type"] in ADAPTERS else "unknown",
            "local_checks_passed": not issues,
            "issues": [{"code": code, "hint": HINTS[code]} for code in issues],
        })
    report["success"] = all(row["local_checks_passed"] for row in report["connections"])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", help="Check only this configured connection ID")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable report")
    args = parser.parse_args(argv)
    report = diagnose(args.connection)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("Broker setup: local checks only; live authorization has not been verified.")
        if "error_code" in report:
            print(f'{report["error_code"]}: {report["hint"]}')
        for row in report["connections"]:
            print(f'{row["connection_id"]} ({row["type"]}): ' +
                  ("local checks passed" if row["local_checks_passed"] else "setup required"))
            for issue in row["issues"]:
                print(f'  {issue["code"]}: {issue["hint"]}')
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
