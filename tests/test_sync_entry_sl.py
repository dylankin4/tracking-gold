"""Entry 1's SL follows entry 2's once entry 2 has taken profit on the TP ladder."""
from types import SimpleNamespace

import MetaTrader5 as real_mt5
import pytest

import mt5_handler
from mt5_handler import synced_entry1_sl

BUY, SELL = real_mt5.POSITION_TYPE_BUY, real_mt5.POSITION_TYPE_SELL


@pytest.mark.parametrize("is_buy,e1_sl,e2_sl,expected", [
    (True, 4148, 4160.3, 4160.3),    # entry 2 tighter: follow it
    (True, 4166, 4160.3, None),      # entry 1 already tighter: never loosen
    (True, 0, 4160.3, 4160.3),       # entry 1 without SL
    (True, 4148, 0, None),           # entry 2 without SL: nothing to copy
    (False, 4185, 4175.7, 4175.7),   # SELL: lower is tighter
    (False, 4170, 4175.7, None),
])
def test_synced_entry1_sl(is_buy, e1_sl, e2_sl, expected):
    assert synced_entry1_sl(is_buy, e1_sl, e2_sl) == expected


class FakeMT5:
    def __getattr__(self, name):
        return getattr(real_mt5, name)

    def __init__(self, positions):
        self.positions = {p.ticket: p for p in positions}
        self.entry_volumes = {p.ticket: p.volume for p in positions}
        self.price = 0.0

    def positions_get(self, symbol):
        return tuple(p for p in self.positions.values() if p.volume > 0)

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(bid=self.price, ask=self.price)

    def symbol_info(self, symbol):
        return SimpleNamespace(volume_step=0.01, volume_min=0.01, digits=2, filling_mode=1)

    def history_deals_get(self, position):
        return (SimpleNamespace(entry=real_mt5.DEAL_ENTRY_IN, volume=self.entry_volumes[position]),)

    def order_send(self, request):
        pos = self.positions[request["position"]]
        if request["action"] == real_mt5.TRADE_ACTION_DEAL:
            pos.volume = round(pos.volume - request["volume"], 8)
        else:
            pos.sl = request["sl"]
        return SimpleNamespace(retcode=real_mt5.TRADE_RETCODE_DONE, price=self.price, comment="done")

    def last_error(self):
        return (0, "ok")


def position(ticket, comment, type_, price_open, sl, volume=0.16):
    tp = price_open + 20 if type_ == BUY else price_open - 20
    return SimpleNamespace(ticket=ticket, comment=comment, type=type_, price_open=price_open, sl=sl, tp=tp,
                           volume=volume, magic=mt5_handler.MAGIC_NUMBER)


@pytest.fixture
def fake(monkeypatch):
    def make(*positions):
        f = FakeMT5(positions)
        monkeypatch.setattr(mt5_handler, "mt5", f)
        monkeypatch.setattr(mt5_handler, "TP_LADDER", [(5, 0.3), (10, 6), (15, 12), (20, None)])
        monkeypatch.setattr(mt5_handler, "TP_LADDER_CLOSE_FRACTION", 0.5)
        monkeypatch.setattr(mt5_handler, "SYNC_ENTRY1_SL", True)
        mt5_handler._entry_volumes.clear()
        mt5_handler._ladder_failures.clear()
        return f
    return make


def tick(f, price):
    f.price = price
    mt5_handler.manage_profitable_positions()
    mt5_handler.sync_entry1_sl_to_entry2()


def test_buy_signal_both_entries(fake):
    # BUY GOLD @ 4163-4160, SL 4148
    f = fake(position(1, "sig1027_e1", BUY, 4163, 4148), position(2, "sig1027_e2", BUY, 4160, 4148))
    e1, e2 = f.positions[1], f.positions[2]

    tick(f, 4164)                         # entry 2 +4, entry 1 +1: nothing yet
    assert (e1.sl, e2.sl) == (4148, 4148)

    tick(f, 4165)                         # entry 2 +5: closes half, SL 4160.3 -> entry 1 follows
    assert e2.volume == pytest.approx(0.08) and e2.sl == 4160.3
    assert e1.volume == pytest.approx(0.16) and e1.sl == 4160.3

    tick(f, 4170)                         # entry 2 +10 (SL 4166); entry 1 +7 (own step 1, SL 4163.3)
    assert e2.sl == 4166 and e2.volume == pytest.approx(0.04)
    assert e1.volume == pytest.approx(0.08)
    assert e1.sl == 4166                  # entry 2's SL is tighter, so entry 1 takes it


def test_sell_signal_both_entries(fake):
    # SELL GOLD 4172-76, SL 4185: entry 2 (4176) is the better price
    f = fake(position(1, "sig1027_e1", SELL, 4172, 4185), position(2, "sig1027_e2", SELL, 4176, 4185))
    tick(f, 4171)                         # entry 2 +5
    assert f.positions[2].sl == 4175.7
    assert f.positions[1].sl == 4175.7


def test_entry_1_alone_is_not_touched(fake):
    f = fake(position(1, "sig1027_e1", BUY, 4163, 4148))
    tick(f, 4164)
    assert f.positions[1].sl == 4148


def test_entry_2_sl_moved_by_hand_without_profit_is_not_copied(fake):
    # Entry 2 still full size: its SL wasn't moved by the ladder, so entry 1 is left alone
    f = fake(position(1, "sig1027_e1", BUY, 4163, 4148), position(2, "sig1027_e2", BUY, 4160, 4155))
    tick(f, 4162)
    assert f.positions[1].sl == 4148


def test_different_signals_are_not_mixed(fake):
    f = fake(position(1, "sig1026_e1", BUY, 4163, 4148), position(2, "sig1027_e2", BUY, 4160, 4148))
    tick(f, 4165)
    assert f.positions[2].sl == 4160.3
    assert f.positions[1].sl == 4148


def test_can_be_turned_off(fake, monkeypatch):
    f = fake(position(1, "sig1027_e1", BUY, 4163, 4148), position(2, "sig1027_e2", BUY, 4160, 4148))
    monkeypatch.setattr(mt5_handler, "SYNC_ENTRY1_SL", False)
    tick(f, 4165)
    assert f.positions[2].sl == 4160.3
    assert f.positions[1].sl == 4148
