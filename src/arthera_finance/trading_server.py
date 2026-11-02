"""arthera-trading: the MCP server that can place (paper) orders.

Kept out of arthera-financial-service on purpose: that server is read-only by
construction, and clients rely on it. Here only trading_execute_order changes
an account, it is marked destructive so that a client asks a person before
every call, and it takes nothing but the id of a preview that person was
shown. See trading/service.py for the host-side gates.
"""
import logging

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import StrictFloat, StrictInt

from . import __version__
from .trading import service
from .trading.service import TradingError

mcp = FastMCP("Arthera Trading")
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
RECORD = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
EXECUTE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)


def invoke(fn, *args):
    try:
        return fn(*args)
    except TradingError as exc:
        return {"success": False, "error_code": exc.code, "error": str(exc)}
    except Exception:
        logging.getLogger(__name__).exception("trading tool failed")
        return {"success": False, "error_code": "internal_error", "error": "The trading service failed; details are in its log"}


@mcp.tool(annotations=READ)
def trading_accounts() -> dict:
    """Whether trading is enabled on this host, whether it is halted, and the paper accounts the host configured."""
    return invoke(service.list_accounts)


@mcp.tool(annotations=READ)
def trading_account_state(account_id: str) -> dict:
    """Cash, positions, today's order count and traded value, and the limits of one paper account."""
    return invoke(service.account_state, account_id)


@mcp.tool(annotations=RECORD)
def trading_preview_order(account_id: str, symbol: str, side: str, limit_price: StrictFloat,
                          quantity: StrictFloat | None = None, target_weight: StrictFloat | None = None,
                          reason: str = "") -> dict:
    """Plan a limit order (by quantity, or by target weight of the account) and check it against the account's limits. Records a preview that expires in 10 minutes; changes no account. Show it to the person before anything else."""
    return invoke(service.preview_order, account_id, symbol, side, quantity, target_weight, limit_price, reason)


@mcp.tool(annotations=EXECUTE)
def trading_execute_order(preview_id: str) -> dict:
    """Execute one pending, unexpired preview after re-checking it against the account as it is now. Only call this when the person has seen that preview and said to execute it."""
    return invoke(service.execute_order, preview_id)


@mcp.tool(annotations=RECORD)
def trading_cancel_preview(preview_id: str) -> dict:
    """Cancel a pending preview so it can no longer be executed."""
    return invoke(service.cancel_preview, preview_id)


@mcp.tool(annotations=READ)
def trading_audit(limit: StrictInt = 20) -> dict:
    """The most recent previews, executions, cancellations and refusals from the audit log."""
    return invoke(service.audit_log, limit)


def main():
    import sys
    if any(arg in ("--version", "-V") for arg in sys.argv[1:]):
        print(f"arthera-trading {__version__}")
        return
    mcp.run()


if __name__ == "__main__":
    main()
