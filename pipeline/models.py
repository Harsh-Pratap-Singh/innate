"""Shared data types across discovery sources (Places / OSM)."""
from dataclasses import dataclass, field
from typing import List, Optional

from pipeline.utils import slugify


@dataclass
class Candidate:
    place_id: str          # "osm/node/123" or a Places place_id
    name: str
    address: str
    postcode: str
    lat: float
    lng: float
    types: List[str]
    photo_refs: List[str] = field(default_factory=list)   # Places-only; empty for OSM
    rejected: Optional[str] = None                        # set by the pre-filter; kept for the report
    area: str = ""                                        # discovery area, used to interleave attempts
    website: str = ""                                     # venue's own site (OSM tag) — web fallback

    @property
    def slug(self) -> str:
        return slugify(self.name)
