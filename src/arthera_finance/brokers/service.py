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
        return {"success": False, "error_code": "broker_configuration_invalid",
                "error": "Check the host-owned broker profiles and file permissions"}


def query(connection_id, operation, status="all", limit=50):
    if not enabled():
        return {"success": False, "error_code": "broker_access_disabled"}
    if operation not in {"account", "positions", "orders"}:
        return {"success": False, "error_code": "broker_operation_denied"}
    if type(limit) is not int or not 1 <= limit <= 200 or status not in {"all", "open", "filled", "cancelled", "partial"}:
        return {"success": False, "error_code": "invalid_broker_query"}
    with _LOCK:
        broker, loop = None, None
        try:
            rows = load_profiles()
            profile = next((p for p in rows if p["id"] == connection_id), None)
            if profile is None:
                return {"success": False, "error_code": "connection_unavailable"}
            kind = profile["type"]
            if kind not in ADAPTERS:
                return {"success": False, "error_code": "adapter_unavailable"}
            if kind != "paper" and os.getenv("FINANCIAL_SERVICE_NETWORK", "0") != "1":
                return {"success": False, "error_code": "network_disabled"}
            cfg = resolve_profile(profile)
            cfg["readonly"] = True
            if kind == "alpaca":
                cfg.setdefault("paper", True)
            # Shared gateways must never silently pick their first account.
            if kind in {"ibkr", "futu"} and not cfg.get("account_id"):
                return {"success": False, "error_code": "account_selection_required"}
            if kind == "ibkr":
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
            broker = get_broker_class(kind)(broker_id=connection_id, config=cfg)
            if not broker.connect():
                return {"success": False, "error_code": "broker_connection_failed"}
            account = broker.account_info()
            expected = cfg.get("account_id")
            if expected and str(account.account_id) != str(expected):
                return {"success": False, "error_code": "broker_account_mismatch"}
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
            return {"success": False, "error_code": "broker_sdk_missing"}
        except Exception:
            # Provider errors may contain account identifiers, keys or private paths.
            return {"success": False, "error_code": "broker_query_failed",
                    "error": "Broker query failed; check connection, authorization and host configuration"}
        finally:
            if broker is not None:
                try:
                    broker.disconnect()
                except Exception:
                    pass
            if loop is not None:
                loop.close()
                asyncio.set_event_loop(None)
