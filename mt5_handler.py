import logging
import math
import time
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5

import mt5_terminal
import tp_store
from config import (
    DAILY_MAX_LOSS,
    DAILY_PROFIT_TARGET,
    FINAL_TP_DISTANCE,
    LOT_SIZE,
    MAGIC_NUMBER,
    MARKET_DEVIATION_POINTS,
    MIN_SL_DISTANCE,
    MT5_LOGIN,
    MT5_PASSWORD,
    MT5_PATH,
    MT5_SERVER,
    ORDER_EXPIRY_HOURS,
    SYMBOL,
    SYNC_ENTRY1_SL,
    TP_LADDER,
    TP_LADDER_CLOSE_FRACTION,
    TP_MODE,
)
from message_parser import TradeSignal

logger = logging.getLogger(__name__)


def connect() -> bool:
    """
    Connects to MT5, launching the terminal if it isn't running, and makes sure
    Algo Trading is switched on so orders can be sent.
    """
    # initialize() starts the terminal itself if it isn't open yet
    kwargs = {"login": MT5_LOGIN, "password": MT5_PASSWORD, "server": MT5_SERVER, "timeout": 60_000}
    if MT5_PATH:
        kwargs["path"] = MT5_PATH
    if not mt5.initialize(**kwargs):
        logger.error("mt5.initialize() failed: %s", mt5.last_error())
        return False

    info = mt5.account_info()
    if info is None or info.login != MT5_LOGIN:
        if not mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
            logger.error("mt5.login() failed: %s", mt5.last_error())
            mt5.shutdown()
            return False
        info = mt5.account_info()
    logger.info("Connected to MT5 — account %s, balance %.2f %s", info.login, info.balance, info.currency)
    if not info.trade_expert:
        logger.warning("The broker does not allow automated trading on account %s", info.login)

    return _ensure_algo_trading()


def _ensure_algo_trading() -> bool:
    terminal = mt5.terminal_info()
    if terminal.trade_allowed:
        return True
    if not mt5_terminal.enable_algo_trading(terminal.path, lambda: mt5.terminal_info().trade_allowed):
        logger.error("Algo Trading is disabled in MT5 and could not be enabled — turn it on manually (Ctrl+E)")
        return False
    logger.info("Algo Trading enabled")
    return True


def ensure_connected() -> bool:
    """Reuses the open MT5 connection if it's still alive, otherwise (re)connects."""
    info = mt5.account_info()
    if mt5.terminal_info() is None or info is None or info.login != MT5_LOGIN:
        return connect()
    return _ensure_algo_trading()


def disconnect():
    mt5.shutdown()


def _tp_for(price: float, direction: str, distance: float) -> float:
    # Fixed TP distance from the entry price (signal's own TP levels are ignored)
    return price + distance if direction == "BUY" else price - distance


def _market_filling_mode() -> int:
    """Picks a filling mode the broker allows for market orders on SYMBOL."""
    info = mt5.symbol_info(SYMBOL)
    if info is not None:
        if info.filling_mode & 1:   # SYMBOL_FILLING_FOK
            return mt5.ORDER_FILLING_FOK
        if info.filling_mode & 2:   # SYMBOL_FILLING_IOC
            return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def _place_limit(signal: TradeSignal, price: float, tp_of, expiry: datetime, comment: str) -> int | None:
    """`tp_of(entry_price)` gives the broker TP (the last step of the TP ladder)."""
    order_type = mt5.ORDER_TYPE_BUY_LIMIT if signal.direction == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT
    tp = tp_of(price)
    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": SYMBOL,
        "volume": LOT_SIZE,
        "type": order_type,
        "price": price,
        "sl": signal.stoploss,
        "tp": tp,
        "magic": MAGIC_NUMBER,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_SPECIFIED,
        "expiration": int(expiry.timestamp()),
        "type_filling": mt5.ORDER_FILLING_RETURN,
    }

    result = mt5.order_send(request)
    if result is None:
        logger.error("order_send returned None for price %.2f: %s", price, mt5.last_error())
        return None
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        logger.error(
            "Order failed for price %.2f — retcode=%s comment=%s",
            price, result.retcode, result.comment,
        )
        return None

    logger.info(
        "Order placed: %s LIMIT %s @ %.2f  SL=%.2f  TP=%.2f  ticket=%s",
        signal.direction, SYMBOL, price, signal.stoploss, tp, result.order,
    )
    return result.order


def _place_market(signal: TradeSignal, market_price: float, tp_of, comment: str) -> int | None:
    order_type = mt5.ORDER_TYPE_BUY if signal.direction == "BUY" else mt5.ORDER_TYPE_SELL
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": LOT_SIZE,
        "type": order_type,
        "price": market_price,
        "sl": signal.stoploss,
        "tp": tp_of(market_price),
        "deviation": MARKET_DEVIATION_POINTS,
        "magic": MAGIC_NUMBER,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _market_filling_mode(),
    }

    result = mt5.order_send(request)
    if result is None:
        logger.error("Market order_send returned None: %s", mt5.last_error())
        return None
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        logger.error("Market order failed — retcode=%s comment=%s", result.retcode, result.comment)
        return None

    # Re-anchor TP on the actual fill price if it slipped from the requested price
    # (a TP taken from the signal is a fixed price, so it stays where it is)
    fill_price = result.price or market_price
    tp = tp_of(fill_price)
    if abs(tp - request["tp"]) > 1e-9:
        modify = mt5.order_send({
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": SYMBOL,
            "position": result.order,
            "sl": signal.stoploss,
            "tp": tp,
        })
        if modify is None or modify.retcode != mt5.TRADE_RETCODE_DONE:
            logger.warning(
                "Could not move TP to %.2f after slippage (kept %.2f): %s",
                tp, request["tp"], modify.comment if modify else mt5.last_error(),
            )
            tp = request["tp"]

    logger.info(
        "Order placed: %s MARKET %s @ %.2f  SL=%.2f  TP=%.2f  ticket=%s",
        signal.direction, SYMBOL, fill_price, signal.stoploss, tp, result.order,
    )
    return result.order


def _comment(group: str, entry_index: int) -> str:
    # e.g. "sig1001223045_e2" — links all orders of one signal (MT5 comments max 31 chars)
    return f"sig{group}_e{entry_index + 1}"


def server_day_start(server_time: int) -> int:
    """
    Midnight of the broker's trading day. MT5 timestamps (tick.time, deal.time) are the
    broker's server clock written as if it were UTC, so the day starts at a multiple of 86400.
    """
    return server_time - server_time % 86400


def sum_trade_profit(deals) -> float:
    """Net result of trading deals (profit + commission + swap + fee); deposits/withdrawals are ignored."""
    return sum(
        d.profit + d.commission + d.swap + getattr(d, "fee", 0.0)
        for d in deals
        if d.type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL)
    )


def daily_closed_profit(server_now: int) -> float | None:
    """Closed profit of the whole account since the start of the broker's day, or None on error."""
    start = datetime.fromtimestamp(server_day_start(server_now), timezone.utc)
    # End is generous: history is filtered on server time, which may be ahead of real UTC
    deals = mt5.history_deals_get(start, datetime.now(timezone.utc) + timedelta(days=2))
    if deals is None:
        logger.error("history_deals_get failed: %s", mt5.last_error())
        return None
    return sum_trade_profit(deals)


def daily_limit_reason(profit: float, target: float, max_loss: float) -> str | None:
    """Why no new trades may be opened today, or None. A limit of 0 is off."""
    if target > 0 and profit > target:
        return f"Today's closed profit {profit:.2f} is above the daily target {target:.2f}"
    if max_loss > 0 and profit <= -max_loss:
        return f"Today's closed loss {-profit:.2f} has reached the daily max loss {max_loss:.2f}"
    return None


def place_orders(signal: TradeSignal) -> list[int]:
    """
    Places one limit order per entry price that is still valid. If price has already
    moved past one or more entries, opens a single market order instead — but only if
    the current price is still at least MIN_SL_DISTANCE away from the SL.
    Returns list of placed ticket IDs.
    """
    if not mt5.symbol_select(SYMBOL, True):
        logger.error("Could not select symbol %s: %s", SYMBOL, mt5.last_error())
        return []
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        logger.error("No tick data for %s: %s", SYMBOL, mt5.last_error())
        return []

    if DAILY_PROFIT_TARGET > 0 or DAILY_MAX_LOSS > 0:
        profit = daily_closed_profit(tick.time)
        if profit is None:
            logger.error("Could not read today's trade history — skipping signal to be safe")
            return []
        reason = daily_limit_reason(profit, DAILY_PROFIT_TARGET, DAILY_MAX_LOSS)
        if reason:
            logger.warning("%s — not opening new trades", reason)
            return []
        logger.info(
            "Today's closed profit %.2f (daily target %.2f, max loss %.2f)",
            profit, DAILY_PROFIT_TARGET, DAILY_MAX_LOSS,
        )

    is_buy = signal.direction == "BUY"
    market_price = tick.ask if is_buy else tick.bid
    expiry = datetime.now(timezone.utc) + timedelta(hours=ORDER_EXPIRY_HOURS)
    group = datetime.now().strftime("%m%d%H%M%S")

    # The ladder follows the signal's own TP1..TPn (SIGNAL_TP_MODE=signal) when they make sense for
    # every entry; otherwise the fixed TP_LADDER. The last step is also the broker TP.
    signal_tps = None
    if TP_MODE == "signal":
        if all(signal_tp_ladder(is_buy, price, signal.take_profits) for price in signal.entry_prices):
            signal_tps = signal.take_profits
            tp_store.save(_group_key(_comment(group, 0)), signal_tps)
        else:
            logger.warning(
                "Signal TPs %s don't fit entries %s — using the fixed TP ladder for this signal",
                signal.take_profits, signal.entry_prices,
            )
    if signal_tps:
        def tp_of(price):
            return signal_tps[-1]
    else:
        def tp_of(price):
            return _tp_for(price, signal.direction, FINAL_TP_DISTANCE)

    # A BUY LIMIT must sit below the ask, a SELL LIMIT above the bid
    limit_entries, passed_entries = [], []
    for i, price in enumerate(signal.entry_prices):
        still_valid = price < market_price if is_buy else price > market_price
        (limit_entries if still_valid else passed_entries).append((i, price))
    passed_prices = [price for _, price in passed_entries]

    tickets = []
    for i, price in limit_entries:
        ticket = _place_limit(signal, price, tp_of, expiry, _comment(group, i))
        if ticket:
            tickets.append(ticket)

    if passed_entries:
        sl_distance = market_price - signal.stoploss if is_buy else signal.stoploss - market_price
        if sl_distance >= MIN_SL_DISTANCE:
            logger.info(
                "Price %.2f already past entries %s — entering at market (%.2f from SL)",
                market_price, passed_prices, sl_distance,
            )
            # The market order takes the place of the first passed entry
            first = passed_entries[0][0]
            ticket = _place_market(signal, market_price, tp_of, _comment(group, first))
            if ticket:
                tickets.append(ticket)
        else:
            logger.warning(
                "Price %.2f already past entries %s and only %.2f from SL (min %.2f) — skipping",
                market_price, passed_prices, sl_distance, MIN_SL_DISTANCE,
            )

    return tickets


# Retcodes worth retrying a market close on: the price moved before the order arrived
_RETRY_RETCODES = {mt5.TRADE_RETCODE_REQUOTE, mt5.TRADE_RETCODE_PRICE_CHANGED, mt5.TRADE_RETCODE_PRICE_OFF}


def _close_position(pos, volume: float | None = None, comment: str = "close cmd") -> bool:
    """Closes `volume` lots of the position at market (all of it when volume is None)."""
    is_buy = pos.type == mt5.POSITION_TYPE_BUY
    volume = pos.volume if volume is None else volume
    result = None
    for _ in range(3):
        tick = mt5.symbol_info_tick(SYMBOL)
        if tick is None:
            logger.error("No tick data to close position %s: %s", pos.ticket, mt5.last_error())
            return False
        result = mt5.order_send({
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": SYMBOL,
            "volume": volume,
            "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
            "position": pos.ticket,
            "price": tick.bid if is_buy else tick.ask,
            "deviation": MARKET_DEVIATION_POINTS,
            "magic": MAGIC_NUMBER,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": _market_filling_mode(),
        })
        if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(
                "Closed %s of %s lots of %s position %s (opened @ %.2f) @ %.2f",
                volume, pos.volume, "BUY" if is_buy else "SELL", pos.ticket, pos.price_open, result.price,
            )
            return True
        if result is None or result.retcode not in _RETRY_RETCODES:
            break
    logger.error(
        "Could not close %s lots of position %s: %s",
        volume, pos.ticket, f"retcode={result.retcode} {result.comment}" if result else mt5.last_error(),
    )
    return False


def close_all() -> tuple[list[int], list[int]]:
    """
    Closes every open position of ours on SYMBOL at market and cancels our pending orders,
    so a pending entry 2 can't fill after the trade was closed. Manual trades (other magic
    numbers) are left alone. Returns (closed position tickets, cancelled order tickets).
    """
    # Pending orders first, so none of them fills while the positions are being closed
    cancelled = []
    for order in mt5.orders_get(symbol=SYMBOL) or ():
        if order.magic != MAGIC_NUMBER:
            continue
        result = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": order.ticket})
        if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info("Cancelled pending order %s @ %.2f", order.ticket, order.price_open)
            cancelled.append(order.ticket)
        else:
            logger.error(
                "Could not cancel pending order %s: %s",
                order.ticket, result.comment if result else mt5.last_error(),
            )

    closed = [
        pos.ticket for pos in (mt5.positions_get(symbol=SYMBOL) or ())
        if pos.magic == MAGIC_NUMBER and _close_position(pos)
    ]
    if not closed and not cancelled:
        logger.info("Close command: no open positions or pending orders of ours")
    return closed, cancelled


def ladder_volumes(entry_volume: float, steps: int, fraction: float, step: float, min_volume: float) -> list[float]:
    """
    Lots left open after each ladder step. Every step but the last closes `fraction` of what is
    left (rounded down to the lot step); the last closes everything.
    0.16 lot, 4 steps, 50% -> [0.08, 0.04, 0.02, 0]. A step is skipped (nothing closed) when
    the part to close or the part left would be below the broker's minimum lot.
    """
    left = entry_volume
    volumes = []
    for _ in range(steps - 1):
        lots = math.floor(left * fraction / step + 1e-9) * step
        if lots < min_volume - 1e-9 or left - lots < min_volume - 1e-9:
            lots = 0.0
        left = round(left - lots, 8)
        volumes.append(left)
    volumes.append(0.0)
    return volumes


def ladder_action(is_buy: bool, open_price: float, price: float, volume: float, sl: float,
                  entry_volume: float, ladder: list[tuple[float, float | None]], fraction: float,
                  step: float, min_volume: float) -> tuple[int, float, float | None] | None:
    """
    What to do with a position now: (ladder step index, lots to close, new SL or None),
    or None when nothing is due. Uses the highest step price has reached, so a jump from +4 to
    +11 does steps 1 and 2 in one go. `price` is the closing price (bid for BUY, ask for SELL).
    Stateless: lots already closed are read from `volume`, so it is safe to repeat every tick.
    """
    profit = price - open_price if is_buy else open_price - price
    reached = [i for i, (level, _) in enumerate(ladder) if profit >= level]
    if not reached:
        return None
    i = reached[-1]

    target_volume = ladder_volumes(entry_volume, len(ladder), fraction, step, min_volume)[i]
    lots = round(volume - target_volume, 8) if volume > target_volume + step / 2 else 0.0

    new_sl = None
    offset = ladder[i][1]
    if offset is not None:
        target_sl = open_price + offset if is_buy else open_price - offset
        # Only ever tighten the SL; a missing SL (0) always counts as looser
        if sl <= 0 or (sl < target_sl if is_buy else sl > target_sl):
            new_sl = target_sl

    if lots <= 0 and new_sl is None:
        return None
    return i, lots, new_sl


# Position ticket -> monotonic time of the last failed close / SL move, so it isn't retried every tick
_ladder_failures: dict[int, float] = {}
_LADDER_RETRY_SECONDS = 30
# Position ticket -> lots it was opened with (fixed for the position's life)
_entry_volumes: dict[int, float] = {}


def _entry_volume(ticket: int) -> float | None:
    """Lots the position was opened with, read from its deal history (survives bot restarts)."""
    if ticket not in _entry_volumes:
        deals = mt5.history_deals_get(position=ticket)
        if not deals:
            return None
        volume = sum(d.volume for d in deals if d.entry == mt5.DEAL_ENTRY_IN)
        if volume <= 0:
            return None
        _entry_volumes[ticket] = volume
    return _entry_volumes[ticket]


def _move_sl(pos, new_sl: float, digits: int) -> bool:
    new_sl = round(new_sl, digits)
    result = mt5.order_send({
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": SYMBOL,
        "position": pos.ticket,
        "sl": new_sl,
        "tp": pos.tp,
    })
    if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info("Position %s: SL %.2f -> %.2f", pos.ticket, pos.sl, new_sl)
        return True
    logger.error(
        "Could not move SL of position %s to %.2f (retrying in %ss): %s",
        pos.ticket, new_sl, _LADDER_RETRY_SECONDS,
        f"retcode={result.retcode} {result.comment}" if result else mt5.last_error(),
    )
    return False


def signal_tp_ladder(is_buy: bool, open_price: float, tps: list[float]) -> list[tuple[float, float | None]] | None:
    """
    Ladder built from a signal's own TPs, as (profit distance, SL offset) steps from `open_price`:
      TP1 -> close part, SL to entry | TP2 -> close part, SL to TP1 | ... | last TP -> close the rest.
    None if the TPs are missing or not all beyond the entry in increasing order.
    """
    distances = [tp - open_price if is_buy else open_price - tp for tp in tps]
    if not distances or distances[0] <= 0 or any(b <= a for a, b in zip(distances, distances[1:])):
        return None
    ladder = [(d, 0.0 if i == 0 else distances[i - 1]) for i, d in enumerate(distances[:-1])]
    ladder.append((distances[-1], None))
    return ladder


def _group_key(comment: str) -> str:
    """'sig1027123456_e2' -> 'sig1027123456': the part shared by all orders of one signal."""
    return comment.rsplit("_e", 1)[0]


# Positions already reported as missing their signal TPs, so the warning is logged once
_no_signal_tps: set[int] = set()


def _ladder_for(pos, is_buy: bool) -> list[tuple[float, float | None]]:
    """The signal's own TP ladder in SIGNAL_TP_MODE=signal (when its TPs were saved), else TP_LADDER."""
    if TP_MODE == "signal":
        tps = tp_store.get(_group_key(pos.comment))
        ladder = signal_tp_ladder(is_buy, pos.price_open, tps) if tps else None
        if ladder:
            return ladder
        if pos.ticket not in _no_signal_tps:
            _no_signal_tps.add(pos.ticket)
            logger.warning("No usable signal TPs for position %s (%s) — using the fixed TP ladder", pos.ticket, pos.comment)
    return TP_LADDER


def manage_profitable_positions() -> list[int]:
    """
    Runs the take-profit ladder on every open position of ours: at each profit level, closes
    part of the position and moves the SL up; the last level closes the rest. The ladder is
    TP_LADDER, or the position's own signal TPs in SIGNAL_TP_MODE=signal.
    Returns the tickets that were changed.
    """
    positions = [p for p in (mt5.positions_get(symbol=SYMBOL) or ()) if p.magic == MAGIC_NUMBER]
    if not positions:
        return []
    tick = mt5.symbol_info_tick(SYMBOL)
    info = mt5.symbol_info(SYMBOL)
    if tick is None or info is None:
        return []

    changed = []
    now = time.monotonic()
    for pos in positions:
        if now - _ladder_failures.get(pos.ticket, -_LADDER_RETRY_SECONDS) < _LADDER_RETRY_SECONDS:
            continue
        entry_volume = _entry_volume(pos.ticket)
        if entry_volume is None:
            continue
        is_buy = pos.type == mt5.POSITION_TYPE_BUY
        price = tick.bid if is_buy else tick.ask
        ladder = _ladder_for(pos, is_buy)
        action = ladder_action(
            is_buy, pos.price_open, price, pos.volume, pos.sl, entry_volume,
            ladder, TP_LADDER_CLOSE_FRACTION, info.volume_step, info.volume_min,
        )
        if action is None:
            continue
        step, lots, new_sl = action
        level = ladder[step][0]

        logger.info(
            "TP ladder step %d/%d (+%g): %s position %s opened @ %.2f is %.2f in profit — close %s of %s lots%s",
            step + 1, len(ladder), round(level, 2), "BUY" if is_buy else "SELL", pos.ticket, pos.price_open,
            abs(price - pos.price_open), lots, pos.volume,
            f", SL -> {new_sl:.2f}" if new_sl is not None else "",
        )
        if lots > 0:
            closing_all = lots >= pos.volume - info.volume_step / 2
            if not _close_position(pos, None if closing_all else lots, comment=f"tp ladder {step + 1}"):
                _ladder_failures[pos.ticket] = now
                continue
            changed.append(pos.ticket)
            if closing_all:
                _entry_volumes.pop(pos.ticket, None)
                continue
        if new_sl is not None:
            if not _move_sl(pos, new_sl, info.digits):
                _ladder_failures[pos.ticket] = now
                continue
            if pos.ticket not in changed:
                changed.append(pos.ticket)
        _ladder_failures.pop(pos.ticket, None)
    return changed


def synced_entry1_sl(is_buy: bool, entry1_sl: float, entry2_sl: float) -> float | None:
    """Entry 2's SL if it is tighter than entry 1's (never loosens entry 1), else None."""
    if entry2_sl <= 0:
        return None
    if entry1_sl <= 0 or (entry2_sl > entry1_sl if is_buy else entry2_sl < entry1_sl):
        return entry2_sl
    return None


def sync_entry1_sl_to_entry2() -> list[int]:
    """
    When both entries of a signal are open and entry 2 has already taken profit on the TP ladder
    (part of it closed, SL moved up), moves entry 1's SL to entry 2's SL. Entry 2 has the better
    price, so it reaches each ladder step first. Returns the entry 1 tickets whose SL was moved.
    """
    if not SYNC_ENTRY1_SL:
        return []
    positions = [p for p in (mt5.positions_get(symbol=SYMBOL) or ()) if p.magic == MAGIC_NUMBER]
    by_comment = {p.comment: p for p in positions}
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        return []

    moved = []
    now = time.monotonic()
    for e2 in positions:
        if not (e2.comment.startswith("sig") and e2.comment.endswith("_e2")):
            continue
        e1 = by_comment.get(e2.comment[:-1] + "1")
        if e1 is None or e1.type != e2.type:
            continue
        # Only once entry 2 has taken profit (some lots closed), not just because its SL differs
        entry_volume = _entry_volume(e2.ticket)
        if entry_volume is None or e2.volume >= entry_volume - info.volume_step / 2:
            continue
        if now - _ladder_failures.get(e1.ticket, -_LADDER_RETRY_SECONDS) < _LADDER_RETRY_SECONDS:
            continue
        new_sl = synced_entry1_sl(e1.type == mt5.POSITION_TYPE_BUY, e1.sl, e2.sl)
        if new_sl is None:
            continue
        logger.info(
            "Entry 2 (%s) has taken profit — moving entry 1 (%s) SL %.2f -> %.2f to match it",
            e2.ticket, e1.ticket, e1.sl, new_sl,
        )
        if _move_sl(e1, new_sl, info.digits):
            moved.append(e1.ticket)
        else:
            _ladder_failures[e1.ticket] = now
    return moved


def _entry1_closed_at_tp(order, entry1_comment: str) -> bool:
    # Window is generous on both sides because history uses broker server time
    setup = datetime.fromtimestamp(order.time_setup, timezone.utc)
    deals = mt5.history_deals_get(setup - timedelta(days=1), datetime.now(timezone.utc) + timedelta(days=2)) or ()
    entry1_positions = {
        d.position_id for d in deals
        if d.magic == MAGIC_NUMBER and d.entry == mt5.DEAL_ENTRY_IN and d.comment == entry1_comment
    }
    return any(
        d.position_id in entry1_positions and d.entry == mt5.DEAL_ENTRY_OUT and d.reason == mt5.DEAL_REASON_TP
        for d in deals
    )


def cancel_entry2_after_entry1_tp(check_history: bool = True) -> list[int]:
    """
    For every pending entry-2 order of ours, cancels it as soon as entry 1 of the same
    signal reaches its TP:
      - real time: entry 1 is still open and the live price has touched its TP
      - fallback (check_history): entry 1 was already closed by the broker at TP
    Returns the cancelled order tickets.
    """
    pending = [
        o for o in (mt5.orders_get(symbol=SYMBOL) or ())
        if o.magic == MAGIC_NUMBER and o.comment.startswith("sig") and o.comment.endswith("_e2")
    ]
    if not pending:
        return []

    open_positions = {
        p.comment: p for p in (mt5.positions_get(symbol=SYMBOL) or ()) if p.magic == MAGIC_NUMBER
    }
    tick = mt5.symbol_info_tick(SYMBOL)

    cancelled = []
    for order in pending:
        entry1_comment = order.comment[:-1] + "1"
        entry1 = open_positions.get(entry1_comment)

        if entry1 is not None:
            if tick is None or entry1.tp <= 0:
                continue
            # A BUY closes on the bid, a SELL on the ask
            if entry1.type == mt5.POSITION_TYPE_BUY:
                price, reached = tick.bid, tick.bid >= entry1.tp
            else:
                price, reached = tick.ask, tick.ask <= entry1.tp
            if not reached:
                continue
            reason = f"price {price:.2f} touched entry 1 TP {entry1.tp:.2f}"
        elif check_history and _entry1_closed_at_tp(order, entry1_comment):
            reason = "entry 1 closed at TP"
        else:
            continue

        result = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": order.ticket})
        if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(
                "%s (%s) — cancelled pending entry 2 order %s @ %.2f",
                reason, entry1_comment, order.ticket, order.price_open,
            )
            cancelled.append(order.ticket)
        else:
            logger.error(
                "Could not cancel entry 2 order %s: %s",
                order.ticket, result.comment if result else mt5.last_error(),
            )
    return cancelled
