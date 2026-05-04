import json
from pathlib import Path

_TABLE_PATH = Path(__file__).parent / "fmv_table.json"

with _TABLE_PATH.open("r", encoding="utf-8") as f:
    _data = json.load(f)

CATEGORIES: dict[str, dict] = _data["categories"]   # bundled defaults — never mutate
SOURCES: dict = _data.get("_sources", {})
METHODOLOGY: str = _data.get("_methodology", "")

# Per-category user overrides loaded from the DB at startup. Each value is
# {low, median, high}. Defaults to empty; populated via load_overrides().
_OVERRIDES: dict[str, dict] = {}


def load_overrides(rows: list[dict]) -> None:
    _OVERRIDES.clear()
    for r in rows:
        _OVERRIDES[r["category_key"]] = {
            "low": float(r["low"]), "median": float(r["median"]), "high": float(r["high"]),
        }


def set_override(key: str, low: float, median: float, high: float) -> None:
    if key not in CATEGORIES:
        raise ValueError(f"unknown category: {key}")
    _OVERRIDES[key] = {"low": float(low), "median": float(median), "high": float(high)}


def clear_override(key: str) -> None:
    _OVERRIDES.pop(key, None)


def clear_all_overrides() -> None:
    _OVERRIDES.clear()


def is_overridden(key: str) -> bool:
    return key in _OVERRIDES


def overrides_count() -> int:
    return len(_OVERRIDES)


def lookup(category_key: str) -> dict | None:
    base = CATEGORIES.get(category_key)
    if not base:
        return None
    if category_key in _OVERRIDES:
        ov = _OVERRIDES[category_key]
        return {**base, "low": ov["low"], "median": ov["median"], "high": ov["high"]}
    return base


def category_keys() -> list[str]:
    return list(CATEGORIES.keys())


def category_options() -> list[dict]:
    out = []
    for k, v in CATEGORIES.items():
        eff = lookup(k)
        out.append({
            "key": k, "label": v["label"],
            "low": eff["low"], "median": eff["median"], "high": eff["high"],
            "default_low": v["low"], "default_median": v["median"], "default_high": v["high"],
            "is_overridden": k in _OVERRIDES,
        })
    return out


def estimate(category_key: str, condition: str | None) -> dict:
    """Return {category_key, category_label, fmv_low, fmv_median, fmv_high, estimated_value}.
    Falls back to 'other_misc' if category unknown. Uses condition to pick low/median/high."""
    info = lookup(category_key) or lookup("other_misc")
    key = category_key if lookup(category_key) else "other_misc"
    cond = (condition or "good").lower()
    if cond in ("excellent", "like_new", "new"):
        value = info["high"]
    elif cond in ("fair", "poor", "worn"):
        value = info["low"]
    else:
        value = info["median"]
    return {
        "category_key": key,
        "category_label": info["label"],
        "fmv_low": info["low"],
        "fmv_median": info["median"],
        "fmv_high": info["high"],
        "estimated_value": value,
    }
