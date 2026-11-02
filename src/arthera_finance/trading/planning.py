"""Order planning and risk gates, ported from aria-code's brokers/planning.py.

Changed from the original: every limit blocks. aria-code only warned when one
order exceeded its share of the account or the cash reserve; a limit that
does not stop the order is not a limit.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Limits:
    max_position_weight: float = 0.20
    max_order_weight: float = 0.10
    min_cash_reserve_weight: float = 0.02
    max_orders_per_day: int = 20
    max_notional_per_day: float = 50_000.0
    allow_fractional: bool = False

    @classmethod
    def from_config(cls, raw):
        raw = raw or {}
        if not isinstance(raw, dict):
            raise ValueError("limits must be an object")
        unknown = set(raw) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError("unknown limits: " + ", ".join(sorted(unknown)))
        values = {}
        for name, spec in cls.__dataclass_fields__.items():
            if name not in raw:
                continue
            value = raw[name]
            if spec.type == "bool":
                if not isinstance(value, bool):
                    raise ValueError(f"{name} must be true or false")
            elif spec.type == "int":
                if type(value) is not int or value < 1:
                    raise ValueError(f"{name} must be a positive integer")
            else:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise ValueError(f"{name} must be a non-negative number")
                if name.endswith("weight") and value > 1:
                    raise ValueError(f"{name} must be at most 1")
            values[name] = value
        return cls(**values)


@dataclass(frozen=True)
class Snapshot:
    cash: float
    positions: dict = field(default_factory=dict)  # symbol -> {"quantity", "price"}

    @property
    def total_assets(self):
        return self.cash + sum(p["quantity"] * p["price"] for p in self.positions.values())

    def quantity(self, symbol):
        return self.positions.get(symbol, {}).get("quantity", 0.0)


def plan_order(snapshot, symbol, side, price, limits, quantity=None, target_weight=None):
    """The order an intent amounts to, with what blocks it. Exactly one of
    quantity and target_weight is given; price is the limit price."""
    total = snapshot.total_assets
    held = snapshot.quantity(symbol)
    current_weight = held * price / total if total > 0 else 0.0
    if quantity is None:
        delta = (target_weight - current_weight) * total
        side = "buy" if delta > 0 else "sell"
        quantity = abs(delta) / price
    if not limits.allow_fractional:
        quantity = math.floor(quantity + 1e-9)
    value = quantity * price
    blockers, warnings = [], []
    if quantity <= 0:
        blockers.append("the order has no quantity at this price")
    if total <= 0:
        blockers.append("the account has no assets to size against")
    if side == "sell" and quantity > held + 1e-9:
        blockers.append(f"selling {quantity:g} exceeds the {held:g} held; short selling is not allowed")
    cash_after = snapshot.cash - value if side == "buy" else snapshot.cash + value
    position_after = (held + quantity if side == "buy" else held - quantity) * price
    weight_after = position_after / total if total > 0 else 0.0
    if side == "buy":
        if value > snapshot.cash + 1e-9:
            blockers.append(f"the order needs {value:,.2f} but only {snapshot.cash:,.2f} cash is available")
        elif total > 0 and cash_after < total * limits.min_cash_reserve_weight - 1e-9:
            blockers.append(f"cash after the order would fall below the {limits.min_cash_reserve_weight:.1%} reserve")
        if total > 0 and weight_after > limits.max_position_weight + 1e-9:
            blockers.append(f"the position would be {weight_after:.1%} of the account; the limit is {limits.max_position_weight:.1%}")
    if total > 0 and value / total > limits.max_order_weight + 1e-9:
        blockers.append(f"the order is {value / total:.1%} of the account; one order may be at most {limits.max_order_weight:.1%}")
    if limits.allow_fractional is False and target_weight is not None and quantity > 0:
        warnings.append("quantity rounded down to whole units")
    return {"symbol": symbol, "side": side, "quantity": quantity, "limit_price": price,
            "estimated_value": round(value, 2), "current_quantity": held,
            "current_weight": round(current_weight, 6), "weight_after": round(weight_after, 6),
            "cash_before": round(snapshot.cash, 2), "cash_after": round(cash_after, 2),
            "blockers": blockers, "warnings": warnings}
