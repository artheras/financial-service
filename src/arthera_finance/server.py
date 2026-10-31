"""Local stdio MCP endpoint. No account mutations, file tools or order execution."""
import logging
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import StrictFloat, StrictInt
from . import __version__, api
from .sec_statements import parse_annual_statements, SECError
from .brokers import service as broker_service
from .brokers.worker import call as broker_query

mcp = FastMCP("Arthera Financial Service")
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
REMOTE_READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)


def raised_by_service(exc):
    """Only this package's validation messages are written for clients; library text may hold private data."""
    tb = exc.__traceback__
    while tb is not None and tb.tb_next is not None:
        tb = tb.tb_next
    return tb is not None and tb.tb_frame.f_globals.get("__name__", "").startswith("arthera_finance.")


def invoke(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except SECError as exc:
        return {"success": False, "error_code": exc.code, "error": str(exc)}
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        if raised_by_service(exc):
            return {"success": False, "error_code": "invalid_input", "error": str(exc)[:300]}
        logging.getLogger(__name__).warning("Financial tool rejected input: %s", type(exc).__name__)
        return {"success": False, "error_code": "invalid_input", "error": "The supplied input could not be processed"}
    except TimeoutError:
        return {"success": False, "error_code": "source_timeout", "error": "Source deadline exceeded"}
    except Exception:
        logging.getLogger(__name__).warning("Financial tool failed; no input values logged")
        return {"success": False, "error_code": "source_unavailable", "error": "The requested operation could not obtain a usable result"}


@mcp.tool(annotations=READ)
def financial_service_info() -> dict:
    """Describe this service version and its host-selected network capability."""
    return {"success": True, "version": __version__, "network_enabled": api.network_enabled(),
            "transport": "stdio", "trading_enabled": False,
            "broker_access_enabled": broker_service.enabled()}


@mcp.tool(annotations=REMOTE_READ)
def financial_analyze_statements(company_name: str = "", financials: dict | None = None) -> dict:
    """Compute margins and Dupont metrics from supplied statements, or host-enabled Yahoo/SEC data. Missing values stay unknown."""
    return invoke(api.analyze_statements, company_name, financials)


@mcp.tool(annotations=READ)
def financial_parse_sec_facts(facts: dict) -> dict:
    """Parse supplied SEC companyfacts, keeping one aligned annual filing and its provenance. Does not access the network."""
    def parse():
        api.bounded_json(facts)
        return {"success": True, **parse_annual_statements(facts)}
    return invoke(parse)


@mcp.tool(annotations=READ)
def financial_risk_metrics(equity: list[StrictFloat], periods_per_year: StrictFloat = 252) -> dict:
    """Calculate drawdown, total return and sample Sharpe on ordered equity values with an explicit annual-frequency assumption."""
    return invoke(api.risk_metrics, equity, periods_per_year)


@mcp.tool(annotations=READ)
def financial_backtest(data: dict, strategy: str = "buy_hold", starting_cash: StrictFloat = 100000,
                       commission: StrictFloat = 0.0005, slippage: StrictFloat = 0.0005,
                       fast: StrictInt = 10, slow: StrictInt = 30) -> dict:
    """Simulate supplied daily OHLC bars with buy_hold or sma_cross; orders fill at the next open. Does not place trades."""
    return invoke(api.backtest, data, strategy, starting_cash, commission, slippage, fast, slow)


@mcp.tool(annotations=REMOTE_READ)
def financial_market_history(symbol: str, days: StrictInt = 90) -> dict:
    """Fetch unadjusted daily Yahoo history only when the host enabled networking. Reports source and dates; not a live quote."""
    return invoke(api.market_history, symbol, days)


@mcp.tool(annotations=READ)
def financial_broker_connections() -> dict:
    """List only this local host's configured connection identifiers; never returns credentials."""
    return broker_service.connections()


@mcp.tool(annotations=REMOTE_READ)
def financial_broker_account(connection_id: str) -> dict:
    """Read the selected host-owned broker account, masking the account identifier. Never places orders."""
    return broker_query(connection_id, "account")


@mcp.tool(annotations=REMOTE_READ)
def financial_broker_positions(connection_id: str, limit: StrictInt = 50) -> dict:
    """Read positions from a configured account in this user's service process."""
    return broker_query(connection_id, "positions", limit=limit)


@mcp.tool(annotations=REMOTE_READ)
def financial_broker_orders(connection_id: str, status: str = "all", limit: StrictInt = 50) -> dict:
    """Read existing broker orders; cannot place, modify, cancel or confirm an order."""
    return broker_query(connection_id, "orders", status, limit)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
