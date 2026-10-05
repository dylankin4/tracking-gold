import pytest

from message_parser import parse_signal

# Formats seen in the signal channel (prices are examples)
SIGNALS = [
    # text, direction, entries, stoploss
    ("💰BUY GOLD @ 4163-4160\nSL 4150\nTP1 4180\nTP2 4175", "BUY", [4163, 4160], 4150),
    ("💰SELL GOLD 4172-76\nSL 4185\nTP1 4160\nTP2 4140", "SELL", [4172, 4176], 4185),
    ("💰SELL nhỏ GOLD 4172-76\nSL 4185\nTP1 4160\nTP2 4140", "SELL", [4172, 4176], 4185),
    ("💰BUY nhỏ GOLD @ 4195\nSL 4180\nTP1 4210\nTP2 4220", "BUY", [4195], 4180),
    ("💰SELL GOLD @4396\nSL 4410\nTP1 4382\nTP2 4370", "SELL", [4396], 4410),
    ("💰SELL GOLD 4340\nSL 4355\nTp1 4325\nTp2 4315", "SELL", [4340], 4355),
    ("💰SELL LIMIT GOLD @ 4405\nSL 4420\nTP1 4390", "SELL", [4405], 4420),
    ("BUY XAUUSD 4160 - 4155\nSL 4145", "BUY", [4160, 4155], 4145),
    ("BUY XAU/USD @ 4160.5-4158\nSL 4150", "BUY", [4160.5, 4158], 4150),
    # Combining accent pasted inside the number
    ("💰BUY GOLD @ 41́91-88\nSL 4176\nTP1 4203", "BUY", [4191, 4188], 4176),
    # Abbreviated SL and an entry zone crossing a hundred
    ("SELL GOLD @ 4164-4167\nSL 418\nTP1 4150", "SELL", [4164, 4167], 4180),
    ("SELL GOLD 4198-02\nSL 4215", "SELL", [4198, 4202], 4215),
]


@pytest.mark.parametrize("text,direction,entries,sl", SIGNALS)
def test_parses_signal(text, direction, entries, sl):
    signal = parse_signal(text)
    assert signal is not None
    assert signal.direction == direction
    assert signal.entry_prices == entries
    assert signal.stoploss == sl


IGNORED = [
    # Recaps / chatter
    "GOLD SELL LÃI +90PIP\nAce chốt lời bớt về túi và dịch sl nhé",
    "GOLD SELL TP1 +140PIP",
    "Gold đang tăng mạnh, chờ tín hiệu",
    # Re-entry without its own SL line
    "GOLD SELL SL -140PIP, ACE SELL LẠI LUÔN 4351-4355",
    "GOLD BUY SL -150PIP, SELL GOLD LUÔN",
    "GOLD lên quét sl xong giảm, sell lại gold 4163-4166",
    # No SL at all
    "SELL GOLD @ 4400\nTP1 4385",
    # SL typo far from the entry
    "💰SELL nhỏ GOLD @ 4400-4405\nSL 44116\nTP1 4388",
    # SL on the wrong side of the entries
    "BUY GOLD 4160-4165\nSL 4170",
    "SELL GOLD 4160-4165\nSL 4150",
]


@pytest.mark.parametrize("text", IGNORED)
def test_ignores_non_signal(text):
    assert parse_signal(text) is None


def test_take_profits_are_collected_in_order():
    signal = parse_signal("SELL GOLD @ 4400\nSL 4415\nTP2 4370\nTP1 4385")
    assert signal.take_profits == [4385, 4370]
