import os
import re
import sys

from dotenv import load_dotenv

# Folder holding .env, the Telegram session and logs: next to the .exe when built with
# PyInstaller (__file__ then points into a temp extraction dir), else next to this file
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))

# Telegram user-client credentials (get from https://my.telegram.org)
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE", "")

# Channel to monitor (username like @mychannel, or numeric ID like -1001234567890)
SIGNAL_CHANNEL = os.getenv("SIGNAL_CHANNEL", "")


def parse_channel(raw: str) -> tuple[int | str, int | None]:
    """
    Splits SIGNAL_CHANNEL into (chat, topic):
      "-1001234567890"   -> (-1001234567890, None)   channel or group
      "-1001234567890_9" -> (-1001234567890, 9)      only topic 9 of a forum group
      "@mychannel"       -> ("@mychannel", None)
    """
    raw = raw.strip()
    m = re.fullmatch(r"(-?\d+)(?:_(\d+))?", raw)
    if m:
        return int(m.group(1)), int(m.group(2)) if m.group(2) else None
    return raw, None


SIGNAL_CHAT, SIGNAL_TOPIC = parse_channel(SIGNAL_CHANNEL)

# MT5
MT5_LOGIN = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "")
# Optional: full path to terminal64.exe (needed if several MT5 terminals are installed)
MT5_PATH = os.getenv("MT5_PATH", "")

# Order settings
SYMBOL = os.getenv("SIGNAL_SYMBOL", "XAUUSD")
LOT_SIZE = float(os.getenv("SIGNAL_LOT_SIZE", "0.01"))
MAGIC_NUMBER = int(os.getenv("SIGNAL_MAGIC", "20260611"))
ORDER_EXPIRY_HOURS = int(os.getenv("SIGNAL_EXPIRY_HOURS", "24"))


def parse_tp_ladder(spec: str) -> list[tuple[float, float | None]]:
    """
    Parses "5:0.3,10:6,15:12,20" into [(5, 0.3), (10, 6), (15, 12), (20, None)].
    Each "profit:sl" step closes part of the position once it is `profit` in our favour and
    moves the SL to entry +/- `sl`; the last step has no SL and closes everything.
    """
    steps = []
    for part in spec.replace(" ", "").split(","):
        profit, _, sl = part.partition(":")
        steps.append((float(profit), float(sl) if sl else None))
    if not steps:
        raise ValueError("needs at least one step")
    if steps[-1][1] is not None:
        raise ValueError("the last step closes everything, so it must have no SL (e.g. '20')")
    for (prev, _), (cur, _) in zip(steps, steps[1:]):
        if cur <= prev:
            raise ValueError("profit levels must increase")
    for profit, sl in steps[:-1]:
        if sl is None or not 0 <= sl < profit:
            raise ValueError(f"step {profit} needs an SL between 0 and {profit}")
    return steps


# Take-profit ladder, applied to every position (entry 1, entry 2 and market entries).
# At each step, TP_LADDER_CLOSE_FRACTION of the lots still open is closed and the SL moves to
# entry +/- the step's SL. The last step closes the rest; it is also sent to the broker as the
# order's TP, so it fires even while the bot is offline.
#   0.16 lot: +5 close 0.08, SL +0.3 | +10 close 0.04, SL +6 | +15 close 0.02, SL +12 | +20 close 0.02
TP_LADDER_SPEC = os.getenv("SIGNAL_TP_LADDER", "5:0.3,10:6,15:12,20")
TP_LADDER_CLOSE_FRACTION = float(os.getenv("SIGNAL_TP_LADDER_CLOSE_FRACTION", "0.5"))
try:
    TP_LADDER = parse_tp_ladder(TP_LADDER_SPEC)
    TP_LADDER_ERROR = ""
except ValueError as e:
    TP_LADDER, TP_LADDER_ERROR = [(20.0, None)], f"SIGNAL_TP_LADDER '{TP_LADDER_SPEC}' is invalid: {e}"
# Distance of the final TP from the entry
FINAL_TP_DISTANCE = TP_LADDER[-1][0]
# When both entries of a signal are open and entry 2 has taken profit on the ladder, move entry 1's
# SL to entry 2's SL (only ever tighter). SIGNAL_SYNC_ENTRY1_SL=0 turns it off.
SYNC_ENTRY1_SL = os.getenv("SIGNAL_SYNC_ENTRY1_SL", "1").strip().lower() not in ("0", "false", "no", "off")

# If price already passed an entry, a market order is opened only when price is at least
# this far from the SL; otherwise the passed entries are skipped
MIN_SL_DISTANCE = float(os.getenv("SIGNAL_MIN_SL_DISTANCE", "5"))
# Max slippage allowed for market orders, in points
MARKET_DEVIATION_POINTS = int(os.getenv("SIGNAL_DEVIATION_POINTS", "20"))
# How often to compare the live price with entry 1's TP (to cancel the pending entry 2)
MONITOR_TICK_SECONDS = float(os.getenv("SIGNAL_MONITOR_TICK", "0.25"))
# How often to also check trade history for an entry 1 already closed at TP
MONITOR_INTERVAL_SECONDS = float(os.getenv("SIGNAL_MONITOR_INTERVAL", "2"))
# No new trades once today's closed profit (whole account, broker day) is above this, in account
# currency. Open trades and pending orders are still managed. 0 turns the limit off.
DAILY_PROFIT_TARGET = float(os.getenv("SIGNAL_DAILY_PROFIT_TARGET", "150"))
# Same, once today's closed loss reaches this much (500 -> stop at a closed result of -500). 0 = off.
DAILY_MAX_LOSS = float(os.getenv("SIGNAL_DAILY_MAX_LOSS", "500"))

# Signals older than this (e.g. delivered late after a reconnect) are ignored
MAX_SIGNAL_AGE_SECONDS = float(os.getenv("SIGNAL_MAX_AGE_SECONDS", "120"))

LOG_DIR = os.path.join(BASE_DIR, "logs")


def validate():
    """Returns a list of problems with the configuration (empty if it's usable)."""
    problems = []
    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        problems.append("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set")
    if not SIGNAL_CHANNEL:
        problems.append("SIGNAL_CHANNEL must be set")
    if not MT5_LOGIN or not MT5_PASSWORD or not MT5_SERVER:
        problems.append("MT5_LOGIN, MT5_PASSWORD and MT5_SERVER must be set")
    if LOT_SIZE <= 0:
        problems.append("SIGNAL_LOT_SIZE must be > 0")
    if TP_LADDER_ERROR:
        problems.append(TP_LADDER_ERROR)
    if not 0 < TP_LADDER_CLOSE_FRACTION < 1:
        problems.append("SIGNAL_TP_LADDER_CLOSE_FRACTION must be between 0 and 1")
    if MT5_PATH and not os.path.isfile(MT5_PATH):
        problems.append(f"MT5_PATH does not exist: {MT5_PATH}")
    return problems
