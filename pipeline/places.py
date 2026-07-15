"""
Stage 1 - Discovery.

Pull a candidate list from a real source (Google Places, legacy Text Search),
then apply a cheap, code-only pre-filter for "independent + street-facing"
before we spend any vision/imagery budget on them:
  - name not on the chain blocklist
  - at least one accepted place type
  - operational

Also exposes Places business photos, used as the capture fallback when Street
View coverage is poor or faces the wrong way.
"""
import re
import time
from typing import List, Optional

import requests

import config
from pipeline.models import Candidate
from pipeline.utils import log

TEXT_SEARCH = "https://maps.googleapis.com/maps/api/place/textsearch/json"
DETAILS = "https://maps.googleapis.com/maps/api/place/details/json"
PHOTO = "https://maps.googleapis.com/maps/api/place/photo"

UK_POSTCODE = re.compile(r"([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})", re.I)


def is_independent(name: str) -> bool:
    low = name.lower()
    return not any(chain in low for chain in config.CHAIN_BLOCKLIST)


def _postcode(address: str) -> str:
    m = UK_POSTCODE.search(address or "")
    return m.group(1).upper().replace("  ", " ") if m else ""


def text_search(query: str) -> List[dict]:
    r = requests.get(
        TEXT_SEARCH,
        params={"query": query, "region": "uk", "key": config.GOOGLE_MAPS_API_KEY},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    status = data.get("status")
    if status not in ("OK", "ZERO_RESULTS"):
        log("discover", f"Places status={status} ({data.get('error_message','')}) for {query!r}")
        return []
    return data.get("results", [])


def discover() -> List[Candidate]:
    """Run all configured queries, dedupe by place_id, apply the pre-filter."""
    seen = {}
    for q in config.SEARCH_QUERIES:
        results = text_search(q)
        log("discover", f"{len(results):>2} raw results for {q!r}")
        for r in results:
            pid = r.get("place_id")
            if not pid or pid in seen:
                continue
            types = r.get("types", [])
            cand = Candidate(
                place_id=pid,
                name=r.get("name", ""),
                address=r.get("formatted_address", ""),
                postcode=_postcode(r.get("formatted_address", "")),
                lat=r["geometry"]["location"]["lat"],
                lng=r["geometry"]["location"]["lng"],
                types=types,
                photo_refs=[p["photo_reference"] for p in r.get("photos", [])[:5]],
            )
            # cheap pre-filter (record the reason instead of silently dropping)
            if r.get("business_status") not in (None, "OPERATIONAL"):
                cand.rejected = f"not operational ({r.get('business_status')})"
            elif not is_independent(cand.name):
                cand.rejected = "looks like a chain (name blocklist)"
            elif not (set(types) & config.ACCEPTED_PLACE_TYPES):
                cand.rejected = f"type not street-facing venue ({','.join(types[:3])})"
            seen[pid] = cand
        time.sleep(0.2)

    cands = list(seen.values())
    kept = [c for c in cands if not c.rejected]
    log("discover", f"{len(cands)} unique candidates -> {len(kept)} pass pre-filter")
    # kept first (they'll be tried first), rejected retained for the report
    return sorted(cands, key=lambda c: c.rejected is not None)


def place_photo_bytes(photo_reference: str, maxwidth: int = 1000) -> Optional[bytes]:
    """Fetch a Google Business / Places photo (capture fallback)."""
    r = requests.get(
        PHOTO,
        params={"maxwidth": maxwidth, "photo_reference": photo_reference,
                "key": config.GOOGLE_MAPS_API_KEY},
        timeout=20,
    )
    if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
        return r.content
    log("capture", f"places photo fetch failed: HTTP {r.status_code}")
    return None
