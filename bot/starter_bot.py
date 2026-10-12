"""A starter MeshCore bot for RemoteTerm (Remote-Terminal-for-MeshCore).

RemoteTerm runs this code itself: deploy_bot.py sends it to RemoteTerm (with
your settings from bot/settings.json filled in below). RemoteTerm calls
bot(**kwargs) for every message; whatever it returns is sent back - a string,
a list of strings (sent in order), or None for no reply.

Commands (by DM, or "@[My-Bot] <command>" in a channel):
  test / path  hop count, straight-line distance and hop codes
               "@[Sam] 4 hops to Fresno, 38mi ACF | 4f4c→3a50→c0d2→1145"
               In the test channel a plain "test" works too.
  ping         "pong" plus the same hop summary
  weather      now + today's high/low where the bot is (Open-Meteo, no key)
  help         the command list
  <yours>      any commands you added in setup ("commands" in settings.json)
Also: short replies to coffee/pizza/donut emojis (rate limited).

Everything except weather is local (RemoteTerm's contact database), so replies
come back well inside RemoteTerm's 10 second limit.
"""
import json
import random
import re
import sqlite3
import time
import urllib.request
from math import asin, cos, radians, sin, sqrt

# ---- SETTINGS ----------------------------------------------------------------
# Don't edit here: run setup.py (or edit bot/settings.json), then deploy_bot.py
# puts your settings into this block when it sends the bot to RemoteTerm.
SETTINGS = {
    "bot_name": "My-Bot",
    "town": "My Town",
    "lat": 0.0, "lon": 0.0,
    "units": "imperial",
    "test_channel": "#test",
    "quiet_channels": ["Public"],
    "admin_keys": [],
    "cooldown_s": 30,
    "weather": True,
    "reactions": True,
    "reaction_channel_s": 60,
    "reaction_person_min": 60,
    "commands": {"about": "I'm a MeshCore bot. Send help for commands."},
    "db": "/home/pi/Remote-Terminal-for-MeshCore/data/meshcore.db",
}
# ---- END SETTINGS ------------------------------------------------------------

CHANNEL_REPLY_BYTES = 110      # longer channel posts collide/miss more often
DM_REPLY_BYTES = 150
HOP_SEP = "→"
HTTP_TIMEOUT = 4               # RemoteTerm drops replies that take over 10 s
BUILT_IN = ["test", "ping", "path", "weather", "help"]

REACTIONS = [
    ("coffee", "☕", ["☕ Good brew, {name}. Enjoy!", "☕ Mmm, coffee! Enjoy, {name}!"]),
    ("pizza", "🍕", ["🍕 Pizza time, {name}!", "🍕 Save me a slice, {name}!"]),
    ("donut", "🍩", ["🍩 Donut time, {name}!", "🍩 Enjoy the donut, {name}!"]),
]

# RemoteTerm keeps _bot_globals between calls (and across code reloads)
_STATE = globals().get("_bot_globals")
if _STATE is None:
    _STATE = {}


# ---- small helpers -----------------------------------------------------------

def _fit(text, limit):
    raw = text.encode()
    return text if len(raw) <= limit else raw[: limit - 3].decode("utf-8", "ignore").rstrip() + "..."


def _metric():
    return SETTINGS.get("units") == "metric"


def distance(a, b):
    """Great-circle distance in miles (or km with metric units)."""
    lat1, lon1, lat2, lon2 = map(radians, (a[0], a[1], b[0], b[1]))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return (6371.0 if _metric() else 3958.8) * 2 * asin(sqrt(h))


def _home():
    home = (float(SETTINGS.get("lat") or 0), float(SETTINGS.get("lon") or 0))
    return None if home == (0.0, 0.0) else home


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


def crow_distance(hops, sender_key=None):
    """Straight-line ("as the crow flies") distance from the sender to the bot: from the
    sender's GPS if their node shares it, else from the first repeater that heard
    them - only if its hop code matches exactly one known repeater."""
    home = _home()
    if not home:
        return None
    try:
        with _db() as c:
            if sender_key:
                me = _located(c, "public_key = ?", (sender_key.lower(),))
                if me:
                    return distance((me[0][1], me[0][2]), home)
            if hops:
                first = _located(c, "type = 2 and public_key like ?", (hops[0] + "%",))
                if len(first) == 1:
                    return distance((first[0][1], first[0][2]), home)
    except sqlite3.Error:
        pass
    return None


def hop_reply(kwargs, word="", limit=CHANNEL_REPLY_BYTES):
    hops = split_path(kwargs.get("path"), kwargs.get("path_bytes_per_hop"))
    if not hops:
        return f"{word}direct to {SETTINGS['town']} (0 hops)"
    n = len(hops)
    head = f"{word}{n} hop{'s' if n != 1 else ''} to {SETTINGS['town']}"
    d = crow_distance(hops, kwargs.get("sender_key"))
    if d is not None:
        unit = "km" if _metric() else "mi"
        head += f", {'<1' if d < 1 else f'{d:.0f}'}{unit} ACF"      # ACF = as the crow flies
    full = f"{head} | {HOP_SEP.join(hops)}"
    if len(full.encode()) <= limit:
        return full
    i = 0       # too long: as many hop codes as fit, then "…"
    while i < n and len(f"{head} | {HOP_SEP.join(hops[:i + 1])}{HOP_SEP}…".encode()) <= limit:
        i += 1
    return f"{head} | {HOP_SEP.join(hops[:i])}{HOP_SEP}…"


WEATHER_CODES = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy", 45: "fog", 48: "fog",
                 51: "drizzle", 53: "drizzle", 55: "drizzle", 61: "rain", 63: "rain", 65: "heavy rain",
                 71: "snow", 73: "snow", 75: "heavy snow", 80: "showers", 81: "showers", 82: "heavy showers",
                 95: "thunderstorms", 96: "thunderstorms", 99: "thunderstorms"}


def weather():
    home = _home()
    if not home or not SETTINGS.get("weather", True):
        return "Weather isn't set up for this bot"
    t, w = ("celsius", "kmh") if _metric() else ("fahrenheit", "mph")
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={home[0]}&longitude={home[1]}"
           f"&current=temperature_2m,weather_code,wind_speed_10m&daily=temperature_2m_max,temperature_2m_min"
           f"&temperature_unit={t}&wind_speed_unit={w}&timezone=auto&forecast_days=1")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "meshcore-starter-bot"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            d = json.loads(r.read().decode())
        cur, day = d["current"], d["daily"]
        deg = "°C" if _metric() else "°F"
        sky = WEATHER_CODES.get(cur.get("weather_code"), "")
        return (f"{SETTINGS['town']}: {cur['temperature_2m']:.0f}{deg} {sky}, wind {cur['wind_speed_10m']:.0f} {w} | "
                f"today {day['temperature_2m_max'][0]:.0f}/{day['temperature_2m_min'][0]:.0f}{deg}")
    except Exception:
        return "Weather unavailable right now, try again later"


def custom_commands():
    return {str(k).lower().lstrip("!"): str(v) for k, v in (SETTINGS.get("commands") or {}).items()}


def help_text():
    names = BUILT_IN if SETTINGS.get("weather", True) and _home() else [c for c in BUILT_IN if c != "weather"]
    names = names + sorted(c for c in custom_commands() if c not in names)
    return f"Commands: {', '.join(names)}. In {SETTINGS['test_channel']} just send: test"


# ---- rate limits -------------------------------------------------------------

def _cooldown_ok(who, key):
    if key and key.lower() in {k.lower() for k in SETTINGS.get("admin_keys", [])}:
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
    if not SETTINGS.get("reactions", True) or "bot" in name.lower():
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
        return random.choice(replies).format(name=name or "friend")
    return None


# ---- the bot -----------------------------------------------------------------

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

    quiet = {c.lstrip("#").lower() for c in SETTINGS.get("quiet_channels", [])}
    if not is_dm and channel.lstrip("#").lower() in quiet:
        return None

    lower = text.lower()
    mention = f"@[{SETTINGS['bot_name'].lower()}]"
    mentioned = lower.startswith(mention)
    if mentioned:
        text = text[len(mention):].lstrip(" :,;-")
        lower = text.lower()
    in_test = not is_dm and channel.lower() == SETTINGS["test_channel"].lower()

    # DMs and @mentions take any command; in the test channel a plain "test"
    # (or "!test" / "!ping" / "!path") works without the @mention.
    custom = custom_commands()
    known = set(BUILT_IN) | set(custom)
    word = lower.lstrip("!").split()[0] if lower.strip("! ") else ""
    cmd = None
    if is_dm or mentioned or (in_test and lower.startswith("!")):
        cmd = word if word in known else None
    elif in_test and re.match(r"(test|testing)\b", lower) and len(lower.split()) <= 12:
        cmd = "test"

    if cmd is None:
        if not is_dm and not mentioned:
            return _reaction(text, name, channel, who)
        if is_dm and not _STATE.setdefault("hinted", {}).get(who):
            _STATE["hinted"][who] = True    # one "try help" hint per person
            return "Hi! Send help for my commands"
        return None
    if not _cooldown_ok(who, key):
        return None

    limit = DM_REPLY_BYTES if is_dm else CHANNEL_REPLY_BYTES
    to = "" if is_dm or not name else f"@[{name}] "
    room = limit - len(to.encode())
    if cmd in custom:
        return to + _fit(custom[cmd].replace("{name}", name or "friend"), room)
    if cmd == "help":
        return to + _fit(help_text(), room)
    if cmd == "weather":
        return to + _fit(weather(), room)
    if cmd == "ping":
        return to + hop_reply(kwargs, "pong | ", room)
    return to + hop_reply(kwargs, "", room)
