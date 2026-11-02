"""Valuation and size of a company against peers the caller names, via yfinance.

Ported from aria-code's peer_comparison, with these corrections: trailing and
forward PE are separate columns (aria-code filled a missing trailing PE with
the forward one in the same column); peers are never guessed from a
hard-coded sector list (which put Amazon among technology peers); and the
result states the target's distance from the peer median instead of
"overvalued" or "undervalued".
"""
from __future__ import annotations

import math
import re
import time
from datetime import datetime, timezone

_TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-^=]{0,14}")


def _num(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def dividend_yield_pct(info):
    """yfinance's dividendYield is already a percentage; the trailing yield is a fraction."""
    trailing = _num(info.get("trailingAnnualDividendYield"))
    if trailing is not None:
        return trailing * 100
    rate = _num(info.get("dividendRate")) or _num(info.get("trailingAnnualDividendRate"))
    price = _num(info.get("regularMarketPrice")) or _num(info.get("currentPrice")) or _num(info.get("previousClose"))
    return rate / price * 100 if rate and price else None


def _median(values):
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _round(value, digits):
    return None if value is None else round(value, digits)


METRICS = ("trailing_pe", "forward_pe", "price_to_book", "roe_pct", "dividend_yield_pct", "market_cap_bn")


def _row(symbol, info):
    roe = _num(info.get("returnOnEquity"))
    cap = _num(info.get("marketCap"))
    return {"symbol": symbol, "name": str(info.get("shortName") or symbol)[:40],
            "currency": str(info.get("currency") or "")[:5] or None,
            "price": _num(info.get("regularMarketPrice")) or _num(info.get("currentPrice")),
            "trailing_pe": _round(_num(info.get("trailingPE")), 2),
            "forward_pe": _round(_num(info.get("forwardPE")), 2),
            "price_to_book": _round(_num(info.get("priceToBook")), 2),
            "roe_pct": _round(roe * 100 if roe is not None else None, 2),
            "dividend_yield_pct": _round(dividend_yield_pct(info), 2),
            "market_cap_bn": _round(cap / 1e9 if cap is not None else None, 2)}


def peer_comparison(symbol, peers):
    norm = lambda s: s.strip().upper() if isinstance(s, str) else s
    symbol = norm(symbol)
    if not isinstance(symbol, str) or not _TICKER.fullmatch(symbol):
        raise ValueError("symbol must be a ticker such as AAPL")
    if not isinstance(peers, list) or not 1 <= len(peers) <= 7:
        raise ValueError("peers must list 1..7 tickers chosen by the caller")
    peers = list(dict.fromkeys(p for p in map(norm, peers) if p != symbol))
    if not peers or not all(isinstance(p, str) and _TICKER.fullmatch(p) for p in peers):
        raise ValueError("peers must be tickers other than the symbol itself")
    try:
        import yfinance as yf
    except ImportError:
        return {"success": False, "error_code": "dependency_missing", "error": "Install arthera-financial-service[market]"}
    rows, failures, end = [], [], time.monotonic() + 40
    for ticker in [symbol] + peers:
        if time.monotonic() > end:
            failures.append({"symbol": ticker, "error_code": "source_timeout"})
            continue
        try:
            info = yf.Ticker(ticker).info or {}
        except Exception:
            info = {}
        if not info or (_num(info.get("regularMarketPrice")) is None and _num(info.get("currentPrice")) is None):
            failures.append({"symbol": ticker, "error_code": "source_unavailable"})
            continue
        rows.append(_row(ticker, info))
    target = next((r for r in rows if r["symbol"] == symbol), None)
    others = [r for r in rows if r["symbol"] != symbol]
    if target is None or not others:
        return {"success": False, "error_code": "source_unavailable", "failures": failures,
                "error": "The source returned no data for the symbol or for any peer"}
    comparison = {}
    for metric in METRICS:
        values = [r[metric] for r in others if r[metric] is not None]
        if target[metric] is None or not values:
            continue
        median = _median(values)
        comparison[metric] = {"target": target[metric], "peer_median": _round(median, 2), "peers_with_value": len(values),
                              "vs_median_pct": _round((target[metric] / median - 1) * 100, 1) if median else None}
    currencies = {r["currency"] for r in rows if r["currency"]}
    limitations = ["The caller chose the peers; the comparison is only as good as that choice.",
                   "Ratios are as reported by the source and may use different fiscal periods per company.",
                   "Distance from the median is descriptive, not a valuation verdict."]
    if len(currencies) > 1:
        limitations.append("Market caps are in different currencies (" + ", ".join(sorted(currencies)) + ") and are not converted.")
    return {"success": True, "symbol": symbol, "table": rows, "comparison": comparison, "failures": failures,
            "source": "yfinance", "retrieved_at": datetime.now(timezone.utc).isoformat(), "limitations": limitations}
