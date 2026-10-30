# Arthera Financial Service

Shared financial calculations and evidence tools for Codex, Claude Code and Aria
Code. The service runs independently of aria-code and provides a local stdio MCP
interface using the official MCP Python SDK. No language-model account or API key
is needed for supplied-data calculations.

## Install from source

Python 3.11+. This initial source version is not a published PyPI release.

```sh
git clone https://github.com/artheras/financial-service.git
cd financial-service
# While the initial PR is under review:
git checkout feat/standalone-finance-service
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

On Windows use .venv/Scripts/python.exe and .venv/Scripts/arthera-financial-service.exe.
After the PR merges, main contains the same source and the branch checkout is
unnecessary. Install `.[market]` for Yahoo market data. The plain package keeps
the calculation cores free of NumPy/pandas/yfinance requirements.

## Tools

| Tool | Input and behavior |
|---|---|
| financial_service_info | Version, host-selected network state, no trading capability |
| financial_analyze_statements | Supplied statements; or ticker-based Yahoo/SEC reads when enabled; success/data/source_attempts output |
| financial_parse_sec_facts | Supplied companyfacts; one aligned annual filing, explicit provenance and stale-period labels |
| financial_risk_metrics | Ordered positive equity values; sample Sharpe, drawdown, total return and annual-frequency assumption |
| financial_backtest | Supplied daily OHLC bars, buy_hold/sma_cross, next-open fills, fees and slippage; simulation only |
| financial_market_history | Host-enabled Yahoo unadjusted daily history, source/as_of/retrieved_at; not a live quote |

Missing metrics remain unknown. A successful calculation does not independently
verify caller-supplied data. Backtests return only the last 100 equity/trade entries,
the source input hash, and their metrics. Use adjusted prices consistently when
appropriate; unadjusted history is explicitly labelled and is not total-return data.
There are no file-reading, broker execution, publishing or paid model tools.

## Connect clients

Use the literal absolute executable path; it is installed in the service's venv.

```sh
codex mcp add arthera-finance -- /absolute/path/to/financial-service/.venv/bin/arthera-financial-service
claude mcp add --transport stdio arthera-finance -- /absolute/path/to/financial-service/.venv/bin/arthera-financial-service
```

For the updated Aria Code host:

```text
/mcp finance install "/absolute/path/to/financial-service/.venv/bin/python"
/mcp reload
/mcp tools
```

Other Aria versions can merge the server entry from the Finance plugin's mcp.json
and restart. The plugin catalogue at https://github.com/artheras/aria-plugins
contains Claude .mcp.json, Codex TOML, Aria configuration, and the finance skill.
Skill installation alone does not install this service.

Networking is off by default. To enable external financial data, explicitly set
FINANCIAL_SERVICE_NETWORK=1 in the server's host environment; configure
EDGAR_USER_AGENT with your company and contact email for SEC requests. Do not
pass network permission as a tool argument. No live data is fetched in CI.
This first version is a local stdio service, not an authenticated hosted endpoint.

## Migration and compatibility

The first transferred modules are corporate statement analysis, SEC annual
disclosure parsing, calendar-aware performance statistics and the event-driven
backtest core. They derive from Aria Code main 1666c4a and retain Apache licensing,
NOTICE and independently checked tests. migration.json records original paths and
hashes. Host data-loading and live-broker order adapters were intentionally excluded
from the shared backtest core.

Existing Aria financial handlers remain as compatibility implementations during
rollout. This source version does not yet move all data sources, financial agents,
report generators, factor tools or broker adapters. Future caller migration must
preserve schemas, numerical results, source provenance and authorization before
old implementations are removed. Plugin wrappers contain no financial algorithms.
