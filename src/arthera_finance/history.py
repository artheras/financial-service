"""Daily A-share history (akshare) and crypto OHLCV (ccxt public endpoints).

Ported from aria-code's local finance tools. Rows use the same shape as
financial_market_history ({date, open, high, low, close, volume}), so they can
be passed straight to financial_backtest and financial_technical_factors.
aria-code silently swapped a failing exchange for yfinance's BTC-USD and kept
the original label; here a failure is reported and the caller decides.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta, timezone

from .derivatives import EXCHANGES

_ASHARE = re.compile(r"(?:SH|SZ|BJ)?(\d{6})(?:\.(?:SS|SH|SZ|BJ))?")
_PAIR = re.compile(r"[A-Z0-9]{2,12}/[A-Z0-9]{2,8}")
TIMEFRAMES = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800}
ADJUST = {"qfq": "qfq", "hfq": "hfq", "none": ""}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _num(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bar(day, o, h, l, c, v):
    values = [_num(x) for x in (o, h, l, c)]
    volume = _num(v)
    if any(x is None or x <= 0 for x in values) or volume is None or volume < 0:
        return None
    o, h, l, c = values
    if h < max(o, c, l) or l > min(o, c, h):
        return None
    return {"date": day, "open": o, "high": h, "low": l, "close": c, "volume": volume}


def ashare_code(symbol):
    if not isinstance(symbol, str):
        raise ValueError("symbol must be a 6-digit A-share code such as 600519 or 600519.SS")
    match = _ASHARE.fullmatch(symbol.strip().upper())
    if not match:
        raise ValueError("symbol must be a 6-digit A-share code such as 600519 or 600519.SS")
    return match.group(1)


def ashare_history(symbol, days=365, adjust="qfq"):
    code = ashare_code(symbol)
    if type(days) is not int or not 1 <= days <= 3650:
        raise ValueError("days must be an integer from 1 to 3650")
    if adjust not in ADJUST:
        raise ValueError("adjust must be qfq (forward), hfq (backward) or none")
    try:
        import akshare as ak
    except Exception:  # a partial akshare install can raise more than ImportError
        return {"success": False, "error_code": "dependency_missing", "error": "Install arthera-financial-service[cn]"}
    end = date.today()
    frame = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=(end - timedelta(days=days)).strftime("%Y%m%d"),
                               end_date=end.strftime("%Y%m%d"), adjust=ADJUST[adjust])
    rows, extra, dropped = [], [], 0
    for record in ([] if frame is None or frame.empty else frame.to_dict("records")):
        day = str(record.get("日期", ""))[:10]
        bar = _bar(day, record.get("开盘"), record.get("最高"), record.get("最低"), record.get("收盘"), record.get("成交量"))
        if bar is None or len(day) != 10:
            dropped += 1
            continue
        rows.append(bar)
        extra.append({k: v for k, v in (("amount_cny", _num(record.get("成交额"))),
                                         ("turnover_rate_pct", _num(record.get("换手率")))) if v is not None})
    if not rows:
        return {"success": False, "error_code": "empty_history", "error": "No daily bars were returned for this code"}
    rows, extra = rows[-2000:], extra[-2000:]
    return {"success": True, "symbol": code, "data": rows, "extra": extra, "source": "akshare (eastmoney)",
            "as_of": rows[-1]["date"], "interval": "1d", "adjusted": adjust, "dropped_rows": dropped,
            "retrieved_at": _now(),
            "limitations": ["Volume is in lots (100 shares) as published.",
                            "qfq prices are forward-adjusted: past bars change after each new dividend or split.",
                            "Suspended days have no bar. At most the last 2000 bars are returned."]}


def crypto_ohlcv(symbol="BTC/USDT", exchange="binance", timeframe="1d", limit=200):
    if not isinstance(symbol, str) or not _PAIR.fullmatch(symbol.upper().replace("-", "/")):
        raise ValueError("symbol must be a spot pair such as BTC/USDT")
    symbol = symbol.upper().replace("-", "/")
    if exchange not in EXCHANGES:
        raise ValueError(f"exchange must be one of: {', '.join(EXCHANGES)}")
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"timeframe must be one of: {', '.join(TIMEFRAMES)}")
    if type(limit) is not int or not 2 <= limit <= 1000:
        raise ValueError("limit must be an integer from 2 to 1000")
    try:
        import ccxt
    except ImportError:
        return {"success": False, "error_code": "dependency_missing", "error": "Install arthera-financial-service[crypto]"}
    client = getattr(ccxt, exchange)({"timeout": 8000, "enableRateLimit": True})
    try:
        candles = client.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    except Exception:
        return {"success": False, "error_code": "source_unavailable", "exchange": exchange,
                "error": "The exchange did not return candles (network or regional restrictions are common); "
                         "try another exchange, or financial_market_history with a yfinance symbol such as BTC-USD"}
    rows, dropped = [], 0
    for candle in candles or []:
        stamp = _num(candle[0]) if isinstance(candle, (list, tuple)) and len(candle) >= 6 else None
        bar = None
        if stamp is not None:
            when = datetime.fromtimestamp(stamp / 1000, timezone.utc)
            label = when.date().isoformat() if timeframe in ("1d", "1w") else when.isoformat().replace("+00:00", "Z")
            bar = _bar(label, *candle[1:6])
        if bar is None:
            dropped += 1
            continue
        rows.append(bar)
    if not rows:
        return {"success": False, "error_code": "empty_history", "exchange": exchange,
                "error": "The exchange returned no usable candles"}
    return {"success": True, "symbol": symbol, "exchange": exchange, "data": rows, "interval": timeframe,
            "as_of": rows[-1]["date"], "dropped_rows": dropped, "source": f"ccxt:{exchange}", "retrieved_at": _now(),
            "limitations": ["Candle times are UTC opens; the last candle may still be forming.",
                            "Volume is in the base currency on this exchange only, not market-wide.",
                            "Only 1d and 1w candles carry plain dates, which financial_backtest requires."]}
