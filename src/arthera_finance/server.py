"""Local stdio MCP endpoint. No account mutations, file tools or order execution."""
import logging
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import StrictFloat, StrictInt
from . import __version__, analytics, api
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


@mcp.tool(annotations=READ)
def financial_technical_factors(closes: list[StrictFloat], volumes: list[StrictFloat] | None = None,
                                benchmark_closes: list[StrictFloat] | None = None) -> dict:
    """Momentum, moving averages and gaps, volatility, RSI(14), MACD, Bollinger position, volume ratio and beta for caller-supplied daily closes (oldest first; e.g. from financial_market_history). No network."""
    return invoke(analytics.technical_factors, closes, volumes, benchmark_closes)


@mcp.tool(annotations=READ)
def financial_optimize_weights(prices: dict[str, list[StrictFloat]], method: str = "max_sharpe",
                               risk_free_rate: StrictFloat = 0.04, periods_per_year: StrictInt = 252) -> dict:
    """Portfolio weights (max_sharpe, min_var or equal_weight) from caller-supplied, date-aligned closes for 2..20 symbols, with expected return, volatility and Sharpe. A historical estimate, not advice. No network."""
    return invoke(analytics.optimize_weights, prices, method, risk_free_rate, periods_per_year)


@mcp.tool(annotations=READ)
def financial_quality_scores(current: dict, prior: dict | None = None) -> dict:
    """Piotroski F-score and Altman Z''-score from caller-supplied statement fields for the current and prior fiscal year. Missing inputs leave signals null; no verdict is given unless all are evaluated. No network."""
    return invoke(analytics.quality_scores, current, prior)


@mcp.tool(annotations=REMOTE_READ)
def financial_market_history(symbol: str, days: StrictInt = 90) -> dict:
    """Fetch unadjusted daily Yahoo history only when the host enabled networking. Reports source and dates; not a live quote."""
    return invoke(api.market_history, symbol, days)


@mcp.tool(annotations=REMOTE_READ)
def financial_quote(symbol: str) -> dict:
    """Latest quote from the first configured source that answers (yfinance, Finnhub, Alpha Vantage, Tushare, AkShare), when the host enabled networking. Names its source; may be delayed."""
    return invoke(api.market_quote, symbol)


@mcp.tool(annotations=REMOTE_READ)
def financial_fundamentals(symbol: str) -> dict:
    """Valuation and growth metrics (PE, PB, ROE, growth, dividend yield, market value) from the first configured source that has them, when the host enabled networking."""
    return invoke(api.market_fundamentals, symbol)


@mcp.tool(annotations=REMOTE_READ)
def financial_macro_series(series: str, source: str = "fred", country: str = "US", years: StrictInt = 10) -> dict:
    """Macroeconomic time series from FRED (US10Y, CPI, UNRATE, FEDFUNDS, M2 or any series id) or the World Bank (GDP, CPI and indicator codes, by country), when the host enabled networking."""
    return invoke(api.macro_series, series, source, country, years)


@mcp.tool(annotations=REMOTE_READ)
def financial_us_macro(indicator: str = "all", periods: StrictInt = 12) -> dict:
    """US macro snapshot from FRED: GDP growth, CPI and core CPI year on year, fed funds, unemployment, 2y and 10y yields with the curve shape, M2, PPI and more; latest, previous and recent observations, when the host enabled networking."""
    return invoke(api.us_macro, indicator, periods)


@mcp.tool(annotations=REMOTE_READ)
def financial_cn_macro(indicator: str = "all", periods: StrictInt = 12) -> dict:
    """China macro snapshot via akshare: CPI, PPI, manufacturing and services PMI, GDP growth, LPR and money supply, when the host enabled networking and installed the [cn] extra."""
    return invoke(api.cn_macro, indicator, periods)


@mcp.tool(annotations=REMOTE_READ)
def financial_central_bank_rates() -> dict:
    """Policy rates for the Fed, ECB, UK, Japan and the PBOC LPR, each with its date and a stale flag, when the host enabled networking."""
    return invoke(api.central_bank_rates)


@mcp.tool(annotations=REMOTE_READ)
def financial_options_chain(symbol: str, expiry: str = "", side: str = "both", limit: StrictInt = 15) -> dict:
    """Listed option expiries and the chain (strike, last, bid, ask, volume, open interest, implied volatility) for one expiry, nearest the money, from yfinance, when the host enabled networking."""
    return invoke(api.options_chain, symbol, expiry, side, limit)


@mcp.tool(annotations=REMOTE_READ)
def financial_crypto_fear_greed(days: StrictInt = 7) -> dict:
    """The crypto Fear & Greed index (alternative.me) for the last N days, when the host enabled networking."""
    return invoke(api.fear_greed, days)


@mcp.tool(annotations=REMOTE_READ)
def financial_funding_rates(symbols: list[str] | None = None, exchanges: list[str] | None = None) -> dict:
    """Current perpetual funding rates from Binance, OKX and Bybit public endpoints (ccxt, [crypto] extra) with cross-exchange spreads, when the host enabled networking. Read-only; no keys."""
    return invoke(api.funding_rates, symbols, exchanges)


@mcp.tool(annotations=REMOTE_READ)
def financial_sector_performance(market: str = "cn", top: StrictInt = 5) -> dict:
    """Best and worst sectors on the latest trading day: China industry boards (akshare) or US SPDR sector ETFs (yfinance), when the host enabled networking."""
    return invoke(api.sector_performance, market, top)


@mcp.tool(annotations=REMOTE_READ)
def financial_northbound_flow() -> dict:
    """Stock Connect northbound net buying into Shanghai and Shenzhen (100 million CNY) for the latest published day, via akshare, when the host enabled networking."""
    return invoke(api.northbound_flow)


@mcp.tool(annotations=REMOTE_READ)
def financial_limit_up_pool(day: str = "", limit: StrictInt = 50) -> dict:
    """A-shares that closed at the daily limit-up on a date (default today), with consecutive limit count and first lock time, via akshare, when the host enabled networking."""
    return invoke(api.limit_up_pool, day, limit)


@mcp.tool(annotations=REMOTE_READ)
def financial_screen_ashare(max_pe: StrictFloat = 50.0, min_market_cap_100m: StrictFloat = 0.0,
                            sort_by: str = "change_pct", limit: StrictInt = 20) -> dict:
    """Screen A-shares by dynamic PE and market cap (100 million CNY), excluding ST names, sorted by change, market cap, turnover or PE, via akshare, when the host enabled networking."""
    return invoke(api.screen_ashare, max_pe, min_market_cap_100m, sort_by, limit)


@mcp.tool(annotations=REMOTE_READ)
def financial_market_indices() -> dict:
    """Latest level and daily change for major US, China, Hong Kong, Japan, UK and German indices and the VIX, via yfinance, when the host enabled networking."""
    return invoke(api.market_indices)


@mcp.tool(annotations=READ)
def financial_datasource_status() -> dict:
    """Which market-data sources this host has keys and packages for. Makes no network request."""
    return invoke(api.datasource_status)


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
