"""Lazy adapter lookup without loading user accounts or optional SDKs."""
from importlib import import_module

ADAPTERS = {
    "paper": ("paper_broker", "PaperBroker"),
    "xtquant": ("cn.xtquant_broker", "XTQuantBroker"),
    "easytrader": ("cn.easytrader_broker", "EasyTraderBroker"),
    "futu": ("cn.futu_broker", "FutuBroker"),
    "tiger": ("cn.tiger_broker", "TigerBroker"),
    "longbridge": ("cn.longbridge_broker", "LongbridgeBroker"),
    "ibkr": ("intl.ibkr_broker", "IBKRBroker"),
    "alpaca": ("intl.alpaca_broker", "AlpacaBroker"),
    "webull": ("intl.webull_broker", "WebullBroker"),
    "trading212": ("intl.trading212_broker", "Trading212Broker"),
}


def get_broker_class(kind):
    module, name = ADAPTERS[kind]
    return getattr(import_module("." + module, __package__), name)
