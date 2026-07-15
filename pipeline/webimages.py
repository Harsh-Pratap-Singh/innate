"""
Capture fallback #2 — web photos of the venue, used only when street-level
imagery fails. Two tiers, both keyless:

  1. The venue's OWN website (OSM `website` tag): og:image + prominent <img>
     tags. The brief explicitly endorses "the venue's own website" as a fallback,
     and rights-wise the owner's published photo used in a private 1:1 mock-up
     back to that same owner is the safest source we have.
  2. Web image search (DuckDuckGo's image endpoint — no API key): query like
     "<name>" <type> London storefront. Rights are murkier (results are often
     Google-Maps/Yelp user photos), so these are last resort, private-1:1 only —
     see design.md §1.4.

Every candidate still passes the SAME vision judge as street imagery; this
module only produces candidate bytes.
"""
import re
from typing import Iterator, List, Tuple

import requests

from pipeline.utils import log

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

IMG_EXT = re.compile(r"\.(jpe?g|png|webp)(\?|$)", re.I)
META_IMG = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)', re.I)
IMG_TAG = re.compile(r'<img[^>]+src=["\']([^"\']+)', re.I)
SKIP_HINTS = ("logo", "icon", "favicon", "sprite", "avatar", "badge", "payment", "tripadvisor")


def _absolute(url: str, base: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http"):
        return url
    from urllib.parse import urljoin
    return urljoin(base, url)


def website_image_urls(site: str, limit: int = 3) -> List[str]:
    """og:image + prominent images from the venue's own homepage."""
    if not site:
        return []
    if not site.startswith("http"):
        site = "https://" + site
    try:
        r = requests.get(site, headers=UA, timeout=15, allow_redirects=True)
        if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
            return []
        html = r.text
    except Exception as e:
        log("capture", f"venue website fetch failed: {e}")
        return []

    urls, seen = [], set()
    for m in META_IMG.finditer(html):
        urls.append(_absolute(m.group(1), site))
    for m in IMG_TAG.finditer(html):
        u = _absolute(m.group(1), site)
        if IMG_EXT.search(u) and not any(h in u.lower() for h in SKIP_HINTS):
            urls.append(u)
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
        if len(out) >= limit:
            break
    return out


def ddg_image_urls(query: str, limit: int = 4) -> List[str]:
    """DuckDuckGo image search (keyless): token handshake then i.js JSON."""
    try:
        r = requests.post("https://duckduckgo.com/", data={"q": query}, headers=UA, timeout=15)
        m = re.search(r"vqd=[\"']?([\d-]+)", r.text)
        if not m:
            log("capture", "ddg: no vqd token")
            return []
        vqd = m.group(1)
        r2 = requests.get(
            "https://duckduckgo.com/i.js",
            params={"l": "uk-en", "o": "json", "q": query, "vqd": vqd, "p": "1"},
            headers={**UA, "Referer": "https://duckduckgo.com/"},
            timeout=15,
        )
        if r2.status_code != 200:
            log("capture", f"ddg images HTTP {r2.status_code}")
            return []
        results = r2.json().get("results", [])
        return [it["image"] for it in results[:limit] if it.get("image")]
    except Exception as e:
        log("capture", f"ddg image search failed: {e}")
        return []


def download(url: str) -> bytes:
    try:
        r = requests.get(url, headers=UA, timeout=20)
        ct = r.headers.get("content-type", "")
        if r.status_code == 200 and (ct.startswith("image") or IMG_EXT.search(url)):
            if len(r.content) > 25_000:          # skip thumbnails/trackers
                return r.content
    except Exception:
        pass
    return b""


def candidates(cand, per_source: int = 3) -> Iterator[Tuple[str, str]]:
    """
    Yield (source_label, url) candidates, best-provenance first:
      1. the venue's own website (brief-endorsed, safest rights)
      2. Google Images via Playwright + system Edge (user-requested; headless,
         human-flow navigation — see pipeline/google_playwright.py)
      3. DuckDuckGo (kept as a last resort; frequently 403s)
    """
    for u in website_image_urls(cand.website, limit=per_source):
        yield ("venue_website", u)

    vtype = (cand.types[0] if cand.types else "venue").replace("_", " ")
    query = f'"{cand.name}" {vtype} London storefront exterior'

    try:
        from pipeline.google_playwright import google_image_urls
        for u in google_image_urls(query, limit=per_source + 1):
            yield ("google_images", u)
    except Exception as e:
        log("capture", f"google images fallback unavailable: {e}")

    for u in ddg_image_urls(query, limit=per_source):
        yield ("web_search", u)
