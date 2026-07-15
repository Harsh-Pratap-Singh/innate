"""
End-to-end orchestrator.

    discover -> (per venue) capture+QA -> suitability -> composite+QA -> gallery

Everything is automated: no human picks a venue, a framing, or a final image.
Every accept/reject is logged into web/data/results.json so the decision-making
is auditable (that's what the brief grades).

Provider stacks (switch in .env, no code change):
    IMAGERY_SOURCE = mapillary (default) | streetview
    AI_PROVIDER    = qwen (default)      | gemini
    Discovery follows imagery: mapillary->OSM Overpass, streetview->Google Places.

Usage:
    python run.py                 # full run, stops after 3 finished venues
    python run.py --stop-after 3
    python run.py --max-attempts 14
    python run.py --showcase      # rotate SKUs so the 3 venues show all products
"""
import argparse
import datetime as dt
import io
import json
import shutil

from PIL import Image

import config
from pipeline import composite, qa
from pipeline.utils import log

# ---- provider wiring (single switch point) ----
if config.AI_PROVIDER == "qwen":
    from pipeline import qwen as ai
else:
    from pipeline import gemini_judge as ai

if config.IMAGERY_SOURCE == "mapillary":
    from pipeline import osm as discovery
    from pipeline import mapillary
else:
    from pipeline import places as discovery
    from pipeline import places, streetview

from pipeline import webimages

# Order matters: the cylinder's simple geometry is the easiest for edit models to
# reproduce faithfully, so lead with it; the subtle-tapered white squares are hardest.
SHOWCASE_ROTATION = ["cylinder_charcoal", "cube_corten", "square_white"]


def save_image(data: bytes, path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.open(io.BytesIO(data)).convert("RGB").save(path, "JPEG", quality=92)
    return str(path)


def px_per_m_from_door(door_bbox, img_h) -> float:
    if not door_bbox:
        return 0.0
    door_px = (door_bbox[3] - door_bbox[1]) * img_h
    return round(door_px / config.DOOR_HEIGHT_M, 1) if door_px else 0.0


# ---------------------------------------------------------------- capture stage
def normalize_bbox(bbox, img_w, img_h):
    """
    Accept a bbox in 0..1, 0..1000 (Qwen-VL grounding convention) or pixel space
    and return it normalised 0..1, or None if unusable.
    """
    if not bbox or len(bbox) != 4:
        return None
    b = [float(v) for v in bbox]
    m = max(b)
    if m <= 1.5:                     # already normalised
        pass
    elif m <= 1000:                  # 0-1000 grounding space
        b = [v / 1000.0 for v in b]
    else:                            # pixel space
        b = [b[0] / img_w, b[1] / img_h, b[2] / img_w, b[3] / img_h]
    x0, y0, x1, y1 = [min(1.0, max(0.0, v)) for v in b]
    if x1 - x0 < 0.02 or y1 - y0 < 0.02:
        return None
    return [x0, y0, x1, y1]


def crop_to_venue(src_path, bbox, out_path, pad_frac=0.22, min_px=520):
    """
    Derive the framing in software: crop the wide street image to the located
    venue's shopfront (bbox normalised 0..1), padded for context. Returns the
    crop path, or None if the located region is too small to be usable.
    """
    with Image.open(src_path) as im:
        W, H = im.size
        x0, y0, x1, y1 = bbox[0] * W, bbox[1] * H, bbox[2] * W, bbox[3] * H
        bw, bh = x1 - x0, y1 - y0
        if bw < min_px * 0.45 or bh < min_px * 0.45:
            return None                      # too far away / too small to composite on
        px, py = bw * pad_frac, bh * pad_frac
        cx0, cy0 = max(0, x0 - px), max(0, y0 - py * 1.6)   # extra headroom above fascia
        cx1, cy1 = min(W, x1 + px), min(H, y1 + py * 0.6)   # keep pavement at the base
        crop = im.crop((int(cx0), int(cy0), int(cx1), int(cy1)))
        if crop.width < min_px:              # keep enough pixels for the edit model
            scale = min_px / crop.width
            crop = crop.resize((min_px, int(crop.height * scale)), Image.LANCZOS)
        crop.convert("RGB").save(out_path, "JPEG", quality=92)
    return out_path


def capture_mapillary(cand, scratch):
    """
    Mapillary: rank images facing the venue, then per image
      locate the venue in the wide frame -> crop to its shopfront -> judge the crop.
    """
    attempts = []
    images, seen = mapillary.find_images(cand.lat, cand.lng)
    if not images:
        attempts.append({"source": "mapillary", "usable": False,
                         "reason": f"no street-level images within ~{config.MAPILLARY_SEARCH_M} m "
                                   f"facing the venue ({seen} images in area)"})
        return None, None, None, None, attempts

    vtype = ",".join(cand.types)
    for i, im in enumerate(images[: config.MAPILLARY_JUDGE_MAX]):
        data = mapillary.download(im["thumb"])
        if not data:
            attempts.append({"source": "mapillary", "index": i, "usable": False,
                             "reason": "image download failed"})
            continue
        wide_path = scratch / f"map_{i}.jpg"
        save_image(data, wide_path)

        att = {"source": "mapillary", "index": i, "usable": False,
               "image_id": im["id"], "distance_m": round(im["dist"], 1),
               "heading": round(im["bearing_to_venue"], 1),
               "heading_err": None if im["heading_err"] is None else round(im["heading_err"], 1),
               "file": str(wide_path)}

        # step 1: locate the most plausible target storefront in the wide frame
        loc = ai.locate_venue(wide_path, cand.name, vtype)
        with Image.open(wide_path) as wim:
            bbox = normalize_bbox(loc.get("venue_bbox"), wim.width, wim.height)
        att["locate"] = {"found": loc.get("found"), "name_confirmed": loc.get("name_confirmed"),
                         "confidence": loc.get("confidence"), "bbox": bbox,
                         "note": "; ".join(loc.get("reasons", []))[:140]}

        judge_path = None
        if loc.get("found") and bbox and loc.get("confidence", 0) >= config.LOCATE_MIN_CONFIDENCE:
            # step 2: derive the framing by cropping to the located shopfront
            judge_path = crop_to_venue(wide_path, bbox, scratch / f"map_{i}_crop.jpg")
            if not judge_path:
                att["reason"] = "venue located but too small in frame (too far away)"
        if judge_path is None and im["dist"] <= 15:
            # fallback: camera is close — the wide frame itself may already be a
            # usable single-storefront shot; judge it directly.
            judge_path = wide_path

        if judge_path is None:
            att.setdefault("reason", "no plausible storefront located: " +
                           ("; ".join(loc.get("reasons", [])) or "not found")[:150])
            attempts.append(att)
            continue

        # step 3: full usability + suitability judgement on the derived frame
        analysis = ai.analyze_frontage(judge_path, venue_name=cand.name, venue_type=vtype)
        usable = bool(analysis.get("usable")) and bool(analysis.get("is_target_business_type", True))
        att.update({"usable": usable, "located_confidence": loc.get("confidence"),
                    "crop": str(judge_path),
                    "reason": "; ".join(analysis.get("reasons", [])) or ("ok" if usable else "rejected")})
        attempts.append(att)
        if usable:
            framing = {"pano_id": im["id"], "heading": round(im["bearing_to_venue"], 1),
                       "fov": None, "pitch": None, "distance_m": round(im["dist"], 1),
                       "captured_at": im.get("captured_at"),
                       "derived": ("wide frame cropped to located shopfront"
                                   if judge_path != wide_path else "wide frame used directly"),
                       "name_confirmed": bool(loc.get("name_confirmed"))}
            return judge_path, "mapillary", framing, analysis, attempts
    return None, None, None, None, attempts


def capture_streetview(cand, scratch):
    """Street View: re-aimed heading with nudges, then Places-photo fallback."""
    attempts = []
    for i, offset in enumerate(config.HEADING_NUDGES[: config.MAX_HEADING_RETRIES + 1]):
        framing = streetview.compute_framing(cand.lat, cand.lng, heading_offset=offset)
        if not framing:
            attempts.append({"source": "streetview", "offset": offset,
                             "usable": False, "reason": "no usable pano coverage"})
            break
        data = streetview.static_image(framing)
        if not data:
            attempts.append({"source": "streetview", "offset": offset,
                             "usable": False, "reason": "static image fetch failed"})
            continue
        path = scratch / f"sv_{i}_off{offset}.jpg"
        save_image(data, path)
        analysis = ai.analyze_frontage(path, venue_name=cand.name, venue_type=",".join(cand.types))
        usable = bool(analysis.get("usable")) and bool(analysis.get("is_target_business_type", True))
        attempts.append({"source": "streetview", "offset": offset,
                         "heading": framing.as_dict()["heading"], "usable": usable,
                         "reason": "; ".join(analysis.get("reasons", [])) or ("ok" if usable else "rejected"),
                         "file": str(path)})
        if usable:
            return path, "streetview", framing.as_dict(), analysis, attempts

    for j, ref in enumerate(getattr(cand, "photo_refs", [])[:3]):
        data = places.place_photo_bytes(ref)
        if not data:
            continue
        path = scratch / f"places_{j}.jpg"
        save_image(data, path)
        analysis = ai.analyze_frontage(path, venue_name=cand.name, venue_type=",".join(cand.types))
        usable = bool(analysis.get("usable")) and bool(analysis.get("is_target_business_type", True))
        attempts.append({"source": "places_photo", "index": j, "usable": usable,
                         "reason": "; ".join(analysis.get("reasons", [])) or ("ok" if usable else "rejected"),
                         "file": str(path)})
        if usable:
            return path, "places_photo", None, analysis, attempts

    return None, None, None, None, attempts


def capture_web(cand, scratch, attempts):
    """
    Fallback tier: the venue's own website photos, then keyless web image search
    — judged by the same vision gate as street imagery.
    """
    vtype = ",".join(cand.types)
    for j, (label, url) in enumerate(webimages.candidates(cand)):
        if j >= 5:
            break
        data = webimages.download(url)
        if not data:
            attempts.append({"source": label, "index": j, "usable": False,
                             "reason": f"download failed ({url[:60]})"})
            continue
        path = scratch / f"web_{j}.jpg"
        try:
            save_image(data, path)
        except Exception:
            attempts.append({"source": label, "index": j, "usable": False,
                             "reason": "not a decodable image"})
            continue
        analysis = ai.analyze_frontage(path, venue_name=cand.name, venue_type=vtype)
        usable = bool(analysis.get("usable")) and bool(analysis.get("is_target_business_type", True))
        attempts.append({"source": label, "index": j, "usable": usable, "url": url[:120],
                         "reason": "; ".join(analysis.get("reasons", [])) or ("ok" if usable else "rejected"),
                         "file": str(path)})
        if usable:
            framing = {"derived": f"web fallback ({label})", "url": url[:160]}
            return path, label, framing, analysis, attempts
    return None, None, None, None, attempts


def capture_frontage(cand, scratch):
    if config.IMAGERY_SOURCE == "mapillary":
        path, source, framing, analysis, attempts = capture_mapillary(cand, scratch)
    else:
        path, source, framing, analysis, attempts = capture_streetview(cand, scratch)
    if path is None:
        # street-level imagery failed -> venue website / web image search
        path, source, framing, analysis, attempts = capture_web(cand, scratch, attempts)
    return path, source, framing, analysis, attempts


# -------------------------------------------------------------- composite stage
def composite_frontage(frontage_path, sku, scratch, door_bbox=None):
    """
    Two strategies, best-first:
      paste+harmonize — WE place the real cutout at door-anchored scale (identity
        and scale correct by construction), the model only blends light/shadows.
        Integrity is checked harmonized-vs-PASTED (the paste is trusted).
      pure edit — reference-conditioned generation (legacy path), used when no
        usable door bbox exists or as a late fallback.
    """
    ref_path = config.PLANTERS_DIR / sku["file"]      # HQ isolated product shot
    ratio, ratio_pct = composite.ratio_target(sku)
    attempts = []
    reasons = None

    pasted_path = None
    if door_bbox:
        pasted_path = composite.paste_planters(
            frontage_path, sku, door_bbox, scratch / "pasted.jpg")

    # strategy per attempt: lead with paste+harmonize when the paste exists
    strategies = (["harmonize", "harmonize", "edit", "edit"] if pasted_path
                  else ["edit"] * (config.MAX_COMPOSITE_RETRIES + 1))

    for attempt, strategy in enumerate(strategies[: config.MAX_COMPOSITE_RETRIES + 1]):
        if strategy == "harmonize":
            prompt = composite.harmonize_prompt(sku)
            data, note = ai.edit_image(pasted_path, ref_path, prompt)
            before_for_integrity = pasted_path      # harmonize must not alter the rest
        else:
            prompt = composite.build_prompt(sku, ratio_pct, reject_reasons=reasons)
            data, note = ai.edit_image(frontage_path, ref_path, prompt)
            before_for_integrity = frontage_path

        if not data:
            attempts.append({"attempt": attempt, "strategy": strategy, "accepted": False,
                             "reasons": [f"no image returned ({note[:80]})"]})
            continue
        cand_path = scratch / f"composite_{attempt}.jpg"
        save_image(data, cand_path)

        vision = ai.qa_composite(frontage_path, cand_path, ref_path,
                                 sku_name=sku["name"], sku_material=sku["material"],
                                 ratio_pct=ratio_pct)
        integrity = qa.scene_integrity(before_for_integrity, cand_path)

        accepted = bool(vision.get("accept")) and integrity.ok
        reasons = list(vision.get("reasons", []))
        if not integrity.ok:
            reasons.append(f"scene changed outside placement zone "
                           f"({integrity.changed_fraction_above_zone:.1%} > "
                           f"{config.QA_MAX_CHANGED_FRACTION:.0%})")
        attempts.append({"attempt": attempt, "strategy": strategy, "accepted": accepted,
                         "file": str(cand_path), "vision_qa": vision,
                         "integrity": integrity.as_dict(), "reasons": reasons})
        log("composite", f"attempt {attempt} [{strategy}]: {'ACCEPT' if accepted else 'reject'} "
                         f"({'passes QA' if accepted else '; '.join(map(str, reasons))[:140]})")
        if accepted:
            return {"accepted": True, "final": cand_path, "ratio_pct": ratio_pct,
                    "strategy": strategy, "attempts": attempts}

    return {"accepted": False, "final": None, "ratio_pct": ratio_pct, "attempts": attempts}


# ------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stop-after", type=int, default=config.STOP_AFTER_N_ACCEPTED)
    ap.add_argument("--max-attempts", type=int, default=config.MAX_CAPTURE_ATTEMPTS)
    ap.add_argument("--showcase", action="store_true",
                    help="rotate SKUs so delivered venues showcase all products")
    args = ap.parse_args()

    config.require_keys()
    planters = composite.load_planters()

    config.WEB_DATA.mkdir(parents=True, exist_ok=True)
    log("run", f"stack: discovery={'OSM' if config.IMAGERY_SOURCE=='mapillary' else 'Places'} "
               f"imagery={config.IMAGERY_SOURCE} ai={config.AI_PROVIDER}")

    candidates = discovery.discover()
    prefiltered_out = [c for c in candidates if c.rejected]
    workable = [c for c in candidates if not c.rejected]

    # Interleave candidates round-robin across discovery areas so one street with
    # bad imagery (e.g. market-day crowds) can't exhaust the attempt budget.
    by_area = {}
    for c in workable:
        by_area.setdefault(c.area or "-", []).append(c)
    interleaved, i = [], 0
    while any(by_area.values()):
        for area in list(by_area):
            if by_area[area]:
                interleaved.append(by_area[area].pop(0))
    workable = interleaved
    log("run", f"{len(workable)} workable candidates across {len(by_area)} areas; "
               f"delivering {args.stop_after}")

    venues, rejected = [], []
    for c in prefiltered_out:
        rejected.append({"name": c.name, "address": c.address, "stage": "pre-filter",
                         "reason": c.rejected})

    accepted_count = 0
    attempted = 0
    composited = 0
    for cand in workable:
        if accepted_count >= args.stop_after or attempted >= args.max_attempts:
            break
        attempted += 1
        log("run", f"--- [{attempted}] {cand.name} ({cand.postcode or cand.address or 'no addr'}) ---")
        scratch = config.OUT / cand.slug

        frontage_path, source, framing, analysis, cap_attempts = capture_frontage(cand, scratch)
        if not frontage_path:
            rejected.append({"name": cand.name, "address": cand.address, "stage": "capture",
                             "reason": (cap_attempts[-1]["reason"] if cap_attempts else "no usable frontage"),
                             "attempts": cap_attempts})
            continue

        if not (analysis.get("suitable") and analysis.get("would_improve", True)):
            rejected.append({"name": cand.name, "address": cand.address, "stage": "suitability",
                             "reason": "; ".join(analysis.get("reasons", [])) or "frontage not under-dressed / no space",
                             "bareness_score": analysis.get("bareness_score")})
            continue

        # --- door bbox: normalise whatever coordinate space the judge used, and if
        # it came back null, make one cheap recovery call — the paste+harmonize
        # strategy (exact scale by construction) is only available with a door box.
        with Image.open(frontage_path) as fim:
            fw, fh = fim.size
        door_bbox = normalize_bbox(analysis.get("door_bbox"), fw, fh)
        if not door_bbox:
            d = ai.find_door(frontage_path)
            door_bbox = normalize_bbox(d.get("door_bbox"), fw, fh)
            log("run", f"door recovery: {'found' if door_bbox else 'none'} "
                       f"(conf={d.get('confidence')})")
        analysis["door_bbox"] = door_bbox

        # rotate by venues that REACHED compositing, not by deliveries — otherwise a
        # hard-to-render SKU pins every venue to the same product until one passes
        prefer = SHOWCASE_ROTATION[composited % len(SHOWCASE_ROTATION)] if args.showcase else None
        composited += 1
        sku = composite.select_sku(analysis, planters, prefer_id=prefer)

        comp = composite_frontage(frontage_path, sku, scratch, door_bbox=door_bbox)
        if not comp["accepted"]:
            last = comp["attempts"][-1] if comp["attempts"] else {}
            rejected.append({"name": cand.name, "address": cand.address, "stage": "composite",
                             "reason": "; ".join(map(str, last.get("reasons", []))) or "composite failed QA after retries",
                             "attempts": comp["attempts"]})
            continue

        # ---- publish frontage + composite (+ optional variants) into web/data/<slug>/ ----
        slug = cand.slug
        pub = config.WEB_DATA / slug
        pub.mkdir(parents=True, exist_ok=True)
        shutil.copy(frontage_path, pub / "frontage.jpg")
        shutil.copy(comp["final"], pub / "composite.jpg")

        variants = [{"file": f"data/{slug}/composite.jpg", "label": "Final", "accepted": True}]
        vdir = pub / "variants"
        pasted = scratch / "pasted.jpg"
        if pasted.exists():
            vdir.mkdir(exist_ok=True)
            shutil.copy(pasted, vdir / "raw_placement.jpg")
            variants.append({"file": f"data/{slug}/variants/raw_placement.jpg",
                             "label": "Raw placement", "accepted": False})
        for a in comp["attempts"]:
            f = a.get("file")
            if f and Path(f) != Path(str(comp["final"])) and Path(f).exists():
                vdir.mkdir(exist_ok=True)
                name = f"attempt_{a['attempt']}.jpg"
                shutil.copy(f, vdir / name)
                variants.append({"file": f"data/{slug}/variants/{name}",
                                 "label": f"Alt {a['attempt'] + 1} ({a.get('strategy', 'edit')})",
                                 "accepted": False})

        door_bbox = analysis.get("door_bbox")
        with Image.open(frontage_path) as im:
            img_h = im.size[1]

        venues.append({
            "slug": slug, "name": cand.name, "address": cand.address,
            "postcode": cand.postcode, "lat": cand.lat, "lng": cand.lng,
            "types": cand.types,
            "sku": {"id": sku["id"], "name": sku["name"], "material": sku["material"]},
            "frontage_source": source,
            "framing": framing,
            "frontage": f"data/{slug}/frontage.jpg",
            "composite": f"data/{slug}/composite.jpg",
            "scale": {"door_ratio_pct": comp["ratio_pct"], "door_bbox": door_bbox,
                      "px_per_m": px_per_m_from_door(door_bbox, img_h),
                      "door_height_assumed_m": config.DOOR_HEIGHT_M},
            "frontage_analysis": {k: analysis.get(k) for k in
                                  ("bareness_score", "pavement_space", "obstruction",
                                   "would_improve", "reasons")},
            "capture_attempts": cap_attempts,
            "composite_attempts": comp["attempts"],
        })
        accepted_count += 1
        log("run", f"DELIVERED {cand.name} ({accepted_count}/{args.stop_after})")

    results = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "delivered": len(venues),
        "venues": venues,
        "rejected_candidates": rejected,
        "stats": {"unique_candidates": len(candidates),
                  "passed_prefilter": len(workable),
                  "prefiltered_out": len(prefiltered_out),
                  "attempted": attempted,
                  "delivered": len(venues)},
        "config": {
            "stack": {"discovery": "osm" if config.IMAGERY_SOURCE == "mapillary" else "places",
                      "imagery": config.IMAGERY_SOURCE, "ai": config.AI_PROVIDER},
            "door_height_m": config.DOOR_HEIGHT_M,
            "planter_door_ratio_band": [config.PLANTER_DOOR_RATIO_MIN, config.PLANTER_DOOR_RATIO_MAX],
            "models": ({"judge": config.QWEN_VL_MODEL, "image": config.QWEN_EDIT_MODEL}
                       if config.AI_PROVIDER == "qwen"
                       else {"judge": config.JUDGE_MODEL, "image": config.IMAGE_MODEL}),
        },
    }
    out = config.WEB_DATA / "results.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    log("run", f"Wrote {out}  ({len(venues)} delivered, {len(rejected)} rejected)")
    print(f"\nDone. {len(venues)} venues delivered -> {out}")
    print("Preview the gallery:  cd web && python -m http.server 8000  ->  http://localhost:8000")


if __name__ == "__main__":
    main()
