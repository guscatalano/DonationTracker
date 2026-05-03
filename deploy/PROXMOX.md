# Hosting on Proxmox

Recommended: a small **Debian 12 LXC container**. Lighter than a VM, shares the host kernel, snapshots are instant, and the app needs none of the things LXC restricts.

## 1. Create the container

In the Proxmox UI:

- **Template:** download `debian-12-standard` from the local-template storage if you don't have it (`pveam update && pveam download local debian-12-standard_12.x_amd64.tar.zst`).
- **General:** unprivileged ✓, password set, SSH key optional.
- **Disk:** 8 GB is plenty (photos live in `/opt/donationtracker/data`; resize later if needed).
- **CPU:** 1 core. **Memory:** 256 MB swap, 256 MB RAM (it's a small Python app).
- **Network:** bridge `vmbr0`, IPv4 = DHCP (or static — pick whatever your phones can reach).
- **DNS:** defaults are fine.

Tick "Start at boot".

## 2. Install the app

SSH into the container (or open the console) and run:

```bash
apt update && apt install -y git
git clone https://github.com/guscatalano/DonationTracker.git /tmp/donationtracker
bash /tmp/donationtracker/deploy/install.sh
```

If you're not using git, just `scp -r` the project folder to `/opt/donationtracker/` and run `bash /opt/donationtracker/deploy/install.sh` instead — the script is idempotent and will skip the clone step when the directory already exists.

## 3. Point it at your LLM

```bash
nano /etc/donationtracker.env       # set LLM_BASE_URL to your LLM box's IP
systemctl restart donationtracker
```

## 4. Reach it from your phones

The install script prints the LAN URL at the end, e.g. `http://192.168.6.50:8000`. Bookmark it on each phone.

For HTTPS (recommended on mobile so the camera picker works on iOS without warnings):

```bash
echo 'USE_HTTPS=1' >> /etc/donationtracker.env
systemctl restart donationtracker
```

The first start generates a self-signed cert under `data/certs/` covering localhost + the container's LAN IP, valid for 10 years. Phones will warn the first time — accept it.

## 5. Backups (don't skip this)

The app already writes daily DB snapshots to `data/backups/`. For real safety, take a **Proxmox snapshot** of the container weekly — that captures the SQLite file *and* every photo:

- Datacenter → your container → Backup → "Add" → Storage `local` (or wherever) → Schedule weekly → Mode "Snapshot" → Compression "zstd".

A 1 GB compressed dump covers thousands of items.

## Updating

```bash
cd /opt/donationtracker
sudo -u donationtracker git pull
sudo -u donationtracker .venv/bin/pip install -r requirements.txt
systemctl restart donationtracker
```

## Useful commands

```bash
systemctl status donationtracker       # is it running?
journalctl -u donationtracker -f       # live logs
systemctl restart donationtracker      # bounce it
```
