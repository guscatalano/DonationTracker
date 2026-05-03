import json
from pathlib import Path

_TABLE_PATH = Path(__file__).parent / "fmv_table.json"

with _TABLE_PATH.open("r", encoding="utf-8") as f:
    _data = json.load(f)

CATEGORIES: dict[str, dict] = _data["categories"]
SOURCES: dict = _data.get("_sources", {})
METHODOLOGY: str = _data.get("_methodology", "")


def lookup(category_key: str) -> dict | None:
    return CATEGORIES.get(category_key)


def category_keys() -> list[str]:
    return list(CATEGORIES.keys())


def category_options() -> list[dict]:
    return [
        {"key": k, "label": v["label"],
         "low": v["low"], "median": v["median"], "high": v["high"]}
        for k, v in CATEGORIES.items()
    ]


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
