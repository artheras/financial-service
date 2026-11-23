---
name: ic-memo-due-diligence
description: >
  Institutional company due diligence, red-flag screening, vertical industry KPI
  analysis, and deterministic DCF valuation SOP using arthera-finance MCP tools.
---

# Institutional Company Due Diligence & IC Memo SOP

Use this workflow whenever asked to conduct company due diligence, evaluate an
investment thesis, screen for accounting/governance red flags, or prepare an
Investment Committee (IC) memo.

## Non-Negotiable Evidence Rules

1. **Numbers you can check**: Never estimate margins, Beneish M-Score, Piotroski
   F-Score, Altman Z'', DCF share price, or Rule of 40 in prose. Always invoke the
   deterministic `arthera-finance` MCP tools and report `source`, `formulas`,
   `limitations`, and any `missing_inputs`.
2. **Missing data stays unknown**: If an input is missing, report it as unknown
   rather than guessing.
3. **Human approval on risk gates**: Any `APPROVAL_REQUIRED` or `BLOCK` verdict
   from `financial_pretrade_risk_check` must be surfaced verbatim at the top of
   the memo.

## Standard 6-Step Due Diligence Sequence

1. **Symbol & Official SEC Filings**
   - Call `financial_resolve_symbol(query)` to lock the canonical ticker.
   - If host networking is enabled, call `financial_sec_filing_monitor(symbol)` to
     pull recent `10-K`, `10-Q`, `8-K`, `Form 4`, and `SC 13D/13G` filings with
     direct `sec.gov` provenance URLs.
2. **Profitability & Earnings Quality**
   - Call `financial_analyze_statements(company_name, financials)` for margins and
     DuPont ROE decomposition.
   - Call `financial_quality_scores(current, prior)` for 9-signal Piotroski F-Score
     and Altman Z''-Score.
3. **Accounting, Governance & Insider Red-Flag Screening**
   - Call `financial_due_diligence_red_flags(current, prior, governance, insider_transactions, current_risk_factors, prior_risk_factors)`
     to compute the 8-variable Beneish M-Score, accruals divergence, receivables vs.
     revenue spread, customer concentration, clustered insider selling, and 10-K
     risk-factor diffs.
4. **Vertical Industry KPIs**
   - For enterprise software/cloud or chip/hardware companies, call
     `financial_industry_kpis(industry="saas"|"semiconductor", metrics=...)` to
     evaluate Rule of 40, NDR, Magic Number, Burn Multiple, or DIO cycle phase,
     Book-to-Bill, CapEx/R&D intensity, and operating leverage.
5. **Deterministic DCF Valuation & Sensitivity Matrix**
   - Call `financial_dcf_valuation(base_fcf, growth_rates, wacc, terminal_growth, net_debt, diluted_shares, current_price)`
     and present both the base-case implied share price and the 2D `WACC x terminal_growth`
     sensitivity grid (explicitly noting terminal value share of EV).
6. **Pre-Trade Portfolio Compliance Gate**
   - If a target allocation is proposed, call `financial_pretrade_risk_check(...)`
     and include the `PASS` / `APPROVAL_REQUIRED` / `BLOCK` verdict before any
     recommendation or Confluence write-back.
