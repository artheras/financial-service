import json
from pathlib import Path
from arthera_finance.server import mcp


async def test_published_tool_contract_matches_live_server():
    contract = json.loads((Path(__file__).resolve().parents[1] / "contracts/tools.json").read_text())
    tools = [tool.model_dump(mode="json", exclude_none=True) for tool in await mcp.list_tools()]
    assert tools == contract["tools"]


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
