"""Seed a scratch DATA_DIR with realistic-looking fake data for screenshots.
Generates colorful synthetic photos and writes them through the live API +
direct DB inserts so all the relations line up correctly.
"""
from __future__ import annotations

import os
import random
import sqlite3
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

DATA_DIR = Path(os.environ.get("DATA_DIR", "./screenshots_data")).resolve()
UPLOADS = DATA_DIR / "uploads"
DB = DATA_DIR / "donations.db"


def _gradient(w: int, h: int, c1, c2) -> Image.Image:
    base = Image.new("RGB", (w, h), c1)
    top = Image.new("RGB", (w, h), c2)
    mask = Image.new("L", (w, h))
    for y in range(h):
        v = int(255 * y / h)
        for x in range(w):
            mask.putpixel((x, y), v)
    base.paste(top, (0, 0), mask)
    return base


def fake_photo(name: str, palette: tuple, label: str) -> bytes:
    """Generate a 600x600 'photo' that looks like an item against a soft
    background — colored gradient + a centered label."""
    bg1, bg2 = palette
    img = _gradient(600, 600, bg1, bg2).filter(ImageFilter.GaussianBlur(8))
    d = ImageDraw.Draw(img)
    # Centered "object" rectangle with rounded look
    d.rounded_rectangle((90, 80, 510, 520), radius=24,
                        fill=(245, 245, 248), outline=(40, 40, 50), width=3)
    try:
        font = ImageFont.truetype("arial.ttf", 28)
    except Exception:
        font = ImageFont.load_default()
    bbox = d.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((600 - tw) / 2, (600 - th) / 2 - 10), label,
           fill=(50, 60, 80), font=font)
    out = BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()


PALETTES = [
    ((142, 197, 252), (224, 195, 252)),
    ((253, 200, 178), (255, 236, 210)),
    ((161, 196, 253), (194, 233, 251)),
    ((255, 154, 158), (250, 208, 196)),
    ((168, 237, 234), (254, 214, 227)),
    ((210, 153, 194), (254, 249, 215)),
]


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS.mkdir(parents=True, exist_ok=True)

    # Ensure DB schema is created by importing the app
    os.environ["DATA_DIR"] = str(DATA_DIR)
    from app.db import init_db
    init_db()

    items = [
        ("Wool sweater, navy blue", "clothing_sweater",       "Sweater",            10,  "good", 1, "Wool sweater"),
        ("Children's books, hardcover (4)", "media_book_hardcover", "Hardcover book", 3, "good", 4, "Hardcover books"),
        ("Cast iron skillet, 10\"", "kitchen_cookware",       "Pots and pans",      20,  "good", 1, "Cast iron skillet"),
        ("Floor lamp with shade",  "furniture_lamp",          "Lamp",                12, "good", 1, "Floor lamp"),
        ("Wooden dining chair",    "furniture_dining_chair",  "Dining chair",        15, "good", 1, "Dining chair"),
        ("Winter coat, men's L",   "clothing_winter_coat",    "Winter coat",         35, "good", 1, "Winter coat"),
        ("Board game, family size","toys_board_game",         "Board game/puzzle",   5,  "good", 1, "Board game"),
        ("Toaster, 4-slice",       "kitchen_small_appliance", "Small kitchen appliance", 15, "good", 1, "Toaster"),
        ("Bath towels, set of 4",  "linens_towel",            "Towel",               3,  "good", 4, "Bath towels"),
    ]

    with sqlite3.connect(DB) as c:
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        # Donors
        for name in ("Gus", "Lauren"):
            c.execute("INSERT OR IGNORE INTO donors(name) VALUES(?)", (name,))
        donors = {r["name"]: r["id"] for r in c.execute("SELECT id, name FROM donors")}

        # Drop-off events
        c.execute("INSERT INTO events(donation_date, charity_name, charity_address) VALUES(?,?,?)",
                  ("2026-04-15", "Goodwill", "1827 Castro Street, Mountain View CA"))
        gw_event = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute("INSERT INTO events(donation_date, charity_name, charity_address) VALUES(?,?,?)",
                  ("2026-03-08", "Salvation Army", "1500 Valencia St, San Francisco CA"))
        sa_event = c.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Items (some assigned to events, some unassigned)
        for i, (desc, cat_key, cat_label, value, condition, qty, label) in enumerate(items):
            palette = PALETTES[i % len(PALETTES)]
            fname = f"item-{i+1:02d}.jpg"
            (UPLOADS / fname).write_bytes(fake_photo(fname, palette, label))
            # First 5 -> Goodwill drop-off, next 2 -> Salvation Army, rest unassigned
            if i < 5:
                event_id = gw_event
            elif i < 7:
                event_id = sa_event
            else:
                event_id = None
            donor = donors["Gus"] if i % 3 != 0 else donors["Lauren"]
            low = max(1, value // 3); high = value * 2 + 1
            c.execute(
                """INSERT INTO items(image_filename, description, category_key, category_label,
                   condition, fmv_low, fmv_median, fmv_high, estimated_value, quantity, status,
                   event_id, donor_id, value_overridden)
                   VALUES(?,?,?,?,?,?,?,?,?,?, 'ready', ?, ?, 0)""",
                (fname, desc, cat_key, cat_label, condition,
                 low, value, high, value, qty, event_id, donor),
            )

        # Cash gifts
        c.execute("""INSERT INTO cash_donations(donation_date, charity_name, charity_address,
                     amount, payment_method, donor_id, notes)
                     VALUES(?,?,?,?,?,?,?)""",
                  ("2026-02-14", "American Red Cross", "431 18th St NW, Washington DC",
                   125.00, "card", donors["Gus"], "Annual February gift"))
        c.execute("""INSERT INTO cash_donations(donation_date, charity_name, charity_address,
                     amount, payment_method, donor_id, notes)
                     VALUES(?,?,?,?,?,?,?)""",
                  ("2026-04-02", "NPR", "1111 N Capitol St NE, Washington DC",
                   60.00, "card", donors["Lauren"], "Spring drive"))

        # One overridden FMV (for the screenshot)
        c.execute("""INSERT INTO fmv_overrides(category_key, low, median, high)
                     VALUES('media_book_hardcover', 2, 6, 12)""")

        c.commit()

    print(f"Seeded {DB}")
    print(f"Uploads: {UPLOADS}")


if __name__ == "__main__":
    main()
