"""
Stage 2 - Frontage capture via Mapillary (crowdsourced street-level imagery).

Unlike Street View's Static API, Mapillary can't render an arbitrary heading -
each image is a fixed capture. So framing here is SELECTION, not rendering:

  1. Fetch images in a small bbox around the venue.
  2. For each, compute the camera->venue bearing and compare it to the image's
     own `compass_angle` (the direction it was shot). An image whose camera is
     near the venue AND pointing roughly at it will contain the frontage.
  3. Rank by (perspective-before-panorama, closeness, heading error) and hand the
     best few to the vision judge, which makes the final usable/not call.

No Places-style business-photo fallback exists in this stack; the fallback is
simply the next-best Mapillary candidate, then dropping the venue.
"""
import math
from typing import List, Optional, Tuple

import requests

import config
from pipeline.utils import bearing_deg, haversine_m, log


def _bbox(lat: float, lng: float, m: float) -> Tuple[float, float, float, float]:
    dlat = m / 111_320.0
    dlng = m / (111_320.0 * max(0.2, math.cos(math.radians(lat))))
    return lng - dlng, lat - dlat, lng + dlng, lat + dlat


def _heading_err(compass: Optional[float], bearing: float) -> Optional[float]:
    if compass is None:
        return None
    return abs((bearing - compass + 180) % 360 - 180)


def find_images(lat: float, lng: float) -> Tuple[List[dict], int]:
    """Return (ranked candidate images facing the venue, total images seen)."""
    minx, miny, maxx, maxy = _bbox(lat, lng, config.MAPILLARY_SEARCH_M)
    fields = f"id,geometry,compass_angle,is_pano,captured_at,{config.MAPILLARY_THUMB}"
    try:
        r = requests.get(
            config.MAPILLARY_GRAPH + "/images",
            params={"fields": fields, "bbox": f"{minx},{miny},{maxx},{maxy}",
                    "limit": 50, "access_token": config.MAPILLARY_TOKEN},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json().get("data", [])
    except Exception as e:
        log("capture", f"Mapillary search error: {e}")
        return [], 0

    scored = []
    for im in data:
        coords = (im.get("geometry") or {}).get("coordinates")
        thumb = im.get(config.MAPILLARY_THUMB)
        if not coords or not thumb:
            continue
        clng, clat = coords[0], coords[1]
        dist = haversine_m(clat, clng, lat, lng)
        bearing = bearing_deg(clat, clng, lat, lng)
        herr = _heading_err(im.get("compass_angle"), bearing)
        scored.append({
            "id": im["id"], "dist": dist, "bearing_to_venue": bearing,
            "compass_angle": im.get("compass_angle"), "heading_err": herr,
            "is_pano": bool(im.get("is_pano")), "thumb": thumb,
            "captured_at": im.get("captured_at"),
        })

    def usable(s):
        return s["dist"] <= config.MAPILLARY_SEARCH_M * 1.6 and \
               (s["heading_err"] is None or s["heading_err"] <= config.MAPILLARY_MAX_HEADING_ERR)

    good = [s for s in scored if usable(s)]
    # Rank: perspective before panorama, then distance to the ~18 m sweet spot
    # (close enough for facade detail, far enough to include door + pavement),
    # then aim. Not raw nearest — 3 m away sees only a wall.
    good.sort(key=lambda s: (s["is_pano"], round(abs(s["dist"] - 18) / 6),
                             s["heading_err"] if s["heading_err"] is not None else 999))

    # Dedupe burst captures from the same spot (same sequence, ~metres apart):
    # judging three near-identical frames wastes model calls.
    deduped, seen_keys = [], set()
    for s in good:
        key = (round(s["dist"] / 4), round((s["compass_angle"] or 0) / 15))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(s)
    return deduped[: config.MAPILLARY_MAX_CANDIDATES], len(data)


def download(thumb_url: str) -> Optional[bytes]:
    try:
        r = requests.get(thumb_url, timeout=30)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            return r.content
    except Exception as e:
        log("capture", f"Mapillary thumb download error: {e}")
    return None
