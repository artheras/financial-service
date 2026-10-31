"""One local host identity per service process; no trading methods are exposed."""
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
import threading
from .config import load_profiles, resolve_profile
from .registry import ADAPTERS, get_broker_class

_LOCK = threading.RLock()
# Fixed client-facing text; provider exception text may hold identifiers or keys.
ERRORS = {
    "broker_access_disabled": "Broker access is disabled in the service host environment",
    "broker_operation_denied": "Only account, positions and orders reads are available",
    "invalid_broker_query": "Invalid broker query parameters",
    "connection_unavailable": "No such connection is configured on this host",
    "adapter_unavailable": "No adapter is available for this connection type",
    "network_disabled": "Networking is disabled in the service host environment",
    "account_selection_required": "This connection requires an explicit account_id in its host profile",
    "broker_connection_failed": "The broker connection could not be established",
    "broker_account_mismatch": "The broker returned a different account than the host profile selects",
    "broker_sdk_missing": "The broker SDK is not installed in the service environment",
    "broker_query_failed": "Broker query failed; check connection, authorization and host configuration",
    "broker_configuration_invalid": "Check the host-owned broker profiles and file permissions",
    "broker_busy": "Another broker query is in progress; retry shortly",
    "broker_timeout": "The broker query exceeded its deadline",
    "broker_worker_failed": "The broker query worker failed",
}


def failure(code):
    return {"success": False, "error_code": code, "error": ERRORS[code]}


def enabled():
    return os.getenv("FINANCIAL_SERVICE_BROKERS_ENABLED", "0") == "1"


def connections():
    if not enabled():
        return {"success": True, "enabled": False, "connections": []}
    try:
        rows = load_profiles()
        return {"success": True, "enabled": True, "connections": [
            {"connection_id": p["id"], "type": p["type"], "label": p.get("label", p["id"]),
             "read_only": True, "adapter_available": p["type"] in ADAPTERS} for p in rows]}
    except Exception:
        return failure("broker_configuration_invalid")


def query(connection_id, operation, status="all", limit=50):
    if not enabled():
        return failure("broker_access_disabled")
    if operation not in {"account", "positions", "orders"}:
        return failure("broker_operation_denied")
    if type(limit) is not int or not 1 <= limit <= 200 or status not in {"all", "open", "filled", "cancelled", "partial"}:
        return failure("invalid_broker_query")
    with _LOCK:
        broker, loop = None, None
        try:
            rows = load_profiles()
            profile = next((p for p in rows if p["id"] == connection_id), None)
            if profile is None:
                return failure("connection_unavailable")
            kind = profile["type"]
            if kind not in ADAPTERS:
                return failure("adapter_unavailable")
            if kind != "paper" and os.getenv("FINANCIAL_SERVICE_NETWORK", "0") != "1":
                return failure("network_disabled")
            cfg = resolve_profile(profile)
            cfg["readonly"] = True
            if kind == "alpaca":
                cfg.setdefault("paper", True)
            # Shared gateways must never silently pick their first account.
            if kind in {"ibkr", "futu"} and not cfg.get("account_id"):
                return failure("account_selection_required")
            if kind == "ibkr":
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
            broker = get_broker_class(kind)(broker_id=connection_id, config=cfg)
            if not broker.connect():
                return failure("broker_connection_failed")
            account = broker.account_info()
            # Tiger profiles select their account with "account", not "account_id".
            expected = cfg.get("account_id") or (cfg.get("account") if kind == "tiger" else None)
            if expected and str(account.account_id) != str(expected):
                return failure("broker_account_mismatch")
            truncated = False
            if operation == "account":
                data = asdict(account)
                data.pop("account_id", None)
                data.pop("extra", None)
                data["masked_account"] = account.masked_account
            else:
                values = broker.positions() if operation == "positions" else broker.orders(status, limit)
                truncated = len(values) > limit
                data = []
                for item in values[:limit]:
                    row = asdict(item)
                    row.pop("extra", None)
                    data.append(row)
            json.dumps(data, allow_nan=False)
            return {"success": True, "connection_id": connection_id, "broker_type": kind,
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "truncated": truncated, "read_only": True, "simulation": (True if kind == "paper" else cfg["paper"] if kind == "alpaca" else
                                   cfg.get("trd_env", "REAL") == "SIMULATE" if kind == "futu" else
                                   cfg.get("sandbox", False) if kind == "tiger" else None), "data": data}
        except ImportError:
            return failure("broker_sdk_missing")
        except Exception:
            # Provider errors may contain account identifiers, keys or private paths.
            return failure("broker_query_failed")
        finally:
            if broker is not None:
                try:
                    broker.disconnect()
                except Exception:
                    pass
            if loop is not None:
                loop.close()
                asyncio.set_event_loop(None)
