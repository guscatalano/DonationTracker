import csv
import datetime as dt
import io
import secrets
import threading
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps

from . import fmv, vision
from .db import DB_PATH, UPLOAD_DIR, init_db, tx

app = FastAPI(title="Donation Tracker")
init_db()

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


# ---------- helpers ----------

def _row_to_item(row) -> dict:
    keys = row.keys() if hasattr(row, "keys") else []
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "image_url": f"/uploads/{row['image_filename']}",
        "description": row["description"],
        "category_key": row["category_key"],
        "category_label": row["category_label"],
        "condition": row["condition"],
        "fmv_low": row["fmv_low"],
        "fmv_median": row["fmv_median"],
        "fmv_high": row["fmv_high"],
        "estimated_value": row["estimated_value"],
        "quantity": row["quantity"],
        "notes": row["notes"],
        "status": row["status"],
        "error": row["error"],
        "event_id": row["event_id"],
        "donor_id": row["donor_id"],
        "donor_name": row["donor_name"] if "donor_name" in keys else None,
    }


def _get_setting(key: str) -> str | None:
    with tx() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _set_setting(key: str, value: str | None) -> None:
    with tx() as conn:
        if value is None:
            conn.execute("DELETE FROM settings WHERE key=?", (key,))
        else:
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )


def _analyze_in_background(item_id: int, filename: str) -> None:
    image_path = UPLOAD_DIR / filename
    try:
        result = vision.analyze_image(image_path)
        est = fmv.estimate(result["category_key"], result["condition"])
        with tx() as conn:
            # Atomic: only write if the row is still 'analyzing'. If the user
            # already saved an edit (status='ready') or deleted the row, do nothing.
            conn.execute(
                """UPDATE items SET description=?, category_key=?, category_label=?,
                   condition=?, fmv_low=?, fmv_median=?, fmv_high=?,
                   estimated_value=?, quantity=?, status='ready', error=NULL
                   WHERE id=? AND status='analyzing'""",
                (result["description"], est["category_key"], est["category_label"],
                 result["condition"], est["fmv_low"], est["fmv_median"], est["fmv_high"],
                 est["estimated_value"], max(1, int(result["quantity"])), item_id),
            )
    except Exception as e:
        with tx() as conn:
            conn.execute(
                "UPDATE items SET status='failed', error=? WHERE id=? AND status='analyzing'",
                (str(e)[:500], item_id),
            )


def _row_to_event(row) -> dict:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "donation_date": row["donation_date"],
        "charity_name": row["charity_name"],
        "charity_address": row["charity_address"],
        "notes": row["notes"],
    }


def _save_upload(file: UploadFile) -> str:
    suffix = Path(file.filename or "upload.jpg").suffix.lower() or ".jpg"
    if suffix not in (".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif"):
        suffix = ".jpg"
    name = f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}{suffix}"
    raw_path = UPLOAD_DIR / name
    raw_path.write_bytes(file.file.read())

    # Re-encode to a sane size (max 1600px on long edge), preserve EXIF orientation.
    try:
        with Image.open(raw_path) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((1600, 1600))
            out_name = name if suffix in (".jpg", ".jpeg") else name.rsplit(".", 1)[0] + ".jpg"
            out_path = UPLOAD_DIR / out_name
            im.convert("RGB").save(out_path, "JPEG", quality=85, optimize=True)
            if out_path != raw_path:
                raw_path.unlink(missing_ok=True)
            return out_name
    except Exception:
        # If re-encode fails, keep the original bytes.
        return name


# ---------- routes ----------

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    try:
        model = vision.get_model()
        return {"ok": True, "model": model, "llm_base_url": vision.LLM_BASE_URL}
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": str(e), "llm_base_url": vision.LLM_BASE_URL},
        )


@app.get("/api/categories")
def categories():
    return fmv.category_options()


@app.get("/api/sources")
def sources():
    return {"sources": fmv.SOURCES, "methodology": fmv.METHODOLOGY,
            "categories": [{"key": k, "label": v["label"], "low": v["low"],
                            "median": v["median"], "high": v["high"]}
                           for k, v in fmv.CATEGORIES.items()]}


@app.get("/sources", response_class=FileResponse)
def sources_page():
    return FileResponse(STATIC_DIR / "sources.html")


# --- items ---

@app.post("/api/items")
async def create_item(image: UploadFile = File(...), donor_id: int | None = Form(None)):
    """Upload an image, save immediately with status='analyzing', kick off vision in background."""
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(400, "image/* required")

    filename = _save_upload(image)
    # Validate the donor still exists; if not, fall back to NULL rather than 500.
    # The "default donor" is a per-device choice and is stored client-side
    # (localStorage), then sent on each upload — there's no server-side default.
    if donor_id is not None:
        with tx() as conn:
            if not conn.execute("SELECT 1 FROM donors WHERE id=?", (donor_id,)).fetchone():
                donor_id = None

    with tx() as conn:
        cur = conn.execute(
            """INSERT INTO items
               (image_filename, description, category_key, category_label,
                condition, fmv_low, fmv_median, fmv_high, estimated_value, quantity, status, donor_id)
               VALUES (?,?,?,?,?,?,?,?,?,?, 'analyzing', ?)""",
            (filename, "", None, None, None, None, None, None, None, 1, donor_id),
        )
        item_id = cur.lastrowid
        row = _fetch_item_row(conn, item_id)

    threading.Thread(
        target=_analyze_in_background, args=(item_id, filename), daemon=True
    ).start()
    return _row_to_item(row)


def _fetch_item_row(conn, item_id: int):
    return conn.execute(
        "SELECT i.*, d.name AS donor_name FROM items i "
        "LEFT JOIN donors d ON i.donor_id = d.id WHERE i.id=?",
        (item_id,),
    ).fetchone()


@app.post("/api/items/{item_id}/reanalyze")
def reanalyze(item_id: int):
    with tx() as conn:
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "not found")
        conn.execute("UPDATE items SET status='analyzing', error=NULL WHERE id=?", (item_id,))
        filename = row["image_filename"]
    threading.Thread(
        target=_analyze_in_background, args=(item_id, filename), daemon=True
    ).start()
    return {"ok": True}


@app.get("/api/items")
def list_items(event_id: int | None = None, unassigned: bool = False,
               donor_id: int | None = None, since_id: int | None = None):
    sql = ("SELECT i.*, d.name AS donor_name FROM items i "
           "LEFT JOIN donors d ON i.donor_id = d.id")
    params: list = []
    where = []
    if unassigned:
        where.append("i.event_id IS NULL")
    elif event_id is not None:
        where.append("i.event_id = ?"); params.append(event_id)
    if donor_id is not None:
        where.append("i.donor_id = ?"); params.append(donor_id)
    if since_id is not None:
        where.append("i.id > ?"); params.append(since_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY i.id DESC"
    with tx() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_item(r) for r in rows]


@app.get("/api/items/{item_id}")
def get_item(item_id: int):
    with tx() as conn:
        row = _fetch_item_row(conn, item_id)
    if not row:
        raise HTTPException(404, "not found")
    return _row_to_item(row)


@app.patch("/api/items/{item_id}")
async def update_item(
    item_id: int,
    description: str | None = Form(None),
    category_key: str | None = Form(None),
    condition: str | None = Form(None),
    quantity: int | None = Form(None),
    estimated_value: float | None = Form(None),
    notes: str | None = Form(None),
    donor_id: str | None = Form(None),  # str so we can parse "" to mean clear
):
    with tx() as conn:
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "not found")
        if donor_id is None:
            new_donor = row["donor_id"]
        elif donor_id == "":
            new_donor = None
        else:
            new_donor = int(donor_id)

        new_desc = description if description is not None else row["description"]
        new_cond = condition if condition is not None else row["condition"]
        new_qty = quantity if quantity is not None else row["quantity"]
        new_notes = notes if notes is not None else row["notes"]

        if category_key is not None and category_key != row["category_key"]:
            est = fmv.estimate(category_key, new_cond)
            new_cat_key = est["category_key"]
            new_cat_label = est["category_label"]
            new_low, new_med, new_high = est["fmv_low"], est["fmv_median"], est["fmv_high"]
            new_value = est["estimated_value"] if estimated_value is None else estimated_value
        else:
            new_cat_key = row["category_key"]
            new_cat_label = row["category_label"]
            new_low, new_med, new_high = row["fmv_low"], row["fmv_median"], row["fmv_high"]
            if estimated_value is not None:
                new_value = estimated_value
            elif condition is not None:
                est = fmv.estimate(new_cat_key, new_cond)
                new_value = est["estimated_value"]
            else:
                new_value = row["estimated_value"]

        conn.execute(
            """UPDATE items SET description=?, category_key=?, category_label=?,
               condition=?, fmv_low=?, fmv_median=?, fmv_high=?,
               estimated_value=?, quantity=?, notes=?, donor_id=?,
               status='ready', error=NULL
               WHERE id=?""",
            (new_desc, new_cat_key, new_cat_label, new_cond,
             new_low, new_med, new_high, new_value, new_qty, new_notes, new_donor, item_id),
        )
        row = _fetch_item_row(conn, item_id)
    return _row_to_item(row)


@app.delete("/api/items/{item_id}")
def delete_item(item_id: int):
    with tx() as conn:
        row = conn.execute("SELECT image_filename FROM items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "not found")
        conn.execute("DELETE FROM items WHERE id=?", (item_id,))
    try:
        (UPLOAD_DIR / row["image_filename"]).unlink(missing_ok=True)
    except Exception:
        pass
    return {"ok": True}


# --- events ---

@app.post("/api/events")
async def create_event(
    charity_name: str = Form(...),
    donation_date: str = Form(...),
    charity_address: str | None = Form(None),
    notes: str | None = Form(None),
):
    with tx() as conn:
        cur = conn.execute(
            """INSERT INTO events (donation_date, charity_name, charity_address, notes)
               VALUES (?,?,?,?)""",
            (donation_date, charity_name, charity_address, notes),
        )
        ev_id = cur.lastrowid
        row = conn.execute("SELECT * FROM events WHERE id=?", (ev_id,)).fetchone()
    return _row_to_event(row)


@app.get("/api/events")
def list_events():
    with tx() as conn:
        rows = conn.execute(
            "SELECT * FROM events ORDER BY donation_date DESC, id DESC"
        ).fetchall()
        events = [_row_to_event(r) for r in rows]
        # attach per-event totals
        for ev in events:
            agg = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(estimated_value*quantity),0) AS total "
                "FROM items WHERE event_id=?",
                (ev["id"],),
            ).fetchone()
            ev["item_count"] = agg["n"]
            ev["total_value"] = agg["total"]
    return events


@app.get("/api/events/{event_id}")
def get_event(event_id: int):
    with tx() as conn:
        row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if not row:
            raise HTTPException(404, "not found")
        ev = _row_to_event(row)
        items = [_row_to_item(r) for r in conn.execute(
            "SELECT i.*, d.name AS donor_name FROM items i "
            "LEFT JOIN donors d ON i.donor_id = d.id "
            "WHERE i.event_id=? ORDER BY i.id ASC", (event_id,)
        ).fetchall()]
    ev["items"] = items
    ev["item_count"] = len(items)
    ev["total_value"] = sum((i["estimated_value"] or 0) * (i["quantity"] or 1) for i in items)
    return ev


@app.post("/api/events/{event_id}/assign")
async def assign_items(event_id: int, item_ids: str = Form("")):
    """Assign specific items (comma-separated ids) to this event."""
    with tx() as conn:
        ev = conn.execute("SELECT id FROM events WHERE id=?", (event_id,)).fetchone()
        if not ev:
            raise HTTPException(404, "event not found")
        ids = [int(x) for x in item_ids.split(",") if x.strip()]
        if ids:
            placeholders = ",".join("?" * len(ids))
            conn.execute(
                f"UPDATE items SET event_id=? WHERE id IN ({placeholders})",
                [event_id, *ids],
            )
    return get_event(event_id)


@app.post("/api/events/{event_id}/assign_all_unassigned")
def assign_all_unassigned(event_id: int):
    """Assign every currently-unassigned item to this event."""
    with tx() as conn:
        ev = conn.execute("SELECT id FROM events WHERE id=?", (event_id,)).fetchone()
        if not ev:
            raise HTTPException(404, "event not found")
        cur = conn.execute(
            "UPDATE items SET event_id=? WHERE event_id IS NULL", (event_id,)
        )
        moved = cur.rowcount
    payload = get_event(event_id)
    payload["moved"] = moved
    return payload


@app.post("/api/events/{event_id}/unassign")
async def unassign_items(event_id: int, item_ids: str = Form(...)):
    ids = [int(x) for x in item_ids.split(",") if x.strip()]
    if not ids:
        return get_event(event_id)
    with tx() as conn:
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE items SET event_id=NULL WHERE event_id=? AND id IN ({placeholders})",
            [event_id, *ids],
        )
    return get_event(event_id)


@app.delete("/api/events/{event_id}")
def delete_event(event_id: int):
    with tx() as conn:
        row = conn.execute("SELECT id FROM events WHERE id=?", (event_id,)).fetchone()
        if not row:
            raise HTTPException(404, "not found")
        conn.execute("UPDATE items SET event_id=NULL WHERE event_id=?", (event_id,))
        conn.execute("DELETE FROM events WHERE id=?", (event_id,))
    return {"ok": True}


@app.get("/receipt/{event_id}", response_class=FileResponse)
def receipt(event_id: int):
    # Receipt is rendered client-side; serve the same shell.
    return FileResponse(STATIC_DIR / "receipt.html")


# --- year-end tax summary ---

@app.get("/api/summary/{year}")
def summary(year: int):
    """Tax-year summary grouped by charity. Year is matched against events.donation_date (YYYY-...)."""
    y = f"{year:04d}-"
    with tx() as conn:
        events = conn.execute(
            "SELECT * FROM events WHERE donation_date LIKE ? ORDER BY donation_date ASC, id ASC",
            (y + "%",),
        ).fetchall()
        by_charity: dict[str, dict] = {}
        grand_total = 0.0
        grand_items = 0
        cat_totals: dict[str, dict] = {}
        for ev in events:
            ev_d = _row_to_event(ev)
            items = [_row_to_item(r) for r in conn.execute(
                "SELECT i.*, d.name AS donor_name FROM items i "
                "LEFT JOIN donors d ON i.donor_id = d.id "
                "WHERE i.event_id=? ORDER BY i.id ASC", (ev["id"],)
            ).fetchall()]
            ev_d["items"] = items
            ev_d["item_count"] = len(items)
            ev_d["total_value"] = sum((i["estimated_value"] or 0) * (i["quantity"] or 1) for i in items)
            grand_total += ev_d["total_value"]
            grand_items += ev_d["item_count"]
            key = ev["charity_name"].strip()
            grp = by_charity.setdefault(key, {
                "charity_name": key,
                "charity_address": ev["charity_address"],
                "events": [],
                "total_value": 0.0,
                "item_count": 0,
            })
            grp["events"].append(ev_d)
            grp["total_value"] += ev_d["total_value"]
            grp["item_count"] += ev_d["item_count"]
            if not grp["charity_address"] and ev["charity_address"]:
                grp["charity_address"] = ev["charity_address"]
            for i in items:
                k = i["category_label"] or "Other"
                ct = cat_totals.setdefault(k, {"category": k, "qty": 0, "value": 0.0})
                ct["qty"] += i["quantity"] or 1
                ct["value"] += (i["estimated_value"] or 0) * (i["quantity"] or 1)

        unassigned_in_year = conn.execute(
            "SELECT COUNT(*) AS n FROM items WHERE event_id IS NULL "
            "AND substr(created_at,1,4)=?",
            (str(year),),
        ).fetchone()["n"]

    # Per-donor breakdown for the year
    donor_totals: dict[str, dict] = {}
    for grp in by_charity.values():
        for ev in grp["events"]:
            for i in ev["items"]:
                key = i.get("donor_name") or "(unspecified donor)"
                dt_ = donor_totals.setdefault(key, {
                    "donor_name": key,
                    "donor_id": i.get("donor_id"),
                    "item_count": 0,
                    "total_value": 0.0,
                    "by_category": {},
                })
                qty = i["quantity"] or 1
                val = (i["estimated_value"] or 0) * qty
                dt_["item_count"] += qty
                dt_["total_value"] += val
                ck = i["category_label"] or "Other"
                cb = dt_["by_category"].setdefault(ck, {"category": ck, "qty": 0, "value": 0.0})
                cb["qty"] += qty
                cb["value"] += val
    for d in donor_totals.values():
        d["by_category"] = sorted(d["by_category"].values(), key=lambda x: -x["value"])

    return {
        "year": year,
        "charities": list(by_charity.values()),
        "category_totals": sorted(cat_totals.values(), key=lambda x: -x["value"]),
        "donor_totals": sorted(donor_totals.values(), key=lambda x: -x["total_value"]),
        "grand_total": grand_total,
        "grand_items": grand_items,
        "event_count": len(events),
        "unassigned_in_year": unassigned_in_year,
        "form_8283_required": grand_total > 500,
        "appraisal_required_threshold_hit": any(
            ((i["estimated_value"] or 0) * (i["quantity"] or 1)) > 5000
            for grp in by_charity.values()
            for ev in grp["events"]
            for i in ev["items"]
        ),
    }


# --- donors ---

@app.get("/api/donors")
def list_donors():
    with tx() as conn:
        rows = conn.execute(
            "SELECT d.id, d.name, d.created_at, "
            "COUNT(i.id) AS item_count, "
            "COALESCE(SUM(i.estimated_value*i.quantity),0) AS total_value "
            "FROM donors d LEFT JOIN items i ON i.donor_id = d.id "
            "GROUP BY d.id ORDER BY d.name COLLATE NOCASE ASC"
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/donors")
async def create_donor(name: str = Form(...)):
    name = name.strip()
    if not name:
        raise HTTPException(400, "name is required")
    with tx() as conn:
        try:
            cur = conn.execute("INSERT INTO donors(name) VALUES(?)", (name,))
        except Exception as e:
            raise HTTPException(409, f"donor already exists: {e}")
        d = conn.execute("SELECT * FROM donors WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(d)


@app.patch("/api/donors/{donor_id}")
async def rename_donor(donor_id: int, name: str = Form(...)):
    name = name.strip()
    if not name:
        raise HTTPException(400, "name is required")
    with tx() as conn:
        cur = conn.execute("UPDATE donors SET name=? WHERE id=?", (name, donor_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "not found")
        d = conn.execute("SELECT * FROM donors WHERE id=?", (donor_id,)).fetchone()
    return dict(d)


@app.delete("/api/donors/{donor_id}")
def delete_donor(donor_id: int):
    with tx() as conn:
        # Items keep their record (donor_id becomes NULL via FK ON DELETE SET NULL,
        # but our migration didn't enforce that for old rows; do it explicitly).
        conn.execute("UPDATE items SET donor_id=NULL WHERE donor_id=?", (donor_id,))
        cur = conn.execute("DELETE FROM donors WHERE id=?", (donor_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "not found")
    # Clear default if it pointed here
    if _get_setting("default_donor_id") == str(donor_id):
        _set_setting("default_donor_id", None)
    return {"ok": True}


# Note: there is no server-side "default donor". Each device chooses its own
# default and stores it in localStorage; that choice is sent with every upload.


@app.get("/api/years")
def years():
    """All years that have at least one donation event."""
    with tx() as conn:
        rows = conn.execute(
            "SELECT DISTINCT substr(donation_date,1,4) AS y FROM events ORDER BY y DESC"
        ).fetchall()
    return [int(r["y"]) for r in rows if r["y"] and r["y"].isdigit()]


@app.get("/api/export")
def export_zip():
    """Stream a ZIP containing the SQLite DB, an Excel-readable CSV, and every photo."""
    with tx() as conn:
        rows = conn.execute("""
            SELECT i.*, e.donation_date, e.charity_name, e.charity_address, e.notes AS event_notes,
                   d.name AS donor_name
            FROM items i
            LEFT JOIN events e ON i.event_id = e.id
            LEFT JOIN donors d ON i.donor_id = d.id
            ORDER BY e.donation_date IS NULL, e.donation_date ASC, i.id ASC
        """).fetchall()

    csv_buf = io.StringIO()
    csv_buf.write("﻿")  # UTF-8 BOM so Excel detects encoding
    writer = csv.writer(csv_buf)
    writer.writerow([
        "item_id", "photo_uploaded_at", "donor", "donation_date", "charity_name", "charity_address",
        "description", "category", "category_key", "condition",
        "fmv_low", "fmv_median", "fmv_high", "value_per_unit", "quantity", "subtotal",
        "item_notes", "drop_off_notes", "status", "image_file", "image_relpath",
    ])
    for r in rows:
        qty = r["quantity"] or 1
        val = r["estimated_value"] or 0
        writer.writerow([
            r["id"], r["created_at"], r["donor_name"] or "",
            r["donation_date"] or "", r["charity_name"] or "", r["charity_address"] or "",
            r["description"] or "", r["category_label"] or "", r["category_key"] or "", r["condition"] or "",
            r["fmv_low"] if r["fmv_low"] is not None else "",
            r["fmv_median"] if r["fmv_median"] is not None else "",
            r["fmv_high"] if r["fmv_high"] is not None else "",
            val, qty, round(val * qty, 2),
            r["notes"] or "", r["event_notes"] or "", r["status"] or "",
            r["image_filename"], f"images/{r['image_filename']}",
        ])
    csv_bytes = csv_buf.getvalue().encode("utf-8")

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("donations.csv", csv_bytes)
        if DB_PATH.exists():
            zf.write(DB_PATH, "donations.db")
        readme = (
            "Donation Tracker export\n"
            "=======================\n\n"
            "donations.csv  - one row per item, opens in Excel (UTF-8 with BOM).\n"
            "                 The 'image_relpath' column points to the file in this archive.\n"
            "donations.db   - SQLite database. Open with any SQLite tool, or drop back into\n"
            "                 the app's data/ directory to restore.\n"
            "images/        - every photo, named by its original upload filename.\n"
            "\nFair-market values are based on Goodwill / Salvation Army valuation guides\n"
            "per IRS Pub. 561.\n"
        )
        zf.writestr("README.txt", readme)
        for img in UPLOAD_DIR.iterdir():
            if img.is_file():
                zf.write(img, f"images/{img.name}")
    zip_buf.seek(0)

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"donations-export-{stamp}.zip"
    return StreamingResponse(
        zip_buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/summary", response_class=FileResponse)
def summary_page_default():
    return FileResponse(STATIC_DIR / "summary.html")


@app.get("/summary/{year}", response_class=FileResponse)
def summary_page(year: int):
    return FileResponse(STATIC_DIR / "summary.html")
