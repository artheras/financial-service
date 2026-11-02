import asyncio
import json

import pytest

from arthera_finance import trading_server
from arthera_finance.trading import service


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCIAL_SERVICE_HOME", str(tmp_path))
    monkeypatch.setenv("ARTHERA_TRADING_ENABLED", "1")
    monkeypatch.delenv("ARTHERA_TRADING_HALT", raising=False)
    (tmp_path / "trading.json").write_text(json.dumps({"accounts": [
        {"id": "paper", "type": "paper", "starting_cash": 100000,
         "limits": {"max_position_weight": 0.2, "max_order_weight": 0.1, "max_orders_per_day": 3,
                    "max_notional_per_day": 25000}}]}))
    return tmp_path


def call(fn, *args):
    return trading_server.invoke(fn, *args)


def preview(qty=50, price=100.0, side="buy", **kw):
    return call(service.preview_order, "paper", "aapl", side, kw.get("quantity", qty), kw.get("target_weight"), price, "")


def test_off_unless_the_host_enables_it(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCIAL_SERVICE_HOME", str(tmp_path))
    monkeypatch.delenv("ARTHERA_TRADING_ENABLED", raising=False)
    assert call(service.list_accounts)["enabled"] is False
    assert preview()["error_code"] == "trading_disabled"
    assert call(service.execute_order, "tp_" + "0" * 16)["error_code"] == "trading_disabled"


def test_preview_then_execute_once(host):
    p = preview()
    assert p["can_execute"] and p["order"] == {"symbol": "AAPL", "side": "buy", "quantity": 50, "limit_price": 100.0,
                                               "estimated_value": 5000.0}
    state = call(service.account_state, "paper")
    assert state["cash"] == 100000 and state["positions"] == []  # a preview changes nothing
    done = call(service.execute_order, p["preview_id"])
    assert done["success"] and done["simulated"] and done["cash_after"] == 95000.0
    state = call(service.account_state, "paper")
    assert state["positions"][0]["quantity"] == 50 and state["today"] == {"orders": 1, "notional": 5000.0}
    assert call(service.execute_order, p["preview_id"])["error_code"] == "preview_used"
    events = [e["event"] for e in call(service.audit_log, 10)["events"]]
    assert events == ["preview", "execute", "execute_refused"]


def test_limits_block(host):
    for kwargs, fragment in [({"qty": 150}, "one order may be at most"),            # 15% of the account
                             ({"qty": 2000}, "cash is available"),
                             ({"side": "sell", "qty": 1}, "short selling"),
                             ({"quantity": None, "target_weight": 0.5}, "the limit is 20.0%")]:
        p = preview(**kwargs)
        assert p["success"] and not p["can_execute"], kwargs
        assert any(fragment in b for b in p["plan"]["blockers"]), (kwargs, p["plan"]["blockers"])
        assert call(service.execute_order, p["preview_id"])["error_code"] == "risk_rejected"


def test_execute_rechecks_against_the_account_now(host):
    first, second, third = preview(qty=90), preview(qty=90), preview(qty=90)
    assert first["can_execute"] and second["can_execute"] and third["can_execute"]
    assert call(service.execute_order, first["preview_id"])["success"]
    assert call(service.execute_order, second["preview_id"])["success"]
    # 270 shares at 100 would be 27% of the account and over the 25,000 daily value.
    refused = call(service.execute_order, third["preview_id"])
    assert refused["error_code"] == "risk_rejected" and "position would be" in refused["error"]


def test_daily_order_count(host):
    for _ in range(3):
        assert call(service.execute_order, preview(qty=10)["preview_id"])["success"]
    p = preview(qty=10)
    assert any("daily limit of 3" in b for b in p["plan"]["blockers"])


def test_expiry_and_cancel(host, monkeypatch):
    p = preview()
    clock = service._now() + service.PREVIEW_TTL_SECONDS + 1
    monkeypatch.setattr(service, "_now", lambda: clock)
    assert call(service.execute_order, p["preview_id"])["error_code"] == "preview_expired"
    monkeypatch.undo()
    monkeypatch.setenv("FINANCIAL_SERVICE_HOME", str(host))
    monkeypatch.setenv("ARTHERA_TRADING_ENABLED", "1")
    q = preview()
    assert call(service.cancel_preview, q["preview_id"])["status"] == "cancelled"
    assert call(service.execute_order, q["preview_id"])["error_code"] == "preview_used"


def test_halt_by_environment_or_file(host, monkeypatch):
    p = preview()
    (host / "HALT").write_text("")
    assert call(service.execute_order, p["preview_id"])["error_code"] == "trading_halted"
    assert preview()["error_code"] == "trading_halted"
    (host / "HALT").unlink()
    monkeypatch.setenv("ARTHERA_TRADING_HALT", "1")
    assert call(service.execute_order, p["preview_id"])["error_code"] == "trading_halted"
    monkeypatch.setenv("ARTHERA_TRADING_HALT", "0")
    assert call(service.execute_order, p["preview_id"])["success"]


def test_configuration_is_checked(host):
    for accounts in [[{"id": "live", "type": "alpaca"}], [{"id": "Bad Id", "type": "paper"}],
                     [{"id": "p", "type": "paper", "limits": {"max_position_weight": 2}}],
                     [{"id": "p", "type": "paper", "limits": {"surprise": 1}}]]:
        (host / "trading.json").write_text(json.dumps({"accounts": accounts}))
        assert call(service.list_accounts).get("error_code") == "configuration_invalid", accounts


def test_inputs(host):
    for args in [("paper", "../x", "buy", 1, None, 10.0, ""), ("paper", "AAPL", "short", 1, None, 10.0, ""),
                 ("paper", "AAPL", "buy", None, None, 10.0, ""), ("paper", "AAPL", "buy", 1, 0.1, 10.0, ""),
                 ("paper", "AAPL", "buy", 1, None, 0, ""), ("nobody", "AAPL", "buy", 1, None, 10.0, "")]:
        assert call(service.preview_order, *args)["error_code"] in ("invalid_input", "unknown_account"), args
    assert call(service.execute_order, "../../etc")["error_code"] == "invalid_input"


def test_tool_annotations():
    tools = {t.name: t.annotations for t in asyncio.run(trading_server.mcp.list_tools())}
    assert tools["trading_execute_order"].destructiveHint is True and tools["trading_execute_order"].readOnlyHint is False
    assert tools["trading_preview_order"].readOnlyHint is False and tools["trading_preview_order"].destructiveHint is False
    for name in ("trading_accounts", "trading_account_state", "trading_audit"):
        assert tools[name].readOnlyHint is True
    assert set(tools) == {"trading_accounts", "trading_account_state", "trading_preview_order",
                          "trading_execute_order", "trading_cancel_preview", "trading_audit"}


def test_the_read_only_server_has_no_trading_tools():
    from arthera_finance import server
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert not any(name.startswith("trading_") for name in names)
