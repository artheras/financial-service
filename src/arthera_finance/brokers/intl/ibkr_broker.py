"""Financial Service brokerage adapter."""

from __future__ import annotations

from typing import Any, Dict, List
import math

from ..base import BrokerBase, AccountInfo, Position, Order


class IBKRBroker(BrokerBase):
    broker_type = "ibkr"
    broker_name = "Interactive Brokers"
    market      = "US"

    def __init__(self, broker_id: str, config: Dict[str, Any]):
        super().__init__(broker_id, config)
        self._host      = config.get("host",      "127.0.0.1")
        self._port      = int(config.get("port",  7496))
        self._client_id = int(config.get("client_id", 1))
        self._readonly  = bool(config.get("readonly", True))
        self._account = str(config.get("account_id", ""))
        self._ib        = None

    def connect(self) -> bool:
        try:
            from ib_insync import IB
        except ImportError:
            raise ImportError("ib_insync 未安装。请运行: pip install ib_insync  并启动 TWS / IB Gateway")

        try:
            ib = IB()
            ib.connect(self._host, self._port, clientId=self._client_id, readonly=self._readonly)
            if not self._account or self._account not in ib.managedAccounts():
                ib.disconnect()
                raise ValueError("Explicit managed account selection required")
            self._ib        = ib
            self._connected = True
            return True
        except Exception as e:
            raise RuntimeError(f"IBKR 连接失败: {e}") from e

    def disconnect(self) -> None:
        if self._ib:
            try:
                self._ib.disconnect()
            except Exception:
                pass
        self._connected = False

    def account_info(self) -> AccountInfo:
        self._require_connected()
        try:
            rows = [v for v in self._ib.accountSummary() if v.account == self._account]
            summary = {v.tag: v.value for v in rows}
            if "NetLiquidation" not in summary:
                raise ValueError("Selected account summary unavailable")
            account_id = self._account
            currency = next((v.currency for v in rows if v.tag == "NetLiquidation"), "")
            if not currency:
                raise ValueError("Selected account currency unavailable")
            netliq   = float(summary.get("NetLiquidation", 0))
            cash     = float(summary.get("TotalCashValue",  0))
            mv       = float(summary.get("GrossPositionValue", 0))
            pnl      = float(summary.get("UnrealizedPnL",   0))
        except Exception as e:
            raise RuntimeError(f"IBKR 账户查询失败: {e}") from e

        return AccountInfo(
            broker_id=self.broker_id,
            broker_type=self.broker_type,
            label=self.label,
            account_id=account_id,
            currency=currency,
            total_assets=netliq,
            cash=cash,
            market_value=mv,
            pnl_total=pnl,
        )

    def positions(self) -> List[Position]:
        self._require_connected()
        try:
            raw = [p for p in self._ib.positions(self._account) if p.account == self._account]
        except Exception as e:
            raise RuntimeError(f"IBKR 持仓查询失败: {e}") from e

        # Request market data for all contracts in batch to get current prices
        _tick_map: dict = {}
        try:
            contracts = [p.contract for p in (raw or [])]
            if contracts:
                tickers = self._ib.reqTickers(*contracts)
                for tk in tickers:
                    _sym = tk.contract.symbol
                    # Use last price, fall back to close, then mark price
                    _px = next((float(value) for value in (tk.last, tk.close, tk.marketPrice())
                                if value is not None and math.isfinite(value) and value > 0), 0.0)
                    if _px and _px > 0:
                        _tick_map[_sym] = float(_px)
        except Exception:
            pass  # tick fetch best-effort; fall back to cost-based estimates below

        result = []
        for p in (raw or []):
            contract = p.contract
            sym  = contract.symbol
            qty  = float(p.position)
            cost = float(p.avgCost)
            price = _tick_map.get(sym)
            mv = qty * price if price is not None else None
            value = getattr(p, "unrealizedPNL", None)
            pnl = float(value) if value is not None and math.isfinite(float(value)) else None
            currency = contract.currency or "USD"
            result.append(Position(
                symbol=sym, name=contract.localSymbol or sym,
                quantity=qty, available_qty=qty,
                cost_price=cost, current_price=price,
                market_value=mv, pnl=pnl,
                pnl_pct=round(pnl / (cost * qty) * 100, 2) if pnl is not None and cost and qty else None,
                extra={"price_quality": "provider" if price is not None else "unavailable"},
                currency=currency, market="us",
            ))
        return result

    def orders(self, status: str = "all", limit: int = 50) -> List[Order]:
        self._require_connected()
        try:
            raw = [t for t in self._ib.openTrades() + self._ib.reqCompletedOrders(apiOnly=False)
                   if t.order.account == self._account]
        except Exception as e:
            raise RuntimeError(f"IBKR 订单查询失败: {e}") from e

        result = []
        for trade in list(raw or [])[:limit]:
            order    = trade.order
            contract = trade.contract
            side     = "buy" if order.action.upper() == "BUY" else "sell"
            mapped   = _ibkr_order_status(trade.orderStatus.status)
            if status != "all" and mapped != status:
                continue
            result.append(Order(
                order_id=str(order.orderId),
                symbol=contract.symbol,
                name=contract.localSymbol or contract.symbol,
                side=side,
                order_type=order.orderType.lower(),
                quantity=float(order.totalQuantity),
                filled_qty=float(trade.orderStatus.filled),
                price=float(getattr(order, "lmtPrice", 0)),
                avg_price=float(trade.orderStatus.avgFillPrice),
                status=mapped,
                currency=contract.currency or "USD",
            ))
        return result

    def _require_connected(self):
        if not self._connected or not self._ib:
            raise RuntimeError("IBKR 未连接，请先调用 connect()")


def _ibkr_order_status(raw: str) -> str:
    raw = raw.lower()
    if raw == "filled":
        return "filled"
    if "partial" in raw:
        return "partial"
    if raw in ("cancelled", "inactive"):
        return "cancelled"
    return "open"
