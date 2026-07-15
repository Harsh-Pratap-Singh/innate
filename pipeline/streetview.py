"""
Stage 2 - Frontage capture (primary source: Google Street View).

The framing trick, in three steps:
  1. metadata()  -> nearest panorama to the venue (its exact camera position).
     This endpoint is FREE, so we always check coverage before paying for pixels.
  2. heading = bearing(pano_position -> venue_position).  Pointing the camera
     from where the car actually stood toward the venue's coordinate is what
     centres the doorway, instead of Street View's default drive-direction view.
  3. static_image() with that heading + a storefront-width FOV + slight up-pitch.

Coverage guard: if the nearest pano is farther than MAX_METADATA_RADIUS_M, or
metadata status != OK, we report "no usable coverage" so the orchestrator can
fall back to the venue's Google Business photos.
"""
from dataclasses import dataclass, asdict
from typing import Optional

import requests

import config
from pipeline.utils import bearing_deg, haversine_m, log

METADATA = "https://maps.googleapis.com/maps/api/streetview/metadata"
STATIC = "https://maps.googleapis.com/maps/api/streetview"


@dataclass
class Framing:
    pano_id: str
    heading: float
    fov: int
    pitch: int
    pano_lat: float
    pano_lng: float
    distance_m: float
    date: str = ""

    def as_dict(self):
        d = asdict(self)
        d["heading"] = round(self.heading, 1)
        d["distance_m"] = round(self.distance_m, 1)
        return d


def metadata(lat: float, lng: float) -> dict:
    r = requests.get(
        METADATA,
        params={"location": f"{lat},{lng}", "key": config.GOOGLE_MAPS_API_KEY},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def compute_framing(venue_lat: float, venue_lng: float,
                    heading_offset: float = 0.0,
                    fov: int = config.DEFAULT_FOV) -> Optional[Framing]:
    """Return a Framing pointed at the venue, or None if coverage is unusable."""
    meta = metadata(venue_lat, venue_lng)
    if meta.get("status") != "OK":
        log("capture", f"no Street View pano (status={meta.get('status')})")
        return None

    ploc = meta["location"]
    dist = haversine_m(ploc["lat"], ploc["lng"], venue_lat, venue_lng)
    if dist > config.MAX_METADATA_RADIUS_M:
        log("capture", f"nearest pano is {dist:.0f} m away (> {config.MAX_METADATA_RADIUS_M} m) — treat as no coverage")
        return None

    heading = (bearing_deg(ploc["lat"], ploc["lng"], venue_lat, venue_lng) + heading_offset) % 360
    return Framing(
        pano_id=meta.get("pano_id", ""),
        heading=heading,
        fov=fov,
        pitch=config.DEFAULT_PITCH,
        pano_lat=ploc["lat"],
        pano_lng=ploc["lng"],
        distance_m=dist,
        date=meta.get("date", ""),
    )


def static_image(framing: Framing) -> Optional[bytes]:
    r = requests.get(
        STATIC,
        params={
            "size": config.STREETVIEW_SIZE,
            "pano": framing.pano_id,
            "heading": round(framing.heading, 2),
            "fov": framing.fov,
            "pitch": framing.pitch,
            "return_error_code": "true",
            "key": config.GOOGLE_MAPS_API_KEY,
        },
        timeout=20,
    )
    if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
        return r.content
    log("capture", f"static image fetch failed: HTTP {r.status_code}")
    return None
