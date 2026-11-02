"""Market overviews: sector moves, A-share northbound flow, limit-up pool,
an A-share screener and major indices.

Ported from aria-code's local finance tools. China data comes from akshare
([cn] extra), US and global data from yfinance ([market] extra). Every row is
reduced to finite numbers and short strings before it leaves the service.
"""
from __future__ import annotations

import math
import re
import time
from datetime import date, datetime, timezone

US_SECTORS = {"Technology": "XLK", "Healthcare": "XLV", "Financials": "XLF", "Consumer Discretionary": "XLY",
              "Industrials": "XLI", "Energy": "XLE", "Utilities": "XLU", "Real Estate": "XLRE",
              "Materials": "XLB", "Communication Services": "XLC", "Consumer Staples": "XLP"}
INDICES = {"S&P 500": "^GSPC", "NASDAQ Composite": "^IXIC", "Dow Jones": "^DJI", "Russell 2000": "^RUT",
           "VIX": "^VIX", "SSE Composite": "000001.SS", "SZSE Component": "399001.SZ", "ChiNext": "399006.SZ",
           "Hang Seng": "^HSI", "Nikkei 225": "^N225", "FTSE 100": "^FTSE", "DAX": "^GDAXI"}
SCREEN_SORT = {"change_pct": "涨跌幅", "market_cap": "总市值", "turnover_rate": "换手率", "pe": "市盈率-动态"}


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


def _text(value, limit=40):
    return None if value is None else str(value)[:limit]


def _akshare():
    try:
        import akshare
    except Exception:  # a partial akshare install can raise more than ImportError
        return None
    return akshare


def _missing(extra):
    return {"success": False, "error_code": "dependency_missing", "error": f"Install arthera-financial-service[{extra}]"}


def _retry(fn, *args, tries=2, **kwargs):
    """akshare's eastmoney hosts fail individually; one retry usually lands on a healthy one."""
    for attempt in range(tries):
        try:
            return fn(*args, **kwargs)
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(0.8)


def _last_change(yf, ticker):
    frame = yf.Ticker(ticker).history(period="5d")
    closes = [c for c in (_num(v) for v in frame["Close"].tolist()) if c is not None and c > 0]
    if len(closes) < 2:
        return None
    return {"price": round(closes[-1], 4), "change_pct": round((closes[-1] / closes[-2] - 1) * 100, 2),
            "date": str(frame.index[-1].date())}


def sector_performance(market="cn", top=5):
    if market not in ("cn", "us"):
        raise ValueError("market must be cn or us")
    if type(top) is not int or not 1 <= top <= 20:
        raise ValueError("top must be an integer from 1 to 20")
    if market == "cn":
        ak = _akshare()
        if ak is None:
            return _missing("cn")
        frame = _retry(ak.stock_board_industry_name_em)
        rows = []
        for record in frame.to_dict("records"):
            change = _num(record.get("涨跌幅"))
            if change is None:
                continue
            rows.append({"sector": _text(record.get("板块名称")), "change_pct": change,
                         "rising": _num(record.get("上涨家数")), "falling": _num(record.get("下跌家数"))})
        source, note = "akshare (eastmoney industry boards)", "Eastmoney industry boards, intraday during trading hours."
    else:
        try:
            import yfinance as yf
        except ImportError:
            return _missing("market")
        rows, end = [], time.monotonic() + 40
        for name, etf in US_SECTORS.items():
            if time.monotonic() > end:
                break
            try:
                move = _last_change(yf, etf)
            except Exception:
                move = None
            if move:
                rows.append({"sector": name, "etf": etf, **move})
        source, note = "yfinance (SPDR sector ETFs)", "Sector ETF close-to-close change on the last trading day."
    if not rows:
        return {"success": False, "error_code": "source_unavailable", "error": "No sector data was returned"}
    rows.sort(key=lambda r: r["change_pct"], reverse=True)
    return {"success": True, "market": market, "sectors": len(rows), "top": rows[:top], "bottom": rows[-top:][::-1],
            "source": source, "retrieved_at": _now(), "limitations": [note]}


def northbound_flow():
    ak = _akshare()
    if ak is None:
        return _missing("cn")
    frame = _retry(ak.stock_hsgt_fund_flow_summary_em)
    north = [r for r in frame.to_dict("records") if r.get("资金方向") == "北向"]
    flows = {}
    for row in north:
        value = _num(row.get("成交净买额"))
        if value is not None and row.get("板块") in ("沪股通", "深股通"):
            flows[row["板块"]] = flows.get(row["板块"], 0.0) + value
    if not flows:
        return {"success": False, "error_code": "source_unavailable", "error": "No northbound rows were returned"}
    if all(value == 0 for value in flows.values()):
        return {"success": False, "error_code": "not_disclosed",
                "error": "Net buying is reported as zero: since August 2024 the exchanges no longer publish "
                         "daily northbound net buying, so zero means undisclosed, not no flow"}
    day = next((_text(r.get("交易日"), 10) for r in north if r.get("交易日") is not None), None)
    return {"success": True, "date": day, "shanghai_connect_net_buy_100m_cny": round(flows.get("沪股通", 0.0), 2),
            "shenzhen_connect_net_buy_100m_cny": round(flows.get("深股通", 0.0), 2),
            "total_net_buy_100m_cny": round(sum(flows.values()), 2), "source": "akshare (eastmoney)",
            "retrieved_at": _now(),
            "limitations": ["Values are in 100 million CNY (亿元) as published.",
                            "Since August 2024 daily northbound net buying is no longer disclosed by the exchanges; "
                            "check that a non-zero figure comes from the source's own estimate before relying on it."]}


def limit_up_pool(day="", limit=50):
    day = day or date.today().isoformat()
    if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
        raise ValueError("day must be an ISO date (YYYY-MM-DD)")
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("limit must be an integer from 1 to 200")
    ak = _akshare()
    if ak is None:
        return _missing("cn")
    frame = _retry(ak.stock_zt_pool_em, date=day.replace("-", ""))
    records = [] if frame is None or frame.empty else frame.to_dict("records")
    stocks = [{k: v for k, v in {"code": _text(r.get("代码"), 10), "name": _text(r.get("名称"), 20),
                                 "change_pct": _num(r.get("涨跌幅")), "consecutive_limits": _num(r.get("连板数")),
                                 "first_lock_time": _text(r.get("首次封板时间"), 8), "industry": _text(r.get("所属行业"), 20)}.items()
               if v is not None} for r in records[:limit]]
    return {"success": True, "date": day, "count": len(records), "stocks": stocks, "source": "akshare (eastmoney)",
            "retrieved_at": _now(),
            "limitations": ["An empty pool on a non-trading day is not an error.", f"At most {limit} stocks are listed."]}


def screen_ashare(max_pe=50.0, min_market_cap_100m=0.0, sort_by="change_pct", limit=20):
    for name, value, low, high in (("max_pe", max_pe, 0.1, 10000), ("min_market_cap_100m", min_market_cap_100m, 0, 1e6)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise ValueError(f"{name} must be a number from {low} to {high:g}")
    if sort_by not in SCREEN_SORT:
        raise ValueError(f"sort_by must be one of: {', '.join(SCREEN_SORT)}")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 to 100")
    ak = _akshare()
    if ak is None:
        return _missing("cn")
    frame = _retry(ak.stock_zh_a_spot_em)
    rows = []
    for r in frame.to_dict("records"):
        name = str(r.get("名称") or "")
        price, pe, cap = _num(r.get("最新价")), _num(r.get("市盈率-动态")), _num(r.get("总市值"))
        if re.search("ST|退", name) or not price or price <= 0 or pe is None or not 0 < pe <= max_pe:
            continue
        if cap is None or cap < min_market_cap_100m * 1e8:
            continue
        key = _num(r.get(SCREEN_SORT[sort_by]))
        if key is None:
            continue
        rows.append({"code": _text(r.get("代码"), 10), "name": name[:20], "price": price,
                     "change_pct": _num(r.get("涨跌幅")), "pe_dynamic": pe, "pb": _num(r.get("市净率")),
                     "market_cap_100m_cny": round(cap / 1e8, 1), "turnover_rate": _num(r.get("换手率")), "_key": key})
    rows.sort(key=lambda r: r["_key"], reverse=sort_by != "pe")
    stocks = [{k: v for k, v in r.items() if k != "_key" and v is not None} for r in rows[:limit]]
    return {"success": True, "matched": len(rows), "stocks": stocks,
            "criteria": {"max_pe": max_pe, "min_market_cap_100m": min_market_cap_100m, "sort_by": sort_by},
            "source": "akshare (eastmoney spot)", "retrieved_at": _now(),
            "limitations": ["Excludes ST and delisting names, loss-making (PE <= 0) and missing-PE stocks.",
                            "Spot data is intraday during trading hours. A screen is not a recommendation."]}


def market_indices():
    try:
        import yfinance as yf
    except ImportError:
        return _missing("market")
    rows, failures, end = [], [], time.monotonic() + 40
    for name, ticker in INDICES.items():
        if time.monotonic() > end:
            failures.append({"index": name, "error_code": "source_timeout"})
            continue
        try:
            move = _last_change(yf, ticker)
        except Exception:
            move = None
        if move:
            rows.append({"index": name, "ticker": ticker, **move})
        else:
            failures.append({"index": name, "error_code": "source_unavailable"})
    if not rows:
        return {"success": False, "error_code": "source_unavailable", "failures": failures,
                "error": "No index data was returned"}
    return {"success": True, "indices": rows, "failures": failures, "source": "yfinance", "retrieved_at": _now(),
            "limitations": ["Close-to-close change on each index's last trading day; dates differ across time zones."]}
