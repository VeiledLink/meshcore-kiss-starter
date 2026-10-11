"""Push starter_bot.py into RemoteTerm so the running bot actually uses it.

RemoteTerm does not run starter_bot.py from disk - it keeps its own copy of the
bot code in its database (a "bot" fanout config). Editing the file and
restarting remoteterm.service changes nothing; the code has to be pushed.

Create the bot once in the RemoteTerm web UI (any code), named like BOT_NAME
below, then:

    python3 deploy_bot.py            # show diff, back up, push, verify
    python3 deploy_bot.py --dry-run  # show diff only, change nothing

Set REMOTETERM_URL if RemoteTerm isn't at http://127.0.0.1:8000.

The push goes through RemoteTerm's own API (same as Save in the web UI), which
reloads the bot immediately - no service restart needed.
"""
import difflib
import json
import os
import py_compile
import sys
import time
import urllib.request
from pathlib import Path

API = os.environ.get("REMOTETERM_URL", "http://127.0.0.1:8000").rstrip("/") + "/api/fanout"
BOT_NAME = os.environ.get("BOT_NAME", "My-Bot")     # the bot config's name in RemoteTerm
SRC = Path(__file__).resolve().parent / "starter_bot.py"
BACKUP_DIR = Path(__file__).resolve().parent / "bot_backups"


def request(url, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def main():
    dry_run = "--dry-run" in sys.argv

    try:
        py_compile.compile(str(SRC), doraise=True)
    except py_compile.PyCompileError as e:
        sys.exit(f"starter_bot.py does not compile - not deploying:\n{e.msg}")

    bots = [c for c in request(API) if c.get("type") == "bot" and c.get("name") == BOT_NAME]
    if len(bots) != 1:
        sys.exit(f"Expected exactly one bot config named {BOT_NAME!r}, found {len(bots)}")
    bot = bots[0]
    live = bot["config"].get("code", "")
    new = SRC.read_text()

    if live == new:
        print("RemoteTerm is already running the current starter_bot.py - nothing to deploy.")
        return

    diff = difflib.unified_diff(
        live.splitlines(True), new.splitlines(True), "running (RemoteTerm)", "starter_bot.py"
    )
    sys.stdout.writelines(diff)
    print()

    if dry_run:
        print("--dry-run: nothing changed.")
        return

    BACKUP_DIR.mkdir(exist_ok=True)
    backup = BACKUP_DIR / f"bot.running-{time.strftime('%Y%m%d-%H%M%S')}.py"
    backup.write_text(live)
    print(f"Backed up running code to {backup}")

    config = dict(bot["config"], code=new)
    updated = request(f"{API}/{bot['id']}", method="PATCH", payload={"config": config})
    if updated["config"].get("code") != new:
        sys.exit("Push returned different code than starter_bot.py - check the RemoteTerm UI.")
    state = "enabled" if updated.get("enabled") else "DISABLED"
    print(f"Deployed: RemoteTerm reloaded {BOT_NAME} ({state}). No restart needed.")


if __name__ == "__main__":
    main()
