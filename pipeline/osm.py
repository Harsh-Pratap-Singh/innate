"""
Stage 1 - Discovery via OpenStreetMap (Overpass API). No key, no billing.

Pulls independent street-facing venues (cafes/restaurants/bars/pubs + hair/beauty/
bakery shops) around each configured London area, then applies the same cheap
code-only pre-filter (name-based chain blocklist) before spending imagery/AI
budget. OSM's amenity/shop tags already constrain the business type, so the query
itself is the type filter.
"""
import time
from typing import List

import requests

import config
from pipeline.models import Candidate
from pipeline.utils import log

# Public Overpass instances 406 without a real User-Agent; keep mirrors as backup.
_HEADERS = {"User-Agent": "innate-storefront/1.0 (assessment prototype)", "Accept": "application/json"}
_MIRRORS = [config.OVERPASS_URL,
            "https://overpass.private.coffee/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"]


def _post(query: str):
    last = None
    for attempt in range(2):                 # two passes over the mirror list
        for base in _MIRRORS:
            try:
                resp = requests.post(base, data={"data": query}, headers=_HEADERS, timeout=60)
                resp.raise_for_status()
                return resp.json().get("elements", [])
            except Exception as e:
                last = e
                continue
        time.sleep(3)
    raise last


def _query(area: dict) -> str:
    amen = "|".join(config.OSM_AMENITIES)
    shop = "|".join(config.OSM_SHOPS)
    r, lat, lng = area["radius"], area["lat"], area["lng"]
    return f"""[out:json][timeout:30];
(
  nwr["amenity"~"^({amen})$"]["name"](around:{r},{lat},{lng});
  nwr["shop"~"^({shop})$"]["name"](around:{r},{lat},{lng});
);
out center tags 120;"""


def _address(tags: dict) -> str:
    street = " ".join(x for x in (tags.get("addr:housenumber"), tags.get("addr:street")) if x).strip()
    bits = [street, tags.get("addr:suburb", ""), "London", tags.get("addr:postcode", "")]
    return ", ".join(b for b in bits if b)


def discover() -> List[Candidate]:
    seen = {}
    for area in config.OSM_AREAS:
        try:
            elements = _post(_query(area))
        except Exception as e:
            log("discover", f"Overpass error for {area['name']}: {e}")
            continue
        log("discover", f"{len(elements):>3} elements in {area['name']}")
        for el in elements:
            tags = el.get("tags", {})
            name = tags.get("name")
            if not name:
                continue
            oid = f"{el['type']}/{el['id']}"
            if oid in seen:
                continue
            lat = el.get("lat") or el.get("center", {}).get("lat")
            lng = el.get("lon") or el.get("center", {}).get("lon")
            if lat is None or lng is None:
                continue
            types = [t for t in (tags.get("amenity"), tags.get("shop")) if t]
            cand = Candidate(place_id=f"osm/{oid}", name=name, address=_address(tags),
                             postcode=tags.get("addr:postcode", ""), lat=lat, lng=lng,
                             types=types, area=area["name"],
                             website=tags.get("website") or tags.get("contact:website", ""))
            if any(ch in name.lower() for ch in config.CHAIN_BLOCKLIST):
                cand.rejected = "looks like a chain (name blocklist)"
            seen[oid] = cand
        time.sleep(2.5)   # be gentle with the public Overpass instance (avoids 429/timeouts)

    cands = list(seen.values())
    kept = [c for c in cands if not c.rejected]
    log("discover", f"{len(cands)} unique OSM venues -> {len(kept)} pass pre-filter")
    return sorted(cands, key=lambda c: c.rejected is not None)
