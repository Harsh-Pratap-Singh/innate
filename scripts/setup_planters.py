"""
Build the product asset set + planters.json manifest.

Sources, in priority order:
  1. HIGH-QUALITY isolated shots (assets/Plant_gemini*.png) — upscaled/cleaned
     versions of the client's product photos; used as the working reference for
     compositing and for cutouts.
  2. ORIGINAL client reference photos (assets/planters/planter_*.jpg) — kept
     untouched as provenance / identity ground truth (the brief requires the
     client's actual products).

For each SKU this script:
  - copies the HQ image to assets/planters/planter_<id>_hq.png
  - strips its background to TRUE TRANSPARENCY (rembg/U^2-Net) and tight-crops
    -> planter_<id>_cutout.png  (alpha PNG: no white box can ever appear)
  - records real-world dimensions and fit heuristics in planters.json

    python scripts/setup_planters.py
"""
import json
import shutil
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config  # noqa: E402

ASSETS = ROOT / "assets"
OUT = config.PLANTERS_DIR

# hq source image -> sku metadata (dims are working estimates; catalogue values in production)
SKUS = [
    (
        "Plant_gemini.png",
        {
            "id": "cylinder_charcoal",
            "name": "Charcoal cylinder planter",
            "original": "planter_cylinder.jpg",
            "shape": "tall round cylinder",
            "material": "powder-coated fibreglass, matte charcoal",
            "real_height_m": 0.55,
            "planted_height_m": 1.10,
            "footprint_m": 0.55,
            "fits": "wide",
            "planting": "lush mixed foliage - fatsia, hosta, ornamental grasses",
            "placement": {
                "arrangement": "single",          # one statement piece
                "count": 1,
                "side": "right-of-door",
                "anchor": "bottom-center",        # paste anchor: base sits on pavement line
                "offset_from_door_m": 0.4,        # clear of the door swing
                "min_pavement_m": 1.2,            # needs generous pavement
                "pot_fraction": 0.50,             # pot = bottom 50% of cutout height (planting above)
                "shadow": "soft ellipse at base, follows scene light",
                "notes": "dark matte pot reads well against light facades",
            },
        },
    ),
    (
        "plant_gemini_2.png",
        {
            "id": "cube_corten",
            "name": "Corten steel tiered cube planters",
            "original": "planter_corten_cube.jpg",
            "shape": "stepped square cubes",
            "material": "weathered corten steel, rust-orange patina",
            "real_height_m": 0.60,
            "planted_height_m": 1.30,
            "footprint_m": 0.60,
            "fits": "wide",
            "planting": "architectural palm with red and green underplanting",
            "placement": {
                "arrangement": "cluster",         # tiered set reads as one group
                "count": 1,
                "side": "right-of-door",
                "anchor": "bottom-center",
                "offset_from_door_m": 0.5,
                "min_pavement_m": 1.5,            # widest footprint of the three
                "pot_fraction": 0.46,             # cubes = bottom ~46% of cutout height
                "shadow": "soft rectangular ground shadow under each cube",
                "notes": "best on forecourts/courtyards; palm adds height - keep below fascia",
            },
        },
    ),
    (
        "Plant_gemini_3.png",
        {
            "id": "square_white",
            "name": "White square planters",
            "original": "planter_white_squares.jpg",
            "shape": "tall tapered square column",
            "material": "matte white composite",
            "real_height_m": 0.85,
            "planted_height_m": 1.15,
            "footprint_m": 0.35,
            "fits": "narrow",
            "planting": "soft seasonal mix - grasses, pink and purple perennials",
            "placement": {
                "arrangement": "pair",            # designed to flank a doorway
                "count": 2,
                "side": "both-sides-of-door",
                "anchor": "bottom-center",
                "offset_from_door_m": 0.3,
                "min_pavement_m": 0.7,            # narrow base fits tight London pavements
                "pot_fraction": 0.62,             # tall pot = bottom ~62% of cutout height
                "shadow": "slim soft shadow at base of each column",
                "notes": "default SKU; symmetrical pair frames the entrance",
            },
        },
    ),
]


def white_border_flood_cutout(img: Image.Image, white_thresh: int = 246) -> Image.Image:
    """
    Deterministic background removal for product shots on a uniform WHITE background.

    Only near-white pixels CONNECTED TO THE IMAGE BORDER become transparent, so
    white/very light subjects (the white pots) keep full opacity — this is where
    ML mattes (rembg/U^2-Net) fail on white-on-white. Edges get a 1px feather.
    """
    import cv2
    import numpy as np

    rgb = np.asarray(img.convert("RGB"))
    near_white = (rgb.min(axis=2) >= white_thresh).astype(np.uint8)

    # connected components of the near-white mask; any component touching the
    # border is background, interior white regions are subject and stay opaque
    n, labels = cv2.connectedComponents(near_white, connectivity=8)
    border_labels = set(np.unique(np.concatenate([
        labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]])))
    border_labels.discard(0)   # 0 = the non-white (subject) area
    background = np.isin(labels, list(border_labels))

    alpha = np.where(background, 0, 255).astype(np.uint8)
    alpha = cv2.GaussianBlur(alpha, (3, 3), 0)   # soften the cut edge slightly

    out = np.dstack([rgb, alpha])
    return Image.fromarray(out, "RGBA")


def main():
    # clear stale generated files (keep originals + this run's outputs)
    for stale in OUT.glob("*_cutout.png"):
        stale.unlink()
    for stale in OUT.glob("*_hq.png"):
        stale.unlink()

    manifest = []
    for src_name, meta in SKUS:
        src = ASSETS / src_name
        if not src.exists():
            raise SystemExit(f"Missing HQ image: {src}")

        hq_name = f"planter_{meta['id']}_hq.png"
        cut_name = f"planter_{meta['id']}_cutout.png"

        img = Image.open(src).convert("RGBA")
        img.save(OUT / hq_name)

        cut = white_border_flood_cutout(img)
        bbox = cut.getbbox()
        if bbox:
            cut = cut.crop(bbox)
        cut.save(OUT / cut_name)

        alpha = cut.split()[-1]
        subject = sum(alpha.getdata()) / (255.0 * cut.width * cut.height)

        entry = {
            **meta,
            "file": hq_name,        # working reference used by the pipeline
            "cutout": cut_name,     # transparent version (no background at all)
            "source_hq": src_name,
            "px_w": cut.width, "px_h": cut.height,
        }
        manifest.append(entry)
        print(f"{meta['id']:18s} hq={img.width}x{img.height} cutout={cut.width}x{cut.height} "
              f"subject={subject:.0%}")

    config.PLANTERS_JSON.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nWrote {config.PLANTERS_JSON} ({len(manifest)} SKUs)")
    print("Originals kept untouched:", ", ".join(m["original"] for _, m in SKUS))


if __name__ == "__main__":
    main()
