"""Options chains, perpetual funding rates and the crypto Fear & Greed index.

Ported from aria-code's local finance tools. Public endpoints only: no
exchange keys are read, and nothing here can place an order.
"""
from __future__ import annotations

import json
import math
import re
import time
import urllib.request
from datetime import date, datetime, timezone

_EQUITY = re.compile(r"[A-Z0-9.^-]{1,12}")
_PAIR = re.compile(r"[A-Z0-9]{2,12}/[A-Z0-9]{2,8}(:[A-Z0-9]{2,8})?")
EXCHANGES = ("binance", "okx", "bybit")
_FNG_URL = "https://api.alternative.me/fng/?limit={limit}&format=json"


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _now():
    return datetime.now(timezone.utc).isoformat()


def options_chain(symbol, expiry="", side="both", limit=15):
    if not isinstance(symbol, str) or not _EQUITY.fullmatch(symbol.upper()) or symbol.startswith("."):
        raise ValueError("symbol must be an equity or ETF ticker such as AAPL")
    if expiry and (not isinstance(expiry, str) or date.fromisoformat(expiry).isoformat() != expiry):
        raise ValueError("expiry must be an ISO date (YYYY-MM-DD) or empty for the nearest")
    if side not in ("calls", "puts", "both"):
        raise ValueError("side must be calls, puts or both")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 to 100")
    try:
        import yfinance as yf
    except ImportError:
        return {"success": False, "error_code": "dependency_missing", "error": "Install arthera-financial-service[market]"}
    ticker = yf.Ticker(symbol.upper())
    expiries = list(ticker.options or [])
    if not expiries:
        return {"success": False, "error_code": "no_options", "error": "The source lists no option expiries for this symbol"}
    if expiry and expiry not in expiries:
        return {"success": False, "error_code": "unknown_expiry", "error": "That expiry is not listed",
                "expiries": expiries[:24]}
    chosen = expiry or expiries[0]
    chain = ticker.option_chain(chosen)
    underlying = getattr(chain, "underlying", None) or {}
    price = _finite(underlying.get("regularMarketPrice")) if isinstance(underlying, dict) else None

    def rows(frame):
        if frame is None or frame.empty:
            return []
        if price is not None:  # strikes nearest the money, then back in strike order
            frame = frame.iloc[(frame["strike"] - price).abs().argsort()[:limit]].sort_values("strike")
        else:
            frame = frame.head(limit)
        out = []
        for record in frame.to_dict("records"):
            row = {k: _finite(record.get(k)) for k in ("strike", "lastPrice", "bid", "ask", "volume", "openInterest")}
            iv = _finite(record.get("impliedVolatility"))
            row["iv_pct"] = round(iv * 100, 1) if iv is not None else None
            row["in_the_money"] = bool(record.get("inTheMoney")) if record.get("inTheMoney") is not None else None
            out.append({k: v for k, v in row.items() if v is not None})
        return out

    result = {"success": True, "symbol": symbol.upper(), "expiry": chosen, "expiries": expiries[:24],
              "underlying_price": price, "source": "yfinance", "retrieved_at": _now(),
              "limitations": ["Quotes may be delayed and bid/ask may be stale outside market hours.",
                              f"At most {limit} strikes per side, nearest the money when the underlying price is known."]}
    if side in ("calls", "both"):
        result["calls"] = rows(chain.calls)
    if side in ("puts", "both"):
        result["puts"] = rows(chain.puts)
    return result


def fear_greed(days=7):
    if type(days) is not int or not 1 <= days <= 365:
        raise ValueError("days must be an integer from 1 to 365")
    request = urllib.request.Request(_FNG_URL.format(limit=days), headers={"User-Agent": "arthera-financial-service"})
    with urllib.request.urlopen(request, timeout=8) as response:
        items = json.loads(response.read(1_000_000)).get("data") or []
    history = []
    for item in items:
        value = _finite(item.get("value"))
        stamp = _finite(item.get("timestamp"))
        if value is None or stamp is None:
            continue
        history.append({"date": datetime.fromtimestamp(stamp, timezone.utc).date().isoformat(),
                        "value": int(value), "label": str(item.get("value_classification", ""))[:20]})
    if not history:
        return {"success": False, "error_code": "source_unavailable", "error": "The index returned no values"}
    return {"success": True, "latest": history[0], "history": history, "source": "alternative.me",
            "retrieved_at": _now(),
            "limitations": ["A sentiment composite for crypto markets (0 = extreme fear, 100 = extreme greed); not a trading signal."]}


def _interval_hours(info):
    text = str(info.get("interval") or "")
    match = re.fullmatch(r"(\d{1,2})h", text)
    return int(match.group(1)) if match else None


def funding_rates(symbols=None, exchanges=None):
    symbols = symbols or ["BTC/USDT:USDT", "ETH/USDT:USDT"]
    exchanges = exchanges or list(EXCHANGES)
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= 10:
        raise ValueError("symbols requires 1..10 perpetual pairs such as BTC/USDT:USDT")
    symbols = [s.upper() if isinstance(s, str) else s for s in symbols]
    if not all(isinstance(s, str) and _PAIR.fullmatch(s) for s in symbols):
        raise ValueError("symbols must look like BTC/USDT or BTC/USDT:USDT")
    if not isinstance(exchanges, list) or not exchanges or any(e not in EXCHANGES for e in exchanges):
        raise ValueError(f"exchanges must be a non-empty list from: {', '.join(EXCHANGES)}")
    try:
        import ccxt
    except ImportError:
        return {"success": False, "error_code": "dependency_missing", "error": "Install arthera-financial-service[crypto]"}
    end, rows, failures = time.monotonic() + 40, [], []
    for name in dict.fromkeys(exchanges):
        exchange = getattr(ccxt, name)({"timeout": 8000, "enableRateLimit": True,
                                        "options": {"defaultType": "swap"}})
        for symbol in symbols:
            if time.monotonic() > end:
                failures.append({"exchange": name, "symbol": symbol, "error_code": "source_timeout"})
                continue
            pair = symbol if ":" in symbol else f"{symbol}:{symbol.split('/')[1]}"
            try:
                info = exchange.fetch_funding_rate(pair)
            except Exception:
                failures.append({"exchange": name, "symbol": symbol, "error_code": "source_unavailable"})
                continue
            rate = _finite(info.get("fundingRate"))
            if rate is None:
                failures.append({"exchange": name, "symbol": symbol, "error_code": "source_unavailable"})
                continue
            hours = _interval_hours(info)
            rows.append({"exchange": name, "symbol": pair, "rate_pct": round(rate * 100, 5),
                         "interval_hours": hours,
                         "annualized_pct": round(rate * 100 * 24 / hours * 365, 2) if hours else None,
                         "next_funding": str(info.get("fundingDatetime") or "")[:20] or None})
    if not rows:
        return {"success": False, "error_code": "source_unavailable", "failures": failures,
                "error": "No exchange returned a funding rate (network or regional restrictions are common)"}
    spreads = {}
    for symbol in {r["symbol"] for r in rows}:
        rates = [r["rate_pct"] for r in rows if r["symbol"] == symbol]
        if len(rates) > 1:
            spreads[symbol] = round(max(rates) - min(rates), 5)
    return {"success": True, "rates": rows, "cross_exchange_spread_pct": spreads, "failures": failures,
            "source": "ccxt public endpoints", "retrieved_at": _now(),
            "limitations": ["Current-period rates; they reset every funding interval.",
                            "annualized_pct is the current rate compounded flat over a year, null when the interval is unknown.",
                            "A spread between exchanges ignores fees, borrow and transfer costs; it is not an arbitrage estimate."]}
