"""Financial Service brokerage adapter."""

from __future__ import annotations

from typing import Any, Dict, List

from ..base import AccountInfo, BrokerBase, Order, Position

DEFAULT_BASE_URL = "https://live.trading212.com/api/v0"


class Trading212Broker(BrokerBase):
    broker_type = "trading212"
    broker_name = "Trading212"
    market = "UK"

    def __init__(self, broker_id: str, config: Dict[str, Any]):
        super().__init__(broker_id, config)
        self._api_key = config.get("api_key", "")
        self._base_url = str(config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        self._session = None

    def connect(self) -> bool:
        try:
            import requests
        except ImportError:
            raise ImportError("requests 未安装。请运行: pip install requests")

        session = requests.Session()
        session.headers.update({"Authorization": self._api_key})
        try:
            resp = session.get(f"{self._base_url}/equity/account/info", timeout=10)
            resp.raise_for_status()
            self._session = session
            self._connected = True
            return True
        except Exception as e:
            raise RuntimeError(f"Trading212 连接失败: {e}") from e

    def _require_connected(self):
        if not self._connected or not self._session:
            raise RuntimeError("Trading212 未连接，请先调用 connect()")

    def _get_json(self, path):
        response = self._session.get(f"{self._base_url}{path}", timeout=10)
        response.raise_for_status()
        return response.json()

    def account_info(self) -> AccountInfo:
        self._require_connected()
        try:
            info = self._get_json("/equity/account/info")
            cash = self._get_json("/equity/account/cash")
        except Exception as e:
            raise RuntimeError(f"Trading212 账户查询失败: {e}") from e

        free_cash = float(cash.get("free", 0) or 0)
        total = float(cash.get("total", 0) or 0)
        invested = float(cash.get("invested", 0) or 0)
        ppl = float(cash.get("ppl", 0) or 0)
        return AccountInfo(
            broker_id=self.broker_id,
            broker_type=self.broker_type,
            label=self.label,
            account_id=str(info.get("id", "")),
            currency=str(info.get("currencyCode", "GBP")),
            total_assets=total,
            cash=free_cash,
            market_value=invested,
            pnl_today=ppl,
            pnl_total=ppl,
            extra={"blocked": float(cash.get("blocked", 0) or 0)},
        )

    def positions(self) -> List[Position]:
        self._require_connected()
        try:
            raw = self._get_json("/equity/portfolio")
        except Exception as e:
            raise RuntimeError(f"Trading212 持仓查询失败: {e}") from e

        result = []
        for p in (raw or []):
            qty = float(p.get("quantity", 0) or 0)
            cost = float(p.get("averagePrice", 0) or 0)
            price = float(p.get("currentPrice", 0) or 0)
            pnl = float(p.get("ppl", 0) or 0)
            mv = qty * price
            pnl_pct = (pnl / (mv - pnl) * 100) if (mv - pnl) else 0.0
            ticker = str(p.get("ticker", ""))
            result.append(Position(
                symbol=ticker, name=ticker,
                quantity=qty, available_qty=qty,
                cost_price=cost, current_price=price,
                market_value=mv, pnl=pnl, pnl_pct=pnl_pct,
                currency="GBP", market="uk",
            ))
        return result

    def orders(self, status: str = "all", limit: int = 50) -> List[Order]:
        self._require_connected()
        try:
            raw = self._get_json("/equity/orders")
        except Exception as e:
            raise RuntimeError(f"Trading212 订单查询失败: {e}") from e

        result = []
        for o in (raw or [])[:limit]:
            mapped = _t212_order_status(str(o.get("status", "")))
            if status not in ("all",) and mapped != status:
                continue
            result.append(Order(
                order_id=str(o.get("id", "")),
                symbol=str(o.get("ticker", "")),
                name=str(o.get("ticker", "")),
                side="buy" if float(o.get("quantity", 0) or 0) >= 0 else "sell",
                order_type="limit",
                quantity=abs(float(o.get("quantity", 0) or 0)),
                filled_qty=abs(float(o.get("filledQuantity", 0) or 0)),
                price=float(o.get("limitPrice", 0) or 0),
                avg_price=float(o.get("fillPrice", 0) or 0),
                status=mapped,
                created_at=str(o.get("creationTime", "")),
                currency="GBP",
            ))
        return result


def _t212_order_status(raw: str) -> str:
    raw = raw.upper()
    if raw in ("FILLED",):
        return "filled"
    if raw in ("CANCELLED", "REJECTED", "EXPIRED"):
        return "cancelled"
    if raw in ("PARTIALLY_FILLED",):
        return "partial"
    return "open"
