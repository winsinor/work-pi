"use strict";

// ── Constants ──────────────────────────────────────────────────────────────

const WEATHER_ICONS = ["sun","cloud","partly_cloudy","rain","heavy_rain",
                       "thunderstorm","snow","fog"];

const PAGE_NAMES = ["clock","forecast","calendar","commute","wfh","ooo","holiday","spotify"];
const PAGE_LABELS = {
  clock:"Clock", forecast:"Forecast", calendar:"Calendar",
  commute:"Commute", wfh:"WFH", ooo:"OOO", holiday:"Holiday",
  spotify:"Spotify",
};

const CALENDAR_DRIVEN_PAGES = ["wfh","ooo","holiday"];
const MARQUEE_PAGES          = ["spotify","calendar"];

// ── State ──────────────────────────────────────────────────────────────────

const state = {
  layout:    null,       // loaded from /work/layout
  dirty:     false,
  currentPage: "clock",  // page name or "global"
  previewIcon: null,     // icon override for forecast preview
  playing:   false,
  playTimer: null,
  calendarEmptyView: false,  // toggle empty-state preview on calendar page
  forecastPreview: "normal", // "normal" | "stale"
  lineCenters: {},           // page name -> exact center-Y per line (from render)
};

// ── D-pad ──────────────────────────────────────────────────────────────────

let _activeElem   = null;  // { label, xInp, yInp }
const _nudgeSteps = [1, 5, 10];
let _nudgeStepIdx = 0;

function _nudgeStep() { return _nudgeSteps[_nudgeStepIdx]; }

function cycleStep() {
  _nudgeStepIdx = (_nudgeStepIdx + 1) % _nudgeSteps.length;
  document.getElementById("dpad-step").textContent = _nudgeStep() + "px";
}

// Compute Y for every line on a page, respecting explicit, auto, and hidden positions
function _computeAllAutoYs(pageName) {
  const L  = state.layout;
  const positions = (L.line_positions && L.line_positions[pageName]) || [];
  const hh = L.header?.height || 0;
  const fh = L.footer?.height || 0;
  const gapMin = L.content?.line_gap_min || 2;
  const hasGrid = pageName === "forecast";
  const gh = hasGrid ? (L.grid?.height || 82) : 0;
  const contentY0 = hh > 0 ? hh + 1 : 0;
  const contentH  = 240 - fh - gh - contentY0;
  let totalAutoH = 0, nAuto = 0;
  for (const pos of positions) {
    if (pos.visible === false) continue;
    if (pos.y == null) { totalAutoH += (pos.h || 14); nAuto++; }
  }
  const gap = Math.max(gapMin, nAuto > 0 ? Math.floor((contentH - totalAutoH) / (nAuto + 1)) : gapMin);
  // Return CENTER Y for each line (top + h/2); null for hidden lines
  const ys = [];
  let autoY = contentY0 + gap;
  for (const pos of positions) {
    if (pos.visible === false) { ys.push(null); continue; }
    const h = pos.h || 14;
    if (pos.y != null) {
      ys.push(pos.y);
    } else {
      ys.push(Math.round(autoY + h / 2));
      autoY += h + gap;
    }
  }
  return ys;
}

// Prefer the exact center-Y the renderer reported (via the preview response);
// fall back to the local estimate until the first preview has loaded.
function _autoCenters(pageName) {
  return state.lineCenters[pageName] || _computeAllAutoYs(pageName);
}

// Lock all auto-Y lines to their computed positions so only one moves at a time
function _lockAutoLines(pageName) {
  const allYs  = _autoCenters(pageName);
  const L      = state.layout;
  const positions = (L.line_positions && L.line_positions[pageName]) || [];
  document.querySelectorAll(`#props-content .elem-row[data-page-name="${pageName}"]`)
    .forEach(row => {
      const idx = parseInt(row.dataset.lineIdx);
      if (isNaN(idx) || idx >= positions.length) return;
      if (positions[idx].y != null) return; // already explicit, leave it
      const yInp = row.querySelector(".elem-y");
      if (!yInp) return;
      const lockedY = allYs[idx];
      yInp.value = lockedY;
      setAt(L, `line_positions.${pageName}.${idx}.y`, lockedY);
    });
}

function nudge(dir) {
  if (!_activeElem) return;
  const step  = _nudgeStep();
  const isX   = dir === "left" || dir === "right";
  const delta = (dir === "right" || dir === "down") ? step : -step;
  const inp   = isX ? _activeElem.xInp : _activeElem.yInp;
  if (!inp) return;

  // On first Y nudge from auto: lock all sibling auto lines first so only this one moves
  if (!isX && inp.value === "" && _activeElem.pageName != null) {
    _lockAutoLines(_activeElem.pageName);
  }

  const maxV = isX ? 319 : 239;
  let cur;
  if (inp.value !== "") {
    cur = parseFloat(inp.value);
  } else if (isX) {
    cur = 160; // center of 320px canvas
  } else {
    // Exact auto-Y for this specific line, as reported by the renderer
    const ys = _autoCenters(_activeElem.pageName);
    cur = (ys[_activeElem.lineIdx] != null) ? ys[_activeElem.lineIdx] : 20;
  }

  inp.value = Math.max(0, Math.min(maxV, Math.round(cur + delta)));
  inp.dispatchEvent(new Event("input"));
  _updateDpadCoords();
}

// On mobile the active element's H/X/Y/Show inputs are relocated into the D-pad
// dock so they stay put while the element list scrolls. Track the moved node so
// it can be returned to its row before another is moved (or the list rebuilds).
const _isMobile = () => window.matchMedia("(max-width: 600px)").matches;
let _movedDetail = null, _movedHome = null;

function _restoreMovedDetail() {
  if (_movedDetail && _movedHome && _movedHome.isConnected) {
    _movedHome.appendChild(_movedDetail);
  }
  _movedDetail = null;
  _movedHome = null;
}

function _relocateDetail(row) {
  _restoreMovedDetail();
  if (!_isMobile()) return;
  const detail = row.querySelector(".elem-detail");
  const slot = document.getElementById("dpad-active");
  if (!detail || !slot) return;
  _movedDetail = detail;
  _movedHome = row;
  slot.appendChild(detail);
}

function activateElem(row) {
  document.querySelectorAll(".elem-row.active-elem")
    .forEach(r => r.classList.remove("active-elem"));
  row.classList.add("active-elem");
  const xInp    = row.querySelector(".elem-x");
  const yInp    = row.querySelector(".elem-y");
  const hInp    = row.querySelector(".elem-h");
  const visInp  = row.querySelector(".elem-vis");
  const label   = row.querySelector(".elem-header")?.textContent?.trim() || "";
  const pageName = row.dataset.pageName || null;
  const lineIdx  = row.dataset.lineIdx != null ? parseInt(row.dataset.lineIdx) : null;
  _activeElem = { label, xInp, yInp, hInp, visInp, pageName, lineIdx };
  document.getElementById("dpad-label").textContent = label;
  _relocateDetail(row);
  _updateDpadCoords();
  // Gray out controls the element doesn't support
  document.querySelectorAll(".dpad-btn[data-dir=left], .dpad-btn[data-dir=right]")
    .forEach(b => b.disabled = !xInp);
  document.querySelectorAll(".dpad-btn[data-dir=up], .dpad-btn[data-dir=down]")
    .forEach(b => b.disabled = !yInp);
  document.querySelectorAll(".dpad-btn[data-dir^=size]")
    .forEach(b => b.disabled = !hInp);
}

function _updateDpadCoords() {
  if (!_activeElem) {
    document.getElementById("dpad-xval").textContent = "—";
    document.getElementById("dpad-yval").textContent = "—";
    return;
  }
  const xv = _activeElem.xInp?.value;
  const yv = _activeElem.yInp?.value;
  // Show actual center coords even when auto (so user knows current position)
  const xDisplay = (xv != null && xv !== "") ? xv : "160";
  let yDisplay;
  if (yv != null && yv !== "") {
    yDisplay = yv;
  } else if (_activeElem.pageName != null && _activeElem.lineIdx != null) {
    const ys = _autoCenters(_activeElem.pageName);
    yDisplay = String(ys[_activeElem.lineIdx] ?? "—");
  } else {
    yDisplay = "—";
  }
  document.getElementById("dpad-xval").textContent = xDisplay;
  document.getElementById("dpad-yval").textContent = yDisplay;
}

// Adjust the font size (H) of the active element by the current step.
function nudgeSize(delta) {
  if (!_activeElem || !_activeElem.hInp) return;
  const inp = _activeElem.hInp;
  const min = parseInt(inp.min) || 6;
  const max = parseInt(inp.max) || 80;
  const cur = parseInt(inp.value) || 14;
  inp.value = Math.max(min, Math.min(max, cur + delta));
  inp.dispatchEvent(new Event("input"));
}

function _initDpad() {
  // Tapping an element row (header or detail) activates it
  document.getElementById("props-content").addEventListener("pointerdown", e => {
    const row = e.target.closest(".elem-row");
    if (row) activateElem(row);
  });

  const dpad = document.getElementById("dpad");

  // Prevent D-pad button clicks from stealing focus on desktop
  dpad.addEventListener("mousedown", e => {
    if (!e.target.matches("input")) e.preventDefault();
  });

  // Direction buttons (X/Y), size buttons (H), and step cycle
  dpad.addEventListener("click", e => {
    const btn = e.target.closest("[data-dir]");
    if (btn) {
      const d = btn.dataset.dir;
      if (d === "size-up")        nudgeSize(_nudgeStep());
      else if (d === "size-down") nudgeSize(-_nudgeStep());
      else                        nudge(d);
    }
    if (e.target.id === "dpad-step") cycleStep();
  });

  // Keyboard-aware: when a docked input is focused, collapse the pad so the
  // on-screen keyboard never covers the field being edited.
  dpad.addEventListener("focusin", e => {
    if (e.target.matches("input[type=number]")) {
      document.body.classList.add("editing-field");
      setTimeout(() => e.target.scrollIntoView({block: "center", behavior: "smooth"}), 50);
    }
  });
  dpad.addEventListener("focusout", e => {
    if (e.target.matches("input[type=number]")) {
      document.body.classList.remove("editing-field");
    }
  });

  // Start with arrows disabled
  document.querySelectorAll(".dpad-btn").forEach(b => b.disabled = true);
}

// ── Boot ───────────────────────────────────────────────────────────────────

async function init() {
  await loadLayout();
  renderSidebar();
  selectPage("clock");
  _initDpad();
  document.getElementById("btn-save").addEventListener("click", saveLayout);
  document.getElementById("btn-play").addEventListener("click", togglePlay);
  document.getElementById("btn-export").addEventListener("click", exportLayout);
  document.getElementById("btn-reset").addEventListener("click", resetLayout);
}

// ── Layout load / save ─────────────────────────────────────────────────────

async function loadLayout() {
  const r = await fetch("/work/layout");
  state.layout = await r.json();
}

async function saveLayout() {
  const btn = document.getElementById("btn-save");
  btn.textContent = "Saving…";
  btn.disabled = true;
  const r = await fetch("/work/layout/save", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(state.layout, null, 2),
  });
  if (r.ok) {
    state.dirty = false;
    btn.textContent = "Saved ✓";
    const dot = document.getElementById("unsaved-dot");
    if (dot) dot.style.display = "none";
    toast("Layout saved ✓");
    loadPreview();
    setTimeout(() => {
      btn.textContent = "Save";
      btn.disabled = !state.dirty;
    }, 2000);
  } else {
    btn.textContent = "Save";
    btn.disabled = false;
    toast("Save failed: " + r.status, "error");
  }
}

let _toastTimer = null;
function toast(msg, kind = "success") {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = msg;
  el.className = "toast toast-" + kind + " show";
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { el.className = "toast"; }, 2600);
}

async function resetLayout() {
  if (!confirm("Reset layout to defaults? This cannot be undone.")) return;
  await fetch("/work/layout/save", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: "{}",
  });
  await loadLayout();
  state.dirty = false;
  document.getElementById("btn-save").disabled = true;
  renderSidebar();
  renderProps();
  loadPreview();
}

function exportLayout() {
  const blob = new Blob([JSON.stringify(state.layout, null, 2)], {type:"application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "work_layout.json";
  a.click();
}

// ── Property change ────────────────────────────────────────────────────────

function setAt(obj, dotPath, value) {
  const parts = dotPath.split(".");
  for (let i = 0; i < parts.length - 1; i++) {
    const key = parts[i];
    if (obj[key] === undefined || obj[key] === null) obj[key] = {};
    // If the current node is an array and the next key is a numeric index,
    // pad with {} so we never create a sparse array (which JSON serialises as null).
    const nextKey = parts[i + 1];
    if (Array.isArray(obj[key]) && /^\d+$/.test(nextKey)) {
      const idx = parseInt(nextKey, 10);
      while (obj[key].length <= idx) obj[key].push({});
    }
    obj = obj[key];
  }
  obj[parts[parts.length - 1]] = value;
}

// Debounced preview refresh — no file save needed, layout is POSTed directly
let _previewTimer = null;
function onPropChange(dotPath, value) {
  setAt(state.layout, dotPath, value);
  markDirty();
  clearTimeout(_previewTimer);
  _previewTimer = setTimeout(loadPreview, 500);
}

function markDirty() {
  if (!state.dirty) {
    state.dirty = true;
    document.getElementById("btn-save").disabled = false;
    const dot = document.getElementById("unsaved-dot");
    if (dot) dot.style.display = "";
  }
}

// ── Sidebar ────────────────────────────────────────────────────────────────

function renderSidebar() {
  const list = document.getElementById("page-list");
  list.innerHTML = "";
  const pages = (state.layout && state.layout.pages) || {};
  for (const name of PAGE_NAMES) {
    const enabled = pages[name] ? pages[name].enabled !== false : true;
    const div = document.createElement("div");
    div.className = "page-tab" + (state.currentPage === name ? " active" : "");
    div.dataset.page = name;
    div.onclick = () => selectPage(name);
    div.innerHTML = `<span class="dot ${enabled ? "enabled" : "disabled"}"></span>${PAGE_LABELS[name]}`;
    list.appendChild(div);
  }
  document.getElementById("tab-global").className =
    "page-tab" + (state.currentPage === "global" ? " active" : "");
}

function selectPage(name) {
  state.currentPage = name;
  state.previewIcon = null;
  renderSidebar();
  renderProps();
  loadPreview();
}

function selectGlobal() {
  state.currentPage = "global";
  renderSidebar();
  renderProps();
  loadPreview();
}

function prevPage() {
  const idx = PAGE_NAMES.indexOf(state.currentPage);
  selectPage(PAGE_NAMES[(idx - 1 + PAGE_NAMES.length) % PAGE_NAMES.length]);
}

function nextPage() {
  const idx = PAGE_NAMES.indexOf(state.currentPage);
  selectPage(PAGE_NAMES[(idx + 1) % PAGE_NAMES.length]);
}

// ── Rendering indicator ────────────────────────────────────────────────────

const _DOTS = ["", ".", "..", "…"];
let _dotsTimer = null;
let _dotsStep  = 0;

function _startProgressBar() {
  _dotsStep = 0;
  _tickDots();
  _dotsTimer = setInterval(_tickDots, 400);
}

function _tickDots() {
  const span = document.getElementById("preview-dots");
  if (span) span.textContent = _DOTS[_dotsStep % _DOTS.length];
  _dotsStep++;
}

function _stopProgressBar() {
  clearInterval(_dotsTimer);
}

// ── Preview ────────────────────────────────────────────────────────────────

function loadPreview() {
  if (!state.layout) return;
  const overlay = document.getElementById("preview-overlay");
  overlay.classList.remove("hidden");
  const status = document.getElementById("preview-status");
  if (status) status.innerHTML = `Rendering<span id="preview-dots"></span>`;
  _startProgressBar();

  let page = state.currentPage === "global" ? "clock" : state.currentPage;
  if (page === "calendar" && state.calendarEmptyView) page = "calendar_empty";
  if (page === "forecast" && state.forecastPreview === "stale") page = "forecast_stale";
  const icon  = state.previewIcon ? `&icon=${state.previewIcon}` : "";
  const url   = `/work/preview/${page}?scale=2${icon}`;
  // The line_positions key the editor edits (forecast variants still use "forecast").
  const posKey = (state.currentPage === "calendar" && state.calendarEmptyView)
    ? "calendar_empty" : state.currentPage;

  // POST the current in-memory layout — server renders from body, no file needed
  fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(state.layout),
  })
  .then(r => {
    if (!r.ok) return r.text().then(t => { throw new Error(`${r.status}: ${t}`); });
    const hdr = r.headers.get("X-Line-Centers");
    if (hdr) { try { state.lineCenters[posKey] = JSON.parse(hdr); } catch (e) {} }
    return r.blob();
  })
  .then(blob => {
    _stopProgressBar();
    const img = document.getElementById("preview-img");
    const old = img.src;
    img.src = URL.createObjectURL(blob);
    if (old.startsWith("blob:")) URL.revokeObjectURL(old);
    overlay.classList.add("hidden");
  })
  .catch(err => {
    _stopProgressBar();
    const status = document.getElementById("preview-status");
    if (status) status.textContent = "Error: " + err.message;
  });

  const indicator = document.getElementById("page-indicator");
  if (state.currentPage === "global") {
    indicator.textContent = "Global Settings";
  } else {
    const idx = PAGE_NAMES.indexOf(state.currentPage);
    indicator.textContent = `Page ${idx + 1} / ${PAGE_NAMES.length}`;
  }
}

// ── Auto-play ──────────────────────────────────────────────────────────────

function togglePlay() {
  state.playing = !state.playing;
  const btn = document.getElementById("btn-play");
  if (state.playing) {
    btn.textContent = "⏹ Stop";
    playStep();
  } else {
    btn.textContent = "▶ Play";
    clearTimeout(state.playTimer);
  }
}

function playStep() {
  if (!state.playing) return;
  const idx = PAGE_NAMES.indexOf(state.currentPage);
  const next = PAGE_NAMES[(idx + 1) % PAGE_NAMES.length];
  selectPage(next);
  const dwell = (state.layout?.pages?.[next]?.dwell_seconds || 8) * 1000;
  state.playTimer = setTimeout(playStep, dwell);
}

// ── Properties panel ───────────────────────────────────────────────────────

function renderProps() {
  // Pull any relocated detail back before the list is rebuilt, then reset the
  // dock to its idle state (nothing selected on the new page).
  _restoreMovedDetail();
  _activeElem = null;
  document.body.classList.remove("editing-field");
  const slot = document.getElementById("dpad-active");
  if (slot) slot.innerHTML = "";
  const dlabel = document.getElementById("dpad-label");
  if (dlabel) dlabel.textContent = "tap an element to position it";
  document.querySelectorAll(".dpad-btn").forEach(b => b.disabled = true);
  _updateDpadCoords();

  const panel = document.getElementById("props-content");
  panel.innerHTML = "";
  if (state.currentPage === "global") {
    panel.appendChild(buildGlobalProps());
  } else {
    panel.appendChild(buildPageProps(state.currentPage));
  }
}

function buildGlobalProps() {
  const frag = document.createDocumentFragment();
  const L = state.layout;

  frag.appendChild(section("Content", [
    numRow("Left margin",  "content.left_margin",  L.content.left_margin,  0, 40),
    numRow("Right margin", "content.right_margin", L.content.right_margin, 0, 40),
  ]));

  // Typeface is a system setting (it needs an on-disk .ttf with existence
  // checking + fallbacks), so it lives in Setup → Hardware → Font, not here.
  // A font picker in the editor had no effect: load_layout() always overrides
  // layout.font.path with the config-resolved font.

  return frag;
}

// Human-readable names for each line on each page
const PAGE_LINE_LABELS = {
  clock:          ["Time",          "Day name",    "Date"],
  forecast:       ["Temperature",   "Description", "Hi / Lo", "Rain chance", "Humidity", "Wind", "Later today"],
  calendar:       ["Meeting title", "Countdown",   "Time range", "Location", "Next event"],
  calendar_empty: ["No upcoming (line 1)", "No upcoming (line 2)",
                   "Next event title", "Next event time",
                   "Then event title", "Then event time"],
  commute:        ["Route 1 label", "Route 1 time","Route 1 via",
                   "Route 2 label", "Route 2 time","Route 2 via"],
  wfh:            ["Status text"],
  ooo:            ["Status text",   "Return date"],
  holiday:        ["Event name"],
};

function buildPageProps(name) {
  const frag = document.createDocumentFragment();
  const L  = state.layout;
  const pg = (L.pages && L.pages[name]) || {};

  // Forecast: preview state selector (normal / stale)
  if (name === "forecast") {
    const sec = document.createElement("div");
    sec.className = "prop-section";
    const row = document.createElement("div");
    row.className = "prop-row";
    row.innerHTML = `<span class="prop-label">Preview state</span>`;
    const sel = document.createElement("select");
    [["normal","Normal"], ["stale","Stale data"]].forEach(([v, lbl]) => {
      const opt = document.createElement("option");
      opt.value = v; opt.textContent = lbl;
      if (state.forecastPreview === v) opt.selected = true;
      sel.appendChild(opt);
    });
    sel.addEventListener("change", () => {
      state.forecastPreview = sel.value;
      loadPreview();
    });
    row.appendChild(sel);
    sec.appendChild(row);
    frag.appendChild(sec);
  }

  // Calendar: toggle between normal and empty-state element views
  if (name === "calendar") {
    const toggleSec = document.createElement("div");
    toggleSec.className = "prop-section";
    const toggleR = document.createElement("div");
    toggleR.className = "prop-row";
    toggleR.innerHTML = `<span class="prop-label">No upcoming events view</span>`;
    const chk = document.createElement("input");
    chk.type = "checkbox";
    chk.checked = state.calendarEmptyView;
    chk.addEventListener("change", () => {
      state.calendarEmptyView = chk.checked;
      renderProps();
      loadPreview();
    });
    toggleR.appendChild(chk);
    toggleSec.appendChild(toggleR);
    frag.appendChild(toggleSec);
  }

  const effectiveName = (name === "calendar" && state.calendarEmptyView) ? "calendar_empty" : name;
  const labels    = PAGE_LINE_LABELS[effectiveName] || [];
  const positions = (L.line_positions && L.line_positions[effectiveName]) || [];
  if (labels.length) {
    const sec = document.createElement("div");
    sec.className = "prop-section";
    sec.innerHTML = `<div class="prop-section-title">Elements — tap to select, D-pad moves X/Y</div>`;
    labels.forEach((lbl, i) => sec.appendChild(lineElemRow(effectiveName, i, lbl, positions)));
    if (name === "forecast") {
      sec.appendChild(elemRow(
        "Weather Icon",
        "icon.radius", "icon.x", "icon.y",
        L.icon.radius, L.icon.x, L.icon.y, 5, 60
      ));
      sec.appendChild(elemRow(
        "AQI Overlay",
        "aqi.value_size", "aqi.cx", "aqi.y",
        L.aqi.value_size, L.aqi.cx, L.aqi.y, 6, 40
      ));
    }
    frag.appendChild(sec);
  }

  if (name === "forecast") frag.appendChild(buildForecastExtra());

  // Page Settings below elements
  const settingsRows = [toggleRow("Enabled", `pages.${name}.enabled`, pg.enabled !== false)];

  if (CALENDAR_DRIVEN_PAGES.includes(name)) {
    const dwellRow = document.createElement("div");
    dwellRow.className = "prop-row";
    dwellRow.innerHTML = `<span class="prop-label">Dwell</span><span style="opacity:0.38;font-size:20px;line-height:1">∞</span>`;
    settingsRows.push(dwellRow);
    const note = document.createElement("p");
    note.className = "prop-note";
    note.textContent = "Shown while a matching calendar event is active — dwell doesn't apply.";
    settingsRows.push(note);
  } else {
    settingsRows.push(numRow("Dwell (s)", `pages.${name}.dwell_seconds`, pg.dwell_seconds || 8, 1, 60));
    if (MARQUEE_PAGES.includes(name)) {
      const note = document.createElement("p");
      note.className = "prop-note";
      note.textContent = "If scrolling text hasn’t finished a full pass, dwell extends up to 3×.";
      settingsRows.push(note);
    }
  }

  frag.appendChild(section("Page Settings", settingsRows));

  return frag;
}

// Generic element card: label, H (size), X, Y — paths are explicit
function elemRow(label, hPath, xPath, yPath, hVal, xVal, yVal, hMin=6, hMax=80, visPath=null, visVal=true) {
  const row = document.createElement("div");
  row.className = "elem-row";

  const hdr = document.createElement("div");
  hdr.className = "elem-header";
  hdr.textContent = label;
  row.appendChild(hdr);

  const detail = document.createElement("div");
  detail.className = "elem-detail";

  function inp(cls, val, min, max, path, nullable) {
    const lbl = document.createElement("label");
    lbl.textContent = cls.toUpperCase();
    const el = document.createElement("input");
    el.type = "number"; el.min = min; el.max = max;
    el.className = "elem-" + cls;
    el.inputMode = "numeric";
    if (nullable) el.placeholder = "auto";
    el.value = (val != null && val !== "") ? val : "";
    el.addEventListener("focus", () => setTimeout(() => el.select(), 0));
    el.addEventListener("input",  () => {
      const v = el.value.trim();
      onPropChange(path, nullable && v === "" ? null : Number(v));
      if (cls === "x" || cls === "y") _updateDpadCoords();
    });
    el.addEventListener("change", () => {
      const v = el.value.trim();
      onPropChange(path, nullable && v === "" ? null : Number(v));
      if (cls === "x" || cls === "y") _updateDpadCoords();
    });
    detail.appendChild(lbl);
    detail.appendChild(el);
    return el;
  }

  inp("h", hVal, hMin, hMax,  hPath, false);
  if (xPath) inp("x", xVal, 0, 319, xPath, true);
  if (yPath) inp("y", yVal, 0, 239, yPath, true);

  if (visPath != null) {
    const visLbl = document.createElement("label");
    visLbl.textContent = "SHOW";
    const visChk = document.createElement("input");
    visChk.type = "checkbox";
    visChk.className = "elem-vis";
    visChk.checked = visVal !== false;
    visChk.addEventListener("change", () => onPropChange(visPath, visChk.checked));
    detail.appendChild(visLbl);
    detail.appendChild(visChk);
  }

  row.appendChild(detail);
  return row;
}

// Convenience wrapper for line_positions entries — sets data attrs for D-pad lock logic
function lineElemRow(pageName, idx, label, positions) {
  const pos  = (positions && positions[idx]) || {};
  const base = `line_positions.${pageName}.${idx}`;
  const row  = elemRow(label, `${base}.h`, `${base}.x`, `${base}.y`, pos.h ?? 14, pos.x, pos.y,
                       6, 80, `${base}.visible`, pos.visible !== false);
  row.dataset.pageName = pageName;
  row.dataset.lineIdx  = idx;
  return row;
}

function buildForecastExtra() {
  const L    = state.layout;
  const frag = document.createDocumentFragment();

  // Weather icon cycling
  const iconSec = document.createElement("div");
  iconSec.className = "prop-section";
  iconSec.innerHTML = `<div class="prop-section-title">Weather Icon (preview only)</div>`;
  const iconRowEl = document.createElement("div");
  iconRowEl.className = "icon-row";
  for (const ic of WEATHER_ICONS) {
    const btn = document.createElement("button");
    btn.className = "icon-btn" + (state.previewIcon === ic ? " selected" : "");
    btn.title = ic;
    const img = document.createElement("img");
    img.src = `/work/icon/${ic}`;
    img.width = 36; img.height = 36;
    img.style.display = "block";
    btn.appendChild(img);
    btn.onclick = () => {
      state.previewIcon = (state.previewIcon === ic) ? null : ic;
      loadPreview();
      renderProps();
    };
    iconRowEl.appendChild(btn);
  }
  iconSec.appendChild(iconRowEl);
  frag.appendChild(iconSec);

  frag.appendChild(section("Icon", [
    numRow("Gap from text", "icon.gap", L.icon.gap, 0, 24),
  ]));

  frag.appendChild(section("AQI Label size", [
    numRow("Label pt", "aqi.label_size", L.aqi.label_size, 6, 40),
  ]));

  frag.appendChild(section("Hourly Grid", [
    numRow("Grid height",   "grid.height",     L.grid.height,    40, 120),
    gridColsRow("Columns",  "grid.columns",    L.grid.columns),
    numRow("Label size pt", "grid.label_size", L.grid.label_size, 6, 24),
    numRow("Temp size pt",  "grid.temp_size",  L.grid.temp_size,  6, 24),
    numRow("Rain % size pt","grid.rain_size",  L.grid.rain_size,  6, 24),
  ]));

  return frag;
}

// ── Section / row builders ─────────────────────────────────────────────────

function section(title, rows) {
  const div = document.createElement("div");
  div.className = "prop-section";
  div.innerHTML = `<div class="prop-section-title">${title}</div>`;
  for (const r of rows) div.appendChild(r);
  return div;
}

function numRow(label, path, value, min, max) {
  const row = document.createElement("div");
  row.className = "prop-row";
  const inp = document.createElement("input");
  inp.type = "number"; inp.min = min; inp.max = max; inp.value = value;
  inp.inputMode = "numeric";
  inp.addEventListener("focus", () => setTimeout(() => inp.select(), 0));
  inp.addEventListener("input",  () => onPropChange(path, Number(inp.value)));
  inp.addEventListener("change", () => onPropChange(path, Number(inp.value)));
  row.innerHTML = `<span class="prop-label">${label}</span>`;
  row.appendChild(inp);
  return row;
}

function toggleRow(label, path, value) {
  const row = document.createElement("div");
  row.className = "prop-row";
  const inp = document.createElement("input");
  inp.type = "checkbox"; inp.checked = value;
  inp.addEventListener("change", () => {
    onPropChange(path, inp.checked);
    renderSidebar(); // update dot color
  });
  row.innerHTML = `<span class="prop-label">${label}</span>`;
  row.appendChild(inp);
  return row;
}

function gridColsRow(label, path, value) {
  const row = document.createElement("div");
  row.className = "prop-row";
  const sel = document.createElement("select");
  for (const n of [3, 4, 5]) {
    const opt = document.createElement("option");
    opt.value = n; opt.textContent = n;
    if (n === value) opt.selected = true;
    sel.appendChild(opt);
  }
  sel.addEventListener("change", () => onPropChange(path, Number(sel.value)));
  row.innerHTML = `<span class="prop-label">${label}</span>`;
  row.appendChild(sel);
  return row;
}

// ── Start ──────────────────────────────────────────────────────────────────

window.addEventListener("DOMContentLoaded", init);
