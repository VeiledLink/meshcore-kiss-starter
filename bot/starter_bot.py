"""A simple MeshCore bot for RemoteTerm (Remote-Terminal-for-MeshCore).

RemoteTerm runs this code itself: deploy_bot.py sends it to RemoteTerm, with
your settings from bot/settings.json filled in below. RemoteTerm calls
bot(**kwargs) for every message; whatever it returns is sent back - a string,
a list of strings (sent in order), or None for no reply.

Commands (by DM, or "@[My-Bot] <command>" in a channel):
  test     "ack: 3 hops | 4f4c→3a50→1145" - how your message reached the bot
           (in the test channel a plain "test" works too)
  ping     "pong"
  help     the command list
  <yours>  any commands you added in setup ("commands" in settings.json)

Make it your own: add commands in setup.py, or edit bot() below.
"""
import re
import time

# ---- SETTINGS ----------------------------------------------------------------
# Don't edit here: run setup.py (or edit bot/settings.json), then deploy_bot.py
# puts your settings into this block when it sends the bot to RemoteTerm.
SETTINGS = {
    "bot_name": "My-Bot",
    "test_channel": "#test",
    "quiet_channels": ["Public"],
    "cooldown_s": 30,
    "commands": {"about": "I'm a MeshCore bot. Send help for commands."},
}
# ---- END SETTINGS ------------------------------------------------------------

MAX_REPLY_BYTES = 110          # longer channel posts collide/miss more often

# RemoteTerm keeps _bot_globals between calls
_STATE = globals().get("_bot_globals")
if _STATE is None:
    _STATE = {}


def _fit(text, limit=MAX_REPLY_BYTES):
    raw = text.encode()
    return text if len(raw) <= limit else raw[: limit - 3].decode("utf-8", "ignore").rstrip() + "..."


def hops_from(path, bytes_per_hop):
    """RemoteTerm's hex path -> hop codes ("4f4c3a50", 2 -> ["4f4c", "3a50"])."""
    s = str(path or "").strip().lower()
    if not s or not all(c in "0123456789abcdef" for c in s):
        return []
    n = int(bytes_per_hop or 1) * 2
    return [s[i:i + n] for i in range(0, len(s), n)]


def test_reply(kwargs, limit):
    hops = hops_from(kwargs.get("path"), kwargs.get("path_bytes_per_hop"))
    if not hops:
        return "ack: direct (0 hops)"
    head = f"ack: {len(hops)} hop{'s' if len(hops) != 1 else ''}"
    reply = f"{head} | {'→'.join(hops)}"
    while len(reply.encode()) > limit and len(hops) > 1:     # too long: drop codes, mark with …
        hops = hops[:-1]
        reply = f"{head} | {'→'.join(hops)}→…"
    return reply


def commands():
    return {str(k).lower().lstrip("!"): str(v) for k, v in (SETTINGS.get("commands") or {}).items()}


def help_text():
    names = ["test", "ping", "help"] + sorted(c for c in commands() if c not in ("test", "ping", "help"))
    return f"Commands: {', '.join(names)}"


def cooldown_ok(who):
    last = _STATE.setdefault("last_reply", {})
    now = time.time()
    if now - last.get(who, 0) < SETTINGS["cooldown_s"]:
        return False
    last[who] = now
    return True


def bot(**kwargs):
    if kwargs.get("is_outgoing"):
        return None
    text = str(kwargs.get("message_text") or "").strip()
    name = str(kwargs.get("sender_name") or "").strip()
    is_dm = bool(kwargs.get("is_dm"))
    channel = str(kwargs.get("channel_name") or "")

    quiet = {c.lstrip("#").lower() for c in SETTINGS.get("quiet_channels", [])}
    if not is_dm and channel.lstrip("#").lower() in quiet:
        return None

    lower = text.lower()
    mention = f"@[{SETTINGS['bot_name'].lower()}]"
    mentioned = lower.startswith(mention)
    if mentioned:
        lower = lower[len(mention):].lstrip(" :,;-")
    in_test = not is_dm and channel.lower() == SETTINGS["test_channel"].lower()

    # DMs and @mentions take any command; in the test channel a plain "test" works too
    word = lower.lstrip("!").split()[0] if lower.strip("! ") else ""
    if is_dm or mentioned or (in_test and lower.startswith("!")):
        cmd = word
    elif in_test and re.match(r"(test|testing)\b", lower):
        cmd = "test"
    else:
        return None

    custom = commands()
    if cmd not in ("test", "ping", "help") and cmd not in custom:
        return None
    if not cooldown_ok((kwargs.get("sender_key") or name).lower()):
        return None

    to = "" if is_dm or not name else f"@[{name}] "
    room = MAX_REPLY_BYTES - len(to.encode())
    if cmd in custom:
        return to + _fit(custom[cmd].replace("{name}", name or "friend"), room)
    if cmd == "help":
        return to + _fit(help_text(), room)
    if cmd == "ping":
        return to + "pong"
    return to + test_reply(kwargs, room)
