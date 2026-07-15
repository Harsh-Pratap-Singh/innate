/* In Bloom — static gallery.
   Reads data/results.json (produced by the automated pipeline) and renders, per
   delivered venue, an Apple-style before/after block plus, where available, the
   pipeline's automated capture decisions. */

const $ = (sel, el = document) => el.querySelector(sel);
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
};
const esc = (s) => String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const pct = (x) => (x == null ? "—" : Math.round(x * 100) + "%");

async function main() {
  let data;
  try {
    data = await (await fetch("data/results.json", { cache: "no-store" })).json();
  } catch (e) {
    renderStats({});
    renderFooter({});
    return renderEmpty("No <code>data/results.json</code> found. Run <code>python run.py</code>.");
  }
  const venues = data.venues || [];
  renderStats(data);
  renderFooter(data);

  if (!venues.length) {
    renderEmpty("No venues delivered yet. Add API keys to <code>.env</code> and run <code>python run.py</code>.");
  } else {
    const gallery = $("#gallery");
    venues.forEach((v, i) => gallery.appendChild(venueBlock(v, i)));
  }
  renderRejected(data.rejected_candidates || []);
  observeReveals();
}

function renderStats(d) {
  const s = d.stats || {};
  const box = $("#stats");
  box.innerHTML = "";
  const items = [
    [s.delivered ?? (d.venues || []).length ?? 0, "delivered"],
    [s.unique_candidates ?? "—", "discovered"],
    [s.passed_prefilter ?? "—", "passed pre-filter"],
    [(d.rejected_candidates || []).length, "auto-rejected"],
  ];
  items.forEach(([n, label]) => box.appendChild(el("div", "stat", `<b>${esc(n)}</b> ${esc(label)}`)));
}

const SOURCE_LABELS = {
  streetview: "Google Street View",
  mapillary: "Mapillary street imagery",
  places_photo: "Google Business photo",
  venue_website: "Venue's own website",
  google_images: "Google Images",
  web_search: "Web image search",
};

function venueBlock(v, i) {
  const wrap = el("section", "venue reveal");
  const src = SOURCE_LABELS[v.frontage_source] || v.frontage_source || "—";

  const head = el("div", "venue-head");
  head.innerHTML =
    `<div class="venue-eyebrow">&#10047; Venue ${i + 1}</div>` +
    `<h2>${esc(v.name)}</h2>` +
    `<div class="venue-addr">${esc(v.address)}${v.postcode ? " &middot; " + esc(v.postcode) : ""}</div>`;
  wrap.appendChild(head);

  // Before/after slider; optional variant chips when the run published extras
  const holder = el("div");
  const renderCompare = (afterSrc) => {
    holder.innerHTML = "";
    holder.appendChild(compare(v.frontage, afterSrc));
  };
  const variants = v.variants || [];
  if (variants.length > 1) {
    const chips = el("div", "variant-chips");
    variants.forEach((va, idx) => {
      const chip = el("button", "variant-chip" + (idx === 0 ? " active" : ""), esc(va.label));
      chip.addEventListener("click", () => {
        chips.querySelectorAll(".variant-chip").forEach((c) => c.classList.remove("active"));
        chip.classList.add("active");
        renderCompare(va.file);
      });
      chips.appendChild(chip);
    });
    wrap.appendChild(chips);
  }
  renderCompare(v.composite);
  wrap.appendChild(holder);

  const f = v.framing;
  const spec = el("div", "spec");
  const cells = [
    ["Product placed", esc(v.sku?.name || "—")],
    ["Frontage source", src],
    ["Camera heading", f ? `${f.heading}&deg; <small>&middot; FOV ${f.fov}&deg;</small>` : "—"],
    ["Scale anchor", `door &asymp; ${v.scale?.door_height_assumed_m ?? 2.05} m <small>&middot; planter ${v.scale?.door_ratio_pct ?? "—"}%</small>`],
    ["Frontage bareness", pct(v.frontage_analysis?.bareness_score)],
  ];
  cells.forEach(([k, val]) => spec.appendChild(el("div", null, `<div class="s-k">${esc(k)}</div><div class="s-v">${val}</div>`)));
  wrap.appendChild(spec);

  const dec = decisions(v);
  if (dec) wrap.appendChild(dec);
  return wrap;
}

function compare(beforeSrc, afterSrc) {
  const c = el("div", "compare");
  // If the composite was re-framed (different aspect), a slider overlay misaligns —
  // fall back to a clean side-by-side once both images have loaded.
  const probeB = new Image(), probeA = new Image();
  let loaded = 0;
  const maybeSwap = () => {
    if (++loaded < 2) return;
    const ab = probeB.width / probeB.height, aa = probeA.width / probeA.height;
    if (Math.abs(ab - aa) / ab > 0.1) {
      c.classList.add("side-by-side");
      c.innerHTML =
        `<figure><img src="${esc(beforeSrc)}" alt="original frontage" /><figcaption>BEFORE</figcaption></figure>` +
        `<figure><img src="${esc(afterSrc)}" alt="planters installed (re-framed on the entrance)" /><figcaption>AFTER &middot; entrance close-up</figcaption></figure>`;
    }
  };
  probeB.onload = maybeSwap; probeA.onload = maybeSwap;
  probeB.src = beforeSrc; probeA.src = afterSrc;
  c.innerHTML =
    `<img class="before" src="${esc(beforeSrc)}" alt="original frontage" />` +
    `<div class="after-wrap"><img class="after" src="${esc(afterSrc)}" alt="planters installed" /></div>` +
    `<div class="handle">&#8646;</div>` +
    `<span class="lbl before">BEFORE</span><span class="lbl after">AFTER</span>`;
  const wrap = $(".after-wrap", c), handle = $(".handle", c),
        afterImg = $(".after", c), beforeImg = $(".before", c);

  const sync = () => { afterImg.style.width = c.clientWidth + "px"; };
  beforeImg.addEventListener("load", sync);
  if (beforeImg.complete) sync();
  window.addEventListener("resize", sync);

  const setPos = (ratio) => {
    ratio = Math.max(0, Math.min(1, ratio));
    wrap.style.width = (1 - ratio) * 100 + "%";   // after-wrap anchored right; before shows on the left
    handle.style.left = ratio * 100 + "%";
  };
  setPos(0.5);

  let dragging = false;
  const toRatio = (clientX) => {
    const r = c.getBoundingClientRect();
    return (clientX - r.left) / r.width;
  };
  const move = (e) => {
    if (!dragging) return;
    const x = e.touches ? e.touches[0].clientX : e.clientX;
    setPos(toRatio(x));
  };
  c.addEventListener("pointerdown", (e) => { dragging = true; setPos(toRatio(e.clientX)); });
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", () => (dragging = false));
  return c;
}

function decisions(v) {
  const caps = v.capture_attempts || [];
  if (!caps.length) return null;              // nothing automated to show for this venue
  const d = el("details", "decisions");
  d.appendChild(el("summary", null, "Automated frontage capture decisions"));
  const log = el("div", "log");

  caps.forEach((a, i) => {
    const verdict = a.usable ? '<span class="verdict-ok">usable</span>' : '<span class="verdict-bad">rejected</span>';
    let src;
    if (a.source === "streetview") src = `Street View, heading offset ${a.offset ?? 0}&deg;`;
    else if (a.source === "mapillary") src = `Mapillary image #${(a.index ?? 0) + 1}` +
      (a.distance_m != null ? ` (${a.distance_m} m away` + (a.heading_err != null ? `, aim error ${a.heading_err}&deg;` : "") + ")" : "");
    else src = `Google Business photo #${(a.index ?? 0) + 1}`;
    log.appendChild(el("div", "step", `<span class="k">Capture ${i + 1}:</span> ${src} &rarr; ${verdict} <span class="k">&mdash; ${esc(a.reason || "")}</span>`));
  });

  d.appendChild(log);
  return d;
}

function renderRejected(rej) {
  if (!rej.length) return;
  $("#rejected-section").hidden = false;
  const box = $("#rejected");
  rej.slice(0, 24).forEach((r) => {
    const card = el("div", "rej reveal");
    card.innerHTML =
      `<div class="stg">${esc(r.stage || "")}</div>` +
      `<div class="nm">${esc(r.name || "(unnamed)")}</div>` +
      `<div class="rs">${esc(r.reason || "")}</div>`;
    box.appendChild(card);
  });
}

function renderFooter(d) {
  const m = (d.config && d.config.models) || {};
  const stack = (d.config && d.config.stack) || {};
  const imagery = stack.imagery === "streetview"
    ? "Street View imagery &copy; Google."
    : "Street-level imagery &copy; Mapillary contributors (CC-BY-SA).";
  $("#foot").innerHTML =
    `Generated ${esc(d.generated_at || "—")}` +
    (m.judge ? ` &middot; judge <code>${esc(m.judge)}</code> &middot; image <code>${esc(m.image)}</code>` : "") +
    ` &middot; ${imagery} See design.md for the imagery-rights position.`;
}

function renderEmpty(msg) {
  $("#gallery").appendChild(el("div", "empty", msg));
}

function observeReveals() {
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } });
  }, { threshold: 0.12 });
  document.querySelectorAll(".reveal").forEach((n) => io.observe(n));
}

main();
