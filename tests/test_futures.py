import sys
import types

import pytest

from arthera_finance import api, futures, server

pd = pytest.importorskip("pandas")


@pytest.fixture
def network(monkeypatch):
    monkeypatch.setenv("FINANCIAL_SERVICE_NETWORK", "1")


def fake_yf(monkeypatch, frames):
    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, period):
            frame = frames.get(self.symbol)
            if isinstance(frame, Exception):
                raise frame
            return frame if frame is not None else pd.DataFrame({"Close": []})

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=Ticker))


def frame(closes, highs=None, lows=None):
    idx = pd.to_datetime([f"2026-10-{i + 1:02d}" for i in range(len(closes))])
    return pd.DataFrame({"Close": closes, "High": highs or closes, "Low": lows or closes}, index=idx)


def test_resolve():
    assert futures.resolve("Gold") == "GC=F" and futures.resolve(" crude  oil ") == "CL=F"
    assert futures.resolve("es=f") == "ES=F" and futures.resolve("黄金") == "GC=F"
    for bad in ("螺纹钢", "AAPL", "../x", ""):
        with pytest.raises(ValueError):
            futures.resolve(bad)


def test_gate_and_bad_name(network, monkeypatch):
    assert server.invoke(api.futures_snapshot, "螺纹钢")["error_code"] == "invalid_input"
    monkeypatch.setenv("FINANCIAL_SERVICE_NETWORK", "0")
    assert server.invoke(api.treasury_yields)["error_code"] == "network_disabled"


def test_snapshot(network, monkeypatch):
    fake_yf(monkeypatch, {"GC=F": frame([2000.0, float("nan"), 2100.0, 2050.0], highs=[2010, 1, 2150, 2060],
                                        lows=[1990, 1, 2080, 2040])})
    result = server.invoke(api.futures_snapshot, "gold")
    assert result["ticker"] == "GC=F" and result["last"] == 2050.0 and result["as_of"] == "2026-10-04"
    assert result["change_pct"] == round((2050 / 2100 - 1) * 100, 3)
    assert result["high_1y"] == 2150.0 and result["return_1y_pct"] == 2.5


def test_yields(network, monkeypatch):
    fake_yf(monkeypatch, {"^IRX": frame([4.3, 4.31]), "^FVX": RuntimeError("down"), "^TNX": frame([4.1, 4.25]),
                          "^TYX": frame([4.5])})
    result = server.invoke(api.treasury_yields)
    tenors = {r["tenor"]: r for r in result["yields"]}
    assert tenors["13w"]["label"] == "13-week Treasury bill" and tenors["10y"]["yield_pct"] == 4.25
    assert result["spread_10y_13w_pct"] == -0.06
    assert result["failures"] == [{"tenor": "5y", "error_code": "source_unavailable"}]
