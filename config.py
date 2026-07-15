"""
Central configuration and thresholds for the storefront-capture pipeline.

Every tunable that governs an automated accept/reject decision lives here, so
the pipeline's "judgement bar" is auditable in one place (the brief grades the
decision-making, not the polish).
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- paths ---
ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
PLANTERS_DIR = ASSETS / "planters"
PLANTERS_JSON = PLANTERS_DIR / "planters.json"
WEB_DATA = ROOT / "web" / "data"          # gallery reads results.json + images from here
OUT = ROOT / "out"                        # raw per-run scratch (git-ignored)

# --- credentials ---
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# --- models ---
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "gemini-2.5-flash")        # vision judging
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gemini-2.5-flash-image")  # nano-banana editing

# --- discovery ---
# Text queries steer Places at areas dense with independent, street-facing venues.
# Kept as data so a new city/area is a one-line change.
SEARCH_QUERIES = [
    "independent cafe Broadway Market London",
    "independent coffee shop Exmouth Market London",
    "cafe Columbia Road London",
    "hair salon Peckham Rye Lane London",
    "independent restaurant Kingsland Road London",
]

# name-based chain filter -> our cheap "independent" signal (pre-vision)
CHAIN_BLOCKLIST = [
    "starbucks", "costa", "pret", "caffe nero", "cafe nero", "greggs",
    "mcdonald", "kfc", "burger king", "subway", "nando", "wagamama",
    "leon", "itsu", "gail", "joe & the juice", "franco manca", "pizza express",
    "wetherspoon", "toni & guy", "toni and guy", "supercuts", "regis",
    "five guys", "shake shack", "tortilla", "wasabi", "eat.", "coco di mama",
]

ACCEPTED_PLACE_TYPES = {
    "cafe", "restaurant", "bakery", "hair_care", "beauty_salon",
    "bar", "meal_takeaway", "book_store", "florist", "clothing_store",
}

# --- capture / framing ---
STREETVIEW_SIZE = "640x640"     # free tier allows up to 640; square frames the door well
DEFAULT_FOV = 78                # degrees; ~storefront-width at pavement distance
DEFAULT_PITCH = 6               # look very slightly up at the facade
HEADING_NUDGES = [0, -18, 18]   # retry offsets (deg) if first framing is rejected
MAX_METADATA_RADIUS_M = 60      # if nearest pano is farther than this, treat as no coverage

# --- scale anchor ---
DOOR_HEIGHT_M = 2.05            # UK commercial door assumption for px-per-metre
PLANTER_DOOR_RATIO_MIN = 0.28   # accept band for planter-height / door-height in output
PLANTER_DOOR_RATIO_MAX = 0.60

# --- budgets (cost control at 5k/week; keep prototype runs cheap) ---
MAX_CAPTURE_ATTEMPTS = 12       # hard cap on venues we try in one run (quality > quantity)
STOP_AFTER_N_ACCEPTED = 3       # deliver 3 GOOD venues then stop
MAX_HEADING_RETRIES = 2         # per venue, before falling back to Places photos
MAX_COMPOSITE_RETRIES = 3       # per venue, before marking the venue failed

# --- QA: scene-integrity pixel diff ---
# fraction of pixels allowed to change OUTSIDE the ground placement zone before
# we call the edit "touched the building". Tolerant of JPEG recompression noise.
QA_PIXEL_DIFF_THRESHOLD = 30    # per-channel abs diff counted as a real change (0-255)
QA_MAX_CHANGED_FRACTION = 0.06  # >6% changed outside the zone -> reject
QA_PLACEMENT_ZONE_TOP = 0.55    # placement zone = image below this y-fraction (ground)

# =====================================================================
# Provider selection — the active stack is OSM + Mapillary + Qwen (no billing card).
# Google Street View + Gemini remain in the repo as the alternative stack.
# =====================================================================
IMAGERY_SOURCE = os.getenv("IMAGERY_SOURCE", "mapillary")   # "mapillary" | "streetview"
AI_PROVIDER = os.getenv("AI_PROVIDER", "qwen")              # "qwen" | "gemini"

# --- OpenStreetMap (Overpass) discovery — no key required ---
OVERPASS_URL = os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
# London areas dense with independents (lat, lng, search radius m).
# Chosen for BOTH indie density and street-imagery quality: wide pavements, no
# permanent market stalls blocking frontages (Broadway Market failed on market-day
# imagery; Gillett Square venues geocode inside a covered arcade).
OSM_AREAS = [
    {"name": "Exmouth Market",       "lat": 51.5266, "lng": -0.1093, "radius": 240},
    {"name": "Upper Street",         "lat": 51.5416, "lng": -0.1027, "radius": 380},
    {"name": "Stoke Newington",      "lat": 51.5620, "lng": -0.0740, "radius": 380},
    {"name": "Bermondsey Street",    "lat": 51.5010, "lng": -0.0810, "radius": 320},
    {"name": "Lordship Lane",        "lat": 51.4560, "lng": -0.0745, "radius": 380},
]
OSM_AMENITIES = ["cafe", "restaurant", "bar", "pub", "fast_food"]
OSM_SHOPS = ["hairdresser", "beauty", "bakery"]

# --- Mapillary street-level imagery ---
MAPILLARY_TOKEN = os.getenv("MAPILLARY_TOKEN", "")
MAPILLARY_GRAPH = os.getenv("MAPILLARY_GRAPH", "https://graph.mapillary.com")
MAPILLARY_SEARCH_M = 55         # bbox half-size around a venue when searching for images
MAPILLARY_MAX_CANDIDATES = 8    # images to consider per venue (best-facing first)
MAPILLARY_MAX_HEADING_ERR = 65  # deg; camera must face within this of the venue bearing
MAPILLARY_JUDGE_MAX = 4         # wide frames to locate/judge per venue before giving up
MAPILLARY_THUMB = "thumb_2048_url"
LOCATE_MIN_CONFIDENCE = 0.45    # venue-in-frame confidence below this -> next image

# --- Qwen / DashScope (OpenAI-compatible mode) ---
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
# International endpoint by default; set DASHSCOPE_BASE for the CN region.
DASHSCOPE_BASE = os.getenv("DASHSCOPE_BASE", "https://dashscope-intl.aliyuncs.com")
QWEN_VL_MODEL = os.getenv("QWEN_VL_MODEL", "qwen-vl-max")          # vision judging
QWEN_EDIT_MODEL = os.getenv("QWEN_EDIT_MODEL", "qwen-image-edit")  # reference image editing


def require_keys():
    """Fail fast with an actionable message instead of a deep stack trace."""
    missing = []
    if IMAGERY_SOURCE == "streetview" and not GOOGLE_MAPS_API_KEY:
        missing.append("GOOGLE_MAPS_API_KEY")
    if IMAGERY_SOURCE == "mapillary" and not MAPILLARY_TOKEN:
        missing.append("MAPILLARY_TOKEN")
    if AI_PROVIDER == "gemini" and not GEMINI_API_KEY:
        missing.append("GEMINI_API_KEY")
    if AI_PROVIDER == "qwen" and not DASHSCOPE_API_KEY:
        missing.append("DASHSCOPE_API_KEY")
    # OpenStreetMap discovery needs no key.
    if missing:
        raise SystemExit(
            "Missing credentials: " + ", ".join(missing) +
            f"\n(imagery={IMAGERY_SOURCE}, ai={AI_PROVIDER}) — fill them in .env (see README)."
        )
