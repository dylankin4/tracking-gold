from collections import namedtuple

import MetaTrader5 as mt5

from mt5_handler import server_day_start, sum_trade_profit

Deal = namedtuple("Deal", "type profit commission swap fee")


def test_server_day_start():
    # 2026-10-09 23:59:59 and 2026-10-09 00:00:00 (server clock) -> 2026-10-09 00:00:00
    assert server_day_start(1791590399) == 1791504000
    assert server_day_start(1791504000) == 1791504000


def test_sum_trade_profit_counts_costs_and_ignores_balance_deals():
    deals = [
        Deal(mt5.DEAL_TYPE_BUY, 0.0, -0.5, 0.0, 0.0),        # entry: commission only
        Deal(mt5.DEAL_TYPE_SELL, 120.0, -0.5, -1.2, 0.0),    # exit in profit, with swap
        Deal(mt5.DEAL_TYPE_SELL, -40.0, -0.5, 0.0, -0.3),    # exit in loss, with fee
        Deal(mt5.DEAL_TYPE_BALANCE, 5000.0, 0.0, 0.0, 0.0),  # deposit: not trading profit
    ]
    assert sum_trade_profit(deals) == 120.0 - 40.0 - 0.5 * 3 - 1.2 - 0.3


def test_sum_trade_profit_without_fee_field():
    OldDeal = namedtuple("OldDeal", "type profit commission swap")
    assert sum_trade_profit([OldDeal(mt5.DEAL_TYPE_SELL, 50.0, -1.0, 0.0)]) == 49.0
