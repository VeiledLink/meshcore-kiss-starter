#!/usr/bin/env python3
"""Set up your bot by answering a few questions.

Writes:
  bot/settings.json        the bot's settings (name, channels, your commands)
  ~/meshcore-pi/config.toml  the radio and node settings (only with --radio, or when asked)

    python3 setup.py           ask everything
    python3 setup.py --bot     just the bot settings (e.g. to add commands)

Safe to run again: your earlier answers are the defaults, and the node's
private key in config.toml is kept (it is the bot's identity on the mesh).
Then send the bot to RemoteTerm:  python3 bot/deploy_bot.py
"""
import glob
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SETTINGS_FILE = HERE / "bot" / "settings.json"
MCP_DIR = Path(os.environ.get("MCP_DIR", Path.home() / "meshcore-pi"))

PRESETS = [   # (label, MHz, kHz, SF, CR) - MeshCore's app presets
    ("USA / Canada (recommended)", 910.525, 62.5, 7, 5),
    ("EU / UK (narrow)", 869.618, 62.5, 8, 8),
]


def ask(question, default=""):
    shown = f" [{default}]" if default not in ("", None) else ""
    try:
        answer = input(f"{question}{shown}: ").strip()
    except EOFError:
        answer = ""
    return answer or ("" if default is None else str(default))


def ask_yes(question, default=True):
    a = ask(question + (" (Y/n)" if default else " (y/N)")).lower()
    return default if not a else a.startswith("y")


def ask_number(question, default, kind=float, lo=None, hi=None):
    while True:
        a = ask(question, default)
        try:
            v = kind(a)
            if (lo is None or v >= lo) and (hi is None or v <= hi):
                return v
        except ValueError:
            pass
        print(f"  Please enter a number{f' from {lo} to {hi}' if lo is not None else ''}.")


def section(title):
    print(f"\n== {title} " + "=" * max(0, 60 - len(title)))


def bot_settings(old):
    section("Your bot")
    s = {}
    s["bot_name"] = ask("Bot name (what people see on the mesh)", old.get("bot_name", "My-Bot"))

    section("Channels")
    s["test_channel"] = "#" + ask("Test channel (a plain 'test' there gets a reply)",
                                  old.get("test_channel", "#test")).lstrip("#")
    quiet = ask("Channels the bot must never reply in (comma separated)",
                ", ".join(old.get("quiet_channels", ["Public"])))
    s["quiet_channels"] = [q.strip() for q in quiet.split(",") if q.strip()]
    s["cooldown_s"] = ask_number("Seconds between replies to the same person", old.get("cooldown_s", 30), int, 0, 3600)

    section("Your own commands")
    print("Commands that answer with a fixed reply, e.g.  rules -> Be kind, no spam.")
    print("{name} in a reply becomes the sender's name. Built in already: test, ping, help.")
    cmds = dict(old.get("commands", {"about": "I'm a MeshCore bot. Send help for commands."}))
    while True:
        if cmds:
            print("  Current: " + ", ".join(cmds))
        word = ask("Command to add or change (blank = done, -word = remove)").lower().lstrip("!")
        if not word:
            break
        if word.startswith("-"):
            cmds.pop(word[1:], None)
            continue
        if not re.fullmatch(r"[a-z0-9_-]{1,20}", word) or word in ("test", "ping", "help"):
            print("  One word, letters/numbers only (and not test, ping or help).")
            continue
        reply = ask(f"Reply for '{word}'", cmds.get(word, ""))
        if reply:
            if len(reply.encode()) > 100:
                print("  Note: long replies get cut to fit one mesh message (~100 characters).")
            cmds[word] = reply
    s["commands"] = cmds
    return s


def radio_config(bot_name):
    section("Radio")
    config = MCP_DIR / "config.toml"
    old = config.read_text() if config.exists() else ""
    ports = sorted(glob.glob("/dev/serial/by-id/*"))
    current = re.search(r'^port\s*=\s*"([^"]+)"', old, re.M)
    if ports:
        print("Radios plugged in:")
        for i, p in enumerate(ports, 1):
            print(f"  {i}) {os.path.basename(p)}")
        pick = ask("Which one is the bot's radio (number)", 1 if len(ports) == 1 else "")
        port = ports[int(pick) - 1] if pick.isdigit() and 0 < int(pick) <= len(ports) else (current[1] if current else ports[0])
    else:
        print("No radio found in /dev/serial/by-id/ - plug it in (KISS modem firmware) and run setup again,")
        port = ask("or type its port path", current[1] if current else "/dev/ttyACM0")

    print("Radio settings must match your mesh:")
    for i, p in enumerate(PRESETS, 1):
        print(f"  {i}) {p[0]}: {p[1]} MHz, {p[2]} kHz, SF{p[3]}, CR{p[4]}")
    print(f"  {len(PRESETS) + 1}) Enter my own")
    pick = ask("Choice", 1)
    if pick.isdigit() and 1 <= int(pick) <= len(PRESETS):
        _, freq, bw, sf, cr = PRESETS[int(pick) - 1]
    else:
        freq = ask_number("Frequency (MHz)", 910.525, float, 100, 1000)
        bw = ask_number("Bandwidth (kHz)", 62.5, float, 7, 500)
        sf = ask_number("Spreading factor", 7, int, 5, 12)
        cr = ask_number("Coding rate", 5, int, 5, 8)
    print("Transmit power: 22 dBm is the usual maximum. Indoors, near other radios, or close to a")
    print("repeater, 10-17 is plenty.")
    power = ask_number("Transmit power (dBm)", 17, int, -9, 22)

    key = re.search(r'^privatekey\s*=\s*"([0-9a-fA-F]+)"', old, re.M)
    lines = [
        "# Written by meshcore-kiss-starter/setup.py - run it again to change these.",
        'interfaces = ["kiss"]', 'devices = ["companion"]', "",
        "[dispatcher]", "pass_internal = false", "",
        "[interface.kiss]", 'type = "kiss"', f'port = "{port}"',
        f"freq = {freq}", f"bw = {bw}", f"sf = {sf}", f"cr = {cr}", f"txpower = {power}", "",
        "[device.companion]", f'name = "{bot_name}"',
        f'privatekey = "{key[1]}"' if key else
        "# privatekey: meshcore-pi prints one on first start - setup.py saves it here next time",
        'contacts = "contacts.mesh"', "channels = 32", 'channelfile = "channels.json"',
        "add_public_channel = false", "",
        "# RemoteTerm connects here; 127.0.0.1 = only programs on this computer can",
        'interface = "wifi"', "wifi.port = 5000", 'wifi.listen = "127.0.0.1"', "",
        "[log]", 'level = "info"', "",
    ]
    if not MCP_DIR.exists():
        print(f"\n{MCP_DIR} doesn't exist yet (install.sh creates it) - radio settings not saved.")
        return
    if old:
        backup = config.with_name(f"config.toml.{time.strftime('%Y%m%d-%H%M%S')}.bak")
        shutil.copy2(config, backup)
        print(f"Old config saved as {backup.name}")
    config.write_text("\n".join(lines))
    print(f"Saved {config}")
    if not key:
        save_key_later(config)


def save_key_later(config):
    """meshcore-pi makes a new key on every start until one is in config.toml - grab it from its log."""
    log = MCP_DIR / "meshcore.log"
    if log.exists():
        found = re.findall(r"Created private key ([0-9a-f]{64,128})", log.read_text(errors="ignore"))
        if found:
            text = config.read_text().replace(
                "# privatekey: meshcore-pi prints one on first start - setup.py saves it here next time",
                f'privatekey = "{found[-1]}"')
            config.write_text(text)
            print("Saved the node's private key from meshcore.log (keeps the same identity).")


def main():
    if "--save-key" in sys.argv:          # used by install.sh after meshcore-pi's first start
        save_key_later(MCP_DIR / "config.toml")
        return
    only_bot = "--bot" in sys.argv
    print("MeshCore bot setup. Press Enter to keep the value in [brackets].")
    old = json.loads(SETTINGS_FILE.read_text()) if SETTINGS_FILE.exists() else {}
    s = bot_settings(old)
    SETTINGS_FILE.write_text(json.dumps(s, indent=2, ensure_ascii=False) + "\n")
    print(f"\nSaved {SETTINGS_FILE.relative_to(HERE)}")
    if not only_bot and ask_yes("\nSet up the radio (meshcore-pi config) too?", True):
        radio_config(s["bot_name"])
    print("\nNext: send the bot to RemoteTerm with   python3 bot/deploy_bot.py")
    if not only_bot:
        print("(If you changed radio settings: sudo systemctl restart meshcore-pi)")


if __name__ == "__main__":
    main()
