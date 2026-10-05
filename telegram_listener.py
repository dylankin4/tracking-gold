import asyncio
import logging
import os
from datetime import datetime, timezone

from telethon import TelegramClient, events
from telethon.errors import ChannelInvalidError, ChannelPrivateError

import config
import mt5_handler
from message_parser import parse_signal

logger = logging.getLogger(__name__)

# Stored next to the bot (or its .exe) so it can be started from any working directory
SESSION_FILE = os.path.join(config.BASE_DIR, "tracking_session")


async def on_new_message(event):
    text = event.raw_text
    if not text:
        return

    signal = parse_signal(text)
    if signal is None:
        logger.info("Message is not a signal, ignored: %r", text[:200])
        return

    age = (datetime.now(timezone.utc) - event.message.date).total_seconds()
    if age > config.MAX_SIGNAL_AGE_SECONDS:
        logger.warning(
            "Ignoring signal from %s — %.0fs old (max %.0fs)",
            event.message.date.isoformat(), age, config.MAX_SIGNAL_AGE_SECONDS,
        )
        return

    logger.info(
        "Signal received: %s  entries=%s  sl=%.2f  tps=%s",
        signal.direction, signal.entry_prices, signal.stoploss, signal.take_profits,
    )

    if not mt5_handler.ensure_connected():
        logger.error("Could not connect to MT5 — skipping order placement")
        return

    tickets = mt5_handler.place_orders(signal)
    if tickets:
        logger.info("Orders placed successfully: tickets=%s", tickets)
    else:
        logger.warning("No orders were placed for this signal")


async def monitor_orders():
    """
    Cancels a pending entry 2 as soon as entry 1 of the same signal reaches TP.
    The live price is checked every tick interval; the slower trade-history check
    (catches TPs hit while the bot was busy or offline) runs every MONITOR_INTERVAL_SECONDS.
    """
    loop = asyncio.get_running_loop()
    next_history_check = 0.0
    while True:
        delay = config.MONITOR_TICK_SECONDS
        try:
            if mt5_handler.ensure_connected():
                check_history = loop.time() >= next_history_check
                if check_history:
                    next_history_check = loop.time() + config.MONITOR_INTERVAL_SECONDS
                mt5_handler.cancel_entry2_after_entry1_tp(check_history=check_history)
            else:
                delay = 30  # back off while MT5 is unavailable
        except Exception:
            logger.exception("Order monitor failed")
        await asyncio.sleep(delay)


async def _resolve_channel(client, channel_ref):
    """
    A numeric ID can only be resolved if Telethon already knows the channel's access hash.
    If it doesn't (e.g. new session or channel not seen yet), load the dialog list to
    populate the cache and look the channel up there.
    """
    try:
        return await client.get_entity(channel_ref)
    except (ValueError, ChannelInvalidError, ChannelPrivateError) as e:
        if not isinstance(channel_ref, int):
            raise
        logger.info("Channel %s not in cache (%s) — searching dialogs", channel_ref, type(e).__name__)

    channels = []
    async for dialog in client.iter_dialogs():
        if dialog.id == channel_ref:
            return dialog.entity
        if dialog.is_channel or dialog.is_group:
            channels.append(f"  {dialog.id}  {dialog.name}")

    logger.error(
        "Channel %s not found among this account's chats. Make sure the account (%s) has joined it. "
        "Channels/groups this account can see:\n%s",
        channel_ref, config.TELEGRAM_PHONE or "current session", "\n".join(channels) or "  (none)",
    )
    raise SystemExit(1)


async def run():
    # Open MT5 and switch on Algo Trading up front, so problems show before a signal arrives.
    # The connection stays open; ensure_connected() re-checks it for every signal and monitor tick.
    if not mt5_handler.connect():
        logger.warning("MT5 is not ready yet — will retry when a signal arrives")

    # Client is created inside the async function so the event loop already exists
    # Keep retrying forever on network drops instead of giving up after 5 attempts
    client = TelegramClient(
        SESSION_FILE, config.TELEGRAM_API_ID, config.TELEGRAM_API_HASH,
        connection_retries=None, retry_delay=5, auto_reconnect=True,
    )
    async with client:
        phone = config.TELEGRAM_PHONE or None
        await client.start(phone=phone)

        # This listener must run as a user account; bots can't list dialogs or read arbitrary channels
        me = await client.get_me()
        if me.bot:
            logger.error(
                "Session '%s.session' is logged in as bot @%s, not a user account. "
                "Delete that file and run again, then log in with your phone number (not a bot token).",
                SESSION_FILE, me.username,
            )
            raise SystemExit(1)

        # Resolve the channel entity first so Telethon caches it before filtering
        raw = config.SIGNAL_CHANNEL
        channel_ref = int(raw) if str(raw).lstrip("-").isdigit() else raw
        channel = await _resolve_channel(client, channel_ref)
        logger.info("Resolved channel: %s (id=%s)", getattr(channel, "title", channel), channel.id)

        @client.on(events.NewMessage(chats=channel))
        async def handler(event):
            await on_new_message(event)

        logger.info("Listening to channel: %s", config.SIGNAL_CHANNEL)
        monitor = asyncio.create_task(monitor_orders())
        try:
            await client.run_until_disconnected()
        finally:
            monitor.cancel()
            mt5_handler.disconnect()

