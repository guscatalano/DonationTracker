#!/usr/bin/env bash
# One-shot installer for a fresh Debian 12 / Ubuntu 22+ LXC or VM.
# Run as root:
#   curl -fsSL <raw-url-to-this-file> | bash
# Or after cloning:
#   sudo bash deploy/install.sh
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/guscatalano/DonationTracker.git}"
INSTALL_DIR="/opt/donationtracker"
USER_NAME="donationtracker"

if [ "$(id -u)" -ne 0 ]; then
    echo "must be run as root (e.g. 'sudo bash $0', or just 'bash $0' from a root shell)"; exit 1
fi

# Run a command as a different user without assuming sudo exists. Picks the
# first available of: runuser (util-linux), sudo, su.
as_user() {
    local user="$1"; shift
    if command -v runuser >/dev/null 2>&1; then
        runuser -u "$user" -- "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo -u "$user" -- "$@"
    elif command -v su >/dev/null 2>&1; then
        # su needs a single shell-string. Quote each arg.
        local cmd=""
        for a in "$@"; do cmd="$cmd $(printf %q "$a")"; done
        su -s /bin/sh -c "$cmd" "$user"
    else
        echo "ERROR: need one of runuser / sudo / su to drop to user $user"; return 1
    fi
}

echo "==> deps"
if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y python3 python3-venv python3-pip git ca-certificates
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y python3 python3-pip git ca-certificates
elif command -v apk >/dev/null 2>&1; then
    apk add --no-cache python3 py3-pip py3-virtualenv git ca-certificates bash
else
    echo "WARN: unknown package manager — make sure python3, python3-venv, and git are installed."
fi

echo "==> service user"
id -u "$USER_NAME" >/dev/null 2>&1 || \
    useradd --system --home "$INSTALL_DIR" --shell /usr/sbin/nologin "$USER_NAME" 2>/dev/null || \
    adduser -S -h "$INSTALL_DIR" -s /sbin/nologin "$USER_NAME"   # alpine fallback

echo "==> code at $INSTALL_DIR"
if [ -d "$INSTALL_DIR/.git" ]; then
    git -C "$INSTALL_DIR" pull --ff-only
else
    if [ ! -d "$INSTALL_DIR" ]; then
        git clone "$REPO_URL" "$INSTALL_DIR"
    fi
fi
mkdir -p "$INSTALL_DIR/data"
chown -R "$USER_NAME:$USER_NAME" "$INSTALL_DIR"

echo "==> python venv"
as_user "$USER_NAME" python3 -m venv "$INSTALL_DIR/.venv"
as_user "$USER_NAME" "$INSTALL_DIR/.venv/bin/pip" install --upgrade pip wheel
as_user "$USER_NAME" "$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

echo "==> env file"
if [ ! -f /etc/donationtracker.env ]; then
    cp "$INSTALL_DIR/deploy/donationtracker.env.example" /etc/donationtracker.env
    chmod 640 /etc/donationtracker.env
    chown root:"$USER_NAME" /etc/donationtracker.env
    echo "    edit /etc/donationtracker.env to set LLM_BASE_URL, then:"
    echo "    systemctl restart donationtracker"
fi

echo "==> systemd unit"
chmod +x "$INSTALL_DIR/deploy/run-systemd.sh"
if [ -d /etc/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
    install -m 644 "$INSTALL_DIR/deploy/donationtracker.service" /etc/systemd/system/donationtracker.service
    systemctl daemon-reload
    systemctl enable --now donationtracker
    echo "Service installed and started."
else
    echo "WARN: systemd not detected. To run manually:"
    echo "  $INSTALL_DIR/deploy/run-systemd.sh"
    echo "(Or write an init.d / OpenRC / supervisor unit pointing at that script.)"
fi

echo
echo "Done. App should be reachable at:"
if command -v ip >/dev/null 2>&1; then
    ip -4 -o addr show scope global 2>/dev/null | awk '{split($4,a,"/"); print "  http://"a[1]":8000"}'
elif command -v hostname >/dev/null 2>&1; then
    for ip in $(hostname -I 2>/dev/null); do echo "  http://$ip:8000"; done
fi
echo
if command -v systemctl >/dev/null 2>&1; then
    echo "Check status:  systemctl status donationtracker"
    echo "Tail logs:     journalctl -u donationtracker -f"
fi
