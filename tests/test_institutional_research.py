"""Tests for institutional DCF valuation, due diligence red-flag engine, and industry KPI packs."""
from __future__ import annotations

from arthera_finance import due_diligence, industry_kpis, valuation


def test_dcf_valuation_and_sensitivity_matrix():
    res = valuation.dcf_valuation(
        base_fcf=100.0,
        growth_rates=[0.12, 0.10, 0.08, 0.06, 0.05],
        wacc=0.09,
        terminal_growth=0.025,
        net_debt=50.0,
        diluted_shares=10.0,
        current_price=180.0,
    )
    assert res["success"] is True
    val = res["valuation"]
    assert val["enterprise_value"] > val["pv_explicit_fcf"] > 0
    assert val["equity_value"] == round(val["enterprise_value"] - 50.0, 2)
    assert val["implied_share_price"] > 0
    assert 0.5 < val["terminal_value_share_of_ev"] < 0.9
    assert len(res["sensitivity_matrix"]["rows"]) == 5
    # Higher WACC must yield lower implied share price at same terminal growth
    prices_at_base_tg = [row["implied_share_prices_by_tg"]["0.0250"] for row in res["sensitivity_matrix"]["rows"]]
    assert prices_at_base_tg == sorted(prices_at_base_tg, reverse=True)


def test_dcf_rejects_wacc_below_terminal_growth():
    try:
        valuation.dcf_valuation(base_fcf=100.0, growth_rates=[0.1], wacc=0.03, terminal_growth=0.04)
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "wacc" in str(exc)


def test_due_diligence_red_flags_detects_beneish_governance_insider_and_risk_diff():
    current = {
        "revenue": 1500.0,
        "receivables": 450.0,
        "gross_profit": 550.0,
        "current_assets": 700.0,
        "ppe": 300.0,
        "total_assets": 2000.0,
        "depreciation": 20.0,
        "sga": 260.0,
        "total_liabilities": 1200.0,
        "net_income": 260.0,
        "operating_cash_flow": 40.0,
    }
    prior = {
        "revenue": 1000.0,
        "receivables": 150.0,
        "gross_profit": 450.0,
        "current_assets": 500.0,
        "ppe": 350.0,
        "total_assets": 1400.0,
        "depreciation": 40.0,
        "sga": 180.0,
        "total_liabilities": 600.0,
    }
    res = due_diligence.due_diligence_red_flags(
        current=current,
        prior=prior,
        governance={
            "auditor_changed": True,
            "top_customer_pct": 34.5,
        },
        insider_transactions=[
            {"insider": "CEO", "side": "sell", "value_usd": 5_000_000},
            {"insider": "CFO", "side": "sell", "value_usd": 2_000_000},
            {"insider": "COO", "side": "sell", "value_usd": 1_500_000},
        ],
        current_risk_factors=["Supply chain concentration", "SEC informal inquiry into revenue timing"],
        prior_risk_factors=["Supply chain concentration"],
    )
    assert res["success"] is True
    assert res["overall_status"] == "ELEVATED_RISK"
    codes = {f["code"] for f in res["red_flags"]}
    assert "BENEISH_M_SCORE_ELEVATED" in codes
    assert "HIGH_ACCRUALS_RATIO" in codes
    assert "RECEIVABLES_OUTPACING_REVENUE" in codes
    assert "AUDITOR_CHANGE" in codes
    assert "HIGH_CUSTOMER_CONCENTRATION" in codes
    assert "CLUSTERED_INSIDER_SELLING" in codes
    assert "NEW_10K_RISK_FACTORS" in codes
    assert res["risk_factor_diff"]["newly_added_risks"] == ["SEC informal inquiry into revenue timing"]


def test_sec_filing_monitor_offline_guard(monkeypatch):
    monkeypatch.setenv("FINANCIAL_SERVICE_NETWORK", "0")
    res = due_diligence.sec_filing_monitor("AAPL")
    assert res["success"] is False
    assert res["error_code"] == "network_disabled"


def test_industry_kpis_saas_and_semiconductor():
    saas = industry_kpis.industry_kpis(
        "saas",
        {
            "revenue": 130.0,
            "prior_revenue": 100.0,
            "gross_profit": 104.0,
            "free_cash_flow": 26.0,
            "arr": 140.0,
            "prior_arr": 105.0,
            "prior_sales_marketing": 40.0,
            "net_dollar_retention": 1.22,
            "cac": 12.0,
            "arpa_annual": 15.0,
            "annual_gross_churn": 0.08,
        },
    )
    assert saas["success"] is True
    assert saas["metrics"]["rule_of_40"] == 50.0
    assert saas["metrics"]["magic_number"] == 0.875
    assert saas["metrics"]["cac_payback_months"] == 12.0

    semi = industry_kpis.industry_kpis(
        "semiconductor",
        {
            "revenue": 1200.0,
            "prior_revenue": 1000.0,
            "cogs": 480.0,
            "prior_cogs": 450.0,
            "gross_profit": 720.0,
            "prior_gross_profit": 550.0,
            "operating_income": 420.0,
            "prior_operating_income": 280.0,
            "inventory": 130.0,
            "prior_inventory": 125.0,
            "capex": 180.0,
            "rd_expense": 210.0,
            "bookings": 1380.0,
            "billings": 1200.0,
        },
    )
    assert semi["success"] is True
    assert semi["metrics"]["book_to_bill"] == 1.15
    assert semi["metrics"]["cycle_phase_indicator"] == "upcycle_expansion"
    assert semi["metrics"]["gross_margin_change_bps"] == 500.0
