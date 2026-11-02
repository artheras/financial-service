"""Paper trading behind preview and execute, with host-owned limits.

Everything the client could use to loosen the rules lives outside its reach:
- Trading is off unless the host sets ARTHERA_TRADING_ENABLED=1.
- Accounts and their limits come from $FINANCIAL_SERVICE_HOME/trading.json,
  which no tool writes.
- Halting needs no tool either: ARTHERA_TRADING_HALT=1, or a file named
  HALT in the service home, freezes previews and executions.
- Only paper accounts exist in this version; a live account type is refused.

An order is executed only by the id of a pending preview, within its expiry,
after its plan is checked again against the account as it is now. Every
preview, execution, cancellation and refusal is appended to an audit log.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .planning import Limits, Snapshot, plan_order

PREVIEW_TTL_SECONDS = 600
_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.\-/]{0,19}")
_ACCOUNT = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
_PREVIEW = re.compile(r"tp_[0-9a-f]{16}")
_LOCK = threading.RLock()


class TradingError(ValueError):
    """A refusal whose message is written for the client."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def home():
    return Path(os.getenv("FINANCIAL_SERVICE_HOME") or "~/.arthera-finance").expanduser()


def enabled():
    return os.getenv("ARTHERA_TRADING_ENABLED", "0") == "1"


def halted():
    return os.getenv("ARTHERA_TRADING_HALT", "0") == "1" or (home() / "HALT").exists()


def _read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _write_json(path, data):
    """Atomic replace, so a crash never leaves half a ledger."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _now():
    return time.time()


def _day(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()


def audit(event, **fields):
    path = home() / "trading_audit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def accounts():
    """The host's paper accounts, validated; a malformed file refuses everything."""
    data = _read_json(home() / "trading.json", None)
    if data is None:
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("accounts"), list):
        raise TradingError("configuration_invalid", "trading.json must hold {\"accounts\": [...]}")
    out = {}
    for raw in data["accounts"]:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not _ACCOUNT.fullmatch(raw["id"]):
            raise TradingError("configuration_invalid", "each account needs an id of lowercase letters, digits, _ or -")
        if raw.get("type") != "paper":
            raise TradingError("configuration_invalid", f"account {raw['id']}: only type \"paper\" is supported")
        cash = raw.get("starting_cash", 100_000)
        if isinstance(cash, bool) or not isinstance(cash, (int, float)) or not 0 < cash <= 1e12:
            raise TradingError("configuration_invalid", f"account {raw['id']}: starting_cash must be a positive number")
        try:
            limits = Limits.from_config(raw.get("limits"))
        except ValueError as exc:
            raise TradingError("configuration_invalid", f"account {raw['id']}: {exc}") from None
        out[raw["id"]] = {"id": raw["id"], "type": "paper", "currency": str(raw.get("currency", "USD"))[:5],
                          "starting_cash": float(cash), "limits": limits}
    return out


def _require(account_id):
    if not enabled():
        raise TradingError("trading_disabled", "Trading is off; the host enables it with ARTHERA_TRADING_ENABLED=1")
    if not isinstance(account_id, str) or not _ACCOUNT.fullmatch(account_id):
        raise TradingError("invalid_input", "account_id is not a valid account id")
    account = accounts().get(account_id)
    if account is None:
        raise TradingError("unknown_account", "No such account in the host's trading.json")
    return account


def _ledger(account):
    ledger = _read_json(home() / "paper_ledger.json", {})
    book = ledger.get(account["id"])
    if book is None:
        book = {"cash": account["starting_cash"], "positions": {}, "orders": []}
    return ledger, book


def _snapshot(book, symbol=None, price=None):
    positions = {s: {"quantity": p["quantity"], "price": p["last_price"]} for s, p in book["positions"].items()}
    if symbol and symbol in positions:
        positions[symbol]["price"] = price
    return Snapshot(cash=book["cash"], positions=positions)


def _today_usage(book):
    today = _day(_now())
    fills = [o for o in book["orders"] if _day(o["filled_at"]) == today]
    return {"orders": len(fills), "notional": round(sum(o["quantity"] * o["price"] for o in fills), 2)}


def account_state(account_id):
    with _LOCK:
        account = _require(account_id)
        _, book = _ledger(account)
        snap = _snapshot(book)
        limits = account["limits"]
        usage = _today_usage(book)
        return {"success": True, "account_id": account_id, "type": "paper", "currency": account["currency"],
                "cash": round(book["cash"], 2), "total_assets": round(snap.total_assets, 2),
                "positions": [{"symbol": s, "quantity": p["quantity"], "avg_cost": round(p["avg_cost"], 6),
                               "last_price": p["last_price"]} for s, p in sorted(book["positions"].items())],
                "today": usage, "limits": limits.__dict__, "halted": halted(),
                "limitations": ["Paper account: fills are simulated at the limit price; no market is reached.",
                                "Position values use each symbol's last fill price, not a live quote."]}


def list_accounts():
    if not enabled():
        return {"success": True, "enabled": False, "accounts": [], "halted": halted()}
    return {"success": True, "enabled": True, "halted": halted(),
            "accounts": [{"account_id": a["id"], "type": a["type"], "currency": a["currency"]} for a in accounts().values()]}


def _validate_order(symbol, side, quantity, target_weight, limit_price):
    symbol = symbol.strip().upper() if isinstance(symbol, str) else symbol
    if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol) or ".." in symbol:
        raise TradingError("invalid_input", "symbol must be a ticker such as AAPL")
    if side not in ("buy", "sell"):
        raise TradingError("invalid_input", "side must be buy or sell")
    if (quantity is None) == (target_weight is None):
        raise TradingError("invalid_input", "give exactly one of quantity and target_weight")
    for name, value, high in (("quantity", quantity, 1e9), ("target_weight", target_weight, 1)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not 0 <= value <= high or (name == "quantity" and value == 0)):
            raise TradingError("invalid_input", f"{name} is out of range")
    if isinstance(limit_price, bool) or not isinstance(limit_price, (int, float)) or not 0 < limit_price <= 1e7:
        raise TradingError("invalid_input", "limit_price must be a positive number")
    return symbol


def preview_order(account_id, symbol, side, quantity=None, target_weight=None, limit_price=None, reason=""):
    with _LOCK:
        account = _require(account_id)
        symbol = _validate_order(symbol, side, quantity, target_weight, limit_price)
        reason = str(reason or "")[:300]
        if halted():
            audit("preview_refused", account_id=account_id, symbol=symbol, reason="halted")
            raise TradingError("trading_halted", "Trading is halted on this host (ARTHERA_TRADING_HALT or a HALT file)")
        _, book = _ledger(account)
        limits = account["limits"]
        plan = plan_order(_snapshot(book, symbol, float(limit_price)), symbol, side, float(limit_price), limits,
                          quantity=float(quantity) if quantity is not None else None,
                          target_weight=float(target_weight) if target_weight is not None else None)
        usage = _today_usage(book)
        if usage["orders"] + 1 > limits.max_orders_per_day:
            plan["blockers"].append(f"today's {usage['orders']} orders already reach the daily limit of {limits.max_orders_per_day}")
        if usage["notional"] + plan["estimated_value"] > limits.max_notional_per_day + 1e-9:
            plan["blockers"].append(f"today's traded value would exceed the daily limit of {limits.max_notional_per_day:,.2f}")
        preview_id = "tp_" + secrets.token_hex(8)
        preview = {"preview_id": preview_id, "account_id": account_id, "status": "pending",
                   "created_at": _now(), "expires_at": _now() + PREVIEW_TTL_SECONDS,
                   "intent": {"symbol": symbol, "side": side, "quantity": quantity, "target_weight": target_weight,
                              "limit_price": float(limit_price), "reason": reason},
                   "plan": plan}
        store = _read_json(home() / "trade_previews.json", {})
        cutoff = _now() - 7 * 86400
        store = {k: v for k, v in store.items() if v.get("created_at", 0) > cutoff}
        store[preview_id] = preview
        _write_json(home() / "trade_previews.json", store)
        audit("preview", preview=preview)
        return {"success": True, "preview_id": preview_id, "account_id": account_id, "order": {
                    k: plan[k] for k in ("symbol", "side", "quantity", "limit_price", "estimated_value")},
                "plan": plan, "can_execute": not plan["blockers"],
                "expires_in_seconds": PREVIEW_TTL_SECONDS,
                "next_step": ("Show this preview to the person; only they decide whether trading_execute_order runs."
                              if not plan["blockers"] else "Blocked; nothing can be executed from this preview."),
                "limitations": ["Paper account: fills are simulated at the limit price."]}


def _refuse(preview_id, code, message, store=None, status=None):
    if store is not None and status:
        store[preview_id]["status"] = status
        store[preview_id]["updated_at"] = _now()
        _write_json(home() / "trade_previews.json", store)
    audit("execute_refused", preview_id=preview_id, code=code, message=message)
    raise TradingError(code, message)


def execute_order(preview_id):
    with _LOCK:
        if not isinstance(preview_id, str) or not _PREVIEW.fullmatch(preview_id):
            raise TradingError("invalid_input", "preview_id is not a valid preview id")
        if not enabled():
            raise TradingError("trading_disabled", "Trading is off; the host enables it with ARTHERA_TRADING_ENABLED=1")
        store = _read_json(home() / "trade_previews.json", {})
        preview = store.get(preview_id)
        if preview is None:
            _refuse(preview_id, "unknown_preview", "No such preview")
        if preview["status"] != "pending":
            _refuse(preview_id, "preview_used", f"This preview is already {preview['status']}; make a new one")
        if _now() > preview["expires_at"]:
            _refuse(preview_id, "preview_expired", "This preview has expired; make a new one", store, "expired")
        if halted():
            _refuse(preview_id, "trading_halted", "Trading is halted on this host")
        account = _require(preview["account_id"])
        ledger, book = _ledger(account)
        intent, old = preview["intent"], preview["plan"]
        limits = account["limits"]
        # Re-plan the exact previewed order against the account as it is now.
        plan = plan_order(_snapshot(book, intent["symbol"], intent["limit_price"]), intent["symbol"], old["side"],
                          intent["limit_price"], limits, quantity=old["quantity"])
        usage = _today_usage(book)
        if usage["orders"] + 1 > limits.max_orders_per_day:
            plan["blockers"].append("the daily order limit is reached")
        if usage["notional"] + plan["estimated_value"] > limits.max_notional_per_day + 1e-9:
            plan["blockers"].append("the daily traded value limit would be exceeded")
        if plan["blockers"]:
            _refuse(preview_id, "risk_rejected", "; ".join(plan["blockers"]), store, "rejected")
        symbol, quantity, price = intent["symbol"], plan["quantity"], intent["limit_price"]
        position = book["positions"].get(symbol, {"quantity": 0.0, "avg_cost": 0.0, "last_price": price})
        if plan["side"] == "buy":
            cost = position["quantity"] * position["avg_cost"] + quantity * price
            position["quantity"] += quantity
            position["avg_cost"] = cost / position["quantity"]
            book["cash"] -= quantity * price
        else:
            position["quantity"] -= quantity
            book["cash"] += quantity * price
        position["last_price"] = price
        if position["quantity"] <= 1e-9:
            book["positions"].pop(symbol, None)
        else:
            book["positions"][symbol] = position
        order_id = "po_" + secrets.token_hex(8)
        fill = {"order_id": order_id, "preview_id": preview_id, "symbol": symbol, "side": plan["side"],
                "quantity": quantity, "price": price, "filled_at": _now()}
        book["orders"].append(fill)
        ledger[account["id"]] = book
        _write_json(home() / "paper_ledger.json", ledger)
        preview["status"], preview["updated_at"], preview["order_id"] = "executed", _now(), order_id
        _write_json(home() / "trade_previews.json", store)
        audit("execute", preview_id=preview_id, fill=fill, account_id=account["id"])
        return {"success": True, "order_id": order_id, "account_id": account["id"], "fill": fill,
                "cash_after": round(book["cash"], 2), "simulated": True}


def cancel_preview(preview_id):
    with _LOCK:
        if not isinstance(preview_id, str) or not _PREVIEW.fullmatch(preview_id):
            raise TradingError("invalid_input", "preview_id is not a valid preview id")
        store = _read_json(home() / "trade_previews.json", {})
        preview = store.get(preview_id)
        if preview is None or preview["status"] != "pending":
            raise TradingError("unknown_preview", "No pending preview with that id")
        preview["status"], preview["updated_at"] = "cancelled", _now()
        _write_json(home() / "trade_previews.json", store)
        audit("cancel", preview_id=preview_id)
        return {"success": True, "preview_id": preview_id, "status": "cancelled"}


def audit_log(limit=20):
    if type(limit) is not int or not 1 <= limit <= 200:
        raise TradingError("invalid_input", "limit must be an integer from 1 to 200")
    path = home() / "trading_audit.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    return {"success": True, "events": [json.loads(line) for line in lines[-limit:]]}
