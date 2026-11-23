"""Deterministic DCF valuation and 2D WACC x Terminal-Growth sensitivity matrix.

All calculations are strictly bounded, deterministic, and require no network access.
"""
from __future__ import annotations

from typing import Any

from .api import finite


def dcf_valuation(
    base_fcf: float,
    growth_rates: list[float],
    wacc: float,
    terminal_growth: float = 0.025,
    net_debt: float = 0.0,
    diluted_shares: float = 1.0,
    current_price: float | None = None,
    wacc_steps: list[float] | None = None,
    terminal_growth_steps: list[float] | None = None,
) -> dict[str, Any]:
    """Compute a deterministic Gordon-Growth DCF valuation and 2D sensitivity matrix."""
    fcf0 = finite(base_fcf, "base_fcf", -1e15, 1e15)
    if fcf0 <= 0:
        raise ValueError("base_fcf must be positive for Gordon-Growth DCF valuation")

    if not isinstance(growth_rates, list) or not 1 <= len(growth_rates) <= 10:
        raise ValueError("growth_rates requires 1..10 annual growth rates (as decimals, e.g. 0.12)")
    rates = [finite(g, "growth_rates[i]", -0.9, 5.0) for g in growth_rates]

    disc = finite(wacc, "wacc", 0.01, 0.50)
    tg = finite(terminal_growth, "terminal_growth", -0.05, 0.15)
    if disc <= tg:
        raise ValueError("wacc must be strictly greater than terminal_growth")

    nd = finite(net_debt, "net_debt", -1e15, 1e15)
    shares = finite(diluted_shares, "diluted_shares", 1e-6, 1e15)
    price = finite(current_price, "current_price", 1e-6, 1e12) if current_price is not None else None

    def _run_dcf(r_wacc: float, r_tg: float) -> dict[str, Any]:
        if r_wacc <= r_tg:
            return {"valid": False, "implied_share_price": None, "enterprise_value": None}
        projections = []
        fcf = fcf0
        pv_fcf_sum = 0.0
        for year, g in enumerate(rates, start=1):
            fcf = fcf * (1.0 + g)
            df = (1.0 + r_wacc) ** year
            pv = fcf / df
            pv_fcf_sum += pv
            projections.append({
                "year": year,
                "growth_rate": round(g, 4),
                "projected_fcf": round(fcf, 2),
                "discount_factor": round(df, 4),
                "pv_fcf": round(pv, 2),
            })
        terminal_fcf = fcf * (1.0 + r_tg)
        terminal_value = terminal_fcf / (r_wacc - r_tg)
        pv_terminal = terminal_value / ((1.0 + r_wacc) ** len(rates))
        ev = pv_fcf_sum + pv_terminal
        equity_value = ev - nd
        implied_price = equity_value / shares
        return {
            "valid": True,
            "projections": projections,
            "pv_explicit_fcf": round(pv_fcf_sum, 2),
            "terminal_value": round(terminal_value, 2),
            "pv_terminal_value": round(pv_terminal, 2),
            "terminal_value_share_of_ev": round(pv_terminal / ev, 4) if ev > 0 else None,
            "enterprise_value": round(ev, 2),
            "equity_value": round(equity_value, 2),
            "implied_share_price": round(implied_price, 2),
        }

    base = _run_dcf(disc, tg)

    w_offsets = wacc_steps if wacc_steps is not None else [-0.01, -0.005, 0.0, 0.005, 0.01]
    tg_offsets = terminal_growth_steps if terminal_growth_steps is not None else [-0.005, -0.0025, 0.0, 0.0025, 0.005]
    if not 1 <= len(w_offsets) <= 7 or not 1 <= len(tg_offsets) <= 7:
        raise ValueError("Sensitivity step grids must each contain 1..7 offsets")

    w_grid = [round(disc + finite(d, "wacc_step", -0.2, 0.2), 4) for d in w_offsets]
    tg_grid = [round(tg + finite(d, "tg_step", -0.1, 0.1), 4) for d in tg_offsets]

    matrix: list[dict[str, Any]] = []
    for w_val in w_grid:
        row_prices = {}
        for tg_val in tg_grid:
            res = _run_dcf(w_val, tg_val)
            row_prices[f"{tg_val:.4f}"] = res["implied_share_price"]
        matrix.append({"wacc": w_val, "implied_share_prices_by_tg": row_prices})

    upside_pct = None
    if price is not None and base["implied_share_price"] is not None:
        upside_pct = round((base["implied_share_price"] - price) / price, 4)

    return {
        "success": True,
        "source": "caller-supplied DCF assumptions",
        "assumptions": {
            "base_fcf": fcf0,
            "forecast_years": len(rates),
            "growth_rates": rates,
            "wacc": disc,
            "terminal_growth": tg,
            "net_debt": nd,
            "diluted_shares": shares,
            "current_price": price,
        },
        "valuation": {
            "pv_explicit_fcf": base["pv_explicit_fcf"],
            "terminal_value": base["terminal_value"],
            "pv_terminal_value": base["pv_terminal_value"],
            "terminal_value_share_of_ev": base["terminal_value_share_of_ev"],
            "enterprise_value": base["enterprise_value"],
            "equity_value": base["equity_value"],
            "implied_share_price": base["implied_share_price"],
            "upside_vs_current_price": upside_pct,
        },
        "projections": base["projections"],
        "sensitivity_matrix": {
            "wacc_axis": w_grid,
            "terminal_growth_axis": tg_grid,
            "rows": matrix,
        },
        "formulas": {
            "terminal_value": "FCF_N * (1 + terminal_growth) / (wacc - terminal_growth)",
            "enterprise_value": "sum(PV(FCF_t)) + PV(terminal_value)",
            "equity_value": "enterprise_value - net_debt",
            "implied_share_price": "equity_value / diluted_shares",
        },
        "limitations": [
            "Deterministic Gordon-Growth DCF based solely on caller-supplied inputs; not investment advice.",
            "Ensure base_fcf, net_debt, and diluted_shares use consistent units (e.g. all in USD millions and millions of shares).",
        ],
    }
