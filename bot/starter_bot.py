"""A starter MeshCore bot for RemoteTerm (Remote-Terminal-for-MeshCore).

RemoteTerm runs this code itself: paste it into a bot (fanout) config in the
RemoteTerm web UI, or push it with deploy_bot.py. It defines bot(**kwargs),
which RemoteTerm calls for every message; whatever it returns is sent back
(a string, a list of strings sent in order, or None for no reply).

What it does:
  test / path  - in the test channel (plain "test"), by DM, or "@[My-Bot] test"
                 anywhere: hop count, straight-line miles, and the hop codes
                 "@[Sam] 4 hops to Fresno, 38mi ACF | 4f4c→3a50→c0d2→1145"
  ping         - "pong" plus the same hop summary
  help         - the command list
  reactions    - a short reply to coffee/pizza/donut emojis (rate limited)

Edit the SETTINGS below. Everything here is local (RemoteTerm's own contact
database), so replies come back well inside RemoteTerm's 10 second limit.
"""
import re
import sqlite3
import time
from math import asin, cos, radians, sin, sqrt

# ---- SETTINGS --------------------------------------------------------------

SETTINGS = {
    "bot_name": "My-Bot",                 # the companion's name (for @[My-Bot] mentions)
    "town": "My Town",                    # said in replies: "4 hops to My Town"
    "lat": 0.0, "lon": 0.0,               # where the bot's radio is (for miles); 0, 0 = no miles
    "test_channel": "#test",              # a plain "test" here gets the hop reply
    "quiet_channels": {"Public"},         # never reply here (busy shared channels)
    "admin_keys": set(),                  # public keys (hex) exempt from the cooldown
    "cooldown_s": 30,                     # one reply per person per this many seconds
    "reaction_channel_s": 60,             # a reaction at most once a minute per channel
    "reaction_person_min": 60,            # ...and once an hour per person per kind
    # RemoteTerm's database (read-only): known repeaters and their positions
    "db": "/home/pi/Remote-Terminal-for-MeshCore/data/meshcore.db",
}

CHANNEL_REPLY_BYTES = 110      # longer channel posts collide/miss more often
DM_REPLY_BYTES = 150
HOP_SEP = "→"

REACTIONS = [
    ("coffee", "☕", ["☕ Good brew, {name}. Enjoy!", "☕ Mmm, coffee! Enjoy, {name}!"]),
    ("pizza", "🍕", ["🍕 Pizza time, {name}!", "🍕 Save me a slice, {name}!"]),
    ("donut", "🍩", ["🍩 Donut time, {name}!", "🍩 Enjoy the donut, {name}!"]),
]

HELP = "Commands: test, ping, path, help. In {test} just send: test"

# RemoteTerm keeps _bot_globals between calls (and across code reloads)
_STATE = globals().get("_bot_globals")
if _STATE is None:
    _STATE = {}


# ---- small helpers ---------------------------------------------------------

def _fit(text, limit):
    raw = text.encode()
    return text if len(raw) <= limit else raw[: limit - 3].decode("utf-8", "ignore").rstrip() + "..."


def miles(a, b):
    lat1, lon1, lat2, lon2 = map(radians, (a[0], a[1], b[0], b[1]))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * asin(sqrt(h))


def _db():
    return sqlite3.connect(f"file:{SETTINGS['db']}?mode=ro", uri=True, timeout=3)


def split_path(path, bph):
    """RemoteTerm's hex path -> hop codes, sender side first ("4f4c3a50", 2 -> ["4f4c", "3a50"])."""
    s = str(path or "").strip().lower()
    if not s or not all(c in "0123456789abcdef" for c in s):
        return []
    n = int(bph or 1) * 2
    return [s[i:i + n] for i in range(0, len(s), n)]


def _located(c, where, args):
    return c.execute(f"select name, lat, lon from contacts where {where} and lat is not null "
                     "and lat != 0 and lon != 0 order by last_seen desc", args).fetchall()


def crow_miles(hops, sender_key=None):
    """Straight-line ("as the crow flies") miles from the sender to the bot: from the
    sender's GPS if their node shares it, else from the first repeater that heard
    them - only if its hop code matches exactly one known repeater."""
    home = (SETTINGS["lat"], SETTINGS["lon"])
    if home == (0.0, 0.0):
        return None
    try:
        with _db() as c:
            if sender_key:
                me = _located(c, "public_key = ?", (sender_key.lower(),))
                if me:
                    return miles((me[0][1], me[0][2]), home)
            if hops:
                first = _located(c, "type = 2 and public_key like ?", (hops[0] + "%",))
                if len(first) == 1:
                    return miles((first[0][1], first[0][2]), home)
    except sqlite3.Error:
        pass
    return None


def hop_reply(kwargs, word="", limit=CHANNEL_REPLY_BYTES):
    hops = split_path(kwargs.get("path"), kwargs.get("path_bytes_per_hop"))
    if not hops:
        return f"{word}direct to {SETTINGS['town']} (0 hops)"
    n = len(hops)
    head = f"{word}{n} hop{'s' if n != 1 else ''} to {SETTINGS['town']}"
    d = crow_miles(hops, kwargs.get("sender_key"))
    if d is not None:
        head += f", {'<1' if d < 1 else f'{d:.0f}'}mi ACF"        # ACF = as the crow flies
    codes = HOP_SEP.join(hops)
    full = f"{head} | {codes}"
    if len(full.encode()) <= limit:
        return full
    # too long: as many hop codes as fit, then "…"
    i = 0
    while i < n and len(f"{head} | {HOP_SEP.join(hops[:i + 1])}{HOP_SEP}…".encode()) <= limit:
        i += 1
    return f"{head} | {HOP_SEP.join(hops[:i])}{HOP_SEP}…"


# ---- rate limits -----------------------------------------------------------

def _cooldown_ok(who, key):
    if key and key.lower() in SETTINGS["admin_keys"]:
        return True
    last = _STATE.setdefault("last_reply", {})
    now = time.time()
    if now - last.get(who, 0) < SETTINGS["cooldown_s"]:
        return False
    last[who] = now
    return True


def _note_sender(name, is_dm):
    if name and not is_dm:
        _STATE.setdefault("recent_names", {})[name.replace("️", "")] = time.time()


def _recent_names():
    seen = _STATE.get("recent_names", {})
    cutoff = time.time() - 1800
    for n in [n for n, t in seen.items() if t < cutoff]:
        del seen[n]
    return sorted(seen, key=len, reverse=True)


def _reaction(text, name, where, who):
    if "bot" in name.lower():
        return None                        # never answer other bots
    text = text.replace("️", "")       # ☕️ == ☕
    # Emojis in someone's name don't count: "@[Pizza Pete 🍕] hi", or another
    # bot echoing a name without the @[ ] ("🍩 Dee! 4 hops").
    text = re.sub(r"@\[[^\]]*\]", "", text)
    for other in _recent_names():
        text = text.replace(other, "")
    now = time.time()
    for kind, emoji, replies in REACTIONS:
        if emoji not in text:
            continue
        per_channel = _STATE.setdefault("react:" + kind, {})
        per_person = _STATE.setdefault("react:person:" + kind, {})
        if now - per_channel.get(where, 0) < SETTINGS["reaction_channel_s"]:
            continue
        if now - per_person.get(who, 0) < SETTINGS["reaction_person_min"] * 60:
            continue
        per_channel[where] = per_person[who] = now
        import random
        return random.choice(replies).format(name=name or "friend")
    return None


# ---- the bot ---------------------------------------------------------------

def bot(**kwargs):
    if kwargs.get("is_outgoing"):
        return None
    text = str(kwargs.get("message_text") or "").strip()
    name = str(kwargs.get("sender_name") or "").strip()
    key = kwargs.get("sender_key") or ""
    is_dm = bool(kwargs.get("is_dm"))
    channel = str(kwargs.get("channel_name") or "")
    who = key.lower() or name.lower()
    _note_sender(name, is_dm)

    if not is_dm and channel.lstrip("#").lower() in {c.lstrip("#").lower() for c in SETTINGS["quiet_channels"]}:
        return None

    lower = text.lower()
    mention = f"@[{SETTINGS['bot_name'].lower()}]"
    mentioned = lower.startswith(mention)
    if mentioned:
        text = text[len(mention):].lstrip(" :,;-")
        lower = text.lower()
    in_test = not is_dm and channel.lower() == SETTINGS["test_channel"].lower()

    # What's being asked: DMs and @mentions take any command; in the test
    # channel a plain "test" (or !test / !ping / !path) works without one.
    cmd = None
    word = lower.lstrip("!").split()[0] if lower.strip("! ") else ""
    if is_dm or mentioned or (in_test and lower.startswith("!")):
        cmd = word if word in ("test", "path", "ping", "help") else None
    elif in_test and re.match(r"(test|testing)\b", lower) and len(lower.split()) <= 12:
        cmd = "test"

    if cmd is None:
        if not is_dm and not mentioned:
            return _reaction(text, name, channel, who)
        return None
    if not _cooldown_ok(who, key):
        return None

    limit = DM_REPLY_BYTES if is_dm else CHANNEL_REPLY_BYTES
    to = "" if is_dm or not name else f"@[{name}] "
    if cmd == "help":
        return _fit(HELP.format(test=SETTINGS["test_channel"]), limit)
    if cmd == "ping":
        return to + hop_reply(kwargs, "pong | ", limit - len(to.encode()))
    return to + hop_reply(kwargs, "", limit - len(to.encode()))
