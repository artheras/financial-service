import importlib.util

import pytest

from arthera_finance import api, server

pandas = pytest.importorskip("pandas") if importlib.util.find_spec("pandas") else None


def test_network_and_input_checks(monkeypatch):
    assert server.invoke(api.macro_series, "CPI")["error_code"] == "network_disabled"
    monkeypatch.setenv("FINANCIAL_SERVICE_NETWORK", "1")
    for args in [("../x",), ("CPI", "bloomberg"), ("CPI", "world_bank", "China!"), ("CPI", "fred", "US", 0)]:
        assert server.invoke(api.macro_series, *args)["error_code"] == "invalid_input", args


def test_missing_pandas_is_reported(monkeypatch):
    monkeypatch.setenv("FINANCIAL_SERVICE_NETWORK", "1")
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name == "pandas" else real(name, *a))
    assert server.invoke(api.macro_series, "CPI")["error_code"] == "dependency_missing"


@pytest.mark.skipif(pandas is None, reason="needs pandas")
def test_series_rows_from_a_source(monkeypatch):
    import pandas as pd
    from arthera_finance.datasources.base import HistoryResult
    from arthera_finance.datasources.sources import fred_source, world_bank_source
    monkeypatch.setenv("FINANCIAL_SERVICE_NETWORK", "1")
    frame = pd.DataFrame({"close": [3.1, float("nan"), 3.4]}, index=pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01"]))
    seen = {}

    def history(self, symbol, days=365, **kw):
        seen["fred"] = (symbol, days)
        return HistoryResult(symbol=symbol, data=frame, source="fred")

    def indicator(self, indicator, country="WLD", start_year=2000, end_year=2024):
        seen["wb"] = (indicator, country, end_year - start_year)
        return HistoryResult(symbol=indicator, data=frame, source="world_bank")

    monkeypatch.setattr(fred_source.FREDSource, "history", history)
    monkeypatch.setattr(world_bank_source.WorldBankSource, "get_indicator", indicator)
    result = server.invoke(api.macro_series, "cpi", "fred", "US", 2)
    assert result["success"] and seen["fred"] == ("CPI", 730)
    assert [r["value"] for r in result["observations"]] == [3.1, 3.4] and result["as_of"] == "2024-03-01"
    result = server.invoke(api.macro_series, "GDP", "world_bank", "cn", 5)
    assert result["country"] == "CN" and seen["wb"] == ("GDP", "CN", 5)
    monkeypatch.setattr(fred_source.FREDSource, "history", lambda *a, **k: None)
    assert server.invoke(api.macro_series, "CPI")["error_code"] == "source_unavailable"
