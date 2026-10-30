"""Bounded, read-only service operations; the model cannot enable network access."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import os
import re

from . import performance_stats as stats
from .backtest_engine import Bar, BacktestEngine, get_strategy
from .statements import tool_analyze_financial_statements


def network_enabled() -> bool:
    return os.getenv("FINANCIAL_SERVICE_NETWORK", "0") == "1"


def finite(value, name, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except OverflowError:
        raise ValueError(f"{name} must be a finite number") from None
    if not math.isfinite(number) or (minimum is not None and number < minimum) or (maximum is not None and number > maximum):
        raise ValueError(f"{name} is outside the supported range")
    return number


def bounded_json(value, limit=1_000_000):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    if len(raw) > limit:
        raise ValueError("Input exceeds the supported byte limit")
    return raw


def analyze_statements(company_name="", financials=None):
    if financials is not None and not isinstance(financials, dict):
        raise ValueError("financials must be an object")
    bounded_json(financials, 65536)
    if not isinstance(company_name, str) or len(company_name) > 80:
        raise ValueError("company_name must be at most 80 characters")
    return tool_analyze_financial_statements({
        "company_name": company_name, "financials": financials or {},
        "network_enabled": network_enabled(),
    })


def risk_metrics(equity, periods_per_year=252):
    if not isinstance(equity, list) or not 2 <= len(equity) <= 10000:
        raise ValueError("equity requires 2..10000 ordered observations")
    values = [finite(v, "equity", 0.000000001) for v in equity]
    annual_periods = finite(periods_per_year, "periods_per_year", 1, 1000000)
    returns = [b / a - 1 for a, b in zip(values, values[1:])]
    # Avoid silently serializing inf on extreme but individually finite inputs.
    for value in returns:
        finite(value, "period return")
    result = {"total_return": values[-1] / values[0] - 1,
              "max_drawdown": stats.max_drawdown(values),
              "sharpe": stats.sharpe(returns, annual_periods)}
    bounded_json(result)
    return {"success": True, "metrics": result, "source": "caller-supplied equity",
            "periods_per_year": annual_periods,
            "limitations": ["Sharpe uses sample standard deviation and zero risk-free rate.",
                            "Observation order and annual frequency are caller supplied."]}


def backtest(data, strategy="buy_hold", starting_cash=100000, commission=0.0005,
             slippage=0.0005, fast=10, slow=30):
    raw = bounded_json(data)
    if not isinstance(data, dict) or not 1 <= len(data) <= 32:
        raise ValueError("data requires 1..32 symbols")
    if type(fast) is not int or type(slow) is not int or not 1 <= fast < slow <= 1000:
        raise ValueError("Require integer windows: 1 <= fast < slow <= 1000")
    cash = finite(starting_cash, "starting_cash", 0.01, 1e15)
    fee = finite(commission, "commission", 0, 0.1)
    slip = finite(slippage, "slippage", 0, 0.1)
    bars, total = {}, 0
    for symbol, rows in data.items():
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Za-z0-9.^=/:-]{1,32}", symbol):
            raise ValueError("Invalid symbol")
        if not isinstance(rows, list) or not 2 <= len(rows) <= 5000:
            raise ValueError("Each symbol requires 2..5000 bars")
        total += len(rows)
        if total > 20000:
            raise ValueError("At most 20000 bars are supported")
        converted, previous = [], ""
        for row in rows:
            if not isinstance(row, dict) or not {"date", "open", "high", "low", "close"} <= set(row):
                raise ValueError("Bars require date, open, high, low and close")
            day = row["date"]
            if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day or day <= previous:
                raise ValueError("Bar dates must be unique, increasing ISO dates")
            previous = day
            o, h, l, c = (finite(row[k], k, 0.000000001, 1e15) for k in ("open", "high", "low", "close"))
            if h < max(o, c, l) or l > min(o, c, h):
                raise ValueError("OHLC values are inconsistent")
            volume = finite(row.get("volume", 0), "volume", 0, 1e18)
            converted.append(Bar(day, o, h, l, c, volume))
        bars[symbol] = converted
    kwargs = {"fast": fast, "slow": slow} if strategy == "sma_cross" else {}
    chosen = get_strategy(strategy, **kwargs)
    result = asdict(BacktestEngine(starting_cash=cash, commission=fee, slippage=slip).run(bars, chosen))
    bounded_json(result, 8_000_000)
    # Keep tool responses bounded; full series remains reproducible from input.
    result["equity_curve"] = result["equity_curve"][-100:]
    result["trades"] = result["trades"][-100:]
    return {"success": True, "simulation": True, "source": "caller-supplied bars",
            "input_sha256": hashlib.sha256(raw).hexdigest(), "result": result,
            "output_tail_limit": 100}


def market_history(symbol, days=90):
    if not network_enabled():
        return {"success": False, "error_code": "network_disabled",
                "error": "The host must explicitly enable FINANCIAL_SERVICE_NETWORK=1"}
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Za-z0-9.^=/-]{1,20}", symbol):
        raise ValueError("Invalid market symbol")
    if type(days) is not int or not 1 <= days <= 2000:
        raise ValueError("days must be an integer from 1 to 2000")
    try:
        import yfinance as yf
        from curl_cffi.requests import Session
    except ImportError:
        return {"success": False, "error_code": "dependency_missing",
                "error": "Install arthera-financial-service[market]"}
    import time
    deadline = time.monotonic() + 20
    class BoundedSession(Session):
        def request(self, method, url, **kwargs):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Market source deadline exceeded")
            kwargs["timeout"] = min(8, remaining)
            return super().request(method, url, **kwargs)
    with BoundedSession(impersonate="chrome") as session:
        ticker = yf.Ticker(symbol, session=session)
        frame = ticker.history(start=(date.today() - timedelta(days=days)).isoformat(), interval="1d", auto_adjust=False)
    if frame.empty:
        return {"success": False, "error_code": "empty_history", "error": "No history returned"}
    rows = [{"date": str(idx.date()), **{k: finite(row[col], col, 0)
             for k, col in (("open", "Open"), ("high", "High"), ("low", "Low"),
                            ("close", "Close"), ("volume", "Volume"))}}
            for idx, row in frame.tail(2000).iterrows()]
    return {"success": True, "symbol": symbol, "data": rows, "source": "yfinance",
            "as_of": rows[-1]["date"], "interval": "1d", "adjusted": False,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "limitations": ["Historical daily bars; not a live quote or a total-return series."]}
