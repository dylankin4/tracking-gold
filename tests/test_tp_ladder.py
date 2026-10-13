import pytest

from config import parse_tp_ladder
from mt5_handler import ladder_action, ladder_volumes

LADDER = [(5, 0.3), (10, 6), (15, 12), (20, None)]
STEP = MIN = 0.01


def action(is_buy, price, volume, sl, open_price=4150, entry_volume=0.16):
    return ladder_action(is_buy, open_price, price, volume, sl, entry_volume, LADDER, 0.5, STEP, MIN)


# --- volumes left after each step -------------------------------------------------------------

@pytest.mark.parametrize("entry_volume,expected", [
    (0.16, [0.08, 0.04, 0.02, 0.0]),
    (0.10, [0.05, 0.03, 0.02, 0.0]),   # 0.025 rounds down to 0.02 closed
    (0.04, [0.02, 0.01, 0.01, 0.0]),   # 0.01 can't be split any more
    (0.01, [0.01, 0.01, 0.01, 0.0]),   # never splittable: only the final close
])
def test_ladder_volumes(entry_volume, expected):
    assert ladder_volumes(entry_volume, 4, 0.5, STEP, MIN) == pytest.approx(expected)


# --- BUY 0.16 @ 4150, full walk up the ladder ---------------------------------------------------

def test_buy_below_first_step_does_nothing():
    assert action(True, 4154.9, 0.16, 4135) is None


def test_buy_step_1():
    step, lots, sl = action(True, 4155, 0.16, 4135)
    assert (step, sl) == (0, 4150.3) and lots == pytest.approx(0.08)


def test_buy_step_1_already_done():
    assert action(True, 4157, 0.08, 4150.3) is None


def test_buy_step_2():
    step, lots, sl = action(True, 4160, 0.08, 4150.3)
    assert (step, sl) == (1, 4156) and lots == pytest.approx(0.04)


def test_buy_step_3():
    step, lots, sl = action(True, 4165, 0.04, 4156)
    assert (step, sl) == (2, 4162) and lots == pytest.approx(0.02)


def test_buy_step_4_closes_the_rest():
    step, lots, sl = action(True, 4170, 0.02, 4162)
    assert (step, sl) == (3, None) and lots == pytest.approx(0.02)


def test_jump_over_steps_goes_straight_to_highest():
    # From untouched straight to +11: close down to step 2's volume and use step 2's SL
    step, lots, sl = action(True, 4161, 0.16, 4135)
    assert (step, sl) == (1, 4156) and lots == pytest.approx(0.12)


def test_closed_lots_done_but_sl_move_failed_retries_only_sl():
    assert action(True, 4155, 0.08, 4135) == (0, 0.0, 4150.3)


def test_sl_is_never_loosened():
    # SL already above step 1's level (moved by hand): close lots, leave the SL alone
    step, lots, sl = action(True, 4155, 0.16, 4152)
    assert sl is None and lots == pytest.approx(0.08)


def test_manually_trimmed_position_is_not_closed_further():
    # 0.05 left by hand is already below step 1's 0.08: only the SL moves
    assert action(True, 4155, 0.05, 4135) == (0, 0.0, 4150.3)


# --- SELL mirrors BUY ---------------------------------------------------------------------------

def test_sell_step_1():
    step, lots, sl = action(False, 4167, 0.16, 4185, open_price=4172)
    assert (step, sl) == (0, 4171.7) and lots == pytest.approx(0.08)


def test_sell_step_3():
    step, lots, sl = action(False, 4157, 0.04, 4166, open_price=4172)
    assert (step, sl) == (2, 4160) and lots == pytest.approx(0.02)


def test_sell_in_loss_does_nothing():
    assert action(False, 4175, 0.16, 4185, open_price=4172) is None


# --- config parsing -----------------------------------------------------------------------------

def test_parse_default_ladder():
    assert parse_tp_ladder("5:0.3,10:6,15:12,20") == LADDER
    assert parse_tp_ladder(" 5:0.3, 20 ") == [(5, 0.3), (20, None)]


@pytest.mark.parametrize("spec", [
    "5:0.3,10:6",        # last step must close everything (no SL)
    "10:6,5:0.3,20",     # levels must increase
    "5,20",              # middle step needs an SL
    "5:6,20",            # SL must be below its level
    "abc",
])
def test_parse_rejects_bad_ladder(spec):
    with pytest.raises(ValueError):
        parse_tp_ladder(spec)
