import math

from arthera_finance import api, server
from arthera_finance.datasources.base import FundamentalsResult, QuoteResult


class FakeRouter:
    def __init__(self, quote=None, fundamentals=None):
        self._quote, self._fundamentals = quote, fundamentals

    def quote(self, symbol):
        return self._quote

    def fundamentals(self, symbol):
        return self._fundamentals


def test_network_must_be_enabled_by_the_host():
    result = server.invoke(api.market_quote, "AAPL")
    assert result["error_code"] == "network_disabled"


def test_quote_names_its_source_and_keeps_only_plain_values(monkeypatch):
    monkeypatch.setenv("FINANCIAL_SERVICE_NETWORK", "1")
    quote = QuoteResult(symbol="AAPL", name="Apple", price=190.5, currency="USD", source="yfinance",
                        extra={"nan": math.nan, "nested": {"x": 1}, "note": "y" * 500, "flag": True})
    monkeypatch.setattr(api, "_router", FakeRouter(quote=quote))
    result = server.invoke(api.market_quote, "AAPL")
    assert result["success"] and result["source"] == "yfinance"
    assert result["data"]["price"] == 190.5 and result["data"]["name"] == "Apple"
    assert "nan" not in result["data"] and "nested" not in result["data"] and "flag" not in result["data"]
    assert len(result["data"]["note"]) == 200


def test_fundamentals_omit_missing_metrics(monkeypatch):
    monkeypatch.setenv("FINANCIAL_SERVICE_NETWORK", "1")
    monkeypatch.setattr(api, "_router", FakeRouter(fundamentals=FundamentalsResult(symbol="600519", pe_ttm=25.1, source="tushare")))
    result = server.invoke(api.market_fundamentals, "600519")
    assert result["data"] == {"pe_ttm": 25.1, "source": "tushare"}


def test_no_source_and_bad_symbols(monkeypatch):
    monkeypatch.setenv("FINANCIAL_SERVICE_NETWORK", "1")
    monkeypatch.setattr(api, "_router", FakeRouter())
    assert server.invoke(api.market_quote, "AAPL")["error_code"] == "source_unavailable"
    assert server.invoke(api.market_quote, "../../etc")["error_code"] == "invalid_input"
    assert server.invoke(api.market_quote, "A" * 40)["error_code"] == "invalid_input"


def test_keys_come_only_from_the_environment(monkeypatch, tmp_path):
    from arthera_finance.datasources.sources import tushare_source
    (tmp_path / ".env").write_text("TUSHARE_TOKEN=from-a-project\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    assert tushare_source._load_token() == ""
    monkeypatch.setenv("TUSHARE_TOKEN", "from-the-host")
    assert tushare_source._load_token() == "from-the-host"


def test_router_config_never_comes_from_the_working_directory(monkeypatch, tmp_path):
    from arthera_finance.datasources import router
    assert all(str(p).startswith(str(router._SERVICE_HOME)) for p in router._CONFIG_PATHS)


def test_status_needs_no_network(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "x")
    result = server.invoke(api.datasource_status)
    fred = next(row for row in result["sources"] if row["source"] == "fred")
    assert result["success"] and fred["key_set"] and result["network_enabled"] is False
