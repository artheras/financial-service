import sys
import types

import pytest

from arthera_finance import api, markets, server

pd = pytest.importorskip("pandas")


@pytest.fixture
def network(monkeypatch):
    monkeypatch.setenv("FINANCIAL_SERVICE_NETWORK", "1")
    monkeypatch.setattr(markets.time, "sleep", lambda s: None)


def fake_ak(monkeypatch, **functions):
    monkeypatch.setattr(markets, "_akshare", lambda: types.SimpleNamespace(**functions))


def test_network_gate():
    for fn in (api.sector_performance, api.northbound_flow, api.limit_up_pool, api.screen_ashare, api.market_indices):
        assert server.invoke(fn)["error_code"] == "network_disabled"


def test_input_checks(network):
    for fn, args in [(api.sector_performance, ("jp",)), (api.sector_performance, ("cn", 0)),
                     (api.limit_up_pool, ("2026-02-30",)), (api.limit_up_pool, ("", 500)),
                     (api.screen_ashare, (0,)), (api.screen_ashare, (50.0, -1.0)),
                     (api.screen_ashare, (50.0, 0.0, "roe")), (api.screen_ashare, (50.0, 0.0, "pe", 0))]:
        assert server.invoke(fn, *args)["error_code"] == "invalid_input", (fn.__name__, args)


def test_missing_akshare(network, monkeypatch):
    monkeypatch.setattr(markets, "_akshare", lambda: None)
    for fn in (api.northbound_flow, api.limit_up_pool, api.screen_ashare):
        assert server.invoke(fn)["error_code"] == "dependency_missing"


def test_cn_sectors_with_one_retry(network, monkeypatch):
    calls = []

    def boards():
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("host 17.push2 down")
        return pd.DataFrame({"板块名称": ["半导体", "银行", "煤炭"], "涨跌幅": [3.2, float("nan"), -1.5],
                             "上涨家数": [80, 20, 3], "下跌家数": [5, 10, 30]})

    fake_ak(monkeypatch, stock_board_industry_name_em=boards)
    result = server.invoke(api.sector_performance, "cn", 1)
    assert len(calls) == 2 and result["sectors"] == 2
    assert result["top"][0]["sector"] == "半导体" and result["bottom"][0]["sector"] == "煤炭"


def test_northbound(network, monkeypatch):
    frame = pd.DataFrame({"交易日": ["2026-10-30"] * 3, "资金方向": ["北向", "北向", "南向"],
                          "板块": ["沪股通", "深股通", "港股通(沪)"], "成交净买额": [12.5, -3.25, 40.0]})
    fake_ak(monkeypatch, stock_hsgt_fund_flow_summary_em=lambda: frame)
    result = server.invoke(api.northbound_flow)
    assert result["total_net_buy_100m_cny"] == 9.25 and result["date"] == "2026-10-30"
    frame["成交净买额"] = 0.0
    assert server.invoke(api.northbound_flow)["error_code"] == "not_disclosed"


def test_limit_up_pool(network, monkeypatch):
    seen = {}

    def pool(date):
        seen["date"] = date
        return pd.DataFrame({"代码": ["600000", "000001"], "名称": ["甲", "乙"], "涨跌幅": [10.0, 9.98],
                             "连板数": [3, 1], "首次封板时间": ["093000", "101500"], "所属行业": ["银行", "银行"]})

    fake_ak(monkeypatch, stock_zt_pool_em=pool)
    result = server.invoke(api.limit_up_pool, "2026-10-30", 1)
    assert seen["date"] == "20261030" and result["count"] == 2 and len(result["stocks"]) == 1
    assert result["stocks"][0]["consecutive_limits"] == 3


def test_screen(network, monkeypatch):
    frame = pd.DataFrame({"代码": ["1", "2", "3", "4", "5"], "名称": ["好", "ST坏", "亏", "大", "贵"],
                          "最新价": [10, 5, 8, 20, 30], "涨跌幅": [2.0, 9.0, 5.0, 1.0, 4.0],
                          "市盈率-动态": [12, 8, -5, 20, 80], "总市值": [5e9, 5e9, 5e9, 9e10, 5e9],
                          "市净率": [1.2, 1, 1, 2, 9], "换手率": [3, 1, 1, 0.5, 2]})
    fake_ak(monkeypatch, stock_zh_a_spot_em=lambda: frame)
    result = server.invoke(api.screen_ashare, 50.0, 10.0)
    assert [s["name"] for s in result["stocks"]] == ["好", "大"] and result["matched"] == 2
    assert result["stocks"][1]["market_cap_100m_cny"] == 900.0
    assert [s["name"] for s in server.invoke(api.screen_ashare, 50.0, 0.0, "pe")["stocks"]] == ["好", "大"]


def test_indices_and_us_sectors(network, monkeypatch):
    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, period):
            if self.symbol in ("^VIX", "XLE"):
                raise RuntimeError("down")
            return pd.DataFrame({"Close": [100.0, 102.0]}, index=pd.to_datetime(["2026-10-29", "2026-10-30"]))

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=Ticker))
    result = server.invoke(api.market_indices)
    assert result["indices"][0] == {"index": "S&P 500", "ticker": "^GSPC", "price": 102.0, "change_pct": 2.0,
                                    "date": "2026-10-30"}
    assert result["failures"] == [{"index": "VIX", "error_code": "source_unavailable"}]
    sectors = server.invoke(api.sector_performance, "us")
    assert sectors["sectors"] == 10 and all(r["etf"] != "XLE" for r in sectors["top"])
