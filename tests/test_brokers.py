import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import os

import pytest
from arthera_finance.brokers import config, service, worker
from arthera_finance.brokers.base import AccountInfo
from arthera_finance.brokers.registry import ADAPTERS, get_broker_class


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    from arthera_finance.brokers import paper_broker
    monkeypatch.setattr(config, "BROKERS_CONFIG_PATH", tmp_path / "brokers.json")
    monkeypatch.setattr(paper_broker, "PAPER_LEDGER_PATH", tmp_path / "paper_ledger.json")
    monkeypatch.setenv("FINANCIAL_SERVICE_HOME", str(tmp_path))
    monkeypatch.setenv("FINANCIAL_SERVICE_BROKERS_ENABLED", "1")
    def save(rows):
        path = tmp_path / "brokers.json"
        path.write_text(json.dumps({"brokers": rows}))
        path.chmod(0o600)
        return path
    return save


@pytest.mark.parametrize("kind", sorted(ADAPTERS))
def test_all_existing_adapters_import_without_installing_their_sdks(kind):
    adapter = get_broker_class(kind)("test", {"account_id": "123456"})
    assert adapter.broker_id == "test"
    for name in ("connect", "disconnect", "account_info", "positions", "orders"):
        assert callable(getattr(adapter, name))


def test_paper_reads_are_explicit_simulation_and_mask_identity(profiles):
    profiles([{"id": "mine", "type": "paper", "starting_cash": 1234}])
    result = service.query("mine", "account")
    assert result["success"] and result["simulation"]
    assert result["data"]["cash"] == 1234
    assert "account_id" not in result["data"] and "extra" not in result["data"]
    assert result["data"]["masked_account"].startswith("****")
    assert service.query("other-user", "account")["error_code"] == "connection_unavailable"


@pytest.mark.parametrize("operation", ["place_order", "cancel_order", "confirm", "connect_all"])
def test_write_operations_never_instantiate_an_adapter(profiles, monkeypatch, operation):
    profiles([{"id": "mine", "type": "paper"}])
    monkeypatch.setattr(service, "get_broker_class", lambda *a: pytest.fail("adapter executed"))
    assert service.query("mine", operation)["error_code"] == "broker_operation_denied"


def test_local_disabled_and_network_disabled_are_separate(profiles, monkeypatch):
    profiles([{"id": "mine", "type": "alpaca", "paper": True}])
    assert service.query("mine", "account")["error_code"] == "network_disabled"
    monkeypatch.setenv("FINANCIAL_SERVICE_BROKERS_ENABLED", "0")
    assert service.query("mine", "account")["error_code"] == "broker_access_disabled"
    assert service.connections()["connections"] == []


def test_credential_references_and_errors_never_leak_values(profiles, monkeypatch):
    profiles([{"id": "mine", "type": "alpaca", "credentials": {"access_token": "TEST_ALPACA_TOKEN"}}])
    monkeypatch.setenv("TEST_ALPACA_TOKEN", "private-value-not-for-model")
    monkeypatch.setenv("FINANCIAL_SERVICE_NETWORK", "1")
    class Adapter:
        def __init__(self, **kw):
            assert kw["config"]["access_token"] == "private-value-not-for-model"
            assert kw["config"]["readonly"] is True
        def connect(self):
            raise RuntimeError("private-value-not-for-model")
        def disconnect(self):
            pass
    monkeypatch.setattr(service, "get_broker_class", lambda kind: Adapter)
    assert "private-value-not-for-model" not in json.dumps(service.query("mine", "account"))
    assert "TEST_ALPACA_TOKEN" not in json.dumps(service.connections())


def test_inline_credentials_and_corrupt_profiles_fail_closed(profiles):
    profiles([{"id": "mine", "type": "alpaca", "api_key": "never-store-inline"}])
    assert service.connections()["success"] is False
    config.BROKERS_CONFIG_PATH.write_text("{bad json")
    assert service.query("mine", "account")["success"] is False


def test_unsafe_profile_permissions_are_rejected(profiles):
    path = profiles([{"id": "mine", "type": "paper"}])
    if os.name == "nt":
        pytest.skip("Windows profile access is governed by host ACLs")
    path.chmod(0o644)
    assert service.connections()["success"] is False


def test_suspect_account_does_not_release_positions(profiles, monkeypatch):
    profiles([{"id": "mine", "type": "alpaca", "account_id": "EXPECTED"}])
    monkeypatch.setenv("FINANCIAL_SERVICE_NETWORK", "1")
    class Adapter:
        def __init__(self, **kw):
            pass
        def connect(self):
            return True
        def account_info(self):
            return AccountInfo("mine", "alpaca", "mine", "SOMEONE_ELSE")
        def positions(self):
            pytest.fail("cross-account positions queried")
        def disconnect(self):
            pass
    monkeypatch.setattr(service, "get_broker_class", lambda kind: Adapter)
    assert service.query("mine", "positions")["error_code"] == "broker_account_mismatch"


def test_ibkr_requires_and_filters_an_explicit_account(monkeypatch):
    from arthera_finance.brokers.intl.ibkr_broker import IBKRBroker
    module = ModuleType("ib_insync")
    class IB:
        def connect(self, *args, **kwargs):
            assert kwargs["readonly"] is True
        def managedAccounts(self):
            return ["A", "B"]
        def accountSummary(self):
            return [SimpleNamespace(account=a, tag=t, value=v, currency="USD")
                    for a, t, v in [("A", "NetLiquidation", "100"), ("B", "NetLiquidation", "9999")]]
        def positions(self, account):
            assert account == "A"
            contract = SimpleNamespace(symbol="TEST", localSymbol="TEST", currency="USD")
            return [SimpleNamespace(account=a, contract=contract, position=1, avgCost=10)
                    for a in ("A", "B")]
        def reqTickers(self, *contracts):
            return []
        def openTrades(self):
            return []
        def reqCompletedOrders(self, **kw):
            return []
        def disconnect(self):
            pass
    module.IB = IB
    monkeypatch.setitem(sys.modules, "ib_insync", module)
    broker = IBKRBroker("mine", {"account_id": "A"})
    assert broker.connect()
    assert broker.account_info().total_assets == 100
    assert broker.account_info().account_id == "A"
    positions = broker.positions()
    assert len(positions) == 1
    assert positions[0].current_price is None
    assert positions[0].pnl is None
    assert broker.orders() == []
    with pytest.raises(RuntimeError):
        IBKRBroker("mine", {"account_id": "UNAUTHORIZED"}).connect()


def test_futu_queries_pass_account_selection():
    from arthera_finance.brokers.cn.futu_broker import FutuBroker
    calls = []
    class Frame:
        iloc = [{"total_assets": 100}]
        def iterrows(self):
            return iter([])
        def head(self, limit):
            return self
    class Context:
        def query(self, **kwargs):
            assert kwargs["acc_id"] == 123456
            calls.append(kwargs)
            return 0, Frame()
        accinfo_query = position_list_query = order_list_query = query
    broker = FutuBroker("mine", {"account_id": "123456"})
    broker._connected = True
    broker._ft = SimpleNamespace(RET_OK=0)
    broker._trd_ctx = Context()
    broker._trd_env_val = "REAL"
    assert broker.account_info().account_id == "123456"
    assert broker.positions() == [] and broker.orders() == []
    assert len(calls) == 3


def test_alpaca_oauth_token_goes_to_sdk_not_model(monkeypatch):
    from arthera_finance.brokers.intl.alpaca_broker import AlpacaBroker
    module = ModuleType("alpaca.trading.client")
    class Client:
        def __init__(self, key, secret, **kwargs):
            assert key is None and secret is None
            assert kwargs == {"oauth_token": "private-oauth", "paper": True}
        def get_account(self):
            return SimpleNamespace(account_number="12345678", equity="100", cash="100")
    module.TradingClient = Client
    monkeypatch.setitem(sys.modules, "alpaca", ModuleType("alpaca"))
    monkeypatch.setitem(sys.modules, "alpaca.trading", ModuleType("alpaca.trading"))
    monkeypatch.setitem(sys.modules, "alpaca.trading.client", module)
    adapter = AlpacaBroker("mine", {"access_token": "private-oauth"})
    assert adapter.connect() and adapter.account_info().cash == 100


def test_actual_worker_paper_query_and_host_revocation(profiles):
    profiles([{"id": "mine", "type": "paper", "starting_cash": 1234}])
    result = worker.call("mine", "account")
    assert result["success"] and result["data"]["cash"] == 1234
    assert result["simulation"] is True
    profiles([])
    assert worker.call("mine", "account")["error_code"] == "connection_unavailable"


def test_worker_gets_only_selected_connection_credentials(profiles, monkeypatch):
    profiles([{"id": "a", "type": "alpaca", "credentials": {"access_token": "TOKEN_FOR_A"}},
              {"id": "b", "type": "alpaca", "credentials": {"access_token": "TOKEN_FOR_B"}}])
    monkeypatch.setenv("TOKEN_FOR_A", "selected")
    monkeypatch.setenv("TOKEN_FOR_B", "not-selected")
    def run(*args, **kwargs):
        assert kwargs["env"]["TOKEN_FOR_A"] == "selected"
        assert "TOKEN_FOR_B" not in kwargs["env"]
        assert kwargs["timeout"] == 30
        return SimpleNamespace(returncode=0, stdout=b'{"success":false,"error_code":"test"}')
    monkeypatch.setattr(worker.subprocess, "run", run)
    assert worker.call("a", "account")["success"] is False


def test_worker_deadline_is_a_failed_query(profiles, monkeypatch):
    profiles([{"id": "mine", "type": "paper"}])
    def timeout(*args, **kwargs):
        raise worker.subprocess.TimeoutExpired("broker-worker", 30)
    monkeypatch.setattr(worker.subprocess, "run", timeout)
    assert worker.call("mine", "account")["error_code"] == "broker_timeout"
