import asyncio
import ctypes
import logging
import os
import sys
import time
from logging.handlers import TimedRotatingFileHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from version import VERSION

os.makedirs(config.LOG_DIR, exist_ok=True)
_file_handler = TimedRotatingFileHandler(
    os.path.join(config.LOG_DIR, "tracking.log"), when="midnight", backupCount=30, encoding="utf-8",
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), _file_handler],
)
logger = logging.getLogger("main")

from telegram_listener import run

# A second copy of the bot would place every signal's orders twice
_MUTEX_NAME = f"Global\\TrackingGoldBot_{config.MAGIC_NUMBER}"
ERROR_ALREADY_EXISTS = 183

# Restart backoff after a crash: doubles up to the max, resets once a run has lasted a while
RESTART_DELAY_MIN = 10
RESTART_DELAY_MAX = 300
STABLE_RUN_SECONDS = 600


def _acquire_single_instance():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not handle or ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return None
    return handle  # keep a reference so the mutex lives as long as the process


def main():
    problems = config.validate()
    if problems:
        for p in problems:
            logger.error("Config error: %s", p)
        logger.error("Fix %s and start again", os.path.join(config.BASE_DIR, ".env"))
        sys.exit(2)

    mutex = _acquire_single_instance()
    if mutex is None:
        logger.error("Another instance of the bot is already running (magic %s) — exiting", config.MAGIC_NUMBER)
        sys.exit(3)

    logger.info(
        "Bot starting — version=%s symbol=%s lot=%s magic=%s base_dir=%s",
        VERSION, config.SYMBOL, config.LOT_SIZE, config.MAGIC_NUMBER, config.BASE_DIR,
    )
    delay = RESTART_DELAY_MIN
    while True:
        started = time.monotonic()
        try:
            asyncio.run(run())
            logger.warning("Telegram client disconnected")
        except KeyboardInterrupt:
            logger.info("Stopped by user")
            return
        except SystemExit:
            raise  # deliberate stop (bad session, channel not found, ...)
        except Exception:
            logger.exception("Bot crashed")

        if time.monotonic() - started > STABLE_RUN_SECONDS:
            delay = RESTART_DELAY_MIN
        logger.info("Restarting in %ss", delay)
        try:
            time.sleep(delay)
        except KeyboardInterrupt:
            logger.info("Stopped by user")
            return
        delay = min(delay * 2, RESTART_DELAY_MAX)


if __name__ == "__main__":
    main()
