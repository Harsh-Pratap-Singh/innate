"""
Vision prompts shared by every AI provider (Qwen / Gemini) so the accept/reject
bar is identical regardless of model.
"""

FRONTAGE_PROMPT = """You are the automated quality gate for a pipeline that shows independent London
venues (cafes, coffee shops, restaurants, bakeries, hair/beauty salons and similar street-facing
businesses) what their entrance could look like dressed with outdoor planters.

Judge the attached street-level photo on TWO things and return STRICT JSON only.
{venue_hint}
A) CAPTURE USABILITY - is this a clean, usable shot of ONE venue's street-level entrance?
   - entrance_visible: is a street-level shopfront entrance/doorway clearly in frame?
   - is_target_business_type: does it look like a cafe/restaurant/bakery/salon/bar or similar
     independent street-facing venue (NOT an office lobby, house, car park, road, or blank wall)?
   - centred: is the entrance reasonably centred, not sliced off at the edge?
   - obstruction: "none", or name what blocks it (parked van, scaffolding, tree, bus, crowd).
   - daylight: is it daytime with even light (not night, not blown-out)?
   - sharp: is it in focus (not motion-blurred / smeared)?
   - usable: overall - is this good enough to build a client mock-up from?

B) SUITABILITY - would the client's planters visibly improve THIS entrance?
   - bareness_score: 0.0 (already heavily planted/decorated) .. 1.0 (totally bare frontage).
   - pavement_space: is there clear pavement/ground beside the entrance to stand planters on?
   - would_improve: would flanking planters visibly enhance this specific entrance?
   - suitable: overall fit for outreach.

C) SCALE ANCHOR
   - door_bbox: bounding box of the main entrance door as [x0,y0,x1,y1] normalised 0..1
     (top-left origin). null if no door is clearly visible.

Return exactly this JSON shape and nothing else:
{{"entrance_visible":bool,"is_target_business_type":bool,"centred":bool,"obstruction":string,
"daylight":bool,"sharp":bool,"usable":bool,"bareness_score":number,"pavement_space":bool,
"would_improve":bool,"suitable":bool,"door_bbox":[number,number,number,number]|null,
"reasons":[string]}}"""


LOCATE_PROMPT = """You are scanning a wide street-level photo taken AT the known location of one
specific venue. The camera was pointed toward the venue, so the target storefront is most likely
the one nearest the CENTRE of the frame.

Target venue: "{venue_name}"{type_hint}.

Your job: identify the storefront in this frame that is most plausibly this venue, and box it.
Because the camera is at the venue's coordinates, DO NOT require legible signage to say "found" —
an unreadable sign on a plausible cafe/restaurant/salon-style shopfront near the centre still
counts as found (with moderate confidence). Name actually readable and matching = high confidence.

Return STRICT JSON only:
 - found: is there a plausible street-facing shopfront for this venue in frame? (false only if
   the frame shows no usable storefront at all: blank walls, residential houses, a tunnel,
   garages, or every shopfront clearly belongs to a DIFFERENT named business.)
 - name_confirmed: is the venue's name actually readable on signage/awning?
 - confidence: 0.0-1.0 that the boxed storefront is the target venue.
 - venue_bbox: [x0,y0,x1,y1] normalised 0..1 (top-left origin) tightly around that shopfront
   (ground floor: door + windows + fascia). null if not found.
 - obstruction: "none", or what blocks the shopfront (van, market stall, scaffolding, crowd, tree).
 - reasons: short strings explaining the choice.

Return exactly:
{{"found":bool,"name_confirmed":bool,"confidence":number,
"venue_bbox":[number,number,number,number]|null,"obstruction":string,"reasons":[string]}}"""


DOOR_PROMPT = """Look at this photo of a shop frontage. Find the venue's main entrance door
(the customer door — not windows, not service/garage doors).

Return STRICT JSON only:
{"door_bbox":[x0,y0,x1,y1]|null,"confidence":number}

door_bbox is normalised 0..1 with top-left origin, tightly around the full door (frame included,
from the top of the door frame down to the threshold at ground level). null if no clear door."""


def locate_prompt(venue_name: str, venue_type: str = "") -> str:
    type_hint = f" (a {venue_type})" if venue_type else ""
    return LOCATE_PROMPT.format(venue_name=venue_name, type_hint=type_hint)


def frontage_prompt(venue_name: str = "", venue_type: str = "") -> str:
    """FRONTAGE_PROMPT with an optional 'which venue to look for' hint."""
    hint = ""
    if venue_name:
        hint = (f"\nYou are looking for the venue named \"{venue_name}\""
                + (f" (a {venue_type})" if venue_type else "")
                + ". Judge THAT venue's frontage — the main storefront in frame. If legible "
                  "signage clearly shows a DIFFERENT business occupies the main storefront, "
                  "set is_target_business_type to false.\n")
    return FRONTAGE_PROMPT.format(venue_hint=hint)


QA_PROMPT = """You are the final gate before a composite image is sent to a real venue owner. Be strict:
a bad image reaching an owner costs the client credibility, so when in doubt REJECT.

You are given three images IN THIS ORDER: 1=BEFORE (original frontage), 2=AFTER (edited, to
evaluate), 3=REFERENCE (the client's actual product the inserted planter MUST match).

The AFTER image should show the reference planter(s) placed on the ground beside the entrance,
with everything else in the BEFORE image unchanged.

Product being placed: {sku_name} - {sku_material}.
Expected scale: the planter should be roughly {ratio_pct}% of the entrance door's height.

Check each and return STRICT JSON:
 - identity_ok: is the inserted planter recognisably THE SAME PRODUCT as the REFERENCE —
   same overall form, same material and colour, similar planting style? Minor differences in
   taper/proportion or slight planting variation are ACCEPTABLE. NEVER reject because a square
   planter looks "tapered" vs "straight-sided" or vice versa — treat those as the same form.
   Fail ONLY if it is clearly a different product: different material (e.g. glossy ceramic vs
   matte composite), different colour, or a completely different shape (round vs square,
   trough vs column).
 - scale_ok: is the planter's size believable vs the door (not a giant, not a toy)?
 - scene_integrity_ok: is EVERYTHING except the added planters + their shadows unchanged?
   Look hard at signage/text, windows, brickwork, people. false if any of it was altered/warped.
 - grounding_ok: does the planter sit ON the ground with a believable contact shadow (not floating)?
 - placement_ok: are planters on the pavement, not blocking the doorway/path or overlapping a person?
 - artifacts: are there obvious generation artifacts (warped lines, duplicated/melted objects)?
 - accept: overall - send this to the owner? (must be false if any critical check fails)
 - reasons: short bullet strings explaining the verdict.

Return exactly:
{{"identity_ok":bool,"scale_ok":bool,"scene_integrity_ok":bool,"grounding_ok":bool,
"placement_ok":bool,"artifacts":bool,"accept":bool,"reasons":[string]}}"""
