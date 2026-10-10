from collections import namedtuple

import MetaTrader5 as mt5
import pytest

from mt5_handler import daily_limit_reason, server_day_start, sum_trade_profit

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


@pytest.mark.parametrize("profit,blocked", [
    (0, False),
    (150, False),       # target is "above 150"
    (150.01, True),
    (-499.99, False),
    (-500, True),       # loss limit is "reached 500"
    (-750, True),
])
def test_daily_limit_reason(profit, blocked):
    assert (daily_limit_reason(profit, target=150, max_loss=500) is not None) == blocked


def test_daily_limits_can_be_turned_off():
    assert daily_limit_reason(1000, target=0, max_loss=500) is None
    assert daily_limit_reason(-1000, target=150, max_loss=0) is None


def test_sum_trade_profit_without_fee_field():
    OldDeal = namedtuple("OldDeal", "type profit commission swap")
    assert sum_trade_profit([OldDeal(mt5.DEAL_TYPE_SELL, 50.0, -1.0, 0.0)]) == 49.0
