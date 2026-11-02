import sys
import types

import pytest

from arthera_finance import api, history, server


@pytest.fixture
def network(monkeypatch):
    monkeypatch.setenv("FINANCIAL_SERVICE_NETWORK", "1")


def test_network_gate():
    assert server.invoke(api.ashare_history, "600519")["error_code"] == "network_disabled"
    assert server.invoke(api.crypto_ohlcv)["error_code"] == "network_disabled"


@pytest.mark.parametrize("symbol,code", [("600519", "600519"), ("sh600519", "600519"), ("600519.SS", "600519"),
                                         ("000001.sz", "000001"), ("BJ430047", "430047")])
def test_ashare_codes(symbol, code):
    assert history.ashare_code(symbol) == code


def test_input_checks(network):
    for fn, args in [(api.ashare_history, ("AAPL",)), (api.ashare_history, ("../600519",)),
                     (api.ashare_history, ("600519", 0)), (api.ashare_history, ("600519", 30, "split")),
                     (api.crypto_ohlcv, ("BTC",)), (api.crypto_ohlcv, ("BTC/USDT", "kraken")),
                     (api.crypto_ohlcv, ("BTC/USDT", "okx", "3d")), (api.crypto_ohlcv, ("BTC/USDT", "okx", "1d", 1))]:
        assert server.invoke(fn, *args)["error_code"] == "invalid_input", (fn.__name__, args)


def test_ashare_history(network, monkeypatch):
    pd = pytest.importorskip("pandas")
    seen = {}

    def hist(symbol, period, start_date, end_date, adjust):
        seen.update(symbol=symbol, adjust=adjust, start=start_date)
        return pd.DataFrame({"日期": ["2026-10-29", "2026-10-30", "2026-10-31"], "开盘": [10.0, 10.2, 0],
                             "最高": [10.5, 10.6, 1], "最低": [9.9, 10.1, 1], "收盘": [10.2, 10.4, 1],
                             "成交量": [1000, 1200, 5], "成交额": [1e6, 1.2e6, 5], "换手率": [0.5, 0.6, 0.1]})

    monkeypatch.setitem(sys.modules, "akshare", types.SimpleNamespace(stock_zh_a_hist=hist))
    result = server.invoke(api.ashare_history, "600519.SS", 30, "none")
    assert seen["symbol"] == "600519" and seen["adjust"] == "" and len(seen["start"]) == 8
    assert result["data"][1] == {"date": "2026-10-30", "open": 10.2, "high": 10.6, "low": 10.1, "close": 10.4,
                                 "volume": 1200.0}
    assert result["dropped_rows"] == 1 and result["as_of"] == "2026-10-30"
    assert result["extra"][0] == {"amount_cny": 1e6, "turnover_rate_pct": 0.5}


def test_crypto_ohlcv(network, monkeypatch):
    calls = {}

    class Exchange:
        def __init__(self, config):
            assert config["timeout"] == 8000

        def fetch_ohlcv(self, symbol, timeframe, limit):
            calls.update(symbol=symbol, timeframe=timeframe, limit=limit)
            if self.name == "okx":
                raise RuntimeError("https://private 451")
            return [[1730419200000, 70000, 71000, 69000, 70500, 12.5], [1730505600000, 70500, 70000, 71000, 70800, 3],
                    [1730592000000, 70800, 72000, 70600, 71900, 9.0]]

    fake = types.ModuleType("ccxt")
    for name in ("binance", "okx", "bybit"):
        setattr(fake, name, type(name, (Exchange,), {"name": name}))
    monkeypatch.setitem(sys.modules, "ccxt", fake)
    result = server.invoke(api.crypto_ohlcv, "btc-usdt", "binance", "1d", 3)
    assert calls == {"symbol": "BTC/USDT", "timeframe": "1d", "limit": 3}
    assert [r["date"] for r in result["data"]] == ["2024-11-01", "2024-11-03"] and result["dropped_rows"] == 1
    failed = server.invoke(api.crypto_ohlcv, "BTC/USDT", "okx")
    assert failed["error_code"] == "source_unavailable" and "private" not in failed["error"]
    hourly = server.invoke(api.crypto_ohlcv, "BTC/USDT", "bybit", "1h", 3)
    assert hourly["data"][0]["date"] == "2024-11-01T00:00:00Z"


def test_rows_feed_the_backtest(network, monkeypatch):
    class Exchange:
        def __init__(self, config):
            pass

        def fetch_ohlcv(self, symbol, timeframe, limit):
            return [[1730419200000 + i * 86400000, 100 + i, 101 + i, 99 + i, 100.5 + i, 10] for i in range(40)]

    fake = types.ModuleType("ccxt")
    fake.binance = Exchange
    monkeypatch.setitem(sys.modules, "ccxt", fake)
    rows = server.invoke(api.crypto_ohlcv, "BTC/USDT", "binance", "1d", 40)["data"]
    result = server.invoke(api.backtest, {"BTC/USDT": rows})
    assert result["success"], result
