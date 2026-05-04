import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data")).resolve()
# Backup-on-startup: keep last 10 daily snapshots of the SQLite file so accidental
# deletes / schema changes can be recovered.
_BACKUP_DIR = DATA_DIR / "backups"
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "donations.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    image_filename TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category_key TEXT,
    category_label TEXT,
    condition TEXT,
    fmv_low REAL,
    fmv_median REAL,
    fmv_high REAL,
    estimated_value REAL,
    quantity INTEGER NOT NULL DEFAULT 1,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'analyzing',
    error TEXT,
    event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
    donor_id INTEGER REFERENCES donors(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    donation_date TEXT NOT NULL,
    charity_name TEXT NOT NULL,
    charity_address TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS donors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS cash_donations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    donation_date TEXT NOT NULL,
    charity_name TEXT NOT NULL,
    charity_address TEXT,
    amount REAL NOT NULL,
    payment_method TEXT NOT NULL DEFAULT 'cash',
    donor_id INTEGER REFERENCES donors(id) ON DELETE SET NULL,
    receipt_filename TEXT,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_items_event ON items(event_id);
CREATE INDEX IF NOT EXISTS idx_cash_date ON cash_donations(donation_date);
"""

# Idempotent migrations for older DBs. Run AFTER SCHEMA so the column exists
# before the index that references it. Each statement is best-effort.
_MIGRATIONS = [
    "ALTER TABLE items ADD COLUMN donor_id INTEGER REFERENCES donors(id) ON DELETE SET NULL",
    "ALTER TABLE items ADD COLUMN value_overridden INTEGER NOT NULL DEFAULT 0",
    "CREATE INDEX IF NOT EXISTS idx_items_donor ON items(donor_id)",
]


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.commit()
    _snapshot_backup()


def _snapshot_backup() -> None:
    """Once per day on startup, copy donations.db into data/backups/. Keep last 10."""
    if not DB_PATH.exists() or DB_PATH.stat().st_size == 0:
        return
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    import datetime, shutil
    today = datetime.date.today().isoformat()
    dest = _BACKUP_DIR / f"donations-{today}.db"
    if not dest.exists():
        shutil.copy2(DB_PATH, dest)
    backups = sorted(_BACKUP_DIR.glob("donations-*.db"))
    for old in backups[:-10]:
        old.unlink(missing_ok=True)


@contextmanager
def tx():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
