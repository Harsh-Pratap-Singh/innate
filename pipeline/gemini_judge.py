"""
The automated "eyes" of the pipeline (Gemini vision). At 5,000 venues/week no
human eyeballs anything, so every visual accept/reject decision is a model call
returning structured JSON.

Two judgements:
  analyze_frontage()  - is this a usable, entrance-centred capture of a
                        cafe/salon/restaurant-type venue, AND is the frontage
                        bare enough that planters would help? Also returns the
                        door bounding box that anchors real-world scale.
  qa_composite()      - is the finished composite good enough to send an owner?
                        (identity / scale / scene-integrity / grounding / etc.)
"""
import json
from functools import lru_cache
from pathlib import Path

from PIL import Image

import config
from pipeline.prompts import frontage_prompt, locate_prompt, QA_PROMPT
from pipeline.utils import log

_FENCE = ("```json", "```")


@lru_cache(maxsize=1)
def _client():
    from google import genai
    return genai.Client(api_key=config.GEMINI_API_KEY)


def _parse_json(text: str) -> dict:
    t = (text or "").strip()
    if t.startswith(_FENCE[0]):
        t = t[len(_FENCE[0]):]
    elif t.startswith("```"):
        t = t[3:]
    if t.endswith("```"):
        t = t[:-3]
    return json.loads(t.strip())


def _json_call(parts, temperature=0.1) -> dict:
    from google.genai import types
    resp = _client().models.generate_content(
        model=config.JUDGE_MODEL,
        contents=parts,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=temperature,
        ),
    )
    return _parse_json(resp.text)


def locate_venue(image_path: Path, venue_name: str, venue_type: str = "") -> dict:
    """Find the target venue inside a wide street image; returns found/bbox/confidence."""
    img = Image.open(image_path).convert("RGB")
    try:
        return _json_call([locate_prompt(venue_name, venue_type), img])
    except Exception as e:
        log("judge", f"locate_venue error: {e}")
        return {"found": False, "confidence": 0.0, "venue_bbox": None,
                "reasons": [f"locate error: {e}"]}


def find_door(image_path: Path) -> dict:
    """Recovery call: entrance-door bbox only (used when analyze left it null)."""
    from pipeline.prompts import DOOR_PROMPT
    img = Image.open(image_path).convert("RGB")
    try:
        return _json_call([DOOR_PROMPT, img])
    except Exception as e:
        log("judge", f"find_door error: {e}")
        return {"door_bbox": None, "confidence": 0.0}


def analyze_frontage(image_path: Path, venue_name: str = "", venue_type: str = "") -> dict:
    img = Image.open(image_path).convert("RGB")
    try:
        return _json_call([frontage_prompt(venue_name, venue_type), img])
    except Exception as e:  # never let one bad JSON/quota blip crash the run
        log("judge", f"analyze_frontage error: {e}")
        return {"usable": False, "suitable": False, "door_bbox": None,
                "reasons": [f"judge error: {e}"]}


def qa_composite(before_path: Path, after_path: Path, ref_path: Path,
                 sku_name: str, sku_material: str, ratio_pct: int) -> dict:
    prompt = QA_PROMPT.format(sku_name=sku_name, sku_material=sku_material, ratio_pct=ratio_pct)
    before = Image.open(before_path).convert("RGB")
    after = Image.open(after_path).convert("RGB")
    ref = Image.open(ref_path).convert("RGB")
    try:
        return _json_call([prompt, "BEFORE:", before, "AFTER:", after, "REFERENCE:", ref])
    except Exception as e:
        log("qa", f"qa_composite error: {e}")
        return {"accept": False, "reasons": [f"qa error: {e}"]}


def edit_image(frontage_path: Path, ref_path: Path, prompt: str):
    """Reference-conditioned edit (nano-banana). Returns (image bytes or None, any text note)."""
    frontage = Image.open(frontage_path).convert("RGB")
    ref = Image.open(ref_path).convert("RGB")
    try:
        resp = _client().models.generate_content(
            model=config.IMAGE_MODEL, contents=[prompt, frontage, ref])
    except Exception as e:
        log("composite", f"gemini edit error: {e}")
        return None, f"error: {e}"
    img_bytes, note = None, ""
    for cand in (resp.candidates or []):
        for part in (cand.content.parts or []):
            inline = getattr(part, "inline_data", None)
            if inline and inline.data:
                img_bytes = inline.data
            elif getattr(part, "text", None):
                note += part.text
    return img_bytes, note
