"""Google Images fetcher via Playwright — prototype-only per-venue fallback.

Scrapes Google Images search results (headless Edge via Playwright) for a
handful of candidate photo URLs when the primary sources (Places, Street View,
Mapillary, web images) come up empty for a venue. Intended usage is low-volume
and driven per-venue with queries like "<venue name> <type> London storefront".

Notes / caveats (for the design note):
  * ToS-GRAY: automated scraping of Google Search results is against Google's
    Terms of Service. This is acceptable only as a low-volume prototype
    fallback; do NOT scale this up or run it in production without switching
    to a licensed API (e.g. SerpAPI, Google Custom Search JSON API).
  * Results are unvetted — every URL returned here still passes through the
    same downstream vision judge / QA as any other candidate image.
  * Uses the system-installed Edge browser (channel="msedge"), so no
    "playwright install" browser download is required.
  * Playwright is imported lazily inside the function so the rest of the
    pipeline works without it installed; any failure returns [].
  * Direct navigation to /search?tbm=isch is frequently answered with
    Google's /sorry (unusual traffic) interstitial for automated browsers,
    even headed. When that happens we fall back to a human-like flow:
    load the homepage, accept consent, type the query into the search box,
    press Enter, click the "Images" tab, then hover the grid thumbnails
    (Google only populates the a[href*="/imgres?"] hrefs lazily on hover).
"""

from __future__ import annotations

import urllib.parse

# Substrings identifying Google's own encrypted thumbnail hosts. These are
# low-res proxies; only used if no full-resolution URLs could be extracted.
_GSTATIC_MARKERS = ("encrypted-tbn", "gstatic.com")

_CONSENT_SELECTORS = (
    'button:has-text("Accept all")',
    'button:has-text("Alle akzeptieren")',
    'button:has-text("I agree")',
    'form[action*="consent"] button',
    '#L2AGLb',  # Google's "Accept all" button id
)

_BAD_EXTENSIONS = (".svg", ".gif")

_LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]


def _is_bad_url(url: str) -> bool:
    """Reject non-photo formats (.svg / .gif), matching on the URL path."""
    path = urllib.parse.urlsplit(url).path.lower()
    return path.endswith(_BAD_EXTENSIONS)


def _is_gstatic_thumb(url: str) -> bool:
    return any(m in url for m in _GSTATIC_MARKERS)


def _dedupe(urls):
    seen = set()
    out = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _accept_consent(page) -> bool:
    """Click through the EU consent interstitial if present."""
    try:
        needs = "consent." in page.url or page.locator(
            'form[action*="consent"]'
        ).count()
    except Exception:
        needs = True
    if not needs:
        # Consent buttons can also appear as an overlay on google.com itself.
        needs = True
    for sel in _CONSENT_SELECTORS:
        try:
            btn = page.locator(sel).first
            if btn.count() and btn.is_visible():
                btn.click(timeout=3000)
                page.wait_for_load_state("domcontentloaded", timeout=15000)
                return True
        except Exception:
            continue
    return False


def _harvest(page, limit: int):
    """Collect (full_urls, thumb_urls) from a rendered image-results page."""
    try:
        page.wait_for_selector(
            'a[href*="/imgres?"], img[src^="http"]', timeout=10000
        )
    except Exception:
        pass
    try:
        page.mouse.wheel(0, 2000)
        page.wait_for_timeout(1200)
    except Exception:
        pass

    # Google populates a[href*="/imgres?"] hrefs lazily on hover, so hover
    # the first batch of grid thumbnails before reading anchors.
    try:
        thumbs = page.locator('img[src^="http"]')
        for i in range(min(thumbs.count(), max(limit * 3, 15))):
            try:
                t = thumbs.nth(i)
                if t.is_visible():
                    t.hover(timeout=1500)
                    page.wait_for_timeout(120)
            except Exception:
                continue
    except Exception:
        pass

    full_urls = []
    thumb_urls = []

    # Preferred path: full-resolution URLs from /imgres? anchors.
    try:
        hrefs = page.eval_on_selector_all(
            'a[href*="/imgres?"]', "els => els.map(e => e.href)"
        )
    except Exception:
        hrefs = []
    for href in hrefs:
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
        for img_url in qs.get("imgurl", []):
            if img_url.startswith("http") and not _is_bad_url(img_url):
                full_urls.append(img_url)

    # Fallback path: grid thumbnails (mark: these are low-res thumbnails).
    if not full_urls:
        try:
            srcs = page.eval_on_selector_all(
                'img[src^="http"]',
                "els => els.filter(e => e.naturalWidth >= 200)"
                ".map(e => e.src)",
            )
        except Exception:
            srcs = []
        for src in srcs:
            if not _is_bad_url(src):
                thumb_urls.append(src)

    return full_urls, thumb_urls


def google_image_urls(query: str, limit: int = 6) -> "list[str]":
    """Return up to `limit` image URLs from a Google Images search.

    Prefers full-resolution URLs extracted from the `imgurl=` param of
    `/imgres?` anchors; falls back to grid thumbnails (>=200px natural width)
    if none are found — those are low-res but still usable candidates.
    Returns [] on any failure (including Playwright not being installed).
    """
    try:
        # Lazy import: the pipeline must work without playwright installed.
        from playwright.sync_api import sync_playwright

        search_url = (
            "https://www.google.com/search?q="
            + urllib.parse.quote_plus(query)
            + "&tbm=isch&hl=en&gl=uk"
        )

        full_urls: list[str] = []
        thumb_urls: list[str] = []

        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel="msedge", headless=True, args=_LAUNCH_ARGS
            )
            try:
                context = browser.new_context(
                    locale="en-GB",
                    viewport={"width": 1440, "height": 900},
                )
                page = context.new_page()

                # Attempt 1: direct navigation to the image-search URL.
                page.goto(
                    search_url, wait_until="domcontentloaded", timeout=30000
                )
                _accept_consent(page)
                if "/sorry/" not in page.url:
                    full_urls, thumb_urls = _harvest(page, limit)

                # Attempt 2: /sorry block (or empty) — human-like flow via
                # the homepage search box and the "Images" tab.
                if not full_urls and not thumb_urls:
                    page.goto(
                        "https://www.google.com/?hl=en",
                        wait_until="domcontentloaded",
                        timeout=30000,
                    )
                    _accept_consent(page)
                    box = page.locator(
                        'textarea[name="q"], input[name="q"]'
                    ).first
                    box.click(timeout=5000)
                    box.type(query, delay=30)
                    page.keyboard.press("Enter")
                    page.wait_for_load_state(
                        "domcontentloaded", timeout=30000
                    )
                    if "/sorry/" not in page.url:
                        tab = page.locator('a:has-text("Images")').first
                        tab.click(timeout=8000)
                        page.wait_for_load_state(
                            "domcontentloaded", timeout=30000
                        )
                        page.wait_for_timeout(1500)
                        if "/sorry/" not in page.url:
                            full_urls, thumb_urls = _harvest(page, limit)
            finally:
                browser.close()

        if full_urls:
            # Full URLs found: drop gstatic encrypted thumbnails entirely.
            urls = [u for u in full_urls if not _is_gstatic_thumb(u)]
        else:
            # Thumbnails only (including gstatic) — still usable, low-res.
            urls = thumb_urls

        return _dedupe(urls)[:limit]
    except Exception:
        return []


if __name__ == "__main__":
    import sys

    q = " ".join(sys.argv[1:]) or "Morchella restaurant Exmouth Market London"
    for u in google_image_urls(q, 6):
        print(u)
