"""Futures snapshots (commodities and index futures) and Treasury yields, via yfinance.

Ported from aria-code's commodity, futures and bond tools. Two labels are
corrected: ^IRX is the 13-week bill (aria-code called it the 2-year; the
2-year is in financial_us_macro), and Chinese contract names that aria-code
mapped to unrelated proxies (rebar to COMEX copper) are not mapped at all.
"""
from __future__ import annotations

import math
import re
import time
from datetime import datetime, timezone

FUTURES = {
    "gold": "GC=F", "黄金": "GC=F", "silver": "SI=F", "白银": "SI=F", "platinum": "PL=F", "palladium": "PA=F",
    "crude oil": "CL=F", "oil": "CL=F", "wti": "CL=F", "原油": "CL=F", "brent": "BZ=F",
    "natural gas": "NG=F", "天然气": "NG=F", "gasoline": "RB=F", "heating oil": "HO=F",
    "copper": "HG=F", "铜": "HG=F", "aluminum": "ALI=F",
    "wheat": "ZW=F", "corn": "ZC=F", "soybeans": "ZS=F", "coffee": "KC=F", "cocoa": "CC=F", "sugar": "SB=F",
    "cotton": "CT=F", "live cattle": "LE=F", "lean hogs": "HE=F",
    "s&p 500": "ES=F", "sp500": "ES=F", "nasdaq 100": "NQ=F", "dow": "YM=F", "russell 2000": "RTY=F",
    "nikkei": "NIY=F", "vix": "^VIX",
}
YIELDS = (("13w", "^IRX", "13-week Treasury bill"), ("5y", "^FVX", "5-year Treasury note"),
          ("10y", "^TNX", "10-year Treasury note"), ("30y", "^TYX", "30-year Treasury bond"))
_TICKER = re.compile(r"\^?[A-Z0-9]{1,6}(=F)?")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _closes(frame, column="Close"):
    out = []
    for idx, value in frame[column].items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number) and number > 0:
            out.append((str(idx.date()), number))
    return out


def resolve(name):
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a commodity or index future such as gold, crude oil, ES=F")
    key = " ".join(name.strip().lower().split())
    if key in FUTURES:
        return FUTURES[key]
    ticker = name.strip().upper()
    if _TICKER.fullmatch(ticker) and (ticker.endswith("=F") or ticker.startswith("^")):
        return ticker
    raise ValueError(f"unknown contract; use a yfinance futures ticker (e.g. GC=F) or one of: {', '.join(sorted(FUTURES))}"[:400])


def futures_snapshot(name):
    ticker = resolve(name)
    try:
        import yfinance as yf
    except ImportError:
        return {"success": False, "error_code": "dependency_missing", "error": "Install arthera-financial-service[market]"}
    frame = yf.Ticker(ticker).history(period="1y")
    rows = _closes(frame)
    if len(rows) < 2:
        return {"success": False, "error_code": "empty_history", "error": "No recent prices for this contract"}
    highs = [h for _, h in _closes(frame, "High")] or [c for _, c in rows]
    lows = [l for _, l in _closes(frame, "Low")] or [c for _, c in rows]
    (day, last), (_, prev) = rows[-1], rows[-2]
    high, low = max(highs), min(lows)
    return {"success": True, "name": name, "ticker": ticker, "as_of": day, "last": round(last, 4),
            "change_pct": round((last / prev - 1) * 100, 3), "high_1y": round(high, 4), "low_1y": round(low, 4),
            "return_1y_pct": round((last / rows[0][1] - 1) * 100, 2),
            "from_1y_high_pct": round((last / high - 1) * 100, 2), "source": "yfinance", "retrieved_at": _now(),
            "limitations": ["Front-month continuous contract: returns include roll effects, not a spot price.",
                            "Prices are in the exchange's quote units (e.g. USD per troy ounce for gold)."]}


def treasury_yields():
    try:
        import yfinance as yf
    except ImportError:
        return {"success": False, "error_code": "dependency_missing", "error": "Install arthera-financial-service[market]"}
    rows, failures, end = [], [], time.monotonic() + 30
    for key, ticker, label in YIELDS:
        if time.monotonic() > end:
            failures.append({"tenor": key, "error_code": "source_timeout"})
            continue
        try:
            closes = _closes(yf.Ticker(ticker).history(period="5d"))
        except Exception:
            closes = []
        if not closes:
            failures.append({"tenor": key, "error_code": "source_unavailable"})
            continue
        rows.append({"tenor": key, "ticker": ticker, "label": label, "yield_pct": round(closes[-1][1], 3),
                     "date": closes[-1][0]})
    if not rows:
        return {"success": False, "error_code": "source_unavailable", "failures": failures,
                "error": "No Treasury yields were returned"}
    by = {r["tenor"]: r["yield_pct"] for r in rows}
    result = {"success": True, "yields": rows, "failures": failures, "source": "yfinance (CBOE yield indices)",
              "retrieved_at": _now(),
              "limitations": ["CBOE indices; the 2-year yield is not among them (use financial_us_macro for FRED DGS2).",
                              "The 13-week bill is quoted on a discount basis, not as a bond-equivalent yield."]}
    if "10y" in by and "13w" in by:
        result["spread_10y_13w_pct"] = round(by["10y"] - by["13w"], 3)
    return result
