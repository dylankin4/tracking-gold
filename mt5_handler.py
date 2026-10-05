import logging
import time
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5

import mt5_terminal
from config import (
    BREAKEVEN_OFFSET,
    BREAKEVEN_TRIGGER,
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
    TP_DISTANCES,
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


def _tp_distance(entry_index: int) -> float:
    # Entry 1 -> TP_DISTANCES[0], entry 2 -> TP_DISTANCES[1], ...; extra entries reuse the last one
    return TP_DISTANCES[min(entry_index, len(TP_DISTANCES) - 1)]


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


def _place_limit(signal: TradeSignal, price: float, tp_distance: float, expiry: datetime, comment: str) -> int | None:
    order_type = mt5.ORDER_TYPE_BUY_LIMIT if signal.direction == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT
    tp = _tp_for(price, signal.direction, tp_distance)
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


def _place_market(signal: TradeSignal, market_price: float, tp_distance: float, comment: str) -> int | None:
    order_type = mt5.ORDER_TYPE_BUY if signal.direction == "BUY" else mt5.ORDER_TYPE_SELL
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": LOT_SIZE,
        "type": order_type,
        "price": market_price,
        "sl": signal.stoploss,
        "tp": _tp_for(market_price, signal.direction, tp_distance),
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
    fill_price = result.price or market_price
    tp = _tp_for(fill_price, signal.direction, tp_distance)
    if abs(fill_price - market_price) > 1e-9:
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

    is_buy = signal.direction == "BUY"
    market_price = tick.ask if is_buy else tick.bid
    expiry = datetime.now(timezone.utc) + timedelta(hours=ORDER_EXPIRY_HOURS)
    group = datetime.now().strftime("%m%d%H%M%S")

    # A BUY LIMIT must sit below the ask, a SELL LIMIT above the bid
    limit_entries, passed_entries = [], []
    for i, price in enumerate(signal.entry_prices):
        still_valid = price < market_price if is_buy else price > market_price
        (limit_entries if still_valid else passed_entries).append((i, price))
    passed_prices = [price for _, price in passed_entries]

    tickets = []
    for i, price in limit_entries:
        ticket = _place_limit(signal, price, _tp_distance(i), expiry, _comment(group, i))
        if ticket:
            tickets.append(ticket)

    if passed_entries:
        sl_distance = market_price - signal.stoploss if is_buy else signal.stoploss - market_price
        if sl_distance >= MIN_SL_DISTANCE:
            logger.info(
                "Price %.2f already past entries %s — entering at market (%.2f from SL)",
                market_price, passed_prices, sl_distance,
            )
            # The market order takes the place of the first passed entry, so it uses that entry's TP distance
            first = passed_entries[0][0]
            ticket = _place_market(signal, market_price, _tp_distance(first), _comment(group, first))
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


def _close_position(pos) -> bool:
    is_buy = pos.type == mt5.POSITION_TYPE_BUY
    result = None
    for _ in range(3):
        tick = mt5.symbol_info_tick(SYMBOL)
        if tick is None:
            logger.error("No tick data to close position %s: %s", pos.ticket, mt5.last_error())
            return False
        result = mt5.order_send({
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": SYMBOL,
            "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
            "position": pos.ticket,
            "price": tick.bid if is_buy else tick.ask,
            "deviation": MARKET_DEVIATION_POINTS,
            "magic": MAGIC_NUMBER,
            "comment": "close cmd",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": _market_filling_mode(),
        })
        if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(
                "Closed %s position %s (%s lots, opened @ %.2f) @ %.2f  profit=%.2f",
                "BUY" if is_buy else "SELL", pos.ticket, pos.volume, pos.price_open, result.price, pos.profit,
            )
            return True
        if result is None or result.retcode not in _RETRY_RETCODES:
            break
    logger.error(
        "Could not close position %s: %s",
        pos.ticket, f"retcode={result.retcode} {result.comment}" if result else mt5.last_error(),
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


def breakeven_sl(is_buy: bool, open_price: float, current_sl: float, price: float,
                 trigger: float, offset: float) -> float | None:
    """
    The SL a position should be moved to, or None if it should stay as it is.
    `price` is the price the position would close at (bid for a BUY, ask for a SELL).
    """
    profit = price - open_price if is_buy else open_price - price
    if trigger <= 0 or profit < trigger:
        return None
    target = open_price + offset if is_buy else open_price - offset
    # Only ever tighten the SL; a missing SL (0) always counts as looser
    if current_sl > 0 and (current_sl >= target if is_buy else current_sl <= target):
        return None
    return target


# Position ticket -> monotonic time of the last failed SL move, so a rejected move isn't retried every tick
_breakeven_failures: dict[int, float] = {}
_BREAKEVEN_RETRY_SECONDS = 30


def move_sl_to_breakeven() -> list[int]:
    """
    Moves the SL of every open position of ours to entry +/- BREAKEVEN_OFFSET once price
    is BREAKEVEN_TRIGGER in profit. Returns the tickets whose SL was moved.
    """
    if BREAKEVEN_TRIGGER <= 0:
        return []
    positions = [p for p in (mt5.positions_get(symbol=SYMBOL) or ()) if p.magic == MAGIC_NUMBER]
    if not positions:
        return []
    tick = mt5.symbol_info_tick(SYMBOL)
    info = mt5.symbol_info(SYMBOL)
    if tick is None or info is None:
        return []

    moved = []
    now = time.monotonic()
    for pos in positions:
        is_buy = pos.type == mt5.POSITION_TYPE_BUY
        price = tick.bid if is_buy else tick.ask
        target = breakeven_sl(is_buy, pos.price_open, pos.sl, price, BREAKEVEN_TRIGGER, BREAKEVEN_OFFSET)
        if target is None:
            continue
        if now - _breakeven_failures.get(pos.ticket, -_BREAKEVEN_RETRY_SECONDS) < _BREAKEVEN_RETRY_SECONDS:
            continue
        target = round(target, info.digits)

        result = mt5.order_send({
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": SYMBOL,
            "position": pos.ticket,
            "sl": target,
            "tp": pos.tp,
        })
        if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(
                "Breakeven: %s position %s opened @ %.2f is %.2f in profit — SL %.2f -> %.2f",
                "BUY" if is_buy else "SELL", pos.ticket, pos.price_open,
                abs(price - pos.price_open), pos.sl, target,
            )
            _breakeven_failures.pop(pos.ticket, None)
            moved.append(pos.ticket)
        else:
            _breakeven_failures[pos.ticket] = now
            logger.error(
                "Could not move SL of position %s to %.2f (retrying in %ss): %s",
                pos.ticket, target, _BREAKEVEN_RETRY_SECONDS,
                f"retcode={result.retcode} {result.comment}" if result else mt5.last_error(),
            )
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
