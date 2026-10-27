"""SIGNAL_TP_MODE=signal: exit on the signal's own TP1/TP2/TP3."""
from types import SimpleNamespace

import MetaTrader5 as real_mt5
import pytest

import mt5_handler
import tp_store
from message_parser import parse_signal
from mt5_handler import signal_tp_ladder

SELL_TEXT = ("🔴 SELL SIGNAL — XAUUSD.c (M1)\n\n✈️ Entry: 4011.13\n🎯 TP1: 4006.31\n🎯 TP2: 4001.48\n"
             "🎯 TP3: 3996.66\n☠️ SL: 4015.95")
TPS = [4006.31, 4001.48, 3996.66]


def approx_ladder(ladder):
    return [(pytest.approx(d), None if sl is None else pytest.approx(sl)) for d, sl in ladder]


def test_ladder_from_signal_tps():
    # TP1 4.82 away -> SL at entry (0); TP2 9.65 away -> SL at TP1 (4.82); TP3 closes everything
    assert signal_tp_ladder(False, 4011.13, TPS) == approx_ladder([(4.82, 0.0), (9.65, 4.82), (14.47, None)])
    assert signal_tp_ladder(True, 4017.35, [4026.16, 4034.96, 4043.77]) == approx_ladder(
        [(8.81, 0.0), (17.61, 8.81), (26.42, None)])


@pytest.mark.parametrize("is_buy,open_price,tps,expected", [
    (True, 4000, [4005], [(5, None)]),             # a single TP closes everything
    (True, 4000, [4005, 4010], [(5, 0.0), (10, None)]),
])
def test_ladder_with_fewer_tps(is_buy, open_price, tps, expected):
    assert signal_tp_ladder(is_buy, open_price, tps) == expected


@pytest.mark.parametrize("is_buy,tps", [
    (True, []),                       # no TPs
    (True, [3995, 4010]),             # TP1 on the wrong side
    (True, [4010, 4005]),             # not increasing
    (False, [4005, 4010]),            # SELL TPs going up
])
def test_unusable_tps(is_buy, tps):
    assert signal_tp_ladder(is_buy, 4000, tps) is None


class FakeMT5:
    def __getattr__(self, name):
        return getattr(real_mt5, name)

    def __init__(self, bid, ask):
        self.bid, self.ask = bid, ask
        self.positions = {}
        self.entry_volumes = {}
        self.requests = []
        self.next_ticket = 100

    # --- what place_orders needs
    def symbol_select(self, symbol, enable):
        return True

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(bid=self.bid, ask=self.ask, time=0)

    def symbol_info(self, symbol):
        return SimpleNamespace(volume_step=0.01, volume_min=0.01, digits=2, filling_mode=1)

    def order_send(self, request):
        self.requests.append(request)
        action = request["action"]
        if action == real_mt5.TRADE_ACTION_DEAL and "position" in request:
            pos = self.positions[request["position"]]
            pos.volume = round(pos.volume - request["volume"], 8)
        elif action == real_mt5.TRADE_ACTION_SLTP:
            self.positions[request["position"]].sl = request["sl"]
        elif action == real_mt5.TRADE_ACTION_DEAL:
            # market entry: becomes a position right away
            self.next_ticket += 1
            self.positions[self.next_ticket] = SimpleNamespace(
                ticket=self.next_ticket, comment=request["comment"], magic=request["magic"],
                type=real_mt5.POSITION_TYPE_SELL if request["type"] == real_mt5.ORDER_TYPE_SELL else real_mt5.POSITION_TYPE_BUY,
                price_open=request["price"], sl=request["sl"], tp=request["tp"], volume=request["volume"],
            )
            self.entry_volumes[self.next_ticket] = request["volume"]
            return SimpleNamespace(retcode=real_mt5.TRADE_RETCODE_DONE, order=self.next_ticket,
                                   price=request["price"], comment="done")
        self.next_ticket += 1  # pending order placed (or SL/partial close done)
        return SimpleNamespace(retcode=real_mt5.TRADE_RETCODE_DONE, order=self.next_ticket, price=self.bid,
                               comment="done")

    # --- what the ladder needs
    def positions_get(self, symbol):
        return tuple(p for p in self.positions.values() if p.volume > 0)

    def history_deals_get(self, position):
        return (SimpleNamespace(entry=real_mt5.DEAL_ENTRY_IN, volume=self.entry_volumes[position]),)

    def last_error(self):
        return (0, "ok")


@pytest.fixture
def fake(monkeypatch, tmp_path):
    def make(bid, ask):
        f = FakeMT5(bid, ask)
        monkeypatch.setattr(mt5_handler, "mt5", f)
        monkeypatch.setattr(mt5_handler, "TP_MODE", "signal")
        monkeypatch.setattr(mt5_handler, "TP_LADDER_CLOSE_FRACTION", 0.5)
        monkeypatch.setattr(mt5_handler, "LOT_SIZE", 0.16)
        monkeypatch.setattr(mt5_handler, "MIN_SL_DISTANCE", 2)
        monkeypatch.setattr(mt5_handler, "DAILY_PROFIT_TARGET", 0)
        monkeypatch.setattr(mt5_handler, "DAILY_MAX_LOSS", 0)
        monkeypatch.setattr(tp_store, "PATH", str(tmp_path / "signal_tps.json"))
        monkeypatch.setattr(tp_store, "_data", None)
        mt5_handler._entry_volumes.clear()
        mt5_handler._ladder_failures.clear()
        return f
    return make


def test_sell_signal_walks_tp1_tp2_tp3(fake):
    # Price is at the entry when the signal arrives: a market SELL at 4011.13
    f = fake(bid=4011.13, ask=4011.30)
    assert mt5_handler.place_orders(parse_signal(SELL_TEXT))
    entry = f.requests[0]
    assert entry["tp"] == 3996.66 and entry["sl"] == 4015.95   # broker TP = TP3
    pos = next(iter(f.positions.values()))

    # The TPs survive a restart: they come back from signal_tps.json
    tp_store._data = None
    assert tp_store.get(pos.comment.rsplit("_e", 1)[0]) == TPS

    def at(price):
        f.bid = f.ask = price
        mt5_handler.manage_profitable_positions()

    at(4008.00)                                   # not at TP1 yet
    assert (pos.volume, pos.sl) == (0.16, 4015.95)
    at(4006.31)                                   # TP1: close half, SL -> entry
    assert pos.volume == pytest.approx(0.08) and pos.sl == 4011.13
    at(4001.48)                                   # TP2: close half of the rest, SL -> TP1
    assert pos.volume == pytest.approx(0.04) and pos.sl == 4006.31
    at(3996.66)                                   # TP3: close everything
    assert pos.volume == pytest.approx(0)


def test_sell_below_entry_waits_with_a_limit_order(fake):
    # Price already dropped under the SELL entry: a SELL LIMIT waits at the entry, TP = TP3
    f = fake(bid=4005.0, ask=4005.2)
    assert mt5_handler.place_orders(parse_signal(SELL_TEXT))
    order = f.requests[0]
    assert order["action"] == real_mt5.TRADE_ACTION_PENDING and order["type"] == real_mt5.ORDER_TYPE_SELL_LIMIT
    assert (order["price"], order["sl"], order["tp"]) == (4011.13, 4015.95, 3996.66)


def test_market_entry_slippage_keeps_signal_tp(fake, monkeypatch):
    f = fake(bid=4012.00, ask=4012.20)            # above the SELL entry: market SELL
    real_send = f.order_send

    def slipped(request):
        result = real_send(request)
        if request["action"] == real_mt5.TRADE_ACTION_DEAL:
            result.price = 4011.80                # filled a bit lower than requested
        return result
    monkeypatch.setattr(f, "order_send", slipped)
    mt5_handler.place_orders(parse_signal(SELL_TEXT))
    # TP3 is a fixed price, so no follow-up request to move the TP after slippage
    assert [r["action"] for r in f.requests] == [real_mt5.TRADE_ACTION_DEAL]
    assert f.requests[0]["tp"] == 3996.66


def test_signal_without_tps_falls_back_to_fixed_ladder(fake, monkeypatch):
    monkeypatch.setattr(mt5_handler, "FINAL_TP_DISTANCE", 20)
    f = fake(bid=4011.13, ask=4011.30)
    mt5_handler.place_orders(parse_signal("🔴 SELL SIGNAL — XAUUSD.c (M1)\n✈️ Entry: 4011.13\n☠️ SL: 4015.95"))
    assert f.requests[0]["tp"] == pytest.approx(4011.13 - 20)
