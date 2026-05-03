import base64
import json
import os
import re
from pathlib import Path

import httpx
from openai import OpenAI

from .fmv import category_keys

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "sk-local")
LLM_MODEL_OVERRIDE = os.environ.get("LLM_MODEL")
USER_AGENT = os.environ.get("LLM_USER_AGENT", "DonationTracker/1.0 (+local-tax-tool)")

# Provide an httpx client whose User-Agent is ours, and override the SDK's
# X-Stainless-* identifier headers so the server sees DonationTracker, not openai-python.
_http_client = httpx.Client(
    headers={"User-Agent": USER_AGENT},
    timeout=httpx.Timeout(120.0, connect=10.0),
)
_client = OpenAI(
    base_url=LLM_BASE_URL,
    api_key=LLM_API_KEY,
    http_client=_http_client,
    default_headers={
        "User-Agent": USER_AGENT,
        "X-Client-Name": "donationtracker",
    },
)

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
        resp = _client.models.list()
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
            _client.chat.completions.create(
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

    completion = _client.chat.completions.create(
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
