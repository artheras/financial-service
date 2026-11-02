import math

import pytest

from arthera_finance import analytics, server


def closes(n, start=100.0, step=0.5):
    return [start + step * i + (1.5 if i % 3 == 0 else -0.7) for i in range(n)]


def test_technical_factors_match_hand_computation():
    series = closes(130)
    f = server.invoke(analytics.technical_factors, series, [1000.0] * 129 + [2000.0])["factors"]
    assert f["return_5d"] == round(series[-1] / series[-6] - 1, 5)
    assert f["ma_20"] == round(sum(series[-20:]) / 20, 4)
    assert "ma_200" not in f and f["volume_ratio_20d"] == round(2000 / (19 * 1000 + 2000) * 20, 3)
    assert 50 < f["rsi_14"] < 100 and f["trend_score"] == 1.0
    assert f["bb_lower"] < series[-1] < f["bb_upper"] or f["bb_position"] is not None


def test_macd_matches_pandas_when_available():
    pd = pytest.importorskip("pandas")
    series = closes(80)
    f = analytics.technical_factors(series)["factors"]
    s = pd.Series(series)
    macd = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()
    assert f["macd"] == round(float(macd.iloc[-1]), 5)
    assert f["bb_upper"] == round(float(s.rolling(20).mean().iloc[-1] + 2 * s.rolling(20).std().iloc[-1]), 4)


def test_beta_of_a_scaled_copy_is_its_scale():
    bench = closes(90)
    asset = [bench[0]]
    for a, b in zip(bench, bench[1:]):
        asset.append(asset[-1] * (1 + 2 * (b / a - 1)))
    assert analytics.technical_factors(asset, benchmark_closes=bench)["factors"]["beta"] == 2.0


def test_technical_inputs_are_checked():
    for args in [(closes(10),), (closes(30) + [float("nan")],), (closes(30) + [-1.0],), (closes(30), [1.0]),
                 (closes(30), None, closes(29))]:
        assert server.invoke(analytics.technical_factors, *args)["error_code"] == "invalid_input", args
    assert analytics.technical_factors(closes(30))["factors"]["trend_score"] is None


def test_weights():
    prices = {"A": closes(60), "B": closes(60, 50, 0.1), "C": [100 + 3 * math.sin(i) for i in range(60)]}
    eq = analytics.optimize_weights(prices, "equal_weight")
    assert eq["weights"] == {"A": 0.3333, "B": 0.3333, "C": 0.3333}
    mv = analytics.optimize_weights(prices, "min_var")
    assert abs(sum(mv["weights"].values()) - 1) < 1e-3 and mv["volatility"] <= eq["volatility"]
    ms = analytics.optimize_weights(prices, "max_sharpe")
    assert all(w >= 0 for w in ms["weights"].values()) and ms["sharpe"] >= eq["sharpe"]
    assert analytics.optimize_weights(prices, "max_sharpe") == ms


def test_weight_inputs_are_checked():
    good = {"A": closes(40), "B": closes(40, 60)}
    for args in [({"A": closes(40)},), ({"A": closes(40), "B": closes(39)},), (good, "kelly"), (good, "min_var", 2.0),
                 ({"../x": closes(40), "B": closes(40)},), ({"A": closes(40), "B": [5.0] * 40}, "min_var")]:
        assert server.invoke(analytics.optimize_weights, *args)["error_code"] == "invalid_input", args


CURRENT = {"net_income": 120, "total_assets": 1000, "operating_cash_flow": 150, "long_term_debt": 200,
           "current_assets": 400, "current_liabilities": 200, "shares_outstanding": 100, "revenue": 900,
           "gross_profit": 400, "retained_earnings": 300, "ebit": 160, "total_equity": 500, "total_liabilities": 500}
PRIOR = {"net_income": 80, "total_assets": 950, "long_term_debt": 250, "current_assets": 350,
         "current_liabilities": 220, "shares_outstanding": 105, "revenue": 800, "gross_profit": 330}


def test_quality_scores_complete():
    result = analytics.quality_scores(CURRENT, PRIOR)
    assert result["piotroski"]["f_score"] == 9 and result["piotroski"]["verdict"] == "high"
    z = 6.56 * 0.2 + 3.26 * 0.3 + 6.72 * 0.16 + 1.05 * 1.0
    assert result["altman"]["z_score"] == round(z, 3) and result["altman"]["zone"] == "safe"


def test_missing_inputs_are_unknown_not_zero():
    current = {k: v for k, v in CURRENT.items() if k not in ("retained_earnings", "operating_cash_flow")}
    result = analytics.quality_scores(current)
    p = result["piotroski"]
    assert p["signals"]["F2_cfo_positive"] is None and p["signals"]["F3_roa_increasing"] is None
    assert p["signals_evaluated"] == 1 and p["verdict"] is None
    assert result["altman"]["z_score"] is None and result["altman"]["missing"] == ["retained_earnings"]


def test_quality_inputs_are_checked():
    for args in [({},), ({"net_income": "1"},), ({"net_income": True},), ({"mystery": 1},), (CURRENT, [1])]:
        assert server.invoke(analytics.quality_scores, *args)["error_code"] == "invalid_input", args
