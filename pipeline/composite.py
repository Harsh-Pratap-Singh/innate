"""
Stage 4 - Compositing logic (provider-agnostic).

We use reference-conditioned image EDITING, not text-to-image generation: the
client's real product photo goes in as pixels alongside the frontage, so the
model places THAT planter rather than inventing a generic one. Fidelity is held
by (1) pixel conditioning, (2) one SKU per generation, (3) a constrained prompt,
and downstream (4) identity QA.

Scale is anchored to the entrance door: given the door's pixel height and the
2.05 m door assumption, we tell the model the planter's target height as a
percentage of the door, and re-check it in QA.

This module only builds the SKU choice, scale target and prompt; the actual edit
call lives in the active provider (pipeline/qwen.py or pipeline/gemini_judge.py),
invoked as ai.edit_image().
"""
import json
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter

import config


def load_planters() -> list:
    return json.loads(config.PLANTERS_JSON.read_text(encoding="utf-8"))


def select_sku(analysis: dict, planters: list, prefer_id: Optional[str] = None) -> dict:
    """
    Pick one SKU for this frontage.

    Rule: default to the white tapered squares — narrow footprint, literally
    designed to flank a doorway on a tight London pavement (matches most Street
    View storefronts). If the judge saw generous pavement space, a wider
    statement piece (cylinder/corten) is allowed. `prefer_id` lets the
    orchestrator rotate SKUs so a 3-venue demo showcases the range.
    """
    by_id = {p["id"]: p for p in planters}
    if prefer_id and prefer_id in by_id:
        return by_id[prefer_id]
    # signal-driven default
    generous = bool(analysis.get("pavement_space")) and (analysis.get("bareness_score", 0) >= 0.5)
    if generous:
        return by_id.get("cylinder_charcoal", planters[0])
    return by_id.get("square_white", planters[0])


def ratio_target(sku: dict) -> Tuple[float, int]:
    """Planter display height as a fraction of door height, clamped to the accept band."""
    r = sku.get("planted_height_m", sku["real_height_m"]) / config.DOOR_HEIGHT_M
    r = max(config.PLANTER_DOOR_RATIO_MIN, min(config.PLANTER_DOOR_RATIO_MAX, r))
    return r, round(r * 100)


def _placement_clause(sku: dict) -> Tuple[str, str]:
    place = sku.get("placement", {})
    arrangement = place.get("arrangement", "single")
    offset = place.get("offset_from_door_m", 0.4)
    if arrangement == "pair":
        return (f"a matching pair — one standing on each side of the entrance about "
                f"{offset} m clear of the door frame, symmetrically framing the doorway", "two")
    if arrangement == "cluster":
        return (f"the tiered planter group beside the entrance, about {offset} m clear "
                f"of the door, on the pavement", "one group of")
    return (f"one beside the entrance, about {offset} m clear of the door, on the pavement", "one")


def build_prompt(sku: dict, ratio_pct: int, reject_reasons: Optional[list] = None) -> str:
    placement, count = _placement_clause(sku)
    height_m = sku.get("planted_height_m", sku["real_height_m"])
    retry = ""
    if reject_reasons:
        retry = ("\nThe previous attempt was REJECTED for: "
                 + "; ".join(reject_reasons)
                 + ". Fix these specifically while keeping every other constraint.\n")
    return f"""You are compositing the client's REAL product into a photo of a shop entrance to make a
sales mock-up. You are given two images:
  IMAGE 1 = the venue's real street frontage (the scene to edit).
  IMAGE 2 = the client's ACTUAL planter product (the reference to reproduce exactly).

TASK: place {count} of the reference planter ({sku['name']}: {sku['shape']}, {sku['material']},
planted with {sku['planting']}) {placement}, as if professionally installed.

HARD CONSTRAINTS:
- Reproduce the planter EXACTLY as in IMAGE 2 — same shape, material, colour and planting.
  Do NOT redesign, recolour, restyle, or substitute a generic planter.
- Change NOTHING else. Building, signage, all text/lettering, windows, door, pavement, people,
  sky and lighting must stay identical outside the added planters and their shadows.
- SCALE: each planter stands about {ratio_pct}% of the entrance door's height (~{height_m:.2f} m
  in reality). Never larger than the door; never a tiny toy.
- Match the photo's perspective, camera angle and light direction. Add a soft, realistic contact
  shadow where each planter meets the ground.
- Keep planters on the pavement. Never block the doorway or the walking path; never on the road.
- Do NOT crop, zoom, rotate or re-frame: keep the exact same camera framing as IMAGE 1.
- Photorealistic and seamless; matching grain, focus and white balance of the original photo.
{retry}Output only the edited image, same framing and resolution as IMAGE 1."""


# ---------------------------------------------------------------------------
# Paste-then-harmonize: deterministic placement, generative blending.
#
# Pure reference-conditioned editing proved unreliable on product identity
# (rendered corten as wood, matte as gloss) and scale (ignored the stated door
# ratio). So the robust strategy is: WE paste the actual product cutout at the
# mathematically correct size (door-anchored) and position (placement metadata),
# then the edit model only harmonizes light/edges/shadows. Identity and scale
# are then correct BY CONSTRUCTION; QA still verifies grounding + integrity.
# ---------------------------------------------------------------------------

def paste_planters(frontage_path: Path, sku: dict, door_bbox, out_path: Path) -> Optional[Path]:
    """
    Alpha-paste the SKU cutout(s) beside the door at door-anchored scale.
    Returns out_path, or None if geometry doesn't allow a sane placement.
    """
    if not door_bbox:
        return None
    base = Image.open(frontage_path).convert("RGBA")
    W, H = base.size
    x0, y0, x1, y1 = door_bbox[0] * W, door_bbox[1] * H, door_bbox[2] * W, door_bbox[3] * H
    door_h = y1 - y0
    door_w = max(1.0, x1 - x0)
    if door_h < H * 0.12:            # door too small — bad bbox, don't trust it
        return None

    cutout = Image.open(config.PLANTERS_DIR / sku["cutout"]).convert("RGBA")
    ratio, _ = ratio_target(sku)
    target_h = int(ratio * door_h)
    scale = target_h / cutout.height
    target_w = max(1, int(cutout.width * scale))
    planter = cutout.resize((target_w, target_h), Image.LANCZOS)

    place = sku.get("placement", {})
    pair = place.get("arrangement") == "pair"
    gap = int(door_w * 0.12)
    base_y = int(min(H - 1, y1 + door_h * 0.015))     # feet a touch below the door line

    positions = []
    if pair:
        positions = [int(x0 - gap - target_w), int(x1 + gap)]
    else:
        positions = [int(x1 + gap)]
        if positions[0] + target_w > W - 4:            # no room right -> try left
            positions = [int(x0 - gap - target_w)]
    positions = [p for p in positions if -target_w * 0.15 < p < W - target_w * 0.85]
    if not positions:
        return None

    for px in positions:
        py = base_y - target_h
        sh = Image.new("RGBA", base.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(sh)
        pot_w = int(target_w * 0.8)
        sd.ellipse([px + (target_w - pot_w) // 2, base_y - int(door_h * 0.02),
                    px + (target_w + pot_w) // 2, base_y + int(door_h * 0.03)],
                   fill=(0, 0, 0, 88))
        sh = sh.filter(ImageFilter.GaussianBlur(6))
        base = Image.alpha_composite(base, sh)
        base.alpha_composite(planter, (px, py))

    base.convert("RGB").save(out_path, "JPEG", quality=93)
    return out_path


def harmonize_prompt(sku: dict) -> str:
    return f"""IMAGE 1 is a photo of a shop entrance in which the client's real planter product
({sku['name']}: {sku['shape']}, {sku['material']}) has already been inserted at the CORRECT
position and CORRECT size. IMAGE 2 shows the same product for material reference.

TASK: make the inserted planter(s) look naturally part of the photo — nothing more.
- Blend the planter edges into the scene; match the photo's lighting direction, colour
  temperature, grain and focus.
- Refine the contact shadow where each planter meets the ground so it reads as real.
- Do NOT move, resize, duplicate, remove or redesign the planters. Keep their shape,
  material ({sku['material']}) and planting exactly as they are in IMAGE 1.
- Do NOT change anything else: building, signage and all text, windows, door, pavement,
  people, sky must stay pixel-identical.
- Do NOT crop, zoom or re-frame. Output the same framing and resolution as IMAGE 1."""
