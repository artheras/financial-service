"""Deterministic vertical Industry KPI Packs (SaaS & Semiconductor).

Computes institutional sector-specific operating metrics from caller-supplied inputs.
Missing inputs stay null; reference thresholds are explicitly labeled as benchmarks, not advice.
"""
from __future__ import annotations

from typing import Any

from .api import bounded_json, finite


def _opt(d: dict[str, Any], key: str) -> float | None:
    v = d.get(key)
    if v is None:
        return None
    return finite(v, key, -1e15, 1e15)


def _div(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den == 0:
        return None
    return num / den


def _saas_kpis(m: dict[str, Any]) -> dict[str, Any]:
    rev = _opt(m, "revenue")
    prior_rev = _opt(m, "prior_revenue")
    gp = _opt(m, "gross_profit")
    fcf = _opt(m, "free_cash_flow")
    arr = _opt(m, "arr")
    prior_arr = _opt(m, "prior_arr")
    ndr = _opt(m, "net_dollar_retention")  # decimal, e.g. 1.18 for 118%
    sm_prior = _opt(m, "prior_sales_marketing")
    net_burn = _opt(m, "net_burn")  # positive number representing cash burned
    cac = _opt(m, "cac")
    arpa_annual = _opt(m, "arpa_annual")
    gross_churn = _opt(m, "annual_gross_churn")

    rev_growth = _div((rev - prior_rev) if None not in (rev, prior_rev) else None, abs(prior_rev) if prior_rev else None)
    arr_growth = _div((arr - prior_arr) if None not in (arr, prior_arr) else None, abs(prior_arr) if prior_arr else None)
    net_new_arr = (arr - prior_arr) if None not in (arr, prior_arr) else None
    gross_margin = _div(gp, rev)
    fcf_margin = _div(fcf, rev)

    rule_of_40 = (
        round((rev_growth + fcf_margin) * 100.0, 2)
        if rev_growth is not None and fcf_margin is not None
        else None
    )
    magic_number = _div(net_new_arr, sm_prior)
    burn_multiple = _div(net_burn, net_new_arr) if net_new_arr and net_new_arr > 0 else None

    ltv = (
        _div(arpa_annual * gross_margin, gross_churn)
        if None not in (arpa_annual, gross_margin, gross_churn) and gross_churn > 0
        else None
    )
    ltv_to_cac = _div(ltv, cac)
    cac_payback_months = (
        _div(cac, (arpa_annual * gross_margin) / 12.0)
        if None not in (cac, arpa_annual, gross_margin) and arpa_annual > 0 and gross_margin > 0
        else None
    )

    signals: list[str] = []
    if rule_of_40 is not None:
        signals.append(f"Rule of 40 is {rule_of_40:.1f} ({'top-quartile >= 40' if rule_of_40 >= 40 else 'below 40 benchmark'}).")
    if ndr is not None:
        signals.append(f"Net Dollar Retention is {ndr:.1%} ({'strong expansion >= 115%' if ndr >= 1.15 else 'healthy >= 100%' if ndr >= 1.0 else 'net contraction < 100%'}).")
    if magic_number is not None:
        signals.append(f"SaaS Magic Number is {magic_number:.2f} ({'efficient >= 0.75' if magic_number >= 0.75 else 'watch GTM efficiency < 0.75'}).")
    if burn_multiple is not None and burn_multiple > 2.0:
        signals.append(f"Burn Multiple is {burn_multiple:.2f}x (> 2.0x watch threshold).")

    return {
        "metrics": {
            "revenue_growth": round(rev_growth, 4) if rev_growth is not None else None,
            "arr_growth": round(arr_growth, 4) if arr_growth is not None else None,
            "net_new_arr": round(net_new_arr, 2) if net_new_arr is not None else None,
            "gross_margin": round(gross_margin, 4) if gross_margin is not None else None,
            "fcf_margin": round(fcf_margin, 4) if fcf_margin is not None else None,
            "rule_of_40": rule_of_40,
            "net_dollar_retention": round(ndr, 4) if ndr is not None else None,
            "magic_number": round(magic_number, 3) if magic_number is not None else None,
            "burn_multiple": round(burn_multiple, 3) if burn_multiple is not None else None,
            "ltv_to_cac": round(ltv_to_cac, 2) if ltv_to_cac is not None else None,
            "cac_payback_months": round(cac_payback_months, 1) if cac_payback_months is not None else None,
        },
        "benchmark_signals": signals,
        "formulas": {
            "rule_of_40": "(revenue_growth + fcf_margin) * 100",
            "magic_number": "(arr - prior_arr) / prior_sales_marketing",
            "burn_multiple": "net_burn / net_new_arr",
            "ltv_to_cac": "((arpa_annual * gross_margin) / annual_gross_churn) / cac",
            "cac_payback_months": "cac / ((arpa_annual * gross_margin) / 12)",
        },
    }


def _semiconductor_kpis(m: dict[str, Any]) -> dict[str, Any]:
    rev = _opt(m, "revenue")
    prior_rev = _opt(m, "prior_revenue")
    cogs = _opt(m, "cogs")
    prior_cogs = _opt(m, "prior_cogs")
    gp = _opt(m, "gross_profit")
    prior_gp = _opt(m, "prior_gross_profit")
    op_inc = _opt(m, "operating_income")
    prior_op_inc = _opt(m, "prior_operating_income")
    inv = _opt(m, "inventory")
    prior_inv = _opt(m, "prior_inventory")
    capex = _opt(m, "capex")
    rd = _opt(m, "rd_expense")
    bookings = _opt(m, "bookings")
    billings = _opt(m, "billings")
    days_in_period = _opt(m, "days_in_period") or 365.0

    rev_growth = _div((rev - prior_rev) if None not in (rev, prior_rev) else None, abs(prior_rev) if prior_rev else None)
    op_inc_growth = _div((op_inc - prior_op_inc) if None not in (op_inc, prior_op_inc) else None, abs(prior_op_inc) if prior_op_inc else None)

    gm = _div(gp, rev)
    prior_gm = _div(prior_gp, prior_rev)
    gm_change_bps = round((gm - prior_gm) * 10_000.0, 1) if gm is not None and prior_gm is not None else None

    dio = _div(inv, cogs) * days_in_period if _div(inv, cogs) is not None else None
    prior_dio = _div(prior_inv, prior_cogs) * days_in_period if _div(prior_inv, prior_cogs) is not None else None
    dio_change_days = round(dio - prior_dio, 1) if dio is not None and prior_dio is not None else None

    inv_to_rev = _div(inv, rev)
    capex_intensity = _div(abs(capex) if capex is not None else None, rev)
    rd_intensity = _div(abs(rd) if rd is not None else None, rev)
    book_to_bill = _div(bookings, billings)
    operating_leverage = _div(op_inc_growth, rev_growth) if rev_growth and abs(rev_growth) > 1e-4 else None

    # Deterministic cycle heuristic from supplied revenue growth & DIO trajectory
    cycle_phase = None
    if rev_growth is not None and dio_change_days is not None:
        if rev_growth > 0.05 and dio_change_days <= 5:
            cycle_phase = "upcycle_expansion"
        elif rev_growth > 0.0 and dio_change_days > 10:
            cycle_phase = "late_cycle_inventory_build"
        elif rev_growth <= 0.0 and dio_change_days > 5:
            cycle_phase = "downcycle_oversupply"
        else:
            cycle_phase = "trough_destocking"

    signals: list[str] = []
    if dio is not None and dio_change_days is not None:
        signals.append(f"DIO is {dio:.1f} days ({dio_change_days:+.1f} days vs prior period).")
    if book_to_bill is not None:
        signals.append(f"Book-to-Bill is {book_to_bill:.2f} ({'demand > supply' if book_to_bill >= 1.0 else 'softening backlog < 1.0'}).")
    if gm_change_bps is not None:
        signals.append(f"Gross margin changed {gm_change_bps:+.1f} bps YoY.")
    if cycle_phase is not None:
        signals.append(f"Deterministic cycle indicator: {cycle_phase}.")

    return {
        "metrics": {
            "revenue_growth": round(rev_growth, 4) if rev_growth is not None else None,
            "gross_margin": round(gm, 4) if gm is not None else None,
            "gross_margin_change_bps": gm_change_bps,
            "dio_days": round(dio, 1) if dio is not None else None,
            "dio_change_days": dio_change_days,
            "inventory_to_revenue": round(inv_to_rev, 4) if inv_to_rev is not None else None,
            "capex_intensity": round(capex_intensity, 4) if capex_intensity is not None else None,
            "rd_intensity": round(rd_intensity, 4) if rd_intensity is not None else None,
            "book_to_bill": round(book_to_bill, 3) if book_to_bill is not None else None,
            "operating_leverage": round(operating_leverage, 2) if operating_leverage is not None else None,
            "cycle_phase_indicator": cycle_phase,
        },
        "benchmark_signals": signals,
        "formulas": {
            "dio_days": "(inventory / cogs) * days_in_period",
            "book_to_bill": "bookings / billings",
            "capex_intensity": "abs(capex) / revenue",
            "rd_intensity": "abs(rd_expense) / revenue",
            "operating_leverage": "operating_income_growth / revenue_growth",
        },
    }


def industry_kpis(industry: str, metrics: dict[str, Any]) -> dict[str, Any]:
    """Evaluate sector-specific operating KPIs for 'saas' or 'semiconductor'."""
    if not isinstance(metrics, dict):
        raise ValueError("metrics must be an object")
    bounded_json(metrics, 65_536)
    sector = (industry or "").strip().lower()
    if sector == "saas":
        res = _saas_kpis(metrics)
    elif sector in ("semiconductor", "semis", "chips"):
        sector = "semiconductor"
        res = _semiconductor_kpis(metrics)
    else:
        raise ValueError("industry must be 'saas' or 'semiconductor'")

    return {
        "success": True,
        "industry": sector,
        "source": "caller-supplied operating metrics",
        **res,
        "limitations": [
            "Benchmark labels are generic institutional reference heuristics, not investment advice.",
            "Missing inputs remain null rather than estimated.",
        ],
    }
