import base64
import json
import os
import re
from pathlib import Path

import httpx
from openai import OpenAI

from .fmv import category_keys

DEFAULT_LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "sk-local")
LLM_MODEL_OVERRIDE = os.environ.get("LLM_MODEL")
USER_AGENT = os.environ.get("LLM_USER_AGENT", "DonationTracker/1.0 (+local-tax-tool)")

# Effective base URL is overridable at runtime via the settings table; main.py
# calls set_base_url() at startup to seed it from the DB.
LLM_BASE_URL = DEFAULT_LLM_BASE_URL

_client: OpenAI | None = None


def _build_client() -> OpenAI:
    http_client = httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=httpx.Timeout(120.0, connect=10.0),
    )
    return OpenAI(
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY,
        http_client=http_client,
        default_headers={
            "User-Agent": USER_AGENT,
            "X-Client-Name": "donationtracker",
        },
    )


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = _build_client()
    return _client


def set_base_url(url: str | None) -> str:
    """Update the active LLM endpoint. Pass None / empty to reset to the env default.
    Returns the now-active URL. Caller is responsible for persisting to settings."""
    global LLM_BASE_URL, _client
    LLM_BASE_URL = (url or "").strip() or DEFAULT_LLM_BASE_URL
    _client = None        # rebuild on next use
    reset_model_cache()   # forget the previously discovered model
    return LLM_BASE_URL


def set_model_override(name: str | None) -> str | None:
    """Pin a specific model name. Pass None / empty to clear and re-enable
    auto-detect. Returns the now-active override (or None)."""
    global LLM_MODEL_OVERRIDE
    LLM_MODEL_OVERRIDE = (name or "").strip() or None
    reset_model_cache()
    return LLM_MODEL_OVERRIDE


def list_available_models(timeout_s: float = 5.0) -> list[dict]:
    """Ask the endpoint what models it has loaded, with a short timeout.
    Returns list of {id, vision_capable}, with vision models sorted to the front."""
    try:
        with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=timeout_s) as c:
            r = c.get(LLM_BASE_URL.rstrip("/") + "/models",
                      headers={"Authorization": f"Bearer {LLM_API_KEY}"})
        if r.status_code >= 400:
            return []
        ids = [m.get("id") for m in (r.json().get("data") or []) if m.get("id")]
    except Exception:
        return []
    items = [{"id": i, "vision_capable": _looks_vision_capable(i)} for i in ids]
    # Vision-capable first, then alphabetical within each group.
    items.sort(key=lambda x: (not x["vision_capable"], x["id"].lower()))
    return items

# Hints for picking a vision-capable model when the server doesn't tell us.
_VISION_HINTS = ("vl", "vision", "llava", "qwen2.5-vl", "qwen2-vl",
                 "gemma3", "minicpm-v", "internvl", "pixtral",
                 "molmo", "moondream", "gpt-4o", "gpt-4-vision",
                 "claude-3", "phi-3-vision", "phi-4")

_cached_model: str | None = None


def _looks_vision_capable(model_id: str) -> bool:
    m = model_id.lower()
    return any(h in m for h in _VISION_HINTS)


def _pick_loaded_model() -> str | None:
    try:
        resp = _get_client().models.list()
    except Exception:
        return None
    ids = [m.id for m in resp.data] if hasattr(resp, "data") else []
    for mid in ids:
        if _looks_vision_capable(mid):
            return mid
    return ids[0] if ids else None


def _try_load_default_model() -> str | None:
    """Some local servers (LM Studio, llama-swap, ollama-style) will load a model
    on first request. Try a tiny chat completion against a likely vision model name."""
    candidates = ["qwen2.5-vl-7b-instruct", "qwen2-vl-7b-instruct",
                  "llava-1.6-mistral-7b", "llava", "moondream",
                  "gemma-3-4b-it", "minicpm-v"]
    for cand in candidates:
        try:
            _get_client().chat.completions.create(
                model=cand,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            return cand
        except Exception:
            continue
    return None


def get_model() -> str:
    global _cached_model
    if LLM_MODEL_OVERRIDE:
        return LLM_MODEL_OVERRIDE
    if _cached_model:
        return _cached_model
    picked = _pick_loaded_model()
    if not picked:
        picked = _try_load_default_model()
    if not picked:
        raise RuntimeError(
            f"No model available at {LLM_BASE_URL}. "
            "Set LLM_MODEL or load a vision-capable model on the server."
        )
    _cached_model = picked
    return picked


def reset_model_cache() -> None:
    global _cached_model
    _cached_model = None


def health_check(timeout_s: float = 5.0) -> dict:
    """Lightweight, fast probe for the status panel. Only hits /v1/models with a
    short timeout. Never tries to load a model. Returns {ok, model?, error?}."""
    if LLM_MODEL_OVERRIDE:
        # Just confirm the endpoint answers anything within the timeout.
        try:
            with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=timeout_s) as c:
                c.get(LLM_BASE_URL.rstrip("/") + "/models")
            return {"ok": True, "model": LLM_MODEL_OVERRIDE}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    try:
        with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=timeout_s) as c:
            r = c.get(LLM_BASE_URL.rstrip("/") + "/models",
                      headers={"Authorization": f"Bearer {LLM_API_KEY}"})
        if r.status_code >= 400:
            return {"ok": False, "error": f"HTTP {r.status_code} from /models"}
        ids = [m.get("id") for m in (r.json().get("data") or []) if m.get("id")]
        if not ids:
            return {"ok": False, "error": "endpoint returned no models — load one in your LLM server"}
        picked = next((m for m in ids if _looks_vision_capable(m)), ids[0])
        return {"ok": True, "model": picked, "vision_capable": _looks_vision_capable(picked),
                "available_count": len(ids)}
    except httpx.ConnectError as e:
        return {"ok": False, "error": f"can't connect: {e}"}
    except httpx.ReadTimeout:
        return {"ok": False, "error": f"timed out after {timeout_s}s"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _image_to_data_url(image_path: Path) -> str:
    suffix = image_path.suffix.lower().lstrip(".")
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png",
            "webp": "webp", "gif": "gif", "heic": "heic"}.get(suffix, "jpeg")
    b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:image/{mime};base64,{b64}"


_PROMPT_TEMPLATE = """You are categorizing a donated item from a photo for a US tax deduction.

Look at the image and respond with ONLY a JSON object (no markdown fences, no commentary) with this exact shape:
{{
  "description": "<short human-readable description, max 12 words>",
  "category_key": "<one of the allowed keys below>",
  "condition": "<one of: excellent, good, fair>",
  "quantity": <integer, default 1; only >1 if multiple identical items are clearly visible>
}}

Pick the single allowed category_key that best matches. If nothing fits, use "other_misc".

Allowed category_keys:
{keys}
"""


def analyze_image(image_path: Path) -> dict:
    """Return {description, category_key, condition, quantity, model}."""
    model = get_model()
    keys_block = ", ".join(category_keys())
    prompt = _PROMPT_TEMPLATE.format(keys=keys_block)
    data_url = _image_to_data_url(image_path)

    completion = _get_client().chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }],
        max_tokens=300,
        temperature=0.2,
    )
    raw = (completion.choices[0].message.content or "").strip()
    parsed = _extract_json(raw)
    return {
        "description": str(parsed.get("description", "")).strip()[:200],
        "category_key": str(parsed.get("category_key", "other_misc")).strip(),
        "condition": str(parsed.get("condition", "good")).strip().lower(),
        "quantity": int(parsed.get("quantity", 1) or 1),
        "model": model,
    }


def _extract_json(text: str) -> dict:
    if not text:
        return {}
    # Strip ```json fences if present
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    # Try direct parse, then first {...} block
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return {}
    return {}
