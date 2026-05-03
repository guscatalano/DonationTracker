# Donation Tracker

A tiny, local, mobile-first web app for tracking non-cash charitable donations for tax purposes. Snap photos from your phone, a vision LLM auto-fills the description and category, fair-market values are looked up from a bundled IRS-style table, and you get a printable per-charity receipt and a year-end summary suitable for IRS Form 8283.

Self-hosted, no auth, no cloud, no telemetry. All data stays on your machine. Designed to be reachable from any phone on your home network.

## What it does

- **Take a photo** from your iPhone or Android — the device camera opens directly via a single tap.
- **Vision LLM** (any OpenAI-compatible endpoint: Ollama, LM Studio, vLLM, llama.cpp, ...) generates a description, picks a category, and judges the condition.
- **Fair-market value** is looked up from a bundled table modeled on the [Salvation Army](https://satruck.org/donation-value-guide) and [Goodwill](https://www.amazinggoodwill.com/donating/IRS-guidelines) valuation guides per [IRS Pub. 561](https://www.irs.gov/pub/irs-pdf/p561.pdf). Condition picks low / median / high.
- **Group items into "drop-offs"** (date + charity). Print a per-drop-off receipt or a per-tax-year summary that shows totals by charity, category, and donor.
- **Donors** — add household members and tag who gave each item; the per-donor breakdown lets multiple people in one household track their own contributions.
- **Async pipeline** — uploads return instantly so you can snap a dozen photos in a row; the LLM works in the background and the UI updates as each item is analyzed.
- **Export** — one-click ZIP of the SQLite DB, an Excel-readable CSV, and every photo.

## Requirements

- A machine you can reach from your phones — a Linux box, a Raspberry Pi, a Proxmox LXC, or your desktop. Python 3.10+.
- An **OpenAI-compatible vision LLM endpoint** somewhere on your network. Anything that exposes `/v1/chat/completions` with image input works:
  - [Ollama](https://ollama.ai) with a vision model (e.g. `ollama pull llava` or `ollama pull qwen2.5vl`) — easiest.
  - [LM Studio](https://lmstudio.ai) with the local server enabled.
  - vLLM, llama.cpp's `--server`, etc.
  
  The app auto-detects the first vision-capable model on the server, or you can pin one via `LLM_MODEL`.

## Quick start

```bash
git clone https://github.com/guscatalano/DonationTracker.git
cd DonationTracker
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Or use the convenience scripts: `run.ps1` (Windows) / `run.sh` (Linux).

Open `http://<your-machine-ip>:8000` from your phone (must be on the same network).

By default the app expects the LLM at `http://localhost:11434/v1` (Ollama's default). If your LLM lives elsewhere:

```bash
LLM_BASE_URL=http://192.168.1.50:11434/v1 python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Hosting it for real

If you have an always-on machine, deploy properly:

- **Proxmox / any Linux server:** see [`deploy/PROXMOX.md`](deploy/PROXMOX.md). Spin up a Debian 12 LXC, run `bash deploy/install.sh`, edit `/etc/donationtracker.env`. The script creates a non-root user, builds the venv, installs a hardened systemd unit, and prints the LAN URL when done.
- **Any other Linux box:** the same `install.sh` works on a vanilla Debian/Ubuntu host.
- **Windows desktop:** Task Scheduler trigger "At log on" running `run.ps1`.
- **Reach it off-LAN:** install [Tailscale](https://tailscale.com) on the host and your phones — no port forwarding, no public exposure.

## Configuration

All settings are environment variables, all optional:

| Variable | Purpose | Default |
|---|---|---|
| `LLM_BASE_URL` | OpenAI-compatible endpoint | `http://localhost:11434/v1` |
| `LLM_API_KEY` | Most local servers ignore this | `sk-local` |
| `LLM_MODEL` | Pin a specific model name (otherwise auto-detect) | unset |
| `LLM_USER_AGENT` | Identifies this app to your LLM server | `DonationTracker/1.0 (+local-tax-tool)` |
| `DATA_DIR` | Where the SQLite DB and uploaded photos live | `./data` |
| `PORT` | HTTP port | `8000` |
| `USE_HTTPS=1` | Serve over HTTPS — auto-generates a self-signed cert under `data/certs/` covering localhost + LAN IPs, valid 10 years | unset |
| `SSL_KEY_FILE`, `SSL_CERT_FILE` | Use your own PEM key + cert (e.g. from `mkcert` or Let's Encrypt) | unset |

Example with HTTPS and a remote LLM:

```bash
LLM_BASE_URL=http://192.168.1.50:11434/v1 USE_HTTPS=1 ./run.sh
```

## Backups

The app writes a daily snapshot of the SQLite DB to `data/backups/` (last 10 kept) on every startup. For real safety, back up the entire `data/` directory periodically — that's where your photos live too. If you're on Proxmox, weekly LXC snapshots cover both.

## How the values are calculated

For every category the bundled table records three values: **low** (used when the LLM judges the item "fair"), **median** (default; "good"), and **high** ("excellent / like new"). Per-unit value × quantity = the line subtotal.

The values are good-faith estimates that fall within the published Salvation Army and Goodwill ranges for the same category in "good used condition or better" — the standard IRS Publication 526 §A.2 sets for clothing and household items. The full table and the methodology are visible in-app at `/sources`.

## What this is not

- **Not tax advice.** It produces a record you attach to your records and use to fill out IRS Form 8283 if your total exceeds $500. The taxpayer is responsible for the final FMV claimed.
- **Not an appraisal.** Single items or "groups of similar items" worth more than $5,000 require a qualified appraisal under Pub. 561 — this app is not sufficient for that case.
- **Not multi-tenant or auth-protected.** Designed for a trusted home network. Don't expose it to the public internet without putting auth (e.g. a Cloudflare Tunnel or reverse-proxy basic auth) in front of it.

## Stack

- Python + FastAPI + SQLite — single binary equivalent, runs on any Linux/Mac/Windows host.
- Plain HTML / CSS / vanilla JS frontend — no build step, mobile-first.
- OpenAI Python SDK pointed at your local LLM.
- `cryptography` for self-signed TLS certs.

## Contributing / forking

PRs welcome. The codebase is intentionally small and readable. To add a new donation category, edit [`app/fmv_table.json`](app/fmv_table.json) — that's it.

## License

MIT — do whatever you want, no warranty.
