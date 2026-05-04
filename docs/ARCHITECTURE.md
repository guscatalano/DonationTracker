# Architecture

A reference for future-me (and contributors). Captures the design choices, the
data model, the request flows, and the deliberately-unusual bits that look like
quirks but aren't.

## Top-level shape

```
                ┌────────────────┐                       ┌────────────────┐
   phone /      │  FastAPI app   │   /v1/chat/completions│  Vision LLM    │
   browser   ◀──┤  (uvicorn)     ├──────────────────────▶│  (Ollama, etc.)│
                └────┬───────────┘                       └────────────────┘
                     │                  ▲
                     │                  │ daily snapshot
                     ▼                  │
                ┌────────────────┐ ┌────┴────┐
                │  SQLite DB     │ │ backups │
                │  data/donations.db
                │                │
                ├────────────────┤
                │  data/uploads/ │  ← every photo + receipt (re-encoded JPEG / passthrough PDF)
                ├────────────────┤
                │  data/certs/   │  ← self-signed or user-supplied PEM
                └────────────────┘
```

- One Python process, single SQLite file, image files on disk.
- No background workers, no queue, no Redis. The "background analyzer" is a daemon
  thread; "auto-update" is another daemon thread.
- Frontend is hand-written HTML / CSS / vanilla JS — no build step, no bundler.

## Repo layout

```
app/
  main.py             FastAPI routes + middleware. Imports everything else.
  db.py               SQLite schema + idempotent migrations + daily-snapshot helper.
  fmv.py              FMV table loader + per-category override layer.
  fmv_table.json      Bundled defaults (low/median/high per category) + IRS source links.
  vision.py           OpenAI-compatible client wrapper, model auto-detect, fast health probe.
  tls.py              Self-signed cert generator + "is this our cert?" detector.
  updater.py          Background git-pull daemon + on-demand pull endpoint.
  static/             Frontend. index.html (SPA-ish), sources.html (settings), summary.html, receipt.html.
  fmv_table.json      Default category data + sources/methodology.
deploy/
  install.sh          Idempotent installer for systemd hosts. Multi-distro, sudo-optional.
  run-systemd.sh      Wrapper used by the systemd unit. Reads DB-persisted https_enabled.
  donationtracker.service  systemd unit (Restart=always, AmbientCapabilities=CAP_NET_BIND_SERVICE).
  donationtracker.env.example  Template for /etc/donationtracker.env.
  PROXMOX.md          Walkthrough for the recommended Debian-LXC deployment.
docs/
  ARCHITECTURE.md     This file.
screenshots/          README assets + the seed/capture scripts that produced them.
run.sh / run.ps1      Local-dev launchers (also honor the DB-persisted https_enabled flag).
```

## Data model

All tables live in `data/donations.db`. Schema is created on every startup
(`CREATE TABLE IF NOT EXISTS`), with a small `_MIGRATIONS` list of best-effort
`ALTER`/`CREATE INDEX` statements to evolve older DBs in place. There is no
formal migration framework; ordering is enforced by the list itself.

### `items`
One row per non-cash donation item.

| column | notes |
|---|---|
| `id` | PK |
| `created_at` | `datetime('now')` default — used as the "photo uploaded" timestamp throughout the UI |
| `image_filename` | base name in `data/uploads/`, served at `/uploads/<filename>` |
| `description`, `category_key`, `category_label`, `condition` | filled by vision LLM, editable |
| `fmv_low`, `fmv_median`, `fmv_high`, `estimated_value` | snapshot at time of analysis. Once `value_overridden=1`, `estimated_value` is the user's number and never recomputed |
| `value_overridden` | bool. Toggle in the modal flips the calc behavior |
| `quantity` | int |
| `notes` | free text |
| `status` | `'analyzing'`, `'ready'`, `'failed'` |
| `error` | error message when status='failed' |
| `event_id` | FK → `events.id`, ON DELETE SET NULL. NULL = "unassigned" |
| `donor_id` | FK → `donors.id`, ON DELETE SET NULL |

### `events`
A "drop-off" — a date + a charity that received items.

| column | notes |
|---|---|
| `donation_date`, `charity_name`, `charity_address`, `notes` | self-explanatory |

Items are assigned/unassigned via `POST /api/events/{id}/assign` (with comma-separated `item_ids`) or `assign_all_unassigned`.

### `cash_donations`
Parallel to `items` but for cash gifts. Has its own `donation_date`, `charity_name`, `amount`, `payment_method`, optional `receipt_filename` pointing into `data/uploads/`.

### `donors`
Just `id` + `name UNIQUE`. Used to tag who gave what; lifetime totals are computed via subqueries that include both items and cash.

### `settings`
Generic key/value store. Used keys:

| key | values | applied at |
|---|---|---|
| `llm_base_url` | URL string | startup + on POST |
| `llm_model` | model name | startup + on POST |
| `app_title` | the `<h1>` text | every page load (frontend fetches it) |
| `default_donor_id` | (deprecated — moved to localStorage) | — |
| `https_enabled` | `"1"` / absent | startup, by run scripts |
| `ssl_custom` | `"1"` / absent | auto-detected at startup if cert isn't ours |
| `auto_update_enabled` | `"1"` / absent | every updater tick |
| `auto_update_interval_hours` | float | every updater tick |

### `fmv_overrides`
Per-category `low/median/high` user overrides. Loaded into a module-level dict
(`fmv._OVERRIDES`) at startup; `fmv.lookup(key)` merges override on top of bundled
default. **Never deletes a category** — only overrides existing ones.

## Request flows

### Item upload (the load-bearing flow)

1. `POST /api/items` (multipart, `image=<file>`, optional `donor_id`).
2. `_save_upload()` writes the raw bytes, then re-encodes via Pillow with EXIF rotation and a 1600×1600 cap. JPEG quality 85.
3. The row is inserted with `status='analyzing'`, all FMV fields NULL.
4. **A daemon thread** is spawned to call `vision.analyze_image(path)`. The HTTP response returns immediately with the placeholder row.
5. The thread does the OpenAI vision call, parses the JSON response, runs `fmv.estimate(category_key, condition)` to derive low/median/high/value, then writes:
   ```sql
   UPDATE items SET ...
     WHERE id=? AND status='analyzing'
   ```
   The `AND status='analyzing'` is critical — if the user opened the modal and saved their own values mid-analysis (which flips status to `'ready'`), the AI's UPDATE becomes a no-op. **No locking, no retry — atomic SQL `WHERE` is the entire concurrency story.**
6. Frontend polls `GET /api/items/{id}` every 1.5 s for items in its `PENDING` set. The middleware below ensures fresh data.

### Browser caching pitfall

`Cache-Control: no-store, must-revalidate` is added to **every `/api/*` response** via FastAPI middleware. Without this the polling fetch returns the stale `status='analyzing'` payload from cache and the UI never updates. Frontend also passes `cache: 'no-store'` on the polling and items-list fetches as belt-and-suspenders.

### Drop-off assignment

- `POST /api/events/{id}/assign` (form `item_ids="1,2,3"`).
- `POST /api/events/{id}/assign_all_unassigned` (no body) — single endpoint because mixing `bool` Form fields with `str=Form(...)` parsed badly in FastAPI and the dual-purpose endpoint was misbehaving.
- `POST /api/events/{id}/unassign` (form `item_ids="1,2"`).

The drop-off detail modal in `app.js` calls `openEvent(id)` after every mutation to re-render. It also `loadEvents()` in the background so the underlying list reflects new totals before the modal closes.

### LLM reachability

`vision.health_check(timeout_s)` is the **fast** probe used by `/api/health`. It only hits `GET /v1/models` with a short HTTP timeout (default 5 s, capped at 8 s). It does **not** invoke `vision.get_model()` because that has a fallback that tries to load a model via a chat-completion request — which can sit blocked for 120 s on a cold server. The /sources status panel uses this fast probe and an `AbortController` with a 10-second hard ceiling on the browser side.

### Auto-update

`updater.py` runs a daemon thread that, when `auto_update_enabled` is set, periodically:

1. `git fetch && git status --porcelain --untracked-files=no` — refuses to pull if there are tracked-file modifications. Untracked files don't count (pip caches under `.cache/` would otherwise trip it).
2. `git pull --ff-only`. If anything moved, `pip install -r requirements.txt` runs (in case deps changed).
3. `os._exit(0)` — systemd's `Restart=always` brings the process back with the new code. `StartLimitBurst=5/IntervalSec=300` prevents loops on a broken commit.

The "Check & pull now" button uses the same `pull_now()` function but defers the exit by 1 second so the HTTP response can flush back to the browser first.

## Frontend conventions

- Plain ES6, no framework. `$` and `$$` are aliases for `querySelector` / `querySelectorAll`.
- The "main" page (`/`) is in `index.html` + `app.js`. Tabs are sections in the same DOM, swapped by `showView(name)`. URL hash (`#items`, `#events`, ...) reflects the active tab and is restored on reload; `localStorage` is a fallback.
- `/sources` is the Settings page. Has a sub-nav (Donation / Technical) that also persists via URL hash.
- `/summary/{year}` and `/receipt/{event_id}` are standalone print-friendly pages with their own `<style>` blocks (light backgrounds for printing on white paper).
- All forms are plain HTML; everything goes via `multipart/form-data` so it survives weird mobile keyboards. JSON is read-only.

### "Auto vs manual" value override pattern

`<input name="value_overridden" type="hidden" value="false">` carries the state. The `#valueBadge` button next to the value input is the toggle: clicking it flips the hidden input AND adds/removes `disabled` on the value field. The backend uses the hidden value (not the disabled field's value) to decide whether to recompute or trust the user's number.

`disabled` (not `readonly`) is used because desktop number inputs don't visually grey out under any CSS — only the native `disabled` attribute does. Submit handler explicitly skips the value field when not in override mode.

## Deployment specifics

### systemd unit hardening

```
ProtectSystem=strict
ReadWritePaths=/opt/donationtracker     ← needs to write here for git pull / pip install / data writes
PrivateTmp=true
ProtectHome=true
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
AmbientCapabilities=CAP_NET_BIND_SERVICE  ← lets the service bind ports < 1024 without setcap on /usr/bin/python3
Restart=always
StartLimitBurst=5
StartLimitIntervalSec=300
```

`Restart=always` instead of `on-failure` because the auto-updater exits cleanly (code 0) and we still want systemd to bring it back.

### TLS

- Self-signed cert auto-generated on first start under `data/certs/{cert,key}.pem`, valid 10 years, covers `localhost` + every detected non-loopback IPv4 address (so the same cert works from any phone on the LAN).
- `tls.is_self_signed_by_us(path)` checks the cert's Organization Name field for the `"Donation Tracker (self-signed)"` marker. If absent on startup, `ssl_custom=1` is auto-set in settings — so dropping a real cert into `data/certs/` Just Works without going through the upload UI.
- User-uploaded certs are validated by parsing both files AND comparing public-key DER bytes — protects against accidentally pairing the wrong key with the cert.

### Multi-distro install.sh

Picks `runuser` / `sudo` / `su` for user-drop, `apt-get` / `dnf` / `apk` for package install, `useradd` / `adduser -S` for user creation, falls back gracefully if `systemctl` isn't present (just prints the path to `run-systemd.sh`). Requires root, but tolerates the absence of `sudo`.

## Backups

`db.py:_snapshot_backup()` runs at startup and copies `donations.db` to `data/backups/donations-YYYY-MM-DD.db` once per day. Keeps the last 10. Photos in `data/uploads/` are **not** snapshotted (would balloon disk); the user is expected to back up the whole `data/` directory periodically (or use Proxmox snapshots / the Export ZIP button).

## Things that look weird but aren't

- **The dual `data/` and `data_test/` paths.** `data_test/` is gitignored and is what every smoke test in this repo writes to so they never touch real user data. There's a feedback memory note enforcing this.
- **`os._exit(0)` after auto-update.** Not `sys.exit()` — uvicorn intercepts that and tries to clean up, which can hang. `_exit` is the brute-force exit needed for systemd to restart cleanly.
- **`fmv.CATEGORIES` is the bundled defaults dict, not the effective values.** Use `fmv.lookup(key)` (returns merged) or `fmv.category_options()` (returns merged + per-row override flags) when you want the value the app would actually use.
- **The frontend does its own auto/manual-value computation** to keep the read-only field in sync with category/condition changes. The server doesn't need to be hit just to display the live auto value.
- **`request.url.scheme` is the source of truth for "is HTTPS active right now?"** Not the `https_enabled` setting, which is just the persisted preference for next restart.
- **`Cache-Control: no-store` on every API response** — without it, polling for a status change never sees fresh data.
