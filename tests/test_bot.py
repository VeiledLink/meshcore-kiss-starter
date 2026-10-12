"""Offline tests for bot/starter_bot.py - no radio, no RemoteTerm.
    python3 tests/test_bot.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bot"))
import starter_bot as b

b.SETTINGS.update(bot_name="My-Bot", test_channel="#test", quiet_channels=["Public"], cooldown_s=30,
                  commands={"rules": "Be kind, {name}."})
ok = True


def check(name, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name)


def call(text, **kw):
    b._STATE.clear()
    args = dict(message_text=text, sender_name="Sam", sender_key=None, is_dm=False,
                channel_name="#test", path="4f4c3a50", path_bytes_per_hop=2)
    args.update(kw)
    return b.bot(**args)


check("plain test in #test", call("test") == "@[Sam] ack: 2 hops | 4f4c→3a50")
check("1-byte hops", call("test", path="4f3a11", path_bytes_per_hop=1) == "@[Sam] ack: 3 hops | 4f→3a→11")
check("zero hops", call("test", path="") == "@[Sam] ack: direct (0 hops)")
check("plain test elsewhere ignored", call("test", channel_name="#chat") is None)
check("@mention test elsewhere", call("@[My-Bot] test", channel_name="#chat") == "@[Sam] ack: 2 hops | 4f4c→3a50")
check("DM test (no @name)", call("test", is_dm=True, channel_name=None) == "ack: 2 hops | 4f4c→3a50")
check("ping", call("!ping") == "@[Sam] pong")
check("help lists custom", call("help", is_dm=True, channel_name=None) == "Commands: test, ping, help, rules")
check("custom command", call("@[My-Bot] rules", channel_name="#chat") == "@[Sam] Be kind, Sam.")
check("unknown command ignored", call("hello", is_dm=True, channel_name=None) is None)
check("chat in #test ignored", call("anyone around?") is None)
check("quiet channel", call("@[My-Bot] test", channel_name="Public") is None)
long_path = "".join(f"{i:02x}{i:02x}" for i in range(30))
r = call("test", path=long_path)
check("long path fits one post", len(r.encode()) <= b.MAX_REPLY_BYTES and r.endswith("→…"))
b._STATE.clear()
a = dict(message_text="test", sender_name="Sam", is_dm=False, channel_name="#test", path="4f4c", path_bytes_per_hop=2)
check("cooldown", b.bot(**a) and b.bot(**a) is None)
check("own messages ignored", call("test", is_outgoing=True) is None)
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
