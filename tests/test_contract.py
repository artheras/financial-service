import json
from pathlib import Path
from arthera_finance.server import mcp


def normalized_tools(tools):
    # JSON Schema permits unspecified object properties by default. Pydantic
    # 2.12 emits that default explicitly for dict inputs; 2.10 omits it.
    def schema(value):
        if isinstance(value, dict):
            result = {key: schema(item) for key, item in value.items()}
            if result.get("type") == "object":
                result.setdefault("additionalProperties", True)
            return result
        if isinstance(value, list):
            return [schema(item) for item in value]
        return value
    return [dict(tool, inputSchema=schema(tool["inputSchema"])) for tool in tools]


async def test_published_tool_contract_matches_live_server():
    contract = json.loads((Path(__file__).resolve().parents[1] / "contracts/tools.json").read_text())
    tools = [tool.model_dump(mode="json", exclude_none=True) for tool in await mcp.list_tools()]
    assert normalized_tools(tools) == normalized_tools(contract["tools"])


def test_library_error_text_is_not_returned_to_clients():
    from arthera_finance import server
    def library_failure():
        json.loads("{private-path-or-token")
    result = server.invoke(library_failure)
    assert result["error_code"] == "invalid_input"
    assert "private" not in result["error"]
    def own_failure():
        from arthera_finance import api
        api.risk_metrics([1.0], 252)
    assert "equity" in server.invoke(own_failure)["error"]


async def test_published_trading_contract_matches_live_server():
    from arthera_finance.trading_server import mcp as trading
    contract = json.loads((Path(__file__).resolve().parents[1] / "contracts/trading_tools.json").read_text())
    tools = [tool.model_dump(mode="json", exclude_none=True) for tool in await trading.list_tools()]
    assert normalized_tools(tools) == normalized_tools(contract["tools"])
