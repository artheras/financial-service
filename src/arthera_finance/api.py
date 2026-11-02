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


# ── Multi-source quotes and fundamentals (datasources/, from aria-code) ──────

_QUOTE_SYMBOL = re.compile(r"[A-Za-z0-9.^=/-]{1,20}")
_router = None


def _datasource_router():
    """The source router, built once; None when its dependencies are missing."""
    global _router
    if _router is None:
        from .datasources.router import DataRouter
        _router = DataRouter()
    return _router


def _plain(values: dict, limit: int = 40) -> dict:
    """Only finite numbers and short strings leave the service."""
    out = {}
    for key, value in list(values.items())[:limit]:
        if not isinstance(key, str) or len(key) > 40:
            continue
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            if math.isfinite(float(value)):
                out[key] = value
        elif isinstance(value, str):
            out[key] = value[:200]
    return out


def _source_call(symbol: str, method: str):
    if not network_enabled():
        return None, {"success": False, "error_code": "network_disabled",
                      "error": "The host must explicitly enable FINANCIAL_SERVICE_NETWORK=1"}
    # Symbols reach source URLs: no path segments such as "..".
    if not isinstance(symbol, str) or not _QUOTE_SYMBOL.fullmatch(symbol) or ".." in symbol or symbol[0] in "./":
        raise ValueError("Invalid market symbol")
    try:
        router = _datasource_router()
    except ImportError:
        return None, {"success": False, "error_code": "dependency_missing",
                      "error": "Install arthera-financial-service[market] (and [cn] for A-share sources)"}
    return getattr(router, method)(symbol), None


def market_quote(symbol):
    result, error = _source_call(symbol, "quote")
    if error:
        return error
    if result is None:
        return {"success": False, "error_code": "source_unavailable",
                "error": "No configured source returned a quote for this symbol"}
    data = _plain(result.to_dict())
    return {"success": True, "symbol": symbol, "data": data, "source": result.source or "unknown",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "limitations": ["As reported by the named source; may be delayed. Not an executable price."]}


def market_fundamentals(symbol):
    result, error = _source_call(symbol, "fundamentals")
    if error:
        return error
    if result is None:
        return {"success": False, "error_code": "source_unavailable",
                "error": "No configured source returned fundamentals for this symbol"}
    data = _plain(asdict(result))
    data.pop("symbol", None)
    return {"success": True, "symbol": symbol, "data": data, "source": result.source or "unknown",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "limitations": ["Missing metrics are omitted, not zero. Definitions differ between sources."]}


def datasource_status():
    """Which sources exist and whether their keys and packages are present; no network."""
    keys = {"tushare": ["TUSHARE_TOKEN"], "fred": ["FRED_API_KEY"], "finnhub": ["FINNHUB_API_KEY", "FINNHUB_KEY"],
            "alpha_vantage": ["ALPHA_VANTAGE_KEY", "ALPHAVANTAGE_KEY"]}
    packages = {"akshare": "akshare", "tushare": "tushare", "yfinance": "yfinance"}
    import importlib.util
    rows = []
    for name in ("yfinance", "akshare", "tushare", "finnhub", "alpha_vantage", "fred", "edgar", "world_bank"):
        row = {"source": name}
        if name in keys:
            row["key_set"] = any(os.getenv(k) for k in keys[name])
        if name in packages:
            row["package_installed"] = importlib.util.find_spec(packages[name]) is not None
        rows.append(row)
    return {"success": True, "network_enabled": network_enabled(), "sources": rows}


# ── Macro series (FRED, World Bank), from aria-code's macro tools ───────────

_SERIES = re.compile(r"[A-Za-z0-9_.]{1,40}")
_COUNTRY = re.compile(r"[A-Za-z]{2,3}")


def _frame_rows(frame, limit=500):
    rows = []
    for idx, value in frame["close"].tail(limit).items():
        number = float(value)
        if math.isfinite(number):
            rows.append({"date": str(getattr(idx, "date", lambda: idx)()), "value": number})
    return rows


def macro_series(series, source="fred", country="US", years=10):
    if not network_enabled():
        return {"success": False, "error_code": "network_disabled",
                "error": "The host must explicitly enable FINANCIAL_SERVICE_NETWORK=1"}
    if not isinstance(series, str) or not _SERIES.fullmatch(series):
        raise ValueError("series must be a FRED series id, a World Bank indicator code or an alias such as CPI")
    if source not in ("fred", "world_bank"):
        raise ValueError("source must be fred or world_bank")
    if not isinstance(country, str) or not _COUNTRY.fullmatch(country):
        raise ValueError("country must be a 2 or 3 letter code")
    if type(years) is not int or not 1 <= years <= 60:
        raise ValueError("years must be an integer from 1 to 60")
    import importlib.util
    if importlib.util.find_spec("pandas") is None:
        return {"success": False, "error_code": "dependency_missing",
                "error": "Install arthera-financial-service[market]"}
    if source == "fred":
        from .datasources.sources.fred_source import FREDSource
        result = FREDSource().history(series.upper(), days=365 * years)
    else:
        from .datasources.sources.world_bank_source import WorldBankSource
        end = date.today().year
        result = WorldBankSource().get_indicator(series.upper(), country.upper(), end - years, end)
    if result is None or result.data is None or len(result.data) == 0:
        return {"success": False, "error_code": "source_unavailable",
                "error": "The source returned no observations for this series"}
    rows = _frame_rows(result.data)
    return {"success": True, "series": series.upper(), "source": source,
            "country": country.upper() if source == "world_bank" else None,
            "observations": rows, "as_of": rows[-1]["date"] if rows else None,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "limitations": ["Values as published by the source; recent observations may be revised.",
                            "At most the last 500 observations are returned."]}


# ── Macro snapshots (US, China, policy rates) ────────────────────────────────

def _network_gate():
    if not network_enabled():
        return {"success": False, "error_code": "network_disabled",
                "error": "The host must explicitly enable FINANCIAL_SERVICE_NETWORK=1"}
    return None


def us_macro(indicator="all", periods=12):
    from . import macro
    return _network_gate() or macro.us_macro(indicator, periods)


def cn_macro(indicator="all", periods=12):
    from . import macro
    return _network_gate() or macro.cn_macro(indicator, periods)


def central_bank_rates():
    from . import macro
    return _network_gate() or macro.central_bank_rates()


# ── Derivatives and crypto sentiment ─────────────────────────────────────────

def options_chain(symbol, expiry="", side="both", limit=15):
    from . import derivatives
    return _network_gate() or derivatives.options_chain(symbol, expiry, side, limit)


def fear_greed(days=7):
    from . import derivatives
    return _network_gate() or derivatives.fear_greed(days)


def funding_rates(symbols=None, exchanges=None):
    from . import derivatives
    return _network_gate() or derivatives.funding_rates(symbols, exchanges)


# ── Market overviews ─────────────────────────────────────────────────────────

def sector_performance(market="cn", top=5):
    from . import markets
    return _network_gate() or markets.sector_performance(market, top)


def northbound_flow():
    from . import markets
    return _network_gate() or markets.northbound_flow()


def limit_up_pool(day="", limit=50):
    from . import markets
    return _network_gate() or markets.limit_up_pool(day, limit)


def screen_ashare(max_pe=50.0, min_market_cap_100m=0.0, sort_by="change_pct", limit=20):
    from . import markets
    return _network_gate() or markets.screen_ashare(max_pe, min_market_cap_100m, sort_by, limit)


def market_indices():
    from . import markets
    return _network_gate() or markets.market_indices()


# ── A-share and crypto history ───────────────────────────────────────────────

def ashare_history(symbol, days=365, adjust="qfq"):
    from . import history
    return _network_gate() or history.ashare_history(symbol, days, adjust)


def crypto_ohlcv(symbol="BTC/USDT", exchange="binance", timeframe="1d", limit=200):
    from . import history
    return _network_gate() or history.crypto_ohlcv(symbol, exchange, timeframe, limit)


# ── Futures and Treasury yields ──────────────────────────────────────────────

def futures_snapshot(name):
    from . import futures
    return _network_gate() or futures.futures_snapshot(name)


def treasury_yields():
    from . import futures
    return _network_gate() or futures.treasury_yields()
