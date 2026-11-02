import json
import sys
import types

import pytest

from arthera_finance import api, news, server


class FakeResponse:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self, limit=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def network(monkeypatch):
    monkeypatch.setenv("FINANCIAL_SERVICE_NETWORK", "1")
    for key in ("FINNHUB_API_KEY", "FINNHUB_KEY", "NEWS_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def test_gate_and_inputs(network, monkeypatch):
    monkeypatch.setenv("FINANCIAL_SERVICE_NETWORK", "0")
    assert server.invoke(api.news, "AAPL")["error_code"] == "network_disabled"
    monkeypatch.setenv("FINANCIAL_SERVICE_NETWORK", "1")
    for args in [("",), ("../x",), ("", "x"), ("", "drop table; --<script>"), ("AAPL", "", 0), ("AAPL", "", 5, 31)]:
        assert server.invoke(api.news, *args)["error_code"] == "invalid_input", args


def test_finnhub_first_then_items_are_plain(network, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    seen = []

    def urlopen(request, timeout):
        seen.append(request.full_url)
        return FakeResponse([{"headline": "  Apple   beats\nestimates ", "source": "Reuters", "datetime": 1730505600,
                              "url": "https://example.com/a"},
                             {"headline": "", "source": "x"},
                             {"headline": "Odd link", "url": "javascript:alert(1)"}])

    monkeypatch.setattr(news.urllib.request, "urlopen", urlopen)
    result = server.invoke(api.news, "aapl", "", 5)
    assert result["source"] == "finnhub" and "symbol=AAPL" in seen[0]
    assert result["items"][0] == {"title": "Apple beats estimates", "source": "Reuters",
                                  "published": "2024-11-02T00:00:00Z", "url": "https://example.com/a"}
    assert result["items"][1]["url"] is None and len(result["items"]) == 2


def test_falls_back_to_yfinance_and_reports_attempts(network, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    monkeypatch.setattr(news.urllib.request, "urlopen", lambda r, timeout: (_ for _ in ()).throw(OSError("token=k leaked")))

    class Ticker:
        def __init__(self, symbol):
            self.news = [{"content": {"title": "Shares rise", "pubDate": "2026-11-01T10:00:00Z",
                                      "provider": {"displayName": "Yahoo"},
                                      "canonicalUrl": {"url": "https://finance.yahoo.com/x"}}}]

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=Ticker))
    result = server.invoke(api.news, "MSFT")
    assert result["source"] == "yfinance" and result["items"][0]["source"] == "Yahoo"
    assert result["attempts"][0] == {"source": "finnhub", "error_code": "source_unavailable"}
    assert "leaked" not in json.dumps(result)


def test_ashare_uses_akshare(network, monkeypatch):
    pd = pytest.importorskip("pandas")
    seen = {}

    def stock_news_em(symbol):
        seen["symbol"] = symbol
        return pd.DataFrame({"新闻标题": ["茅台发布三季报"], "文章来源": ["证券时报"], "发布时间": ["2026-10-30 18:00:00"],
                             "新闻链接": ["https://finance.eastmoney.com/a/1.html"]})

    monkeypatch.setitem(sys.modules, "akshare", types.SimpleNamespace(stock_news_em=stock_news_em))
    result = server.invoke(api.news, "600519.SS")
    assert seen["symbol"] == "600519" and result["source"] == "akshare"
    assert result["items"][0]["title"] == "茅台发布三季报"


def test_query_needs_newsapi_key(network, monkeypatch):
    result = server.invoke(api.news, "", "semiconductor tariffs")
    assert result["error_code"] == "source_unavailable" and "NEWS_API_KEY" in result["error"]
    monkeypatch.setenv("NEWS_API_KEY", "n")
    monkeypatch.setattr(news.urllib.request, "urlopen", lambda r, timeout: FakeResponse(
        {"articles": [{"title": "Chip tariffs", "source": {"name": "FT"}, "publishedAt": "2026-11-01T08:00:00Z",
                       "url": "https://ft.com/x"}]}))
    assert server.invoke(api.news, "", "semiconductor tariffs")["items"][0]["source"] == "FT"
