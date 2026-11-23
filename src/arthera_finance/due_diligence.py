"""Institutional due diligence red-flag engine and official SEC EDGAR filing monitor.

- `due_diligence_red_flags`: 100% offline deterministic screening (Beneish M-Score,
  accruals quality, receivables/inventory divergence, customer concentration,
  governance alerts, Form 4 insider net-selling signal, and 10-K risk-factor diff).
- `sec_filing_monitor`: queries official data.sec.gov submissions (10-K, 10-Q, 8-K,
  Form 4, SC 13D/13G) with direct sec.gov provenance URLs when host networking is enabled.
"""
from __future__ import annotations

import re
from typing import Any

from .api import bounded_json, finite, network_enabled
from .datasources.sources.edgar_source import EDGARSource, _fetch_json


def _opt_num(d: dict[str, Any], key: str) -> float | None:
    val = d.get(key)
    if val is None:
        return None
    return finite(val, key, -1e15, 1e15)


def _safe_div(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den == 0:
        return None
    return num / den


def _compute_beneish(current: dict[str, Any], prior: dict[str, Any]) -> dict[str, Any]:
    """Compute 8-variable Beneish M-Score where inputs exist; missing variables stay null."""
    rev_c, rev_p = _opt_num(current, "revenue"), _opt_num(prior, "revenue")
    ar_c, ar_p = _opt_num(current, "receivables"), _opt_num(prior, "receivables")
    gp_c, gp_p = _opt_num(current, "gross_profit"), _opt_num(prior, "gross_profit")
    ca_c, ca_p = _opt_num(current, "current_assets"), _opt_num(prior, "current_assets")
    ppe_c, ppe_p = _opt_num(current, "ppe"), _opt_num(prior, "ppe")
    ta_c, ta_p = _opt_num(current, "total_assets"), _opt_num(prior, "total_assets")
    dep_c, dep_p = _opt_num(current, "depreciation"), _opt_num(prior, "depreciation")
    sga_c, sga_p = _opt_num(current, "sga"), _opt_num(prior, "sga")
    tl_c, tl_p = _opt_num(current, "total_liabilities"), _opt_num(prior, "total_liabilities")
    ni_c = _opt_num(current, "net_income")
    cfo_c = _opt_num(current, "operating_cash_flow")

    dsri = _safe_div(_safe_div(ar_c, rev_c), _safe_div(ar_p, rev_p))
    gmi = _safe_div(_safe_div(gp_p, rev_p), _safe_div(gp_c, rev_c))

    aq_c = (
        1.0 - (ca_c + ppe_c) / ta_c
        if None not in (ca_c, ppe_c, ta_c) and ta_c != 0
        else None
    )
    aq_p = (
        1.0 - (ca_p + ppe_p) / ta_p
        if None not in (ca_p, ppe_p, ta_p) and ta_p != 0
        else None
    )
    aqi = _safe_div(aq_c, aq_p)
    sgi = _safe_div(rev_c, rev_p)

    dep_rate_c = _safe_div(dep_c, (dep_c + ppe_c) if None not in (dep_c, ppe_c) else None)
    dep_rate_p = _safe_div(dep_p, (dep_p + ppe_p) if None not in (dep_p, ppe_p) else None)
    depi = _safe_div(dep_rate_p, dep_rate_c)

    sgai = _safe_div(_safe_div(sga_c, rev_c), _safe_div(sga_p, rev_p))
    lvgi = _safe_div(_safe_div(tl_c, ta_c), _safe_div(tl_p, ta_p))
    tata = _safe_div((ni_c - cfo_c) if None not in (ni_c, cfo_c) else None, ta_c)

    components = {
        "DSRI": round(dsri, 4) if dsri is not None else None,
        "GMI": round(gmi, 4) if gmi is not None else None,
        "AQI": round(aqi, 4) if aqi is not None else None,
        "SGI": round(sgi, 4) if sgi is not None else None,
        "DEPI": round(depi, 4) if depi is not None else None,
        "SGAI": round(sgai, 4) if sgai is not None else None,
        "LVGI": round(lvgi, 4) if lvgi is not None else None,
        "TATA": round(tata, 4) if tata is not None else None,
    }
    missing = [k for k, v in components.items() if v is None]
    m_score = None
    if not missing:
        m_score = round(
            -4.84
            + 0.92 * dsri
            + 0.528 * gmi
            + 0.404 * aqi
            + 0.892 * sgi
            + 0.115 * depi
            - 0.172 * sgai
            + 4.679 * tata
            - 0.327 * lvgi,
            3,
        )
    return {
        "m_score": m_score,
        "threshold": -1.78,
        "manipulation_flag": (m_score > -1.78) if m_score is not None else None,
        "components": components,
        "missing_components": missing,
    }


def _risk_factor_diff(current_risks: list[str] | None, prior_risks: list[str] | None) -> dict[str, Any] | None:
    if current_risks is None and prior_risks is None:
        return None
    cur_list = [str(x).strip() for x in (current_risks or []) if str(x).strip()]
    pri_list = [str(x).strip() for x in (prior_risks or []) if str(x).strip()]
    norm = lambda s: re.sub(r"\s+", " ", s.lower()).strip()
    pri_set = {norm(s) for s in pri_list}
    cur_set = {norm(s) for s in cur_list}
    added = [s for s in cur_list if norm(s) not in pri_set]
    removed = [s for s in pri_list if norm(s) not in cur_set]
    return {
        "current_count": len(cur_list),
        "prior_count": len(pri_list),
        "newly_added_risks": added[:25],
        "removed_risks": removed[:25],
    }


def due_diligence_red_flags(
    current: dict[str, Any],
    prior: dict[str, Any] | None = None,
    governance: dict[str, Any] | None = None,
    insider_transactions: list[dict[str, Any]] | None = None,
    current_risk_factors: list[str] | None = None,
    prior_risk_factors: list[str] | None = None,
) -> dict[str, Any]:
    """Screen supplied company disclosures for accounting, governance, and insider red flags."""
    if not isinstance(current, dict):
        raise ValueError("current must be an object")
    bounded_json(current, 131_072)
    if prior is not None:
        if not isinstance(prior, dict):
            raise ValueError("prior must be an object")
        bounded_json(prior, 131_072)
    pri = prior or {}
    gov = governance or {}
    if not isinstance(gov, dict):
        raise ValueError("governance must be an object")

    red_flags: list[dict[str, Any]] = []

    # 1. Beneish M-Score
    beneish = _compute_beneish(current, pri)
    if beneish["manipulation_flag"] is True:
        red_flags.append({
            "severity": "P0",
            "category": "accounting_quality",
            "code": "BENEISH_M_SCORE_ELEVATED",
            "detail": f"Beneish M-Score {beneish['m_score']} exceeds -1.78 threshold.",
            "evidence": beneish["components"],
        })

    # 2. Cash-flow accruals divergence
    ni = _opt_num(current, "net_income")
    cfo = _opt_num(current, "operating_cash_flow")
    ta = _opt_num(current, "total_assets")
    accruals_ratio = _safe_div((ni - cfo) if None not in (ni, cfo) else None, ta)
    if accruals_ratio is not None and accruals_ratio > 0.08:
        red_flags.append({
            "severity": "P1",
            "category": "earnings_quality",
            "code": "HIGH_ACCRUALS_RATIO",
            "detail": f"Accruals ratio (Net Income - CFO) / Total Assets is {accruals_ratio:.2%} (> 8%).",
            "evidence": {"accruals_ratio": round(accruals_ratio, 4)},
        })

    # 3. Receivables growing much faster than revenue
    rev_c, rev_p = _opt_num(current, "revenue"), _opt_num(pri, "revenue")
    ar_c, ar_p = _opt_num(current, "receivables"), _opt_num(pri, "receivables")
    rev_g = _safe_div((rev_c - rev_p) if None not in (rev_c, rev_p) else None, abs(rev_p) if rev_p else None)
    ar_g = _safe_div((ar_c - ar_p) if None not in (ar_c, ar_p) else None, abs(ar_p) if ar_p else None)
    if rev_g is not None and ar_g is not None and (ar_g - rev_g) > 0.15:
        red_flags.append({
            "severity": "P1",
            "category": "revenue_recognition",
            "code": "RECEIVABLES_OUTPACING_REVENUE",
            "detail": f"Receivables grew {ar_g:.1%} vs revenue growth {rev_g:.1%} (spread +{(ar_g - rev_g):.1%}).",
            "evidence": {"receivables_growth": round(ar_g, 4), "revenue_growth": round(rev_g, 4)},
        })

    # 4. Governance & concentration disclosures
    if gov.get("auditor_changed") is True:
        red_flags.append({
            "severity": "P0",
            "category": "governance",
            "code": "AUDITOR_CHANGE",
            "detail": "Recent external auditor change reported; inspect Item 4.01 8-K for disagreements.",
        })
    if gov.get("financial_restatement") is True:
        red_flags.append({
            "severity": "P0",
            "category": "accounting_quality",
            "code": "FINANCIAL_RESTATEMENT",
            "detail": "Financial restatement or Item 4.02 non-reliance disclosure flagged.",
        })
    if gov.get("going_concern_doubt") is True:
        red_flags.append({
            "severity": "P0",
            "category": "solvency",
            "code": "GOING_CONCERN_WARNING",
            "detail": "Auditor or management going-concern doubt flagged.",
        })
    top_cust = _opt_num(gov, "top_customer_pct")
    if top_cust is not None and top_cust >= 20.0:
        red_flags.append({
            "severity": "P1" if top_cust >= 30.0 else "P2",
            "category": "customer_concentration",
            "code": "HIGH_CUSTOMER_CONCENTRATION",
            "detail": f"Single top customer accounts for {top_cust:.1f}% of revenue.",
            "evidence": {"top_customer_pct": top_cust},
        })

    # 5. Insider Form 4 net selling signal
    insider_summary = None
    if insider_transactions is not None:
        if not isinstance(insider_transactions, list) or len(insider_transactions) > 500:
            raise ValueError("insider_transactions must be a list of at most 500 entries")
        buys = 0.0
        sells = 0.0
        sellers: set[str] = set()
        for tx in insider_transactions:
            if not isinstance(tx, dict):
                raise ValueError("Each insider transaction must be an object")
            val = finite(tx.get("value_usd", 0.0), "value_usd", 0.0, 1e15)
            side = str(tx.get("side", "")).lower()
            if side in ("buy", "purchase", "p"):
                buys += val
            elif side in ("sell", "sale", "s"):
                sells += val
                if tx.get("insider"):
                    sellers.add(str(tx["insider"]))
        net = buys - sells
        insider_summary = {
            "total_buys_usd": round(buys, 2),
            "total_sells_usd": round(sells, 2),
            "net_usd": round(net, 2),
            "distinct_sellers": len(sellers),
        }
        if sells > 0 and sells >= 5.0 * max(buys, 1.0) and len(sellers) >= 3:
            red_flags.append({
                "severity": "P2",
                "category": "insider_activity",
                "code": "CLUSTERED_INSIDER_SELLING",
                "detail": f"{len(sellers)} distinct insiders sold ${sells:,.0f} vs ${buys:,.0f} bought.",
                "evidence": insider_summary,
            })

    # 6. 10-K Risk Factor Diff
    risk_diff = _risk_factor_diff(current_risk_factors, prior_risk_factors)
    if risk_diff and len(risk_diff["newly_added_risks"]) > 0:
        red_flags.append({
            "severity": "P2",
            "category": "disclosure_change",
            "code": "NEW_10K_RISK_FACTORS",
            "detail": f"{len(risk_diff['newly_added_risks'])} newly added risk factor(s) vs prior filing.",
            "evidence": {"newly_added_count": len(risk_diff["newly_added_risks"])},
        })

    p0 = sum(1 for f in red_flags if f["severity"] == "P0")
    p1 = sum(1 for f in red_flags if f["severity"] == "P1")
    if p0 > 0:
        status = "ELEVATED_RISK"
    elif p1 > 0 or red_flags:
        status = "WATCH"
    elif beneish["m_score"] is None and accruals_ratio is None and not gov:
        status = "INSUFFICIENT_DATA"
    else:
        status = "CLEAR"

    return {
        "success": True,
        "source": "caller-supplied due diligence disclosures",
        "overall_status": status,
        "summary": {
            "p0_critical": p0,
            "p1_high": p1,
            "p2_watch": sum(1 for f in red_flags if f["severity"] == "P2"),
            "total_flags": len(red_flags),
        },
        "red_flags": red_flags,
        "beneish_m_score": beneish,
        "accruals_ratio": round(accruals_ratio, 4) if accruals_ratio is not None else None,
        "insider_summary": insider_summary,
        "risk_factor_diff": risk_diff,
        "limitations": [
            "Deterministic screening over caller-supplied disclosures; does not verify unaudited data or constitute legal/investment advice.",
        ],
    }


def sec_filing_monitor(symbol: str, limit: int = 20) -> dict[str, Any]:
    """Fetch recent official SEC EDGAR filings (10-K, 10-Q, 8-K, Form 4, SC 13D/G) with sec.gov URLs."""
    if not network_enabled():
        return {
            "success": False,
            "error_code": "network_disabled",
            "error": "SEC EDGAR filing monitor requires host FINANCIAL_SERVICE_NETWORK=1.",
        }
    sym = (symbol or "").strip().upper()
    if not sym or len(sym) > 16:
        raise ValueError("symbol must be 1..16 characters")
    lim = int(finite(limit, "limit", 1, 100))

    src = EDGARSource()
    cik = src.ticker_to_cik(sym)
    if not cik:
        return {"success": False, "error_code": "symbol_not_found", "error": f"No SEC CIK found for {sym}"}

    sub = _fetch_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
    if not sub:
        return {"success": False, "error_code": "source_unavailable", "error": "Unable to fetch SEC submissions"}

    recent = sub.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    descs = recent.get("primaryDocDescription", [])

    cik_int = str(int(cik))
    tracked = {"10-K", "10-Q", "8-K", "4", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A", "DEF 14A"}
    buckets: dict[str, list[dict[str, Any]]] = {
        "periodic_reports": [],
        "material_events_8k": [],
        "insider_form4": [],
        "ownership_13d_13g": [],
        "proxy_def14a": [],
    }

    collected = 0
    for i, form in enumerate(forms):
        if form not in tracked:
            continue
        acc = accessions[i] if i < len(accessions) else ""
        acc_clean = acc.replace("-", "")
        doc = docs[i] if i < len(docs) else ""
        url = (
            f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/{doc}"
            if acc_clean and doc
            else None
        )
        entry = {
            "form": form,
            "filing_date": dates[i] if i < len(dates) else None,
            "accession_number": acc,
            "description": descs[i] if i < len(descs) else "",
            "sec_url": url,
        }
        if form in ("10-K", "10-Q"):
            buckets["periodic_reports"].append(entry)
        elif form == "8-K":
            buckets["material_events_8k"].append(entry)
        elif form == "4":
            buckets["insider_form4"].append(entry)
        elif form.startswith("SC 13"):
            buckets["ownership_13d_13g"].append(entry)
        elif form == "DEF 14A":
            buckets["proxy_def14a"].append(entry)
        collected += 1
        if collected >= lim:
            break

    return {
        "success": True,
        "symbol": sym,
        "cik": cik,
        "entity_name": sub.get("name"),
        "sic_description": sub.get("sicDescription"),
        "source": "SEC EDGAR Official Submissions API (data.sec.gov)",
        "counts": {k: len(v) for k, v in buckets.items()},
        "filings": buckets,
    }
