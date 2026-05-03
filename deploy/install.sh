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
    echo "must be run as root"; exit 1
fi

echo "==> apt deps"
apt-get update
apt-get install -y python3 python3-venv python3-pip git ca-certificates

echo "==> service user"
id -u "$USER_NAME" >/dev/null 2>&1 || useradd --system --home "$INSTALL_DIR" --shell /usr/sbin/nologin "$USER_NAME"

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
sudo -u "$USER_NAME" python3 -m venv "$INSTALL_DIR/.venv"
sudo -u "$USER_NAME" "$INSTALL_DIR/.venv/bin/pip" install --upgrade pip wheel
sudo -u "$USER_NAME" "$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

echo "==> env file"
if [ ! -f /etc/donationtracker.env ]; then
    cp "$INSTALL_DIR/deploy/donationtracker.env.example" /etc/donationtracker.env
    chmod 640 /etc/donationtracker.env
    chown root:"$USER_NAME" /etc/donationtracker.env
    echo "    edit /etc/donationtracker.env to set LLM_BASE_URL, then:"
    echo "    systemctl restart donationtracker"
fi

echo "==> systemd unit"
install -m 644 "$INSTALL_DIR/deploy/donationtracker.service" /etc/systemd/system/donationtracker.service
systemctl daemon-reload
systemctl enable --now donationtracker

echo
echo "Done. App should be reachable at:"
ip -4 -o addr show scope global | awk '{split($4,a,"/"); print "  http://"a[1]":8000"}'
echo
echo "Check status:  systemctl status donationtracker"
echo "Tail logs:     journalctl -u donationtracker -f"
