"""Tests for Unified Risk Policy Schema and strictest-wins resolution."""

from arthera_finance.risk_policy import (
    RiskPolicy,
    check_order_compliance,
    resolve_risk_policy,
)


def test_resolve_risk_policy_strictest_wins():
    account = RiskPolicy(
        name="account",
        max_position_weight=0.40,
        max_leverage=1.5,
        max_drawdown_pct=0.25,
        approval_required_above_weight=0.20,
        min_cash_reserve_pct=0.05,
        allow_short=True,
    )
    strategy = RiskPolicy(
        name="strategy",
        max_position_weight=0.25,
        max_leverage=1.2,
        max_drawdown_pct=0.18,
        approval_required_above_weight=0.15,
        min_cash_reserve_pct=0.08,
        allow_short=True,
    )
    execution = RiskPolicy(
        name="execution",
        max_position_weight=0.15,
        max_leverage=1.0,
        max_drawdown_pct=0.15,
        approval_required_above_weight=0.10,
        min_cash_reserve_pct=0.10,
        allow_short=False,
    )

    eff = resolve_risk_policy(account, strategy, execution)
    assert eff.max_position_weight == 0.15
    assert eff.max_leverage == 1.0
    assert eff.max_drawdown_pct == 0.15
    assert eff.approval_required_above_weight == 0.10
    assert eff.min_cash_reserve_pct == 0.10
    assert eff.allow_short is False


def test_check_order_compliance_violations_and_approval():
    policy = RiskPolicy(
        max_position_weight=0.25,
        max_leverage=1.0,
        max_drawdown_pct=0.15,
        approval_required_above_weight=0.10,
        min_cash_reserve_pct=0.05,
        allow_short=False,
    )

    ok_needs_approval = check_order_compliance(
        policy,
        target_weight=0.15,
        gross_leverage=1.0,
        current_drawdown_pct=0.05,
        cash_reserve_pct=0.20,
    )
    assert ok_needs_approval.allowed is True
    assert ok_needs_approval.requires_approval is True

    blocked = check_order_compliance(
        policy,
        target_weight=0.30,
        gross_leverage=1.2,
        current_drawdown_pct=0.16,
        cash_reserve_pct=0.02,
        is_short=True,
    )
    assert blocked.allowed is False
    assert len(blocked.violations) == 5
