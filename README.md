# MeshCore bot starter kit

Everything you need to run your own **MeshCore bot** on a Raspberry Pi: one install script, a few setup
questions, and your bot is on the mesh. Add your own commands without touching any code.

It runs on a radio with **MeshCore's official KISS modem firmware**, so there's no custom radio firmware to
build or maintain. The radio and node setup comes from **VLK-Bot** on the Central Valley (California) mesh.
The bot here is a separate, simpler starter bot: VLK-Bot itself is private for now.

## What the bot does

A simple, friendly bot you can build on:

| Command | Reply |
|---|---|
| `test` | `@[Sam] ack: 3 hops \| 4f4c→3a50→1145`: how many hops your message took, and through which repeaters |
| `ping` | `pong` |
| `help` | The command list, including yours |
| **your own** | Anything you add during setup, e.g. `rules` → "Be kind, no spam." |

People use commands by **DM**, or in a channel with **`@[YourBot] command`**. In your test channel a plain
**`test`** works on its own. The bot never talks in channels you mark as quiet (like Public), and answers each
person at most once every 30 seconds (you can change this).

## What you need

- A **Raspberry Pi** (3, 4, 5 or Zero 2 W) with Raspberry Pi OS (Bookworm or newer) and internet access
- A **LoRa radio MeshCore supports**, e.g. a RAK4631 (WisBlock), **with an antenna**
- A USB cable from the radio to the Pi

## Install (about 15 minutes)

**1. Flash the radio with the KISS modem firmware.** Get your board's **kiss_modem** build from MeshCore's
releases (e.g. `RAK_4631_kiss_modem`), or use the MeshCore web flasher if it lists one for your board. For a
RAK4631: double-tap its reset button and copy the `.uf2` file onto the `RAK4631` drive that appears.
Then plug the radio into the Pi. **Never power a radio without its antenna.**

**2. On the Pi, run:**
```bash
git clone https://github.com/VeiledLink/meshcore-kiss-starter ~/meshcore-kiss-starter
cd ~/meshcore-kiss-starter
./install.sh
```
The installer:
1. installs **meshcore-pi** (the MeshCore node software) and adds KISS radio support
2. asks your **bot settings**: name, channels, your own commands, and the radio and frequency
3. starts the node as a service (it starts at boot and restarts by itself)
4. installs **RemoteTerm** (a web UI that also runs the bot). Its installer asks a few questions, and
   `install.sh` tells you exactly what to answer.
5. sends your bot to RemoteTerm

**3. Try it:** send `test` in your test channel, or DM your bot `help`.
The web UI is at `http://<your-pi>.local:8000`, where you can see messages and send your own.

## Changing things later

```bash
cd ~/meshcore-kiss-starter
python3 setup.py --bot          # change bot settings or add/remove commands
python3 bot/deploy_bot.py       # send the updated bot (takes effect right away)
```
Changed the radio (frequency, power, port)? Run `python3 setup.py` (answer yes to the radio question), then
`sudo systemctl restart meshcore-pi`.

Your settings live in `bot/settings.json`, and you can edit that file directly instead of running setup.
To change how the bot *works* or add smarter commands, edit `bot/starter_bot.py`. It's short, plain Python. Then run
`python3 bot/deploy_bot.py` again.

## Your own commands

During setup, answer the "Your own commands" questions:
```
Command to add or change: rules
Reply for 'rules': Be kind, no spam. Welcome {name}!
```
`{name}` becomes the sender's name. Keep replies under ~100 characters so they fit in one mesh message.
Remove a command by entering `-rules`.

## How it fits together

```
radio (KISS modem firmware) --USB--> meshcore-pi --TCP 5000--> RemoteTerm --> your bot
```
- **[meshcore-pi](https://github.com/brianwiddas/meshcore-pi)** is the MeshCore node: keys, encryption and
  routing all run on the Pi, and the radio is just a radio. `meshcore-pi/kissinterface.py` adds KISS radio
  support, and `meshcore-pi/patch_meshcore_pi.sh` adds it plus some crash fixes (listed at the top of the script).
- **[RemoteTerm](https://github.com/jkingsman/Remote-Terminal-for-MeshCore)** connects to the node like the
  MeshCore app does, gives you a web UI, and runs the bot code.
- **`bot/starter_bot.py`** is the bot. RemoteTerm keeps its own copy, and `deploy_bot.py` updates it.

| File | What |
|---|---|
| `install.sh` | One-time install (safe to run again) |
| `setup.py` | Setup questions; writes `bot/settings.json` and `~/meshcore-pi/config.toml` |
| `bot/starter_bot.py` | The bot |
| `bot/deploy_bot.py` | Sends the bot plus your settings to RemoteTerm |
| `meshcore-pi/` | KISS radio support, fixes, and an example config for doing it by hand |
| `tests/` | `python3 tests/test_bot.py` and `python3 tests/test_deploy.py` (no radio needed) |

## Troubleshooting

| Problem | Try |
|---|---|
| The bot never answers | `journalctl -u meshcore-pi -n 30`: does it say "KISS modem connected"? Check that the frequency settings match your mesh. |
| "could not open port" | Wrong radio picked, or the radio isn't on the KISS firmware. Run `ls /dev/serial/by-id/`, then `python3 setup.py`. |
| Replies in the web UI, but nobody hears them | Antenna, transmit power, or frequency settings. Are you in range of a repeater? |
| deploy says "Can't reach RemoteTerm" | `sudo systemctl status remoteterm` |
| deploy says bots are disabled | Re-run `bash ~/Remote-Terminal-for-MeshCore/scripts/setup/install_service.sh` and answer **y** to "Enable bots?" |
| Same name, but the identity changed after a reboot | `config.toml` lost its `privatekey` line. Run `python3 setup.py --save-key` right after a start. |

## Security

RemoteTerm's bots run Python on your Pi, so **anyone who can open the web UI can run code on the Pi.** Only use
it on a network you trust. Choose **Basic Auth** in RemoteTerm's installer (a login for the web page), or use a
firewall that only allows your own devices (e.g. via Tailscale). The node's port (5000) is set to
`127.0.0.1`, so only the Pi itself can reach it.

## License

MIT. See `LICENSE`. meshcore-pi, RemoteTerm and MeshCore are separate projects under their own licenses.
