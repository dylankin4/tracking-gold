from types import SimpleNamespace

import pytest

from config import parse_channel
from telegram_listener import in_topic


@pytest.mark.parametrize("raw,expected", [
    ("-1002151688960", (-1002151688960, None)),
    ("-1003831596643_9", (-1003831596643, 9)),
    (" -1003831596643_9 ", (-1003831596643, 9)),
    ("@my_channel", ("@my_channel", None)),   # underscores in usernames are not topics
])
def test_parse_channel(raw, expected):
    assert parse_channel(raw) == expected


def msg(forum_topic=True, msg_id=None, top_id=None):
    reply = None if msg_id is None and top_id is None else SimpleNamespace(
        forum_topic=forum_topic, reply_to_msg_id=msg_id, reply_to_top_id=top_id,
    )
    return SimpleNamespace(reply_to=reply)


def test_post_in_topic():
    assert in_topic(msg(msg_id=9), 9)          # what the signal channel's posts look like


def test_reply_inside_topic():
    assert in_topic(msg(msg_id=1234, top_id=9), 9)


def test_other_topic_is_ignored():
    assert not in_topic(msg(msg_id=5), 9)
    assert not in_topic(msg(msg_id=1234, top_id=5), 9)


def test_general_topic_message_is_not_in_topic_9():
    assert not in_topic(msg(), 9)


def test_general_topic():
    assert in_topic(msg(), 1)
    assert in_topic(msg(forum_topic=False, msg_id=77), 1)   # plain reply in General
    assert not in_topic(msg(msg_id=9), 1)
