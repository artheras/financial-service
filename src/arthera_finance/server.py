"""Local stdio or Streamable HTTP MCP endpoint. No account mutations, file tools or order execution."""
import argparse
import logging
import os
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import StrictFloat, StrictInt
from . import __version__, analytics, api, due_diligence, industry_kpis, strategies, valuation
from .sec_statements import parse_annual_statements, SECError
from .brokers import service as broker_service
from .brokers.worker import call as broker_query

mcp = FastMCP("Arthera Financial Service")
_ACTIVE_TRANSPORT = "stdio"
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
REMOTE_READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)


def _load_ic_memo_skill() -> str:
    skill_path = Path(__file__).resolve().parents[2] / "skills" / "ic-memo-due-diligence" / "SKILL.md"
    if skill_path.is_file():
        return skill_path.read_text(encoding="utf-8")
    return "Institutional Company Due Diligence & IC Memo SOP"


@mcp.prompt(
    name="ic-memo-due-diligence",
    description="Institutional company due diligence, red-flag screening, vertical industry KPI analysis, and deterministic DCF valuation SOP using arthera-finance MCP tools.",
)
def ic_memo_due_diligence_prompt() -> str:
    return _load_ic_memo_skill()


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
            "transport": _ACTIVE_TRANSPORT, "trading_enabled": False,
            "broker_access_enabled": broker_service.enabled()}


@mcp.tool(annotations=REMOTE_READ)
def financial_analyze_statements(company_name: str = "", financials: dict | None = None, as_of: str = "") -> dict:
    """Compute margins and Dupont metrics from supplied statements, or host-enabled Yahoo/SEC data (optionally point-in-time on or before ISO date as_of). Missing values stay unknown."""
    return invoke(api.analyze_statements, company_name, financials, as_of)


@mcp.tool(annotations=READ)
def financial_parse_sec_facts(facts: dict, as_of: str = "") -> dict:
    """Parse supplied SEC companyfacts, keeping one aligned annual filing filed on or before ISO date as_of (default today) and its provenance. Does not access the network."""
    def parse():
        cutoff = api.parse_as_of(as_of)
        api.bounded_json(facts)
        res = parse_annual_statements(facts, today=cutoff)
        if cutoff is not None:
            res["as_of"] = cutoff.isoformat()
        return {"success": True, **res}
    return invoke(parse)


@mcp.tool(annotations=READ)
def financial_risk_metrics(equity: list[StrictFloat], periods_per_year: StrictFloat = 252) -> dict:
    """Calculate drawdown, total return and sample Sharpe on ordered equity values with an explicit annual-frequency assumption."""
    return invoke(api.risk_metrics, equity, periods_per_year)


@mcp.tool(annotations=READ)
def financial_backtest(data: dict, strategy: str = "buy_hold", starting_cash: StrictFloat = 100000,
                       commission: StrictFloat = 0.0005, slippage: StrictFloat = 0.0005,
                       fast: StrictInt = 10, slow: StrictInt = 30, as_of: str = "") -> dict:
    """Simulate supplied daily OHLC bars with buy_hold or sma_cross (optionally truncated on or before ISO date as_of to prevent look-ahead bias); orders fill at the next open. Does not place trades."""
    return invoke(api.backtest, data, strategy, starting_cash, commission, slippage, fast, slow, as_of)


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


@mcp.tool(annotations=READ)
def financial_ichimoku(highs: list[StrictFloat], lows: list[StrictFloat], closes: list[StrictFloat]) -> dict:
    """Ichimoku components (tenkan, kijun, both cloud spans) and their states for caller-supplied daily bars, oldest first, at least 78. Not a trade signal. No network."""
    return invoke(strategies.ichimoku, highs, lows, closes)


@mcp.tool(annotations=READ)
def financial_walk_forward(closes: list[StrictFloat], windows: StrictInt = 5, train_ratio: StrictFloat = 0.7,
                           commission: StrictFloat = 0.0005) -> dict:
    """Walk-forward test of the moving-average crossover on caller-supplied closes: per window, choose fast/slow in sample, then measure out of sample against buy and hold, with costs and no look-ahead. No network."""
    return invoke(strategies.walk_forward, closes, windows, train_ratio, commission)


@mcp.tool(annotations=READ)
def financial_resolve_symbol(query: str) -> dict:
    """Resolve a Chinese or English company name, brand alias, bare 6-digit A-share code, or HK numeric code into a canonical ticker and market classification. No network."""
    return invoke(api.resolve_symbol, query)


@mcp.tool(annotations=READ)
def financial_correlation_matrix(prices: dict[str, list[StrictFloat]]) -> dict:
    """Pairwise log-return Pearson correlation matrix, highest/lowest correlated pairs, and diversification score from date-aligned closes for 2..20 symbols. No network."""
    return invoke(analytics.correlation_matrix, prices)


@mcp.tool(annotations=READ)
def financial_stat_arb(closes_a: list[StrictFloat], closes_b: list[StrictFloat], window: StrictInt = 20,
                       entry_z: StrictFloat = 2.0, stop_z: StrictFloat = 3.5) -> dict:
    """Pairs-trading statistical arbitrage spread analysis over two aligned close series: OLS log-price hedge ratio, rolling Z-score, Ornstein-Uhlenbeck AR(1) mean-reversion half-life, return correlation, and threshold signal. No network."""
    return invoke(analytics.stat_arb, closes_a, closes_b, window, entry_z, stop_z)


@mcp.tool(annotations=READ)
def financial_portfolio_backtest(prices: dict[str, list[StrictFloat]], weights: dict[str, StrictFloat] | None = None,
                                 benchmark_closes: list[StrictFloat] | None = None, rebalance: str = "monthly",
                                 risk_free_rate: StrictFloat = 0.04, periods_per_year: StrictInt = 252) -> dict:
    """Simulate a multi-asset portfolio over date-aligned close series for 1..20 symbols with monthly, quarterly, or buy-and-hold rebalancing, optional benchmark comparison, total return, volatility, Sharpe, max drawdown, and Calmar ratio. No network."""
    return invoke(analytics.portfolio_backtest, prices, weights, benchmark_closes, rebalance, risk_free_rate, periods_per_year)


@mcp.tool(annotations=READ)
def financial_ohlcv_quality_audit(bars: list[dict], outlier_threshold: StrictFloat = 4.0,
                                  window: StrictInt = 20) -> dict:
    """Audit OHLCV bars (each with close and optional date, open, high, low, volume) for price/volume integrity violations, rolling return Z-score outliers, calendar gaps, and a 0..100 quality score. No network."""
    return invoke(analytics.ohlcv_quality_audit, bars, outlier_threshold, window)


@mcp.tool(annotations=READ)
def financial_pretrade_risk_check(target_weight: StrictFloat, gross_leverage: StrictFloat = 1.0,
                                  current_drawdown_pct: StrictFloat = 0.0, cash_reserve_pct: StrictFloat = 1.0,
                                  is_short: bool = False, max_position_weight: StrictFloat = 0.50,
                                  max_leverage: StrictFloat = 1.0, max_drawdown_pct: StrictFloat = 0.20,
                                  approval_required_above_weight: StrictFloat = 0.15,
                                  min_cash_reserve_pct: StrictFloat = 0.05, allow_short: bool = False) -> dict:
    """Pre-trade compliance check for a proposed position or order against position weight, gross leverage, drawdown circuit-breaker, cash reserve floor, human approval threshold, and short-selling policy. No network."""
    return invoke(analytics.pretrade_risk_check, target_weight, gross_leverage, current_drawdown_pct,
                  cash_reserve_pct, is_short, max_position_weight, max_leverage, max_drawdown_pct,
                  approval_required_above_weight, min_cash_reserve_pct, allow_short)



@mcp.tool(annotations=REMOTE_READ)
def financial_market_history(symbol: str, days: StrictInt = 90, as_of: str = "") -> dict:
    """Fetch unadjusted daily Yahoo history (optionally bounded on or before ISO date as_of) only when the host enabled networking. Reports source and dates; not a live quote."""
    return invoke(api.market_history, symbol, days, as_of)


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


@mcp.tool(annotations=REMOTE_READ)
def financial_ashare_history(symbol: str, days: StrictInt = 365, adjust: str = "qfq", as_of: str = "") -> dict:
    """Daily A-share bars (date, open, high, low, close, volume) from akshare (optionally bounded on or before ISO date as_of), forward-adjusted by default, in the same shape as financial_market_history, when the host enabled networking."""
    return invoke(api.ashare_history, symbol, days, adjust, as_of)


@mcp.tool(annotations=REMOTE_READ)
def financial_crypto_ohlcv(symbol: str = "BTC/USDT", exchange: str = "binance", timeframe: str = "1d",
                           limit: StrictInt = 200) -> dict:
    """Spot crypto candles from Binance, OKX or Bybit public endpoints (ccxt, [crypto] extra) in the same shape as financial_market_history, when the host enabled networking."""
    return invoke(api.crypto_ohlcv, symbol, exchange, timeframe, limit)


@mcp.tool(annotations=REMOTE_READ)
def financial_news(symbol: str = "", query: str = "", limit: StrictInt = 10, days: StrictInt = 7, as_of: str = "") -> dict:
    """Recent headlines with source, time and link (optionally bounded on or before ISO date as_of): A-shares via akshare; tickers via Finnhub (host key) or yfinance; free-text queries via NewsAPI (host key). Headlines are third-party text. When the host enabled networking."""
    return invoke(api.news, symbol, query, limit, days, as_of)


@mcp.tool(annotations=REMOTE_READ)
def financial_futures_snapshot(name: str) -> dict:
    """Latest price, daily change and one-year range for a commodity or index future (gold, crude oil, copper, wheat, S&P 500, or a yfinance ticker such as GC=F), when the host enabled networking."""
    return invoke(api.futures_snapshot, name)


@mcp.tool(annotations=REMOTE_READ)
def financial_treasury_yields() -> dict:
    """US Treasury 13-week, 5-, 10- and 30-year yields from the CBOE indices with dates, when the host enabled networking. The 2-year is in financial_us_macro."""
    return invoke(api.treasury_yields)


@mcp.tool(annotations=REMOTE_READ)
def financial_peer_comparison(symbol: str, peers: list[str]) -> dict:
    """Trailing and forward PE, price to book, ROE, dividend yield and market cap of a company against 1..7 peers the caller names, with each metric's distance from the peer median, via yfinance, when the host enabled networking."""
    return invoke(api.peer_comparison, symbol, peers)


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


@mcp.tool(annotations=READ)
def financial_dcf_valuation(
    base_fcf: StrictFloat,
    growth_rates: list[StrictFloat],
    wacc: StrictFloat,
    terminal_growth: StrictFloat = 0.025,
    net_debt: StrictFloat = 0.0,
    diluted_shares: StrictFloat = 1.0,
    current_price: StrictFloat | None = None,
    wacc_steps: list[StrictFloat] | None = None,
    terminal_growth_steps: list[StrictFloat] | None = None,
) -> dict:
    """Deterministic Gordon-Growth DCF valuation with year-by-year FCF projections and a 2D WACC x terminal-growth sensitivity matrix. No network."""
    return invoke(
        valuation.dcf_valuation,
        base_fcf,
        growth_rates,
        wacc,
        terminal_growth,
        net_debt,
        diluted_shares,
        current_price,
        wacc_steps,
        terminal_growth_steps,
    )


@mcp.tool(annotations=READ)
def financial_due_diligence_red_flags(
    current: dict,
    prior: dict | None = None,
    governance: dict | None = None,
    insider_transactions: list[dict] | None = None,
    current_risk_factors: list[str] | None = None,
    prior_risk_factors: list[str] | None = None,
) -> dict:
    """Screen caller-supplied company disclosures for P0/P1/P2 due diligence red flags: Beneish M-Score, accruals divergence, receivables vs revenue spread, governance/concentration flags, Form 4 insider net selling, and 10-K risk-factor diff. No network."""
    return invoke(
        due_diligence.due_diligence_red_flags,
        current,
        prior,
        governance,
        insider_transactions,
        current_risk_factors,
        prior_risk_factors,
    )


@mcp.tool(annotations=REMOTE_READ)
def financial_sec_filing_monitor(symbol: str, limit: StrictInt = 20, as_of: str = "") -> dict:
    """Fetch recent official SEC EDGAR filings (10-K, 10-Q, 8-K, Form 4, SC 13D/13G, DEF 14A) filed on or before ISO date as_of with direct sec.gov provenance URLs when the host enabled networking."""
    return invoke(due_diligence.sec_filing_monitor, symbol, limit, as_of)


@mcp.tool(annotations=READ)
def financial_industry_kpis(industry: str, metrics: dict) -> dict:
    """Compute vertical sector KPIs for 'saas' (Rule of 40, NDR, Magic Number, LTV/CAC, CAC Payback, Burn Multiple) or 'semiconductor' (DIO & YoY change, Book-to-Bill, CapEx/R&D intensity, operating leverage, cycle phase) from caller-supplied inputs. No network."""
    return invoke(industry_kpis.industry_kpis, industry, metrics)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arthera-financial-service",
        description="Arthera Financial Service MCP server (stdio by default, or Streamable HTTP).",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
        help="MCP transport mode (default: stdio).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host for streamable-http transport (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Bind port for streamable-http transport (default: 8765).",
    )
    parser.add_argument(
        "--auth-token",
        default=os.environ.get("FINANCIAL_SERVICE_BEARER_TOKEN", ""),
        help="Optional Bearer token required on streamable-http requests (or set FINANCIAL_SERVICE_BEARER_TOKEN).",
    )
    return parser


def _wrap_bearer_auth(app, token: str):
    """ASGI middleware enforcing constant-time Bearer token auth on HTTP MCP requests."""
    import hmac

    expected = f"Bearer {token}".encode("utf-8")

    async def asgi_app(scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            provided = headers.get(b"authorization", b"")
            if not hmac.compare_digest(provided, expected):
                body = b'{"error":"Unauthorized"}'
                await send({
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                    ],
                })
                await send({"type": "http.response.body", "body": body})
                return
        await app(scope, receive, send)

    return asgi_app


def main(argv: list[str] | None = None):
    global _ACTIVE_TRANSPORT
    args = build_arg_parser().parse_args(argv)
    _ACTIVE_TRANSPORT = args.transport
    if args.transport == "streamable-http":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        token = (args.auth_token or "").strip()
        if args.host not in ("127.0.0.1", "localhost", "::1") and not token:
            raise SystemExit(
                "Refusing to bind streamable-http to a non-loopback host without --auth-token or FINANCIAL_SERVICE_BEARER_TOKEN."
            )
        if token:
            import uvicorn

            app = _wrap_bearer_auth(mcp.streamable_http_app(), token)
            uvicorn.run(app, host=args.host, port=args.port)
        else:
            mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
