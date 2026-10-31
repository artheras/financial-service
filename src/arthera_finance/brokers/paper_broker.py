"""Financial Service brokerage adapter."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

from .base import AccountInfo, BrokerBase, Order, Position
from .config import BROKERS_CONFIG_PATH, read_owner_only_json


PAPER_LEDGER_PATH = BROKERS_CONFIG_PATH.parent / "paper_ledger.json"


def _load_ledger() -> Dict[str, Any]:
    # A missing ledger is an empty one; reads never create it.
    if not PAPER_LEDGER_PATH.exists():
        return {"accounts": {}}
    data = read_owner_only_json(PAPER_LEDGER_PATH, 8 * 1024 * 1024, "paper ledger")
    if not isinstance(data, dict) or not isinstance(data.get("accounts"), dict):
        raise ValueError("Paper ledger is invalid")
    return data


def _save_ledger(data: Dict[str, Any]) -> None:
    PAPER_LEDGER_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if PAPER_LEDGER_PATH.is_symlink():
        raise ValueError("Paper ledger must not be a symbolic link")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=PAPER_LEDGER_PATH.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, PAPER_LEDGER_PATH)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class PaperBroker(BrokerBase):
    broker_type = "paper"
    broker_name = "Aria Paper Broker"
    market = "GLOBAL"

    def __init__(self, broker_id: str, config: Dict[str, Any]):
        super().__init__(broker_id, config)
        self.currency = str(config.get("currency", "USD") or "USD").upper()
        self.starting_cash = float(config.get("starting_cash", 100000.0) or 100000.0)

    def connect(self) -> bool:
        _load_ledger()
        self._connected = True
        return True

    def ping(self) -> bool:
        """Real liveness probe: the local ledger must remain readable."""
        try:
            _load_ledger()
            return self._connected
        except Exception:
            return False

    def reset(self, starting_cash: float | None = None, currency: str | None = None) -> None:
        ledger = _load_ledger()
        accounts = ledger.setdefault("accounts", {})
        cash = float(starting_cash if starting_cash is not None else self.starting_cash)
        curr = str(currency or self.currency).upper()
        accounts[self.broker_id] = {
            "broker_id": self.broker_id,
            "label": self.label,
            "currency": curr,
            "starting_cash": cash,
            "cash": cash,
            "positions": {},
            "orders": [],
            "created_at": int(time.time()),
        }
        _save_ledger(ledger)
        self.currency = curr
        self.starting_cash = cash
        self._connected = True

    def _account(self) -> Dict[str, Any]:
        if not self._connected:
            self.connect()
        return _load_ledger()["accounts"].get(self.broker_id) or {
            "broker_id": self.broker_id,
            "label": self.label,
            "currency": self.currency,
            "starting_cash": self.starting_cash,
            "cash": self.starting_cash,
            "positions": {},
            "orders": [],
        }

    def account_info(self) -> AccountInfo:
        account = self._account()
        positions = self._positions_from_account(account)
        market_value = sum(p.market_value for p in positions)
        cost_basis = sum(p.cost_price * p.quantity for p in positions)
        pnl_total = market_value - cost_basis
        cash = float(account.get("cash", 0.0) or 0.0)
        return AccountInfo(
            broker_id=self.broker_id,
            broker_type=self.broker_type,
            label=self.label,
            account_id=f"PAPER-{self.broker_id}",
            currency=str(account.get("currency", self.currency)),
            total_assets=cash + market_value,
            cash=cash,
            market_value=market_value,
            pnl_total=pnl_total,
            pnl_pct=(pnl_total / cost_basis * 100) if cost_basis > 0 else 0.0,
            extra={"mode": "paper", "ledger_path": str(PAPER_LEDGER_PATH)},
        )

    def positions(self) -> List[Position]:
        return self._positions_from_account(self._account())

    def _positions_from_account(self, account: Dict[str, Any]) -> List[Position]:
        out: List[Position] = []
        for symbol, raw in sorted((account.get("positions") or {}).items()):
            qty = float(raw.get("quantity", 0.0) or 0.0)
            if qty <= 0:
                continue
            price = float(raw.get("current_price", raw.get("cost_price", 0.0)) or 0.0)
            cost = float(raw.get("cost_price", 0.0) or 0.0)
            market_value = qty * price
            pnl = (price - cost) * qty
            out.append(Position(
                symbol=symbol,
                name=symbol,
                quantity=qty,
                available_qty=qty,
                cost_price=cost,
                current_price=price,
                market_value=market_value,
                pnl=pnl,
                pnl_pct=(pnl / (cost * qty) * 100) if cost > 0 and qty > 0 else 0.0,
                currency=str(account.get("currency", self.currency)),
                market="paper",
            ))
        return out

    def orders(self, status: str = "all", limit: int = 50) -> List[Order]:
        raw_orders = list(self._account().get("orders") or [])
        raw_orders = list(reversed(raw_orders))[: max(0, int(limit or 50))]
        out: List[Order] = []
        for raw in raw_orders:
            mapped = str(raw.get("status", "filled"))
            if status != "all" and mapped != status:
                continue
            out.append(Order(
                order_id=str(raw.get("order_id", "")),
                symbol=str(raw.get("symbol", "")),
                name=str(raw.get("symbol", "")),
                side=str(raw.get("side", "")),
                order_type=str(raw.get("order_type", "")),
                quantity=float(raw.get("quantity", 0.0) or 0.0),
                filled_qty=float(raw.get("filled_qty", raw.get("quantity", 0.0)) or 0.0),
                price=float(raw.get("price", 0.0) or 0.0),
                avg_price=float(raw.get("avg_price", raw.get("price", 0.0)) or 0.0),
                status=mapped,
                created_at=str(raw.get("created_at", "")),
                currency=str(raw.get("currency", self.currency)),
            ))
        return out


def reset_paper_account(broker_id: str = "paper_main", starting_cash: float = 100000.0, currency: str = "USD") -> None:
    PaperBroker(broker_id, {
        "id": broker_id,
        "type": "paper",
        "label": "Aria 仿盘账户",
        "starting_cash": starting_cash,
        "currency": currency,
    }).reset(starting_cash=starting_cash, currency=currency)
