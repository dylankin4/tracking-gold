import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TradeSignal:
    direction: str       # "BUY" or "SELL"
    entry_prices: list   # [price1] or [price1, price2]
    stoploss: float
    take_profits: list = field(default_factory=list)  # [tp1, tp2, ...]


_NUM = r"(\d+(?:[.,]\d+)?)"

# Direction, then anything without digits on the same line (symbol, "@", words like "nhỏ",
# but not "SL"/"TP"), then one entry or an entry zone:
#   "BUY GOLD @ 4163-4160", "SELL GOLD 4172-76", "SELL nhỏ GOLD 4172-76", "SELL GOLD @4396"
_HEADER_RE = re.compile(
    rf"\b(BUY|SELL)\b(?:(?!SL|TP)[^\d\n])*?{_NUM}(?:\s*[-–]\s*{_NUM})?",
    re.IGNORECASE,
)
# SL must start a line, so recaps like "GOLD SELL SL -140PIP" are not read as a stop loss
_SL_RE = re.compile(rf"^\s*SL\s*[:\-]?\s*{_NUM}", re.IGNORECASE | re.MULTILINE)
_TP_RE = re.compile(rf"\bTP\s*(\d*)\s*[:\-]?\s*{_NUM}", re.IGNORECASE)


# "CLOSE SELL GOLD -90PIP", "💥CLOSE LUÔN SELL GOLD", "CLOSE HẾT SELL GOLD ENTRY, BUY TIẾP"
_CLOSE_RE = re.compile(r"^\W*CLOSE\b", re.IGNORECASE)

# An SL further than this fraction of the price from the entry is treated as a misread
_MAX_SL_FRACTION = 0.05


def _normalize(text: str) -> str:
    """Drops combining marks that sometimes get pasted inside numbers, e.g. '41́ 91' -> '4191'."""
    return "".join(ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn")


def _to_float(s: str) -> float:
    return float(s.replace(",", "."))


def _fix_magnitude(value: float, ref: float) -> float:
    """Expands abbreviated prices, e.g. 'SL 418' next to entry 4164 -> 4180."""
    while value > 0 and value * 5 < ref:
        value *= 10
    return value


def _complete_entry(second: str, first: float) -> float:
    """Expands an abbreviated second entry from the first one's leading digits, e.g. 4172-76 -> 4176."""
    value = _to_float(second)
    digits = len(second.replace(",", ".").split(".")[0])
    first_digits = len(str(int(first)))
    if digits >= first_digits:
        return value
    step = 10 ** digits
    base = int(first) // step * step + value
    # Pick the completion closest to the first entry (handles crossing a boundary, e.g. 4198-02 -> 4202)
    return min((base - step, base, base + step), key=lambda v: abs(v - first))


def is_close_command(text: str) -> bool:
    """True for messages that start with CLOSE — the channel's order to close all open trades."""
    return bool(_CLOSE_RE.match(_normalize(text)))


def parse_signal(text: str) -> Optional[TradeSignal]:
    """
    Parses messages like:
        SELL GOLD @ 4164-4167        (or a single entry: SELL GOLD @ 4164)
        SL 4180
        TP1 4150
        TP2 4140
    Returns a TradeSignal or None if the message doesn't match.
    """
    text = _normalize(text)
    header = _HEADER_RE.search(text)
    if not header:
        return None
    direction = header.group(1).upper()
    first = _to_float(header.group(2))
    entries = [first] if header.group(3) is None else [first, _complete_entry(header.group(3), first)]
    ref = sum(entries) / len(entries)

    sl_match = _SL_RE.search(text)
    if not sl_match:
        return None
    stoploss = _fix_magnitude(_to_float(sl_match.group(1)), ref)

    tps = sorted(
        ((int(m.group(1) or 0), _fix_magnitude(_to_float(m.group(2)), ref)) for m in _TP_RE.finditer(text)),
        key=lambda t: t[0],
    )
    take_profits = [price for _, price in tps]

    # Sanity check: SL must be on the correct side of the entry zone and not absurdly far
    sl_ok = stoploss > max(entries) if direction == "SELL" else stoploss < min(entries)
    sl_ok = sl_ok and abs(stoploss - ref) <= ref * _MAX_SL_FRACTION
    if not sl_ok:
        logger.warning(
            "Rejected %s signal with inconsistent levels: entries=%s sl=%s tps=%s",
            direction, entries, stoploss, take_profits,
        )
        return None

    return TradeSignal(
        direction=direction,
        entry_prices=entries,
        stoploss=stoploss,
        take_profits=take_profits,
    )
