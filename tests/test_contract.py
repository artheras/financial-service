import json
from pathlib import Path
from arthera_finance.server import mcp


async def test_published_tool_contract_matches_live_server():
    contract = json.loads((Path(__file__).resolve().parents[1] / "contracts/tools.json").read_text())
    tools = [tool.model_dump(mode="json", exclude_none=True) for tool in await mcp.list_tools()]
    assert tools == contract["tools"]
