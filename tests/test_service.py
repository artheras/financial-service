import json
import os
from pathlib import Path
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from arthera_finance import api


def bars():
    return {"TEST": [{"date": f"2026-01-0{i}", "open": price, "high": price,
                       "low": price, "close": price} for i, price in enumerate([10, 10, 12], 1)]}


def test_migrated_engine_preserves_hand_computed_next_open_result():
    result = api.backtest(bars(), starting_cash=1000, commission=0, slippage=0)
    assert result["simulation"] is True
    assert result["result"]["metrics"]["total_return"] == pytest.approx(0.2)
    assert result["result"]["trades"][0]["date"] == "2026-01-02"
    assert len(result["input_sha256"]) == 64


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), -1, "10"])
def test_invalid_equity_never_becomes_a_metric(bad):
    with pytest.raises(ValueError):
        api.risk_metrics([100, bad])


def test_dates_prices_and_fees_validated():
    values = bars()
    values["TEST"][1]["date"] = values["TEST"][0]["date"]
    with pytest.raises(ValueError):
        api.backtest(values)
    with pytest.raises(ValueError):
        api.backtest(bars(), commission=-1)


def test_network_setting_is_owned_by_host():
    result = api.analyze_statements("TEST", {"network_enabled": True})
    assert not result["success"]
    assert result["error_code"] == "network_disabled"
    assert api.market_history("AAPL")["error_code"] == "network_disabled"


async def test_real_standard_mcp_stdio_session():
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, FINANCIAL_SERVICE_NETWORK="0",
               PYTHONPATH=os.pathsep.join([str(root / "src"), os.environ.get("PYTHONPATH", "")]))
    params = StdioServerParameters(command=sys.executable, args=["-m", "arthera_finance.server"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            names = {tool.name for tool in tools}
            assert names == {"financial_service_info", "financial_analyze_statements",
                             "financial_parse_sec_facts", "financial_risk_metrics",
                             "financial_backtest", "financial_market_history"}
            assert all(tool.annotations.readOnlyHint for tool in tools)
            result = await session.call_tool("financial_analyze_statements", {
                "financials": {"income_statement": {"revenue": 1000, "net_income": 100}}})
            data = json.loads(result.content[0].text)
            assert data["success"] and data["data"]["net_margin_pct"] == 10
            result = await session.call_tool("financial_backtest", {
                "data": bars(), "starting_cash": 1000, "commission": 0, "slippage": 0})
            assert json.loads(result.content[0].text)["result"]["metrics"]["total_return"] == pytest.approx(0.2)
            result = await session.call_tool("financial_market_history", {"symbol": "AAPL"})
            assert json.loads(result.content[0].text)["error_code"] == "network_disabled"
            result = await session.call_tool("financial_risk_metrics", {"equity": [100, True]})
            assert result.isError
