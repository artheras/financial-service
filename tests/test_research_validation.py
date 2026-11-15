"""Tests for independent Research & Backtest Validation Gate."""

from arthera_finance.research_validation import (
    deflated_sharpe_ratio,
    minimum_backtest_length_years,
    validate_research_backtest,
)
from arthera_finance.risk_policy import RiskPolicy


def test_deflated_sharpe_ratio_penalizes_multiple_trials():
    dsr_single = deflated_sharpe_ratio(1.8, n_obs=500, n_trials=1)
    dsr_many = deflated_sharpe_ratio(1.8, n_obs=500, n_trials=100)
    assert dsr_single > 0.99
    assert dsr_many < dsr_single


def test_minimum_backtest_length_increases_with_trials():
    btl_1 = minimum_backtest_length_years(1.5, n_trials=1)
    btl_50 = minimum_backtest_length_years(1.5, n_trials=50)
    assert btl_50 > btl_1 > 0


def test_validate_research_backtest_approved_and_rejected():
    policy = RiskPolicy(
        max_position_weight=0.25,
        max_drawdown_pct=0.20,
        approval_required_above_weight=0.15,
    )

    ok = validate_research_backtest(
        is_sharpe=2.0,
        oos_sharpe=1.6,
        n_obs=750,
        n_trials=5,
        max_drawdown_pct=0.10,
        pit_passed=True,
        risk_policy=policy,
        target_weight=0.10,
    )
    assert ok.passed is True
    assert ok.verdict == "approved"
    assert ok.dsr >= 0.95

    overfit_and_leaked = validate_research_backtest(
        is_sharpe=3.0,
        oos_sharpe=0.3,
        n_obs=40,
        n_trials=200,
        max_drawdown_pct=0.25,
        pit_passed=False,
        risk_policy=policy,
        target_weight=0.30,
    )
    assert overfit_and_leaked.passed is False
    assert overfit_and_leaked.verdict == "rejected"
    assert any("Point-in-Time" in r for r in overfit_and_leaked.reasons)
    assert any("degradation" in r for r in overfit_and_leaked.reasons)
    assert any("Deflated Sharpe" in r for r in overfit_and_leaked.reasons)
