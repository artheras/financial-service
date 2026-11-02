import math
import sys
import types

import pytest

from arthera_finance import api, peers, server, strategies


def bars(n, seed=1.0):
    closes = [100 + 10 * math.sin(i / 7 + seed) + i * 0.2 for i in range(n)]
    return [c + 1.5 for c in closes], [c - 1.5 for c in closes], closes


def test_ichimoku_matches_pandas():
    pd = pytest.importorskip("pandas")
    highs, lows, closes = bars(120)
    result = strategies.ichimoku(highs, lows, closes)["ichimoku"]
    h, l = pd.Series(highs), pd.Series(lows)
    mid = lambda n: (h.rolling(n).max() + l.rolling(n).min()) / 2
    assert result["tenkan_sen"] == round(mid(9).iloc[-1], 4)
    assert result["kijun_sen"] == round(mid(26).iloc[-1], 4)
    assert result["senkou_span_a"] == round(((mid(9) + mid(26)) / 2).shift(26).iloc[-1], 4)
    assert result["senkou_span_b"] == round(mid(52).shift(26).iloc[-1], 4)
    assert result["lagging_span_vs_price_26_bars_ago"] == ("above" if closes[-1] > closes[-27] else "below")


def test_ichimoku_inputs():
    highs, lows, closes = bars(80)
    for args in [(highs, lows, closes[:70]), (highs[:-1], lows, closes), (lows, highs, closes)]:
        assert server.invoke(strategies.ichimoku, *args)["error_code"] == "invalid_input"


def test_walk_forward_has_no_look_ahead_and_counts_costs():
    _, _, closes = bars(400)
    base = strategies.walk_forward(closes, 4)
    changed = closes[:300] + [c * 1.5 for c in closes[300:]]
    later = strategies.walk_forward(changed, 4)
    # Windows that end before bar 300 cannot see the change.
    assert base["windows"][:3] == later["windows"][:3] and base["windows"][3] != later["windows"][3]
    free = strategies.walk_forward(closes, 4, 0.7, 0.0)
    assert all(f["test_return"] >= b["test_return"] for f, b in zip(free["windows"], base["windows"]))
    for w in base["windows"]:
        assert 0 <= w["test_max_drawdown"] < 1 and w["fast"] < w["slow"]


def test_walk_forward_drawdown_uses_the_running_peak():
    total, _, drawdown = strategies._stats([0.10, -0.50, 0.40])
    assert round(drawdown, 6) == 0.5 and round(total, 6) == round(1.1 * 0.5 * 1.4 - 1, 6)


def test_walk_forward_inputs():
    _, _, closes = bars(250)
    for args in [(closes[:150],), (closes, 1), (closes, 21), (closes, 5, 0.95), (closes, 5, 0.7, 0.5), (closes, 12)]:
        assert server.invoke(strategies.walk_forward, *args)["error_code"] == "invalid_input", args[1:]


def test_peer_comparison(monkeypatch):
    monkeypatch.setenv("FINANCIAL_SERVICE_NETWORK", "1")
    infos = {"AAPL": {"regularMarketPrice": 200, "trailingPE": 30, "forwardPE": 27, "returnOnEquity": 1.5,
                      "marketCap": 3e12, "currency": "USD", "trailingAnnualDividendYield": 0.005},
             "MSFT": {"regularMarketPrice": 400, "forwardPE": 30, "returnOnEquity": 0.35, "marketCap": 3.1e12, "currency": "USD"},
             "GOOGL": {"regularMarketPrice": 170, "trailingPE": 20, "forwardPE": 19, "returnOnEquity": 0.3, "marketCap": 2e12,
                       "currency": "USD", "dividendYield": 0.5}}

    class Ticker:
        def __init__(self, symbol):
            if symbol == "BAD":
                raise RuntimeError("down")
            self.info = infos.get(symbol, {})

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=Ticker))
    result = server.invoke(api.peer_comparison, "aapl", ["MSFT", "googl", "BAD", "AAPL"])
    table = {r["symbol"]: r for r in result["table"]}
    assert table["MSFT"]["trailing_pe"] is None and table["MSFT"]["forward_pe"] == 30
    assert table["AAPL"]["dividend_yield_pct"] == 0.5 and table["GOOGL"]["dividend_yield_pct"] is None
    assert result["comparison"]["trailing_pe"] == {"target": 30, "peer_median": 20, "peers_with_value": 1, "vs_median_pct": 50.0}
    assert result["failures"] == [{"symbol": "BAD", "error_code": "source_unavailable"}]
    for args in [("AAPL", []), ("AAPL", ["AAPL"]), ("../x", ["MSFT"]), ("AAPL", [f"T{i}" for i in range(8)])]:
        assert server.invoke(api.peer_comparison, *args)["error_code"] == "invalid_input", args
