import os
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
# TP distance from entry, per entry: entry 1 -> 5, entry 2 -> 7 (e.g. BUY 4150 as entry 2 gets TP 4157)
TP_DISTANCES = [
    float(os.getenv("SIGNAL_TP_DISTANCE", "5")),
    float(os.getenv("SIGNAL_TP_DISTANCE_2", "7")),
]
# If price already passed an entry, a market order is opened only when price is at least
# this far from the SL; otherwise the passed entries are skipped
MIN_SL_DISTANCE = float(os.getenv("SIGNAL_MIN_SL_DISTANCE", "5"))
# Max slippage allowed for market orders, in points
MARKET_DEVIATION_POINTS = int(os.getenv("SIGNAL_DEVIATION_POINTS", "20"))
# How often to compare the live price with entry 1's TP (to cancel the pending entry 2)
MONITOR_TICK_SECONDS = float(os.getenv("SIGNAL_MONITOR_TICK", "0.25"))
# How often to also check trade history for an entry 1 already closed at TP
MONITOR_INTERVAL_SECONDS = float(os.getenv("SIGNAL_MONITOR_INTERVAL", "2"))
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
    if MT5_PATH and not os.path.isfile(MT5_PATH):
        problems.append(f"MT5_PATH does not exist: {MT5_PATH}")
    return problems
