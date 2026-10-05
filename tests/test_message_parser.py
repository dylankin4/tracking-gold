import pytest

from message_parser import is_close_command, parse_signal

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


CLOSE_COMMANDS = [
    "CLOSE SELL GOLD -90PIP",
    "CLOSE BUY GOLD +30PIP\nChốt lời nghỉ ngơi thôi nhé cả nhà",
    "CLOSE LUÔN SELL GOLD +20PIP",
    "CLOSE HẾT SELL GOLD ENTRY, BUY TIẾP",
    "CLOSE BUY GOLD ENTRY\nChờ BUY tại 4365-4370",
    "💥 CLOSE SELL GOLD",
    "close sell gold",
]

NOT_CLOSE_COMMANDS = [
    "📊PHÂN TÍCH GOLD\nGiá vàng kết thúc phiên hôm qua đóng cửa tại 4274$/OUNCE",
    "Vừa đóng buy xong cậu phi lên lãi buy luôn",
    "GOLD SELL LÃI +90PIP, sẽ CLOSE sau",
    "CLOSED market today",
    "💰SELL GOLD @ 4400\nSL 4415",
]


@pytest.mark.parametrize("text", CLOSE_COMMANDS)
def test_detects_close_command(text):
    assert is_close_command(text)


@pytest.mark.parametrize("text", NOT_CLOSE_COMMANDS)
def test_ignores_non_close_message(text):
    assert not is_close_command(text)


def test_take_profits_are_collected_in_order():
    signal = parse_signal("SELL GOLD @ 4400\nSL 4415\nTP2 4370\nTP1 4385")
    assert signal.take_profits == [4385, 4370]
