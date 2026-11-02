import json
import sys
import types
from datetime import date, timedelta

import pytest

from arthera_finance import api, macro, server


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def read(self, limit=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def fred(monkeypatch):
    """Answers FRED requests from a table; records the URLs it saw."""
    monkeypatch.setenv("FINANCIAL_SERVICE_NETWORK", "1")
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    seen, table = [], {}

    def urlopen(request, timeout):
        assert 0 < timeout <= 8
        url = request.full_url
        seen.append(url)
        for series, body in table.items():
            if f"id={series}" in url or f"series_id={series}&" in url:
                return FakeResponse(body.encode())
        raise OSError("not found")

    monkeypatch.setattr(macro.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(macro, "_akshare", lambda: None)
    return seen, table


def csv_body(series, rows):
    return "observation_date," + series + "\n" + "".join(f"{d},{v}\n" for d, v in rows)


def test_network_gate_and_inputs(monkeypatch):
    for fn in (api.us_macro, api.cn_macro, api.central_bank_rates):
        assert server.invoke(fn)["error_code"] == "network_disabled"
    monkeypatch.setenv("FINANCIAL_SERVICE_NETWORK", "1")
    for args in [("gold",), ("cpi", 0), ("cpi", 61), ("cpi", True)]:
        assert server.invoke(api.us_macro, *args)["error_code"] == "invalid_input", args
    assert server.invoke(api.cn_macro, "nfp")["error_code"] == "invalid_input"


def test_us_macro_from_public_csv(fred):
    seen, table = fred
    table["DGS10"] = csv_body("DGS10", [("2024-01-02", "4.0"), ("2024-01-03", "."), ("2024-01-04", "4.2")])
    table["DGS2"] = csv_body("DGS2", [("2024-01-04", "4.5")])
    table["CPIAUCSL"] = csv_body("CPIAUCSL", [("2023-12-01", "3.4"), ("2024-01-01", "3.1")])
    result = server.invoke(api.us_macro)
    assert result["success"]
    assert result["data"]["10y_yield"]["observations"] == [{"date": "2024-01-02", "value": 4.0},
                                                           {"date": "2024-01-04", "value": 4.2}]
    assert result["data"]["10y_yield"]["change"] == 0.2
    assert result["yield_curve"] == {"spread_10y_2y": -0.3, "shape": "inverted"}
    assert any("transformation=pc1" in url and "CPIAUCSL" in url for url in seen)
    assert {f["indicator"] for f in result["failures"]} == {"gdp_growth", "fed_rate", "unemployment", "m2", "ppi"}


def test_us_macro_with_key_uses_api_and_limit(fred, monkeypatch):
    seen, table = fred
    monkeypatch.setenv("FRED_API_KEY", "k")
    table["UNRATE"] = json.dumps({"observations": [{"date": "2024-02-01", "value": "3.9"},
                                                   {"date": "2024-01-01", "value": "3.7"}]})
    result = server.invoke(api.us_macro, "unemployment", 2)
    assert result["data"]["unemployment"]["latest"] == {"date": "2024-02-01", "value": 3.9}
    assert "api.stlouisfed.org" in seen[0] and "limit=2" in seen[0]


def test_us_macro_reports_when_nothing_comes_back(fred):
    result = server.invoke(api.us_macro, "vix")
    assert result["error_code"] == "source_unavailable" and result["failures"][0]["indicator"] == "vix"


def test_policy_rates_mark_stale_values(fred):
    _, table = fred
    recent = (date.today() - timedelta(days=20)).isoformat()
    table["FEDFUNDS"] = csv_body("FEDFUNDS", [(recent, "4.33")])
    table["IUDSOIA"] = csv_body("IUDSOIA", [("2017-01-01", "0.25")])
    result = server.invoke(api.central_bank_rates)
    rows = {r["key"]: r for r in result["rates"]}
    assert rows["fed"]["value"] == 4.33 and not rows["fed"]["stale"]
    assert rows["uk"]["stale"]
    assert {f["indicator"] for f in result["failures"]} == {"ecb", "jp"}


def test_cn_macro_needs_akshare(monkeypatch):
    monkeypatch.setenv("FINANCIAL_SERVICE_NETWORK", "1")
    monkeypatch.setattr(macro, "_akshare", lambda: None)
    assert server.invoke(api.cn_macro)["error_code"] == "dependency_missing"


def test_cn_macro_rows_are_plain(monkeypatch):
    pd = pytest.importorskip("pandas")
    monkeypatch.setenv("FINANCIAL_SERVICE_NETWORK", "1")
    frame = pd.DataFrame({"日期": pd.to_datetime(["2024-01-01", "2024-02-01"]), "今值": [0.2, float("nan")],
                          "flag": [True, False]})
    fake = types.SimpleNamespace(macro_china_cpi_yearly=lambda: frame)
    monkeypatch.setattr(macro, "_akshare", lambda: fake)
    result = server.invoke(api.cn_macro, "cpi", 5)
    assert result["data"]["cpi"]["rows"] == [{"日期": "2024-01-01", "今值": 0.2}, {"日期": "2024-02-01"}]
    result = server.invoke(api.cn_macro, "ppi")
    assert result["error_code"] == "source_unavailable"
