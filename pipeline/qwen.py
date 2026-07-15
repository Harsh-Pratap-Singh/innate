"""
AI provider: Qwen via Alibaba DashScope (international endpoint).

Exposes the provider-neutral interface the orchestrator expects:
  analyze_frontage(path)                 -> dict   (Qwen-VL, JSON)
  qa_composite(before, after, ref, ...)  -> dict   (Qwen-VL, 3 images)
  edit_image(frontage, ref, prompt)      -> (bytes, note)   (Qwen-Image-Edit, 2 images)

Vision uses the OpenAI-compatible chat endpoint; image editing uses the native
multimodal-generation endpoint (returns an image URL we download).
"""
import base64
import json
from pathlib import Path

import requests

import config
from pipeline.prompts import frontage_prompt, locate_prompt, QA_PROMPT
from pipeline.utils import log

_CHAT = "/compatible-mode/v1/chat/completions"
_MMGEN = "/api/v1/services/aigc/multimodal-generation/generation"


def _headers():
    return {"Authorization": f"Bearer {config.DASHSCOPE_API_KEY}", "Content-Type": "application/json"}


def _data_url(path) -> str:
    p = Path(path)
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()


def _parse_json(text: str) -> dict:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
    if t.endswith("```"):
        t = t[:-3]
    t = t.strip()
    # tolerate leading/trailing prose around the JSON object
    if not t.startswith("{"):
        i, j = t.find("{"), t.rfind("}")
        if i != -1 and j != -1:
            t = t[i:j + 1]
    return json.loads(t)


def _vl(prompt: str, image_paths, temperature=0.1) -> str:
    content = [{"type": "text", "text": prompt}]
    for p in image_paths:
        content.append({"type": "image_url", "image_url": {"url": _data_url(p)}})
    r = requests.post(
        config.DASHSCOPE_BASE + _CHAT, headers=_headers(), timeout=90,
        json={"model": config.QWEN_VL_MODEL,
              "messages": [{"role": "user", "content": content}],
              "temperature": temperature},
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def find_door(image_path: Path) -> dict:
    """Recovery call: entrance-door bbox only (used when analyze left it null)."""
    from pipeline.prompts import DOOR_PROMPT
    try:
        return _parse_json(_vl(DOOR_PROMPT, [image_path]))
    except Exception as e:
        log("judge", f"find_door error: {e}")
        return {"door_bbox": None, "confidence": 0.0}


def locate_venue(image_path: Path, venue_name: str, venue_type: str = "") -> dict:
    """Find the target venue inside a wide street image; returns found/bbox/confidence."""
    try:
        return _parse_json(_vl(locate_prompt(venue_name, venue_type), [image_path]))
    except Exception as e:
        log("judge", f"locate_venue error: {e}")
        return {"found": False, "confidence": 0.0, "venue_bbox": None,
                "reasons": [f"locate error: {e}"]}


def analyze_frontage(image_path: Path, venue_name: str = "", venue_type: str = "") -> dict:
    try:
        return _parse_json(_vl(frontage_prompt(venue_name, venue_type), [image_path]))
    except Exception as e:
        log("judge", f"analyze_frontage error: {e}")
        return {"usable": False, "suitable": False, "door_bbox": None,
                "reasons": [f"judge error: {e}"]}


def qa_composite(before_path, after_path, ref_path,
                 sku_name: str, sku_material: str, ratio_pct: int) -> dict:
    prompt = QA_PROMPT.format(sku_name=sku_name, sku_material=sku_material, ratio_pct=ratio_pct)
    try:
        return _parse_json(_vl(prompt, [before_path, after_path, ref_path]))
    except Exception as e:
        log("qa", f"qa_composite error: {e}")
        return {"accept": False, "reasons": [f"qa error: {e}"]}


def edit_image(frontage_path, ref_path, prompt: str):
    """Qwen-Image-Edit: frontage + product reference -> composited image bytes."""
    content = [{"image": _data_url(frontage_path)},
               {"image": _data_url(ref_path)},
               {"text": prompt}]
    try:
        r = requests.post(
            config.DASHSCOPE_BASE + _MMGEN, headers=_headers(), timeout=180,
            json={"model": config.QWEN_EDIT_MODEL,
                  "input": {"messages": [{"role": "user", "content": content}]},
                  "parameters": {}},
        )
        r.raise_for_status()
        j = r.json()
        parts = j["output"]["choices"][0]["message"]["content"]
        url = next((c["image"] for c in parts if isinstance(c, dict) and c.get("image")), None)
        if not url:
            return None, f"no image in response: {str(parts)[:120]}"
        img = requests.get(url, timeout=60)
        img.raise_for_status()
        return img.content, ""
    except Exception as e:
        log("composite", f"qwen edit error: {e}")
        return None, f"error: {e}"
