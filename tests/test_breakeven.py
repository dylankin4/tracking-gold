import pytest

from mt5_handler import breakeven_sl, partial_close_volume

TRIGGER, OFFSET = 5, 0.5


@pytest.mark.parametrize("is_buy,open_price,sl,price,expected", [
    # BUY: closes at the bid
    (True, 4150, 4140, 4154.9, None),     # 4.9 in profit: too early
    (True, 4150, 4140, 4155, 4150.5),     # exactly 5: move
    (True, 4150, 4140, 4160, 4150.5),     # well past 5: move
    (True, 4150, 0, 4155, 4150.5),        # no SL yet: move
    (True, 4150, 4150.5, 4156, None),     # already at breakeven
    (True, 4150, 4153, 4156, None),       # SL already tighter: never loosen it
    (True, 4150, 4140, 4145, None),       # in loss
    # SELL: closes at the ask
    (False, 4172, 4185, 4167.1, None),    # 4.9 in profit: too early
    (False, 4172, 4185, 4167, 4171.5),    # exactly 5: move
    (False, 4172, 0, 4160, 4171.5),       # no SL yet: move
    (False, 4172, 4171.5, 4166, None),    # already at breakeven
    (False, 4172, 4169, 4166, None),      # SL already tighter: never loosen it
    (False, 4172, 4185, 4178, None),      # in loss
])
def test_breakeven_sl(is_buy, open_price, sl, price, expected):
    assert breakeven_sl(is_buy, open_price, sl, price, TRIGGER, OFFSET) == expected


@pytest.mark.parametrize("volume,entry_volume,fraction,expected", [
    (0.1, 0.1, 0.5, 0.05),     # half of 0.1
    (0.2, 0.2, 0.5, 0.1),
    (0.15, 0.15, 0.5, 0.07),   # rounded down to the lot step
    (0.05, 0.1, 0.5, 0.0),     # already partially closed
    (0.06, 0.1, 0.5, 0.0),     # partly closed by hand: don't close again
    (0.01, 0.01, 0.5, 0.0),    # too small to split
    (0.02, 0.02, 0.5, 0.01),   # smallest splittable size
    (0.1, 0.1, 0.0, 0.0),      # feature off
])
def test_partial_close_volume(volume, entry_volume, fraction, expected):
    assert partial_close_volume(volume, entry_volume, fraction, step=0.01, min_volume=0.01) == pytest.approx(expected)


def test_trigger_zero_disables_breakeven():
    assert breakeven_sl(True, 4150, 4140, 4200, 0, OFFSET) is None
