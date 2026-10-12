"""deploy_bot.py fills settings.json into the bot code, and the result runs.
    python3 tests/test_deploy.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bot"))
import deploy_bot

settings = {"bot_name": "Deploy-Test", "town": "Testville", "lat": 0.0, "lon": 0.0, "units": "imperial",
            "test_channel": "#test", "quiet_channels": [], "admin_keys": [], "cooldown_s": 0, "weather": False,
            "reactions": True, "reaction_channel_s": 60, "reaction_person_min": 60,
            "commands": {"hi": "Hello {name}! \"quotes\" ok"}, "db": os.path.join(tempfile.mkdtemp(), "none.db")}
f = os.path.join(tempfile.mkdtemp(), "settings.json")
json.dump(settings, open(f, "w"))
deploy_bot.SETTINGS_FILE = deploy_bot.Path(f)
code, name = deploy_bot.build_code()
ns = {"_bot_globals": {}}
exec(code, ns)
ok = name == "Deploy-Test" and ns["SETTINGS"]["reactions"] is True
ok &= ns["bot"](message_text="hi", sender_name="Al", is_dm=True) == 'Hello Al! "quotes" ok'
print("PASS deploy fills settings and the bot runs" if ok else "FAIL")
sys.exit(0 if ok else 1)
