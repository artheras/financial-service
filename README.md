# Arthera Financial Service

Shared financial calculations and evidence tools for Codex, Claude Code and Aria
Code. The service runs independently of aria-code and provides a local stdio MCP
interface using the official MCP Python SDK. No language-model account or API key
is needed for supplied-data calculations.

## Install from source

Python 3.11+. Install from source:

```sh
git clone https://github.com/artheras/financial-service.git
cd financial-service
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

On Windows use .venv/Scripts/python.exe and .venv/Scripts/arthera-financial-service.exe.
Install `.[market]` for Yahoo market data. The plain package keeps
the calculation cores free of NumPy/pandas/yfinance requirements.

Broker account, position and order queries are available through the [broker connection guide](docs/broker-connections.md). Each user runs an isolated local service process.
Run `python -m arthera_finance.doctor` in the service environment to check local
broker setup without connecting or displaying credentials. Use `--json` for a
structured report and `--connection ID` to check one configured connection.

## Tools

| Tool | Input and behavior |
|---|---|
| financial_service_info | Version, host-selected network state, no trading capability |
| financial_analyze_statements | Supplied statements; or ticker-based Yahoo/SEC reads when enabled; success/data/source_attempts output |
| financial_parse_sec_facts | Supplied companyfacts; one aligned annual filing, explicit provenance and stale-period labels |
| financial_risk_metrics | Ordered positive equity values; sample Sharpe, drawdown, total return and annual-frequency assumption |
| financial_backtest | Supplied daily OHLC bars, buy_hold/sma_cross, next-open fills, fees and slippage; simulation only |
| financial_market_history | Host-enabled Yahoo unadjusted daily history, source/as_of/retrieved_at; not a live quote |
| financial_broker_connections | This host user's configured connection identifiers; no credentials |
| financial_broker_account | Read a selected account and mask its identifier |
| financial_broker_positions | Read selected-account positions |
| financial_broker_orders | Read existing orders; cannot place or cancel orders |

Missing metrics remain unknown. A successful calculation does not independently
verify caller-supplied data. Backtests return only the last 100 equity/trade entries,
the source input hash, and their metrics. Use adjusted prices consistently when
appropriate; unadjusted history is explicitly labelled and is not total-return data.
There are no file-reading, broker execution, publishing or paid model tools.

## Connect clients

Use the literal absolute path of the arthera-financial-service console script in
the service's venv. A bare command name only works when that venv's bin/Scripts
directory is on the client's PATH, and otherwise starts whichever copy (if any) is
found first, so prefer the absolute path in client configuration. If you launch a
module directly, use `python -I -m arthera_finance.server` with the venv's absolute
interpreter path: plain `python -m` puts the client's current directory, usually an
untrusted workspace, first on the import path.

```sh
codex mcp add arthera-finance -- /absolute/path/to/financial-service/.venv/bin/arthera-financial-service
claude mcp add --transport stdio arthera-finance -- /absolute/path/to/financial-service/.venv/bin/arthera-financial-service
```

For Aria Code:

```text
/mcp finance install "/absolute/path/to/financial-service/.venv/bin/python"
/mcp reload
/mcp tools
```

You can also merge the server entry from the Finance plugin's mcp.json
and restart. The plugin catalogue at https://github.com/artheras/aria-plugins
contains Claude .mcp.json, Codex TOML, Aria configuration, and the finance skill.
Skill installation alone does not install this service.

Networking and broker access are off by default. To enable external financial
data, explicitly set FINANCIAL_SERVICE_NETWORK=1 in the server's host environment;
configure EDGAR_USER_AGENT with your company and contact email for SEC requests.
Do not pass network permission as a tool argument. MCP client configurations
should not hard-code FINANCIAL_SERVICE_NETWORK or FINANCIAL_SERVICE_BROKERS_ENABLED
in the server's env block: a value there overrides the host environment, so a
template that sets "0" silently disables what the host enabled. Leave both unset
in client configuration and let the host environment decide; unset means off.
No live data is fetched in CI. The service uses local stdio transport.

Broker adapters are read-only: the service's adapter copies contain no order
placement or cancellation code, and the broker worker runs isolated (`python -I`)
from the installed package directory so modules in the client's workspace are
never imported next to broker credentials. Install the service into a venv, not
with `pip install --user`, because isolated mode ignores user site-packages.

