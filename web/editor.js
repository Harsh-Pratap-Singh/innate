/* In Bloom — planter placement editor.
   Vanilla JS, no dependencies.

   Model: every placed planter is stored in image-relative units so that the
   on-screen preview (CSS %) and the exported PNG (natural pixels) share the
   same coordinates:
     cx   — anchor x, fraction of image width  (0..1), bottom-center anchor
     by   — anchor y, fraction of image height (0..1), bottom-center anchor
     h    — planter height as a fraction of image height (0..1)
     flip — horizontal mirror flag
   Export just replays those fractions against naturalWidth/naturalHeight. */

"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const esc = (s) => String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

const PLANTERS_JSON = "assets/planters/planters.json";
const PLANTER_DIR = "assets/planters/";
const UPLOAD_VALUE = "__upload__";

const state = {
  products: [],          // planters.json entries
  items: [],             // placed planters
  selected: null,        // currently selected placed item
  baseLoaded: false,
  nextId: 1,
};

const stage = $("#stage");
const baseImg = $("#base-img");
const stageEmpty = $("#stage-empty");
const venueSelect = $("#venue-select");
const fileInput = $("#file-input");
const downloadBtn = $("#download-btn");
const sizeSlider = $("#size-slider");
const flipBtn = $("#flip-btn");
const deleteBtn = $("#delete-btn");

/* ---------------- bootstrap ---------------- */

async function main() {
  await Promise.all([loadProducts(), loadVenues()]);
  wireStage();
  wireControls();
}

async function loadProducts() {
  let products = [];
  try {
    products = await (await fetch(PLANTERS_JSON, { cache: "no-store" })).json();
  } catch (e) {
    $("#products").innerHTML = '<p class="ctrl-hint">Could not load <code>assets/planters/planters.json</code>.</p>';
    return;
  }
  state.products = products;
  const list = $("#products");
  products.forEach((p) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "product-card";
    card.draggable = true;
    card.dataset.id = p.id;
    const potH = p.real_height_m != null ? `${p.real_height_m.toFixed(2).replace(/0$/, "")} m pot` : "";
    const plantedH = p.planted_height_m != null ? ` &middot; ${p.planted_height_m.toFixed(2).replace(/0$/, "")} m planted` : "";
    card.innerHTML =
      `<span class="pc-thumb"><img src="${esc(PLANTER_DIR + p.cutout)}" alt="${esc(p.name)}" draggable="false" /></span>` +
      `<span class="pc-meta"><span class="pc-name">${esc(p.name)}</span>` +
      `<span class="pc-dim">${potH}${plantedH}</span></span>`;
    // click → drop in at the center of the photo
    card.addEventListener("click", () => addItem(p, 0.5, 0.88));
    // drag → drop at pointer position on the photo
    card.addEventListener("dragstart", (e) => {
      e.dataTransfer.setData("text/plain", p.id);
      e.dataTransfer.effectAllowed = "copy";
    });
    list.appendChild(card);
  });
}

async function loadVenues() {
  let venues = [];
  try {
    const data = await (await fetch("data/results.json", { cache: "no-store" })).json();
    venues = data.venues || [];
  } catch (e) { /* fine — upload-only mode */ }

  venueSelect.innerHTML = "";
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.disabled = true;
  placeholder.selected = true;
  placeholder.textContent = venues.length
    ? "Choose a venue frontage…"
    : "No venues in results.json — upload a photo";
  venueSelect.appendChild(placeholder);

  venues.forEach((v) => {
    const opt = document.createElement("option");
    opt.value = v.frontage || `data/${v.slug}/frontage.jpg`;
    opt.textContent = v.name || v.slug || "venue";
    venueSelect.appendChild(opt);
  });

  const up = document.createElement("option");
  up.value = UPLOAD_VALUE;
  up.textContent = "Upload photo…";
  venueSelect.appendChild(up);

  venueSelect.addEventListener("change", () => {
    if (venueSelect.value === UPLOAD_VALUE) {
      fileInput.click();
      return;
    }
    if (venueSelect.value) setBaseImage(venueSelect.value);
  });

  fileInput.addEventListener("change", () => {
    const f = fileInput.files && fileInput.files[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => setBaseImage(reader.result);
    reader.readAsDataURL(f);
    fileInput.value = ""; // allow re-uploading the same file
  });

  $("#empty-upload").addEventListener("click", () => fileInput.click());
}

/* ---------------- base image ---------------- */

function setBaseImage(src) {
  baseImg.onload = () => {
    state.baseLoaded = true;
    stage.hidden = false;
    stageEmpty.hidden = true;
    downloadBtn.disabled = false;
    clearItems();
  };
  baseImg.onerror = () => {
    state.baseLoaded = false;
    stage.hidden = true;
    stageEmpty.hidden = false;
    stageEmpty.querySelector("p").innerHTML = "<b>Could not load that photo.</b>";
    downloadBtn.disabled = true;
  };
  baseImg.src = src;
}

/* ---------------- placed items ---------------- */

function addItem(product, cx, by) {
  if (!state.baseLoaded) return;
  // sensible default size: taller SKUs land bigger (planted height vs ~3.2 m of visible facade)
  const defH = clamp((product.planted_height_m || 1.1) / 3.2, 0.12, 0.6);
  const el = document.createElement("img");
  el.className = "placed";
  el.src = PLANTER_DIR + product.cutout;
  el.alt = product.name;
  el.draggable = false;
  stage.appendChild(el);

  const item = {
    id: state.nextId++,
    product,
    el,
    cx: clamp(cx, 0, 1),
    by: clamp(by, 0.05, 1),
    h: defH,
    flip: false,
    aspect: (product.px_w && product.px_h) ? product.px_w / product.px_h : 0.8,
  };
  // once the cutout is loaded we know the true aspect ratio
  if (el.complete && el.naturalWidth) item.aspect = el.naturalWidth / el.naturalHeight;
  else el.addEventListener("load", () => { item.aspect = el.naturalWidth / el.naturalHeight; render(item); }, { once: true });

  state.items.push(item);
  wireItem(item);
  render(item);
  select(item);
}

function render(it) {
  it.el.style.left = it.cx * 100 + "%";
  it.el.style.top = it.by * 100 + "%";
  it.el.style.height = it.h * 100 + "%";
  it.el.style.transform = `translate(-50%, -100%)${it.flip ? " scaleX(-1)" : ""}`;
}

function select(it) {
  if (state.selected) state.selected.el.classList.remove("selected");
  state.selected = it;
  if (it) {
    it.el.classList.add("selected");
    sizeSlider.value = Math.round(it.h * 100);
  }
  const on = !!it;
  sizeSlider.disabled = !on;
  flipBtn.disabled = !on;
  deleteBtn.disabled = !on;
  $("#item-controls").classList.toggle("has-selection", on);
}

function removeItem(it) {
  it.el.remove();
  state.items = state.items.filter((x) => x !== it);
  if (state.selected === it) select(null);
}

function clearItems() {
  state.items.forEach((it) => it.el.remove());
  state.items = [];
  select(null);
}

function wireItem(it) {
  const el = it.el;

  el.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    select(it);
    el.setPointerCapture(e.pointerId);
    const r = stage.getBoundingClientRect();
    const startCx = it.cx, startBy = it.by, sx = e.clientX, sy = e.clientY;
    const move = (ev) => {
      it.cx = clamp(startCx + (ev.clientX - sx) / r.width, 0, 1);
      it.by = clamp(startBy + (ev.clientY - sy) / r.height, 0.02, 1.05);
      render(it);
    };
    const up = (ev) => {
      el.releasePointerCapture(e.pointerId);
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up);
      el.removeEventListener("pointercancel", up);
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
    el.addEventListener("pointercancel", up);
  });

  el.addEventListener("dblclick", () => removeItem(it));

  el.addEventListener("wheel", (e) => {
    e.preventDefault();
    select(it);
    resizeSelected(e.deltaY < 0 ? 1.06 : 1 / 1.06);
  }, { passive: false });
}

function resizeSelected(factor) {
  const it = state.selected;
  if (!it) return;
  it.h = clamp(it.h * factor, 0.06, 0.9);
  sizeSlider.value = Math.round(it.h * 100);
  render(it);
}

/* ---------------- stage-level wiring ---------------- */

function wireStage() {
  // drag a product card onto the photo
  stage.addEventListener("dragover", (e) => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; });
  stage.addEventListener("drop", (e) => {
    e.preventDefault();
    const id = e.dataTransfer.getData("text/plain");
    const p = state.products.find((x) => x.id === id);
    if (!p) return;
    const r = stage.getBoundingClientRect();
    addItem(p, (e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height);
  });

  // clicking the background deselects
  stage.addEventListener("pointerdown", (e) => {
    if (e.target === baseImg) select(null);
  });

  // keyboard: R flips, Delete/Backspace removes
  window.addEventListener("keydown", (e) => {
    if (e.target && /INPUT|SELECT|TEXTAREA/.test(e.target.tagName)) return;
    if (!state.selected) return;
    if (e.key === "r" || e.key === "R") { flipSelected(); e.preventDefault(); }
    if (e.key === "Delete" || e.key === "Backspace") { removeItem(state.selected); e.preventDefault(); }
  });
}

function flipSelected() {
  const it = state.selected;
  if (!it) return;
  it.flip = !it.flip;
  render(it);
}

function wireControls() {
  sizeSlider.addEventListener("input", () => {
    const it = state.selected;
    if (!it) return;
    it.h = clamp(Number(sizeSlider.value) / 100, 0.06, 0.9);
    render(it);
  });
  flipBtn.addEventListener("click", flipSelected);
  deleteBtn.addEventListener("click", () => state.selected && removeItem(state.selected));
  downloadBtn.addEventListener("click", exportPNG);
}

/* ---------------- export ---------------- */

function exportPNG() {
  if (!state.baseLoaded) return;
  const NW = baseImg.naturalWidth, NH = baseImg.naturalHeight;
  const canvas = document.createElement("canvas");
  canvas.width = NW;
  canvas.height = NH;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(baseImg, 0, 0, NW, NH);

  state.items.forEach((it) => {
    const h = it.h * NH;
    const w = h * it.aspect;
    const x = it.cx * NW;         // bottom-center anchor
    const y = it.by * NH;
    ctx.save();
    ctx.translate(x, y);
    if (it.flip) ctx.scale(-1, 1);
    // mirror the on-screen CSS drop-shadow, scaled to natural resolution
    const s = NH / Math.max(1, stage.getBoundingClientRect().height);
    ctx.shadowColor = "rgba(20, 30, 20, 0.35)";
    ctx.shadowBlur = 10 * s;
    ctx.shadowOffsetX = 0;
    ctx.shadowOffsetY = 6 * s;
    ctx.drawImage(it.el, -w / 2, -h, w, h);
    ctx.restore();
  });

  canvas.toBlob((blob) => {
    if (!blob) return;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "in-bloom-mockup.png";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }, "image/png");
}

main();
