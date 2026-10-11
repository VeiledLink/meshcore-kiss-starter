# MeshCore KISS starter

Run a MeshCore bot on a Raspberry Pi using a radio with **MeshCore's official
KISS modem firmware**, so you never have to maintain a patched radio build.

From the setup behind **VLK-Bot** on the Central Valley (California) mesh,
cut down to the reusable parts.

```
 radio (kiss_modem firmware) --USB--> meshcore-pi --TCP 5000--> RemoteTerm --> your bot
```

- **[meshcore-pi](https://github.com/brianwiddas/meshcore-pi)** runs the MeshCore node (keys, crypto, routing) on the Pi.
  `meshcore-pi/kissinterface.py` lets it use a KISS modem radio. The keys stay on the Pi, and the radio is just a radio.
- **[RemoteTerm](https://github.com/jkingsman/Remote-Terminal-for-MeshCore)** connects to meshcore-pi as a companion app,
  gives you a web UI, and runs bot code.
- **`bot/starter_bot.py`** is a small bot with `test` / `path` replies (hops, miles, hop codes), `ping`, `help`,
  per-person cooldowns, quiet channels and emoji reactions.

## What's here

| Path | What |
|---|---|
| `meshcore-pi/kissinterface.py` | KISS modem interface for meshcore-pi (`type = "kiss"`) |
| `meshcore-pi/patch_meshcore_pi.sh` | Installs the interface, plus crash and compatibility fixes (see the top of the script) |
| `meshcore-pi/config.example.toml` | meshcore-pi config: one KISS radio and one companion identity |
| `bot/starter_bot.py` | The bot (edit `SETTINGS` at the top) |
| `bot/deploy_bot.py` | Pushes the bot code into RemoteTerm (RemoteTerm keeps its own copy) |
| `tests/test_bot.py` | Offline bot tests, no radio needed |

## Setup

**1. Flash the radio.** Use the official MeshCore flasher, or for a RAK4631, double-tap reset and copy the
`RAK_4631_kiss_modem` `.uf2` onto the `RAK4631` drive. Then plug it into the Pi, **with an antenna on**.

**2. Install meshcore-pi** (follow its README), then patch it:
```bash
git clone <this repo> ~/meshcore-kiss-starter
~/meshcore-kiss-starter/meshcore-pi/patch_meshcore_pi.sh
cp ~/meshcore-kiss-starter/meshcore-pi/config.example.toml ~/meshcore-pi/config.toml
ls /dev/serial/by-id/          # put your radio's path in config.toml
```
Edit `config.toml`: the radio port, your mesh's frequency, bandwidth, SF and CR, and the bot's name. Then start it:
`cd ~/meshcore-pi && venv/bin/python meshcore.py config.toml`. It prints the identity's private key on first
start. Copy it into `config.toml` (`privatekey = "..."`) so the bot keeps the same identity. Run it as a systemd
service once it works.

**3. Install RemoteTerm** (follow its README) and point it at meshcore-pi:
`MESHCORE_TCP_HOST=127.0.0.1` and `MESHCORE_TCP_PORT=5000`.

**4. Add the bot.** In RemoteTerm's web UI, add a bot named like your node (e.g. `My-Bot`), then:
```bash
cd ~/meshcore-kiss-starter/bot
# edit SETTINGS in starter_bot.py: bot_name, town, lat/lon, test channel, db path
BOT_NAME=My-Bot python3 deploy_bot.py
```
Then send `test` in your test channel.

## Security

RemoteTerm's bots run Python on the Pi, so **don't expose RemoteTerm (port 8000) or meshcore-pi (port 5000)
to a network you don't trust**. Keep meshcore-pi on `127.0.0.1` (as in the example config). For RemoteTerm, use
its Basic Auth (`MESHCORE_BASIC_AUTH_USERNAME` / `_PASSWORD`), or a firewall that only allows the Pi itself and
your own devices (e.g. Tailscale). Note that Basic Auth also applies to scripts on the Pi that call RemoteTerm's
API, including `deploy_bot.py`.

## Tips

- **KISS radio settings:** the modem forgets its settings on reboot. meshcore-pi re-sends them from `config.toml`
  every time it connects, and reconnects by itself if the radio is unplugged.
- **Several radios on one Pi:** keep the antennas apart (a metre or more), or lower `txpower`. Radios side by side
  desensitize each other.
- **Replies:** channel replies are kept to ~110 bytes. Longer posts collide more often on a busy mesh.

## License

MIT. See `LICENSE`. meshcore-pi, RemoteTerm and MeshCore are separate projects under their own licenses.
