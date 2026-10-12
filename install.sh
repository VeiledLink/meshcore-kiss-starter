#!/bin/bash
# One-time install on a Raspberry Pi (Raspberry Pi OS / Debian), run as your
# normal user (not root - it uses sudo when it needs to):
#
#   git clone https://github.com/VeiledLink/meshcore-kiss-starter ~/meshcore-kiss-starter
#   cd ~/meshcore-kiss-starter && ./install.sh
#
# It installs meshcore-pi (patched for KISS modem radios) and RemoteTerm, asks
# your bot's settings (setup.py), runs both as services that start at boot,
# and sends the bot to RemoteTerm. Safe to run again: finished steps are skipped.
#
# Options:  --no-services   install and configure only (no systemd, no sudo)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MCP_DIR="${MCP_DIR:-$HOME/meshcore-pi}"
RT_DIR="${REMOTETERM_DIR:-$HOME/Remote-Terminal-for-MeshCore}"
SERVICES=1
[[ "${1:-}" == "--no-services" ]] && SERVICES=0

step() { echo; echo "=== $* ==="; }
die() { echo "ERROR: $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] && die "run this as your normal user, not root (it uses sudo when needed)"

step "1/7 System packages"
if [[ $SERVICES -eq 1 ]]; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq git python3 python3-venv python3-pip curl >/dev/null
    # serial port access for the radio (takes effect for services right away, for you after re-login)
    groups | grep -qw dialout || sudo usermod -aG dialout "$USER"
fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || die "Python 3.11 or newer is needed"

step "2/7 meshcore-pi (the MeshCore node)"
if [[ ! -d "$MCP_DIR/.git" ]]; then
    git clone -q https://github.com/brianwiddas/meshcore-pi.git "$MCP_DIR"
fi
if [[ ! -x "$MCP_DIR/venv/bin/python" ]]; then
    python3 -m venv "$MCP_DIR/venv"
fi
"$MCP_DIR/venv/bin/pip" install -q --disable-pip-version-check pycryptodome aiotools pyserial_asyncio typing-extensions
MCP_DIR="$MCP_DIR" "$HERE/meshcore-pi/patch_meshcore_pi.sh"

step "3/7 Your bot's settings"
MCP_DIR="$MCP_DIR" REMOTETERM_DIR="$RT_DIR" python3 "$HERE/setup.py"
[[ -f "$MCP_DIR/config.toml" ]] || die "no $MCP_DIR/config.toml - run setup.py again and answer yes to the radio question"

step "4/7 meshcore-pi service"
if [[ $SERVICES -eq 1 ]]; then
    sudo tee /etc/systemd/system/meshcore-pi.service >/dev/null <<EOF
[Unit]
Description=meshcore-pi (MeshCore node on a KISS modem radio)
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$MCP_DIR
ExecStart=$MCP_DIR/venv/bin/python $MCP_DIR/meshcore.py $MCP_DIR/config.toml
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable -q meshcore-pi
    sudo systemctl restart meshcore-pi
    if ! grep -q '^privatekey' "$MCP_DIR/config.toml"; then
        echo "Waiting for meshcore-pi to create the node's identity..."
        for _ in $(seq 1 20); do
            grep -q "Created private key" "$MCP_DIR/meshcore.log" 2>/dev/null && break
            sleep 1
        done
        MCP_DIR="$MCP_DIR" python3 "$HERE/setup.py" --save-key
        grep -q '^privatekey' "$MCP_DIR/config.toml" || die "meshcore-pi didn't start - check: journalctl -u meshcore-pi -n 30"
        sudo systemctl restart meshcore-pi
    fi
    sleep 3
    systemctl is-active -q meshcore-pi || die "meshcore-pi isn't running - check: journalctl -u meshcore-pi -n 30"
    echo "meshcore-pi is running."
fi

step "5/7 RemoteTerm (web UI + bot runner)"
if ! command -v uv >/dev/null && [[ ! -x "$HOME/.local/bin/uv" ]]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
if [[ ! -d "$RT_DIR/.git" ]]; then
    git clone -q https://github.com/jkingsman/Remote-Terminal-for-MeshCore.git "$RT_DIR"
fi

step "6/7 RemoteTerm service"
if [[ $SERVICES -eq 1 ]]; then
    if systemctl list-unit-files remoteterm.service >/dev/null 2>&1 && [[ -f /etc/systemd/system/remoteterm.service ]]; then
        echo "RemoteTerm service already installed - skipping (re-run its installer to change it:"
        echo "  bash $RT_DIR/scripts/setup/install_service.sh)"
    else
        cat <<'EOF'
RemoteTerm's own installer runs next and asks a few questions. Answer:
  Transport:        3  (TCP)
  TCP host:         127.0.0.1
  TCP port:         5000
  Frontend:         2  (prebuilt - fastest)
  Enable bots?      y
  Basic Auth?       your choice. Yes = a login for the web page (recommended if
                    others share your network); the bot deploy will ask for it.
EOF
        read -rp "Press Enter to start RemoteTerm's installer..." _
        (cd "$RT_DIR" && bash scripts/setup/install_service.sh)
    fi
    echo "Waiting for RemoteTerm..."
    for _ in $(seq 1 60); do
        code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/api/health || true)
        [[ "$code" == "200" || "$code" == "401" ]] && break
        sleep 2
    done
fi

step "7/7 Send the bot to RemoteTerm"
if [[ $SERVICES -eq 1 ]]; then
    python3 "$HERE/bot/deploy_bot.py" || echo "Deploy didn't finish - fix the problem above, then run: python3 bot/deploy_bot.py"
fi

cat <<EOF

Done!
  Web UI:          http://$(hostname).local:8000  (or http://<this Pi's address>:8000)
  Try it:          send "test" in your test channel, or DM the bot "help"
  Change settings: python3 setup.py --bot   then   python3 bot/deploy_bot.py
  Logs:            journalctl -u meshcore-pi -f      journalctl -u remoteterm -f
EOF
