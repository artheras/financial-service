import json
import sys
import types

import pytest

from arthera_finance import api, derivatives, server


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def read(self, limit=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def network(monkeypatch):
    monkeypatch.setenv("FINANCIAL_SERVICE_NETWORK", "1")


def test_network_gate():
    for fn in (api.options_chain, api.fear_greed, api.funding_rates):
        args = ("AAPL",) if fn is api.options_chain else ()
        assert server.invoke(fn, *args)["error_code"] == "network_disabled"


def test_input_checks(network):
    for fn, args in [(api.options_chain, ("../etc",)), (api.options_chain, ("AAPL", "2024-13-01")),
                     (api.options_chain, ("AAPL", "", "straddle")), (api.options_chain, ("AAPL", "", "both", 0)),
                     (api.fear_greed, (0,)), (api.funding_rates, (["BTC"],)),
                     (api.funding_rates, (["BTC/USDT"], ["kraken"])), (api.funding_rates, ([f"A{i}/USDT" for i in range(11)],))]:
        assert server.invoke(fn, *args)["error_code"] == "invalid_input", (fn.__name__, args)


def test_fear_greed(network, monkeypatch):
    body = {"data": [{"value": "72", "value_classification": "Greed", "timestamp": "1730505600"},
                     {"value": "bad", "timestamp": "1730419200"}]}
    seen = []

    def urlopen(request, timeout):
        seen.append(request.full_url)
        return FakeResponse(json.dumps(body).encode())

    monkeypatch.setattr(derivatives.urllib.request, "urlopen", urlopen)
    result = server.invoke(api.fear_greed, 30)
    assert result["latest"] == {"date": "2024-11-02", "value": 72, "label": "Greed"}
    assert len(result["history"]) == 1 and "limit=30" in seen[0]


def test_funding_rates(network, monkeypatch):
    class Exchange:
        def __init__(self, config):
            assert config["timeout"] == 8000

        def fetch_funding_rate(self, symbol):
            if self.name == "okx":
                raise RuntimeError("http://private-url 451")
            rate = {"binance": 0.0001, "bybit": 0.0003}[self.name]
            return {"fundingRate": rate, "interval": "8h", "fundingDatetime": "2026-11-02T08:00:00.000Z"}

    fake = types.ModuleType("ccxt")
    for name in ("binance", "okx", "bybit"):
        setattr(fake, name, type(name, (Exchange,), {"name": name}))
    monkeypatch.setitem(sys.modules, "ccxt", fake)
    result = server.invoke(api.funding_rates, ["btc/usdt"])
    rates = {r["exchange"]: r for r in result["rates"]}
    assert rates["binance"]["symbol"] == "BTC/USDT:USDT" and rates["binance"]["rate_pct"] == 0.01
    assert rates["bybit"]["annualized_pct"] == round(0.03 * 3 * 365, 2)
    assert result["cross_exchange_spread_pct"] == {"BTC/USDT:USDT": 0.02}
    assert result["failures"] == [{"exchange": "okx", "symbol": "BTC/USDT", "error_code": "source_unavailable"}]
    assert "private-url" not in json.dumps(result)


def test_funding_rates_need_ccxt(network, monkeypatch):
    monkeypatch.setitem(sys.modules, "ccxt", None)
    assert server.invoke(api.funding_rates)["error_code"] == "dependency_missing"


def test_options_chain(network, monkeypatch):
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame({"strike": [90.0, 100.0, 110.0, 120.0], "lastPrice": [11, 3, 0.5, float("nan")],
                          "bid": [10.8, 2.9, 0.4, 0.0], "ask": [11.2, 3.1, 0.6, 0.05], "volume": [5, 50, 20, 0],
                          "openInterest": [100, 900, 300, 10], "impliedVolatility": [0.31, 0.25, 0.27, 0.4],
                          "inTheMoney": [True, True, False, False]})

    class Ticker:
        options = ("2026-11-20", "2026-12-18")

        def __init__(self, symbol):
            assert symbol == "AAPL"

        def option_chain(self, expiry):
            return types.SimpleNamespace(calls=frame, puts=frame, underlying={"regularMarketPrice": 104.0})

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=Ticker))
    result = server.invoke(api.options_chain, "aapl", "", "calls", 2)
    assert result["expiry"] == "2026-11-20" and "puts" not in result
    assert [r["strike"] for r in result["calls"]] == [100.0, 110.0]
    assert result["calls"][0]["iv_pct"] == 25.0 and result["calls"][0]["in_the_money"] is True
    assert server.invoke(api.options_chain, "AAPL", "2027-01-15")["error_code"] == "unknown_expiry"
