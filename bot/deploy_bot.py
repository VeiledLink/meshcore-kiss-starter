#!/usr/bin/env python3
"""Send the bot to RemoteTerm, with your settings filled in.

RemoteTerm doesn't run starter_bot.py from disk - it keeps its own copy of the
bot code. This script puts bot/settings.json into the code's SETTINGS block and
sends it to RemoteTerm: it creates the bot the first time, then updates it
(RemoteTerm reloads it at once - no restart needed).

    python3 bot/deploy_bot.py            # send it (shows what changed)
    python3 bot/deploy_bot.py --dry-run  # show what would change, send nothing

RemoteTerm address: REMOTETERM_URL (default http://127.0.0.1:8000). If you
turned on RemoteTerm's Basic Auth, also set REMOTETERM_USER and REMOTETERM_PASSWORD.
"""
import base64
import difflib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "starter_bot.py"
SETTINGS_FILE = HERE / "settings.json"
BACKUP_DIR = HERE / "bot_backups"
API = os.environ.get("REMOTETERM_URL", "http://127.0.0.1:8000").rstrip("/") + "/api/fanout"
START, END = "# ---- SETTINGS ", "# ---- END SETTINGS"


def request(url, method="GET", payload=None):
    headers = {"Content-Type": "application/json"}
    user, pw = os.environ.get("REMOTETERM_USER"), os.environ.get("REMOTETERM_PASSWORD")
    if user and pw:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def build_code():
    code = SRC.read_text()
    if not SETTINGS_FILE.exists():
        sys.exit("No bot/settings.json yet - run:  python3 setup.py")
    settings = json.loads(SETTINGS_FILE.read_text())
    a = code.index(START)
    b = code.index("\n", code.index(END)) + 1
    block = (f"{START}{'-' * 64}\n# Filled in by deploy_bot.py from bot/settings.json\n"
             f"SETTINGS = {json.dumps(settings, indent=4, ensure_ascii=False)}\n"
             .replace(": true", ": True").replace(": false", ": False").replace(": null", ": None")
             + f"{END} {'-' * 60}\n")
    code = code[:a] + block + code[b:]
    compile(code, "starter_bot.py", "exec")     # never send code that doesn't compile
    return code, settings["bot_name"]


def main():
    dry_run = "--dry-run" in sys.argv
    code, name = build_code()
    try:
        configs = request(API)
    except urllib.error.HTTPError as e:
        if e.code != 401 or not sys.stdin.isatty():
            sys.exit(f"RemoteTerm answered {e.code}" + (" - set REMOTETERM_USER / REMOTETERM_PASSWORD" if e.code == 401 else ""))
        import getpass     # RemoteTerm has Basic Auth on: ask for its login
        print("RemoteTerm asks for its login (the Basic Auth username/password you chose when installing it).")
        os.environ["REMOTETERM_USER"] = input("Username: ").strip()
        os.environ["REMOTETERM_PASSWORD"] = getpass.getpass("Password: ")
        try:
            configs = request(API)
        except urllib.error.HTTPError as e2:
            sys.exit(f"RemoteTerm answered {e2.code} - wrong username or password?")
    except OSError as e:
        sys.exit(f"Can't reach RemoteTerm at {API}: {e}\nIs it running?  sudo systemctl status remoteterm")

    bots = [c for c in configs if c.get("type") == "bot" and c.get("name") == name]
    if len(bots) > 1:
        sys.exit(f"RemoteTerm has {len(bots)} bots named {name!r} - rename or delete the extras in its web UI")

    if not bots:
        if dry_run:
            print(f"--dry-run: would create bot {name!r} in RemoteTerm.")
            return
        try:
            made = request(API, "POST", {"type": "bot", "name": name, "config": {"code": code}, "enabled": True})
        except urllib.error.HTTPError as e:
            sys.exit(f"RemoteTerm refused the bot ({e.code}): {e.read().decode()[:300]}\n"
                     "If it says bots are disabled, re-run RemoteTerm's install_service.sh and answer yes to 'Enable bots?'")
        print(f"Created bot {name!r} in RemoteTerm ({'enabled' if made.get('enabled') else 'disabled'}).")
        return

    bot = bots[0]
    live = bot["config"].get("code", "")
    if live == code:
        print("RemoteTerm already has this version - nothing to send.")
        return
    sys.stdout.writelines(difflib.unified_diff(live.splitlines(True), code.splitlines(True), "in RemoteTerm", "new"))
    print()
    if dry_run:
        print("--dry-run: nothing sent.")
        return
    BACKUP_DIR.mkdir(exist_ok=True)
    backup = BACKUP_DIR / f"bot.running-{time.strftime('%Y%m%d-%H%M%S')}.py"
    backup.write_text(live)
    updated = request(f"{API}/{bot['id']}", "PATCH", {"config": dict(bot["config"], code=code)})
    if updated["config"].get("code") != code:
        sys.exit("RemoteTerm returned different code - check its web UI.")
    print(f"Updated bot {name!r} (old version saved in {backup.name}). RemoteTerm reloaded it.")


if __name__ == "__main__":
    main()
