"""
Deterministic scene-integrity backstop for the composite QA gate.

The vision judge (qa_composite) is the primary integrity check, but it can be
fooled. This adds a cheap, model-free second opinion: the ONLY region allowed to
change between BEFORE and AFTER is the ground band where planters are placed
(below QA_PLACEMENT_ZONE_TOP). If a large fraction of pixels changed ABOVE that
band, the edit model altered the facade/signage/windows -> reject.

Tolerant of JPEG recompression: only per-pixel changes above QA_PIXEL_DIFF_THRESHOLD
count, so global re-encode noise doesn't trip it.
"""
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
from PIL import Image

import config


@dataclass
class IntegrityResult:
    changed_fraction_above_zone: float
    changed_fraction_total: float
    ok: bool
    reframed: bool = False   # model returned a different aspect (zoom/crop) — pixel diff n/a

    def as_dict(self):
        d = asdict(self)
        d["changed_fraction_above_zone"] = round(self.changed_fraction_above_zone, 4)
        d["changed_fraction_total"] = round(self.changed_fraction_total, 4)
        return d


def _load(path: Path, size) -> np.ndarray:
    img = Image.open(path).convert("RGB")
    if size and img.size != size:
        img = img.resize(size, Image.BILINEAR)
    return np.asarray(img, dtype=np.int16)


def scene_integrity(before_path: Path, after_path: Path) -> IntegrityResult:
    before = Image.open(before_path).convert("RGB")
    after_img = Image.open(after_path)
    W, H = before.size

    # If the edit model re-framed the shot (zoomed into the doorway), a pixel diff
    # against the original is meaningless — every pixel "changed". Flag it and let
    # the vision QA's scene_integrity_ok carry the integrity judgement instead.
    aspect_before = W / H
    aspect_after = after_img.width / after_img.height
    if abs(aspect_before - aspect_after) / aspect_before > 0.08:
        return IntegrityResult(0.0, 1.0, ok=True, reframed=True)

    a = np.asarray(before, dtype=np.int16)
    b = _load(after_path, (W, H))

    # per-pixel max abs difference across channels
    diff = np.abs(a - b).max(axis=2)
    changed = diff > config.QA_PIXEL_DIFF_THRESHOLD

    cut = int(config.QA_PLACEMENT_ZONE_TOP * H)     # rows [0:cut) = facade we must preserve
    above = changed[:cut, :]
    frac_above = float(above.mean()) if above.size else 0.0
    frac_total = float(changed.mean())

    ok = frac_above <= config.QA_MAX_CHANGED_FRACTION
    return IntegrityResult(frac_above, frac_total, ok)
