"""Macro snapshots: US indicators from FRED, China indicators and policy rates.

Ported from aria-code's macro tools. Keys come only from this service's
environment; the old fallback that read ~/.arthera/providers.json is gone.
FRED needs only the standard library; China data needs the [cn] extra.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

_FRED_API = "https://api.stlouisfed.org/fred/series/observations"
_FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
_DEADLINE = 30  # seconds for one tool call, across all its requests
_STALE_DAYS = 180

# key: (FRED series, label, unit, FRED units transformation)
US_INDICATORS = {
    "gdp": ("GDP", "GDP (USD bn, quarterly)", "B", ""),
    "gdp_growth": ("A191RL1Q225SBEA", "Real GDP growth (%)", "%", ""),
    "cpi": ("CPIAUCSL", "CPI (urban consumers)", "idx", ""),
    "cpi_yoy": ("CPIAUCSL", "CPI year on year (%)", "%", "pc1"),
    "core_cpi": ("CPILFESL", "Core CPI (ex food and energy)", "idx", ""),
    "core_cpi_yoy": ("CPILFESL", "Core CPI year on year (%)", "%", "pc1"),
    "pce": ("PCEPI", "PCE price index", "idx", ""),
    "fed_rate": ("FEDFUNDS", "Fed funds rate (%)", "%", ""),
    "unemployment": ("UNRATE", "Unemployment rate (%)", "%", ""),
    "m2": ("M2SL", "M2 money supply (USD bn)", "B", ""),
    "10y_yield": ("DGS10", "10-year Treasury yield (%)", "%", ""),
    "2y_yield": ("DGS2", "2-year Treasury yield (%)", "%", ""),
    "vix": ("VIXCLS", "VIX", "", ""),
    "retail_sales": ("RSAFS", "Retail sales (USD m, SA)", "M", ""),
    "industrial": ("INDPRO", "Industrial production index", "idx", ""),
    "housing": ("HOUST", "Housing starts (thousands, SAAR)", "K", ""),
    "ppi": ("PPIACO", "PPI (producer prices)", "idx", ""),
}
US_DEFAULT = ("gdp_growth", "cpi_yoy", "fed_rate", "unemployment", "10y_yield", "2y_yield", "m2", "ppi")

# The Bank of England's BOERUKM stopped in 2017; SONIA tracks Bank Rate.
# ECBDFR is the deposit facility rate; the Japan series is the call rate.
POLICY_RATES = (
    ("fed", "FEDFUNDS", "Fed funds (US)"),
    ("ecb", "ECBDFR", "ECB deposit facility rate"),
    ("uk", "IUDSOIA", "UK SONIA (tracks Bank Rate)"),
    ("jp", "IRSTCI01JPM156N", "Japan call rate (tracks BoJ policy)"),
)

CN_INDICATORS = {
    "cpi": ("macro_china_cpi_yearly", "China CPI year on year (%)"),
    "ppi": ("macro_china_ppi_yearly", "China PPI year on year (%)"),
    "pmi": ("macro_china_pmi_yearly", "China manufacturing PMI"),
    "pmi_service": ("macro_china_non_man_pmi", "China non-manufacturing PMI"),
    "gdp": ("macro_china_gdp_yearly", "China GDP growth (%)"),
    "lpr": ("macro_china_lpr", "Loan prime rate"),
    "m2": ("macro_china_money_supply", "China money supply"),
}


class _Clock:
    def __init__(self, seconds=_DEADLINE):
        self.end = time.monotonic() + seconds

    def timeout(self, cap=8.0):
        left = self.end - time.monotonic()
        if left <= 0:
            raise TimeoutError("Source deadline exceeded")
        return min(cap, left)


def _get(url, clock):
    request = urllib.request.Request(url, headers={"User-Agent": "arthera-financial-service"})
    with urllib.request.urlopen(request, timeout=clock.timeout()) as response:
        return response.read(2_000_000)


def _number(text):
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def fred_observations(series_id, limit, units, clock):
    """Last *limit* observations, oldest first, as {date, value}; missing values dropped."""
    key = os.getenv("FRED_API_KEY", "")
    if key:
        query = {"series_id": series_id, "limit": limit, "sort_order": "desc",
                 "file_type": "json", "api_key": key}
        if units:
            query["units"] = units
        data = json.loads(_get(f"{_FRED_API}?{urllib.parse.urlencode(query)}", clock))
        rows = [{"date": o.get("date", ""), "value": _number(o.get("value"))}
                for o in reversed(data.get("observations", []))]
    else:
        query = {"id": series_id}
        if units:
            query["transformation"] = units
        text = _get(f"{_FRED_CSV}?{urllib.parse.urlencode(query)}", clock).decode("utf-8", "replace")
        reader = csv.reader(io.StringIO(text))
        next(reader, None)
        rows = [{"date": r[0], "value": _number(r[1])} for r in reader if len(r) == 2]
    return [r for r in rows if r["value"] is not None and len(r["date"]) >= 10][-limit:]


def _fetch_fred(series_id, limit, units, clock, failures, key):
    try:
        return fred_observations(series_id, limit, units, clock)
    except TimeoutError:
        failures.append({"indicator": key, "error_code": "source_timeout"})
    except Exception:
        failures.append({"indicator": key, "error_code": "source_unavailable"})
    return []


def _age_days(day):
    try:
        return (date.today() - date.fromisoformat(day[:10])).days
    except ValueError:
        return None


def us_macro(indicator="all", periods=12):
    if not isinstance(indicator, str) or (indicator != "all" and indicator not in US_INDICATORS):
        raise ValueError(f"indicator must be all or one of: {', '.join(US_INDICATORS)}")
    if type(periods) is not int or not 1 <= periods <= 60:
        raise ValueError("periods must be an integer from 1 to 60")
    keys = US_DEFAULT if indicator == "all" else (indicator,)
    clock, failures, data = _Clock(), [], {}
    for key in keys:
        series_id, label, unit, units = US_INDICATORS[key]
        rows = _fetch_fred(series_id, periods, units, clock, failures, key)
        if not rows:
            continue
        latest, prev = rows[-1], (rows[-2] if len(rows) > 1 else None)
        data[key] = {"series_id": series_id, "label": label, "unit": unit, "latest": latest,
                     "previous": prev, "change": round(latest["value"] - prev["value"], 3) if prev else None,
                     "observations": rows}
    if not data:
        return {"success": False, "error_code": "source_unavailable", "failures": failures,
                "error": "FRED returned no observations for the requested indicators"}
    result = {"success": True, "country": "US", "source": "fred", "data": data, "failures": failures,
              "retrieved_at": datetime.now(timezone.utc).isoformat(),
              "limitations": ["Values as published by FRED; recent observations may be revised.",
                              "Each indicator has its own frequency and last date; compare latest.date before combining."]}
    if "10y_yield" in data and "2y_yield" in data:
        spread = round(data["10y_yield"]["latest"]["value"] - data["2y_yield"]["latest"]["value"], 3)
        result["yield_curve"] = {"spread_10y_2y": spread,
                                 "shape": "normal" if spread > 0.2 else "inverted" if spread < 0 else "flat"}
    return result


def _akshare():
    try:
        import akshare
    except Exception:  # akshare can raise more than ImportError on a partial install
        return None
    return akshare


def _cell(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value if math.isfinite(float(value)) else None
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    number = getattr(value, "item", None)
    if callable(number):
        try:
            return _cell(number())
        except (TypeError, ValueError):
            return None
    return str(value)[:80]


def _records(frame, limit):
    rows = []
    for record in frame.tail(limit).to_dict("records"):
        row = {str(k)[:40]: _cell(v) for k, v in list(record.items())[:12]}
        rows.append({k: v for k, v in row.items() if v is not None})
    return rows


def cn_macro(indicator="all", periods=12):
    if not isinstance(indicator, str) or (indicator != "all" and indicator not in CN_INDICATORS):
        raise ValueError(f"indicator must be all or one of: {', '.join(CN_INDICATORS)}")
    if type(periods) is not int or not 1 <= periods <= 60:
        raise ValueError("periods must be an integer from 1 to 60")
    ak = _akshare()
    if ak is None:
        return {"success": False, "error_code": "dependency_missing",
                "error": "Install arthera-financial-service[cn]"}
    keys = tuple(CN_INDICATORS) if indicator == "all" else (indicator,)
    clock, data, failures = _Clock(60), {}, []
    for key in keys:
        function, label = CN_INDICATORS[key]
        try:
            clock.timeout()
            frame = getattr(ak, function)()
        except TimeoutError:
            failures.append({"indicator": key, "error_code": "source_timeout"})
            continue
        except Exception:
            failures.append({"indicator": key, "error_code": "source_unavailable"})
            continue
        if frame is None or getattr(frame, "empty", True):
            failures.append({"indicator": key, "error_code": "source_unavailable"})
            continue
        rows = _records(frame, periods)
        data[key] = {"label": label, "latest": rows[-1] if rows else {}, "rows": rows}
    if not data:
        return {"success": False, "error_code": "source_unavailable", "failures": failures,
                "error": "akshare returned no rows for the requested indicators"}
    return {"success": True, "country": "CN", "source": "akshare", "data": data, "failures": failures,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "limitations": ["Rows and column names as published by akshare's upstream sources (Chinese).",
                            "Release calendars differ; check each row's date before comparing."]}


def central_bank_rates():
    clock, failures, rates = _Clock(), [], []
    for key, series_id, label in POLICY_RATES:
        rows = _fetch_fred(series_id, 3, "", clock, failures, key)
        if not rows:
            continue
        latest = rows[-1]
        age = _age_days(latest["date"])
        rates.append({"key": key, "series_id": series_id, "label": label, "value": latest["value"],
                      "date": latest["date"][:10], "stale": age is not None and age > _STALE_DAYS})
    ak = _akshare()
    if ak is not None:
        try:
            clock.timeout()
            frame = ak.macro_china_lpr()
            row = frame.iloc[-1]
            when = str(_cell(row.iloc[0]))[:10]
            for key, column, position, label in (("lpr1y", "1年期贷款市场报价利率", 1, "PBOC LPR 1-year"),
                                                 ("lpr5y", "5年期贷款市场报价利率", 2, "PBOC LPR 5-year")):
                value = _number(row.get(column, row.iloc[position]))
                if value is not None:
                    age = _age_days(when)
                    rates.append({"key": key, "series_id": "akshare:macro_china_lpr", "label": label,
                                  "value": value, "date": when,
                                  "stale": age is not None and age > _STALE_DAYS})
        except Exception:
            failures.append({"indicator": "lpr", "error_code": "source_unavailable"})
    if not rates:
        return {"success": False, "error_code": "source_unavailable", "failures": failures,
                "error": "No policy rate could be retrieved"}
    return {"success": True, "rates": rates, "failures": failures,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "limitations": [f"A rate older than {_STALE_DAYS} days is marked stale; do not describe it as current.",
                            "UK and Japan are market rates that track the policy rate, not the policy rate itself."]}
