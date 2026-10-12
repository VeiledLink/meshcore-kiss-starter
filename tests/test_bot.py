"""Offline tests for bot/starter_bot.py - a fake RemoteTerm database, no radio.
    python3 tests/test_bot.py
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bot"))
import starter_bot as b

db = os.path.join(tempfile.mkdtemp(), "meshcore.db")
with sqlite3.connect(db) as c:
    c.execute("create table contacts (public_key text, name text, type int, lat real, lon real, last_seen int)")
    c.executemany("insert into contacts values (?,?,?,?,?,?)", [
        ("4f4c" + "0" * 60, "Hill Repeater", 2, 37.0, -120.0, 1),
        ("aa11" + "0" * 60, "Car", 1, 36.5, -119.5, 1),
    ])
b.SETTINGS.update(db=db, lat=37.3, lon=-120.4, town="Merced", bot_name="My-Bot", weather=False,
                  commands={"rules": "Be kind, {name}."}, quiet_channels=["Public"])
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


r = call("test")
check("plain test in #test", r and r.startswith("@[Sam] 2 hops to Merced") and "4f4c→3a50" in r)
check("miles from first repeater", r and "mi ACF" in r)
check("plain test elsewhere ignored", call("test", channel_name="#chat") is None)
check("@mention test elsewhere", (call("@[My-Bot] test", channel_name="#chat") or "").startswith("@[Sam] 2 hops"))
check("DM test, miles from sender GPS", "ACF" in (call("test", is_dm=True, channel_name=None, sender_key="aa11" + "0" * 60) or ""))
check("ping", "pong | 2 hops" in (call("!ping") or ""))
check("zero hops", "0 hops" in (call("test", path="") or ""))
check("quiet channel", call("@[My-Bot] test", channel_name="Public") is None)
long_path = "".join(f"{i:02x}{i:02x}" for i in range(30))
r = call("test", path=long_path)
check("long path fits one post", r and len(r.encode()) <= b.CHANNEL_REPLY_BYTES and r.endswith("…"))
b._STATE.clear()
args = dict(message_text="test", sender_name="Sam", sender_key=None, is_dm=False, channel_name="#test", path="4f4c", path_bytes_per_hop=2)
first, second = b.bot(**args), b.bot(**args)
check("cooldown", first and second is None)
check("coffee reaction", (call("morning ☕", channel_name="#chat") or "").startswith("☕"))
b._STATE.clear()
b._note_sender("🍩 Dee", False)
check("emoji in a name ignored", b.bot(message_text="🍩 Dee! 4 hops", sender_name="Other", channel_name="#chat") is None)
check("other bots ignored", call("☕", sender_name="SomeBot", channel_name="#chat") is None)
check("custom command", call("@[My-Bot] rules", channel_name="#chat") == "@[Sam] Be kind, Sam.")
check("custom command by DM", call("!rules", is_dm=True, channel_name=None) == "Be kind, Sam.")
h = call("help", is_dm=True, channel_name=None)
check("help lists custom, hides weather when off", "rules" in h and "weather" not in h)
check("unknown DM gets one hint", call("hi", is_dm=True, channel_name=None).startswith("Hi!"))
b.SETTINGS["units"] = "metric"
check("metric distance", "km ACF" in (call("test") or ""))
b.SETTINGS["units"] = "imperial"
b.SETTINGS["reactions"] = False
check("reactions can be turned off", call("morning ☕", channel_name="#chat") is None)
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
