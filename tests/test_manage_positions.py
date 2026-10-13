"""Walks a fake MT5 position up the TP ladder and checks the requests the bot sends."""
from types import SimpleNamespace

import MetaTrader5 as real_mt5
import pytest

import mt5_handler


class FakeMT5:
    """Just enough of the MetaTrader5 module for manage_profitable_positions()."""

    def __getattr__(self, name):  # constants (TRADE_ACTION_DEAL, POSITION_TYPE_BUY, ...)
        return getattr(real_mt5, name)

    def __init__(self, is_buy=True, open_price=4150.0, volume=0.16, sl=4135.0):
        self.pos = SimpleNamespace(
            ticket=1, magic=mt5_handler.MAGIC_NUMBER, volume=volume, price_open=open_price, sl=sl,
            tp=open_price + 20 if is_buy else open_price - 20,
            type=real_mt5.POSITION_TYPE_BUY if is_buy else real_mt5.POSITION_TYPE_SELL,
        )
        self.entry_volume = volume
        self.price = open_price
        self.requests = []

    def positions_get(self, symbol):
        return (self.pos,) if self.pos.volume > 0 else ()

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(bid=self.price, ask=self.price)

    def symbol_info(self, symbol):
        return SimpleNamespace(volume_step=0.01, volume_min=0.01, digits=2, filling_mode=1)

    def history_deals_get(self, position):
        return (SimpleNamespace(entry=real_mt5.DEAL_ENTRY_IN, volume=self.entry_volume),)

    def order_send(self, request):
        self.requests.append(request)
        if request["action"] == real_mt5.TRADE_ACTION_DEAL:
            self.pos.volume = round(self.pos.volume - request["volume"], 8)
        elif request["action"] == real_mt5.TRADE_ACTION_SLTP:
            self.pos.sl = request["sl"]
        return SimpleNamespace(retcode=real_mt5.TRADE_RETCODE_DONE, price=self.price, comment="done")

    def last_error(self):
        return (0, "ok")


@pytest.fixture
def fake(monkeypatch):
    def make(**kwargs):
        f = FakeMT5(**kwargs)
        monkeypatch.setattr(mt5_handler, "mt5", f)
        monkeypatch.setattr(mt5_handler, "TP_LADDER", [(5, 0.3), (10, 6), (15, 12), (20, None)])
        monkeypatch.setattr(mt5_handler, "TP_LADDER_CLOSE_FRACTION", 0.5)
        mt5_handler._entry_volumes.clear()
        mt5_handler._ladder_failures.clear()
        return f
    return make


def summary(f):
    """(closed lots, new SL) per request, in order."""
    return [
        ("close", r["volume"]) if r["action"] == real_mt5.TRADE_ACTION_DEAL else ("sl", r["sl"])
        for r in f.requests
    ]


def test_buy_walks_the_whole_ladder(fake):
    f = fake(is_buy=True)
    for price in (4154, 4155, 4156, 4160, 4165, 4170):
        f.price = price
        mt5_handler.manage_profitable_positions()
    assert summary(f) == [
        ("close", 0.08), ("sl", 4150.3),   # +5
        ("close", 0.04), ("sl", 4156.0),   # +10
        ("close", 0.02), ("sl", 4162.0),   # +15
        ("close", 0.02),                   # +20: the rest
    ]
    assert f.pos.volume == 0


def test_sell_jump_straight_to_step_3(fake):
    f = fake(is_buy=False, open_price=4172.0, sl=4185.0)
    f.price = 4156.5  # +15.5 in one move
    mt5_handler.manage_profitable_positions()
    assert summary(f) == [("close", 0.14), ("sl", 4160.0)]


def test_repeating_a_tick_does_nothing_more(fake):
    f = fake(is_buy=True)
    f.price = 4155
    for _ in range(5):
        mt5_handler.manage_profitable_positions()
    assert summary(f) == [("close", 0.08), ("sl", 4150.3)]
