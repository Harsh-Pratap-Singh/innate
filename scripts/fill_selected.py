"""
Generate SELECTED_VENUES.md from the latest run (web/data/results.json).

Keeps design.md's "selected venues" claim honest: it always reflects what the
code actually picked and rejected, rather than a hand-typed list that drifts.

    python scripts/fill_selected.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "web" / "data" / "results.json"
OUT = ROOT / "SELECTED_VENUES.md"


def main():
    if not RESULTS.exists():
        raise SystemExit("No web/data/results.json yet — run `python run.py` first.")
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    venues = data.get("venues", [])
    rejected = data.get("rejected_candidates", [])
    stats = data.get("stats", {})

    lines = ["# Selected venues (auto-generated from the latest run)\n"]
    lines.append(f"_Generated: {data.get('generated_at', '—')}_\n")
    lines.append(
        f"Discovered {stats.get('unique_candidates','?')} unique candidates → "
        f"{stats.get('passed_prefilter','?')} passed the pre-filter → "
        f"**{len(venues)} delivered**.\n")

    lines.append("## Delivered\n")
    if not venues:
        lines.append("_None yet._\n")
    else:
        lines.append("| # | Name | Address | Postcode | Product placed | Frontage source |")
        lines.append("|---|------|---------|----------|----------------|-----------------|")
        for i, v in enumerate(venues, 1):
            src = {"streetview": "Google Street View", "mapillary": "Mapillary street imagery",
                   "places_photo": "Google Business photo", "venue_website": "Venue website",
                   "google_images": "Google Images", "web_search": "Web image search",
                   }.get(v.get("frontage_source", ""), v.get("frontage_source", "—"))
            lines.append(f"| {i} | {v.get('name','')} | {v.get('address','')} | "
                         f"{v.get('postcode','')} | {v.get('sku',{}).get('name','')} | {src} |")
    lines.append("")

    lines.append("## Rejected (automated, with reason)\n")
    if not rejected:
        lines.append("_None recorded._\n")
    else:
        lines.append("| Name | Stage | Reason |")
        lines.append("|------|-------|--------|")
        for r in rejected[:60]:
            reason = (r.get("reason", "") or "").replace("|", "/")[:160]
            lines.append(f"| {r.get('name','(unnamed)')} | {r.get('stage','')} | {reason} |")
    lines.append("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUT} ({len(venues)} delivered, {len(rejected)} rejected)")


if __name__ == "__main__":
    main()
