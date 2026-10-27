"""
Remembers each signal's TP levels on disk (signal_tps.json next to the bot), keyed by the signal's
order-comment group ("sig1027123456"). An MT5 position holds only one TP, so in
SIGNAL_TP_MODE=signal this is where the TP ladder finds TP1..TPn, also after a bot restart.
"""
import json
import logging
import os
import time

import config

logger = logging.getLogger(__name__)

PATH = os.path.join(config.BASE_DIR, "signal_tps.json")
# Entries older than this are dropped when saving (pending orders expire long before)
KEEP_SECONDS = 14 * 86400

_data: dict | None = None


def _load() -> dict:
    global _data
    if _data is None:
        try:
            with open(PATH, encoding="utf-8") as f:
                _data = json.load(f)
        except FileNotFoundError:
            _data = {}
        except (OSError, ValueError) as e:
            logger.error("Could not read %s (%s) — starting with no saved signal TPs", PATH, e)
            _data = {}
    return _data


def save(group: str, tps: list[float]):
    data = _load()
    now = time.time()
    data[group] = {"tps": list(tps), "saved": now}
    for key in [k for k, v in data.items() if now - v.get("saved", 0) > KEEP_SECONDS]:
        del data[key]
    tmp = PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, PATH)  # atomic, so a crash mid-write can't corrupt the file
    except OSError as e:
        logger.error("Could not save signal TPs to %s: %s", PATH, e)


def get(group: str) -> list[float] | None:
    entry = _load().get(group)
    return entry["tps"] if entry else None
