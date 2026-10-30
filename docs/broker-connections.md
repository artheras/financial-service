# Broker account connections

The stdio service exposes account, position and existing-order queries. It does
not expose order placement, modification, cancellation, trading unlock or transfer.
Each local user runs their own service process and profile directory.

| Adapter | Connection mechanism | Extra |
|---|---|---|
| paper | Local simulated ledger, no real broker account | Built in |
| alpaca | User OAuth token or API key/secret; paper selected by default | alpaca |
| ibkr | User's logged-in TWS/IB Gateway, explicit managed account | ibkr |
| futu | User's OpenD gateway, explicit account_id | futu |
| tiger | Tiger ID, account and private key file | tiger |
| longbridge | App key/secret and access token | longbridge |
| webull | Existing adapter's username/device/MFA login | webull |
| trading212 | Existing adapter's API-key connection | trading212 |
| xtquant | User's logged-in QMT terminal and account_id | Broker-supplied SDK |
| easytrader | Logged-in supported desktop trading client | easytrader |

Install only the required SDK, for example pip install -e ".[alpaca]". Vendor
availability, account entitlements and platform support must be checked with the
broker. Adapter availability is not a statement that a live account was tested.
Webull's existing third-party SDK path is not an official OAuth integration.

## Local user setup

Create brokers.json in FINANCIAL_SERVICE_HOME, or ~/.arthera-finance by default.
Keep the file owner-only (chmod 600 on POSIX; user-private ACLs on Windows).
Store credential environment-variable names, not secret values:

```json
{
  "brokers": [
    {
      "id": "my_alpaca",
      "type": "alpaca",
      "paper": true,
      "credentials": {"access_token": "MY_ALPACA_OAUTH_TOKEN"}
    },
    {
      "id": "my_ibkr",
      "type": "ibkr",
      "host": "127.0.0.1",
      "port": 7497,
      "account_id": "YOUR_MANAGED_ACCOUNT",
      "client_id": 21
    },
    {
      "id": "practice",
      "type": "paper",
      "starting_cash": 100000,
      "currency": "USD"
    }
  ]
}
```

Set the service host's FINANCIAL_SERVICE_BROKERS_ENABLED=1 to permit account
queries. Real broker connections also require FINANCIAL_SERVICE_NETWORK=1.
Paper ledger queries work with networking off. The Finance MCP templates keep
networking off, so set these values explicitly in the selected server's host
environment and restart that server. Never paste credentials into chat.

The tools financial_broker_connections, financial_broker_account,
financial_broker_positions and financial_broker_orders accept connection
identifiers from this profile file. Account identifiers are masked and private
adapter extras are omitted. Missing quotes/values must remain unavailable rather
than being interpreted as current market data. simulation=true denotes a known
simulated environment; null means the adapter does not establish that status.
Query results include retrieval time, not a claim of provider quote freshness.

SDK calls run in a short-lived subprocess with a 30-second execution timeout.
The worker receives only the selected profile's credential variables plus
runtime/transport settings. SDK output is excluded from MCP responses. Concurrent
broker queries can return broker_busy instead of starting competing gateway
sessions. Removing a profile denies subsequent calls; changed host credentials
require restarting the service. Revoke the broker authorization at the broker
when disconnecting an external account.

## Local setup diagnostics

Run diagnostics with the same interpreter and environment as the MCP server:

```sh
python -m arthera_finance.doctor
python -m arthera_finance.doctor --connection my_alpaca --json
```

The installed command arthera-finance-doctor provides the same interface. Exit
code 0 means local configuration checks passed; code 1 means setup needs attention.
Checks cover all ten adapters, optional SDK availability, permission flags,
required settings, credential-variable presence and desktop platform support.
The command never connects to a broker, imports an optional SDK, creates a ledger,
or displays account identifiers, credential names or values. A passing local check
does not verify package compatibility, token validity, gateway login or live data.
Use a read-only account query to verify the authorized connection separately.
EasyTrader's optional comm_password must also use a credentials environment reference.

## Platform accounts

For a hosted multi-user product, bind every broker authorization to the
authenticated application user on the backend. Keep tokens in a per-user secret
store and dispatch workers using that user's authorized connection. Do not expose
one local stdio process or a shared profile directory as a multi-tenant endpoint.
The current local service does not implement hosted login/callback routes.

Alpaca supports third-party OAuth authorization. Register an application and its
redirect URI, bind an unguessable one-use state to the logged-in user, exchange the
code on the backend, and bind the returned broker account to that user. Read-only
access is the default scope; do not request trading or account:write for these
tools. Select paper/live explicitly. The adapter accepts the resulting token by
environment reference; it does not perform the browser authorization itself.
[Alpaca OAuth](https://docs.alpaca.markets/us/docs/using-oauth2-and-trading-api).

IBKR's local route connects to the user's authenticated TWS/IB Gateway. Configure
the gateway endpoint and select a managed account; use read-only access for account
queries. The adapter rejects unselected accounts and filters account-specific
results. Futu queries similarly pass the selected account_id to OpenD and never
unlock trading. Each gateway must be isolated to its owning user.
