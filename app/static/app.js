"use strict";

const $ = sel => document.querySelector(sel);
const $$ = sel => document.querySelectorAll(sel);
const fmt = n => "$" + (Number(n || 0)).toFixed(2);

let CATEGORIES = [];
let DONORS = [];
let SELECTED = new Set();
const LS_DEFAULT_DONOR = "dt.defaultDonorId";  // per-device, in localStorage
let CURRENT_EVENT = null;
const RECENT = new Map();   // id -> item, in insertion order
const PENDING = new Set();  // ids we are still polling
let POLL_TIMER = null;

// ---------- nav ----------
function showView(name) {
  $$(".view").forEach(v => v.classList.add("hidden"));
  const target = $(`#view-${name}`);
  if (!target) return;
  target.classList.remove("hidden");
  $$(".tab").forEach(t => t.classList.toggle("active", t.dataset.view === name));
  try { localStorage.setItem("dt.activeTab", name); } catch {}
  if (location.hash !== "#" + name) {
    history.replaceState(null, "", "#" + name);
  }
  if (name === "items") loadItems();
  if (name === "events") loadEvents();
  if (name === "donors") loadDonors();
}
$$(".tab[data-view]").forEach(t => t.addEventListener("click", () => showView(t.dataset.view)));
window.addEventListener("hashchange", () => {
  const n = location.hash.replace(/^#/, "");
  if ($(`#view-${n}`)) showView(n);
});

// ---------- categories ----------
async function loadCategories() {
  const r = await fetch("/api/categories");
  CATEGORIES = await r.json();
  const sel = $("#itemForm select[name=category_key]");
  sel.innerHTML = CATEGORIES
    .map(c => `<option value="${c.key}">${c.label}</option>`).join("");
}

// ---------- donors ----------
function getDefaultDonorId() {
  try { return localStorage.getItem(LS_DEFAULT_DONOR) || ""; } catch { return ""; }
}
function setDefaultDonorId(v) {
  try { v ? localStorage.setItem(LS_DEFAULT_DONOR, v) : localStorage.removeItem(LS_DEFAULT_DONOR); } catch {}
}

async function loadDonorsAndSettings() {
  const r = await fetch("/api/donors");
  DONORS = await r.json();
  populateDonorSelectors();
}

function populateDonorSelectors() {
  const opts = `<option value="">— none —</option>` +
    DONORS.map(d => `<option value="${d.id}">${escapeHTML(d.name)}</option>`).join("");
  for (const sel of [$("#defaultDonor"), $("#itemForm select[name=donor_id]")]) {
    if (!sel) continue;
    const cur = sel.value;
    sel.innerHTML = opts;
    sel.value = cur;
  }
  // Apply per-device default to the capture dropdown, but only if that donor still exists.
  const def = getDefaultDonorId();
  if (def && DONORS.some(d => String(d.id) === String(def))) {
    $("#defaultDonor").value = def;
  } else if (def) {
    setDefaultDonorId("");  // donor was deleted; clear stale choice
  }
}

$("#defaultDonor").addEventListener("change", () => {
  setDefaultDonorId($("#defaultDonor").value);
});

async function loadDonors() {
  await loadDonorsAndSettings();
  const list = $("#donorList");
  list.innerHTML = DONORS.map(d => `
    <div class="donor-item" data-id="${d.id}">
      <div>
        <div class="name">${escapeHTML(d.name)}</div>
        <div class="stats">${d.item_count} items · ${fmt(d.total_value)}</div>
      </div>
      <div style="display:flex;gap:.4rem">
        <button class="rename">Rename</button>
        <button class="danger del">Delete</button>
      </div>
    </div>`).join("") ||
    `<p class="hint">No donors yet. Add one above and pick it as the default on the Add tab.</p>`;
  list.querySelectorAll(".donor-item").forEach(row => {
    const id = Number(row.dataset.id);
    row.querySelector(".rename").addEventListener("click", async () => {
      const cur = DONORS.find(x => x.id === id);
      const name = prompt("New name", cur ? cur.name : "");
      if (!name) return;
      const fd = new FormData(); fd.append("name", name);
      await fetch(`/api/donors/${id}`, { method: "PATCH", body: fd });
      loadDonors();
    });
    row.querySelector(".del").addEventListener("click", async () => {
      if (!confirm("Delete this donor? Items keep their record but lose the tag.")) return;
      await fetch(`/api/donors/${id}`, { method: "DELETE" });
      loadDonors();
    });
  });
}

$("#donorForm").addEventListener("submit", async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const r = await fetch("/api/donors", { method: "POST", body: fd });
  if (!r.ok) { alert("Failed: " + await r.text()); return; }
  e.target.reset();
  loadDonors();
});

// ---------- capture (multi-file, async) ----------
$("#captureInput").addEventListener("change", async e => {
  const files = Array.from(e.target.files || []);
  if (!files.length) return;
  const status = $("#captureStatus");
  status.classList.remove("hidden", "error");
  status.textContent = files.length === 1
    ? "Uploading…"
    : `Uploading ${files.length} photos…`;
  const donorId = $("#defaultDonor").value;

  // Fire all uploads in parallel; each returns immediately with status='analyzing'
  let okCount = 0, failCount = 0;
  await Promise.all(files.map(async file => {
    const fd = new FormData();
    fd.append("image", file);
    if (donorId) fd.append("donor_id", donorId);
    try {
      const r = await fetch("/api/items", { method: "POST", body: fd });
      if (!r.ok) throw new Error(await r.text());
      const item = await r.json();
      RECENT.set(item.id, item);
      if (item.status !== "ready") PENDING.add(item.id);
      okCount++;
      renderRecent();
    } catch (err) {
      failCount++;
      console.error(err);
    }
  }));

  status.textContent = failCount
    ? `Uploaded ${okCount}, ${failCount} failed. Vision is still running…`
    : `Uploaded ${okCount}. Vision is filling in details…`;
  e.target.value = "";
  startPolling();
});

function renderRecent() {
  const list = $("#recentList");
  const heading = $("#recentHeading");
  if (!RECENT.size) {
    list.innerHTML = "";
    heading.classList.add("hidden");
    return;
  }
  heading.classList.remove("hidden");
  // Show newest first, cap at 12 to stay tidy
  const items = [...RECENT.values()].reverse().slice(0, 12);
  list.innerHTML = items.map(renderCard).join("");
  list.querySelectorAll(".card").forEach(c => {
    c.addEventListener("click", () => {
      const id = Number(c.dataset.id);
      const it = RECENT.get(id);
      if (it && it.status === "failed") {
        fetch(`/api/items/${id}/reanalyze`, { method: "POST" }).then(() => {
          PENDING.add(id);
          it.status = "analyzing";
          renderRecent();
          startPolling();
        });
      } else {
        openItem(id);
      }
    });
  });
}

function renderCard(it) {
  const ready = it.status === "ready" || !it.status;
  const subtotal = (it.estimated_value || 0) * (it.quantity || 1);
  const cls = "card" + (it.status === "analyzing" ? " analyzing"
                       : it.status === "failed" ? " failed" : "");
  const desc = ready ? (it.description || it.category_label || "Item")
                     : (it.status === "failed" ? "Tap to retry" : "");
  const val = ready ? `<div class="val">${fmt(subtotal)}${it.quantity > 1 ? ` <span class="qty">(×${it.quantity})</span>` : ""}</div>` : "";
  const date = it.created_at ? `<div class="date">${escapeHTML(formatDate(it.created_at))}</div>` : "";
  const donor = it.donor_name ? `<div class="donor-tag">👤 ${escapeHTML(it.donor_name)}</div>` : "";
  return `
    <div class="${cls}" data-id="${it.id}">
      <img src="${it.image_url}" alt="">
      <div class="meta">
        <div class="desc">${escapeHTML(desc)}</div>
        ${val}
        ${donor}
        ${date}
      </div>
    </div>`;
}

function formatDate(s) {
  // "YYYY-MM-DD HH:MM:SS" (UTC from sqlite datetime('now')) -> local short form
  if (!s) return "";
  const d = new Date(s.replace(" ", "T") + "Z");
  if (isNaN(d)) return s;
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  const y = new Date(today); y.setDate(y.getDate() - 1);
  const yesterday = d.toDateString() === y.toDateString();
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (sameDay) return `Today ${time}`;
  if (yesterday) return `Yesterday ${time}`;
  return d.toLocaleDateString([], { month: "short", day: "numeric", year: d.getFullYear() === today.getFullYear() ? undefined : "2-digit" });
}

// ---------- polling for analyzing items ----------
function startPolling() {
  if (POLL_TIMER || !PENDING.size) return;
  POLL_TIMER = setInterval(pollPending, 1500);
}
async function pollPending() {
  if (!PENDING.size) {
    clearInterval(POLL_TIMER);
    POLL_TIMER = null;
    return;
  }
  const ids = [...PENDING];
  await Promise.all(ids.map(async id => {
    try {
      const r = await fetch(`/api/items/${id}`);
      if (!r.ok) return;
      const it = await r.json();
      RECENT.set(id, it);
      if (it.status !== "analyzing") PENDING.delete(id);
    } catch {}
  }));
  renderRecent();
  // Also refresh items view if visible
  if (!$("#view-items").classList.contains("hidden")) loadItems();
}

// ---------- items list ----------
async function loadItems() {
  const onlyUn = $("#onlyUnassigned").checked;
  const r = await fetch("/api/items" + (onlyUn ? "?unassigned=true" : ""));
  const items = await r.json();
  const total = items.reduce((s, i) =>
    s + (i.status === "ready" ? (i.estimated_value || 0) * (i.quantity || 1) : 0), 0);
  const pending = items.filter(i => i.status === "analyzing").length;
  $("#itemsTotal").textContent =
    `${items.length} items · ${fmt(total)}${pending ? ` · ${pending} analyzing` : ""}`;
  const list = $("#itemList");
  list.innerHTML = items.map(renderCard).join("") ||
    `<p style="color:var(--muted)">No items yet. Tap <b>Add</b> to take a photo.</p>`;
  list.querySelectorAll(".card").forEach(c => {
    c.addEventListener("click", () => openItem(Number(c.dataset.id)));
  });
  // If there are still-analyzing items, keep polling even if we navigated here directly
  items.forEach(i => { if (i.status === "analyzing") PENDING.add(i.id); RECENT.has(i.id) || RECENT.set(i.id, i); });
  if (pending) startPolling();
}
$("#onlyUnassigned").addEventListener("change", loadItems);

// ---------- item edit modal ----------
let CURRENT_ITEM = null;

async function openItem(id) {
  const r = await fetch(`/api/items/${id}`);
  const it = await r.json();
  // If we have no FMV data yet (still analyzing), seed with the median for the
  // default category so the form is internally consistent and the calc box renders.
  if (it.fmv_median == null) {
    const def = CATEGORIES.find(c => c.key === (it.category_key || "other_misc"))
             || CATEGORIES.find(c => c.key === "other_misc");
    if (def) {
      it.category_key = it.category_key || def.key;
      it.category_label = it.category_label || def.label;
      it.fmv_low = def.low ?? 1;
      it.fmv_median = def.median ?? 5;
      it.fmv_high = def.high ?? 25;
      it.estimated_value = it.estimated_value ?? def.median;
      it.condition = it.condition || "good";
    }
  }
  CURRENT_ITEM = it;
  const f = $("#itemForm");
  f.elements.id.value = it.id;
  f.elements.description.value = it.description || "";
  f.elements.category_key.value = it.category_key || "other_misc";
  f.elements.condition.value = it.condition || "good";
  f.elements.quantity.value = it.quantity || 1;
  f.elements.estimated_value.value = it.estimated_value ?? "";
  f.elements.notes.value = it.notes || "";
  f.elements.donor_id.value = it.donor_id || "";
  $("#modalImg").src = it.image_url;
  $("#modalDate").textContent = it.created_at ? `Photo uploaded ${formatDate(it.created_at)}` : "";
  $("#analyzingBanner").classList.toggle("hidden", it.status !== "analyzing");
  updateCalcBox();
  updateFmvHint();
  $("#itemModal").classList.remove("hidden");
}

function updateCalcBox() {
  const f = $("#itemForm");
  const key = f.elements.category_key.value;
  const cond = f.elements.condition.value;
  const qty = Math.max(1, Number(f.elements.quantity.value) || 1);
  const userVal = parseFloat(f.elements.estimated_value.value);
  const it = CURRENT_ITEM || {};
  const cat = CATEGORIES.find(x => x.key === key);
  const low = it.fmv_low, med = it.fmv_median, high = it.fmv_high;

  // Map condition -> which bucket the table uses
  const condBucket = ({excellent:"high", like_new:"high", new:"high",
                       good:"median",
                       fair:"low", poor:"low", worn:"low"})[cond] || "median";
  const tableValue = condBucket === "high" ? high : condBucket === "low" ? low : med;
  const usingOverride = !isNaN(userVal) && tableValue != null && Math.abs(userVal - tableValue) > 0.001;
  const perUnit = !isNaN(userVal) ? userVal : (tableValue ?? 0);
  const subtotal = perUnit * qty;

  const range = (low != null && med != null && high != null)
    ? `<div class="row2"><span>FMV range for <b>${escapeHTML(cat ? cat.label : "this category")}</b>:</span>
         <span>${fmt(low)} &middot; <b>${fmt(med)}</b> &middot; ${fmt(high)}</span></div>
       <div class="row2"><span>(low &middot; <b>good (default)</b> &middot; like-new)</span><span></span></div>`
    : `<div class="row2"><span>FMV range:</span><span>not yet set (still analyzing)</span></div>`;

  const condLine = `<div class="row2"><span>Condition <span class="pick">${escapeHTML(cond)}</span> &rarr; uses the <b>${condBucket}</b> bucket</span><span>${tableValue != null ? fmt(tableValue) : "—"}</span></div>`;

  const overrideLine = usingOverride
    ? `<div class="row2"><span>You overrode the per-unit value</span><span><b>${fmt(userVal)}</b></span></div>`
    : "";

  const formula = `<div class="formula">${fmt(perUnit)} &times; ${qty} qty = <b>${fmt(subtotal)}</b></div>`;
  const src = `<div class="src">Source: bundled valuation table from the Salvation Army &amp; Goodwill guides per <a href="/sources" target="_blank">IRS Pub. 561</a>. ${it.category_key === "other_misc" ? "(Falling back to <i>Miscellaneous</i> — pick a more specific category to improve the estimate.)" : ""}</div>`;

  $("#modalCalc").innerHTML = range + condLine + overrideLine + formula + src;
}

function updateFmvHint() {
  const key = $("#itemForm").elements.category_key.value;
  const c = CATEGORIES.find(x => x.key === key);
  $("#modalFmvHint").textContent = c
    ? `Changing the category resets the per-unit value to the default for ${c.label}.`
    : "";
}
["change","input"].forEach(ev => {
  $("#itemForm").addEventListener(ev, e => {
    if (["category_key","condition","quantity","estimated_value"].includes(e.target.name)) {
      updateCalcBox();
    }
    if (e.target.name === "category_key") updateFmvHint();
  });
});

$("#itemForm").addEventListener("submit", async e => {
  e.preventDefault();
  const f = e.target;
  const id = f.elements.id.value;
  const fd = new FormData();
  for (const k of ["description", "category_key", "condition", "quantity", "estimated_value", "notes", "donor_id"]) {
    fd.append(k, f.elements[k].value);
  }
  const r = await fetch(`/api/items/${id}`, { method: "PATCH", body: fd });
  if (!r.ok) { alert("Save failed: " + await r.text()); return; }
  const updated = await r.json();
  RECENT.set(updated.id, updated);
  renderRecent();
  closeModals();
  loadItems();
});

$("#deleteItem").addEventListener("click", async () => {
  const id = Number($("#itemForm").elements.id.value);
  if (!confirm("Delete this item?")) return;
  await fetch(`/api/items/${id}`, { method: "DELETE" });
  RECENT.delete(id);
  PENDING.delete(id);
  renderRecent();
  closeModals();
  loadItems();
});

// ---------- events ----------
$("#eventForm").addEventListener("submit", async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  if (!fd.get("donation_date")) fd.set("donation_date", new Date().toISOString().slice(0, 10));
  const r = await fetch("/api/events", { method: "POST", body: fd });
  if (!r.ok) { alert(await r.text()); return; }
  e.target.reset();
  e.target.closest("details").open = false;
  loadEvents();
});

async function loadEvents() {
  const r = await fetch("/api/events");
  const evs = await r.json();
  const list = $("#eventList");
  list.innerHTML = evs.map(ev => `
    <div class="event-row" data-id="${ev.id}">
      <div>
        <div class="who">${escapeHTML(ev.charity_name)}</div>
        <div class="when">${escapeHTML(ev.donation_date)} · ${ev.item_count} items</div>
      </div>
      <div class="amt">${fmt(ev.total_value)}</div>
    </div>`).join("") ||
    `<p style="color:var(--muted)">No drop-offs yet.</p>`;
  list.querySelectorAll(".event-row").forEach(row => {
    row.addEventListener("click", () => openEvent(Number(row.dataset.id)));
  });
}

async function openEvent(id) {
  CURRENT_EVENT = id;
  SELECTED.clear();
  const [evRes, unRes] = await Promise.all([
    fetch(`/api/events/${id}`),
    fetch(`/api/items?unassigned=true`),
  ]);
  const ev = await evRes.json();
  const unassigned = await unRes.json();
  $("#evTitle").textContent = `${ev.charity_name}`;
  $("#evMeta").textContent = `${ev.donation_date}${ev.charity_address ? " · " + ev.charity_address : ""}`;
  $("#evTotal").textContent = `${ev.item_count} · ${fmt(ev.total_value)}`;
  $("#evItems").innerHTML = ev.items.map(renderCard).join("") ||
    `<p style="color:var(--muted)">No items in this drop-off yet.</p>`;
  $("#evItems").querySelectorAll(".card").forEach(c => {
    c.addEventListener("click", async () => {
      if (!confirm("Remove this item from the drop-off?")) return;
      const fd = new FormData(); fd.append("item_ids", c.dataset.id);
      await fetch(`/api/events/${id}/unassign`, { method: "POST", body: fd });
      openEvent(id);
    });
  });
  const un = $("#evUnassigned");
  un.innerHTML = unassigned.map(renderCard).join("") ||
    `<p style="color:var(--muted)">Nothing unassigned.</p>`;
  un.querySelectorAll(".card").forEach(c => {
    c.addEventListener("click", () => {
      const id2 = c.dataset.id;
      if (SELECTED.has(id2)) { SELECTED.delete(id2); c.classList.remove("selected"); }
      else { SELECTED.add(id2); c.classList.add("selected"); }
    });
  });
  $("#receiptLink").href = `/receipt/${id}`;
  $("#eventModal").classList.remove("hidden");
}

$("#donateAllBtn").addEventListener("click", async () => {
  const r = await fetch(`/api/events/${CURRENT_EVENT}/assign_all_unassigned`, { method: "POST" });
  if (!r.ok) { alert("Failed: " + await r.text()); return; }
  const result = await r.json();
  openEvent(CURRENT_EVENT);
  if (result.moved === 0) alert("Nothing to add — there are no unassigned items.");
});
$("#addSelectedBtn").addEventListener("click", async () => {
  if (!SELECTED.size) return;
  const fd = new FormData();
  fd.append("item_ids", [...SELECTED].join(","));
  await fetch(`/api/events/${CURRENT_EVENT}/assign`, { method: "POST", body: fd });
  openEvent(CURRENT_EVENT);
});
$("#deleteEventBtn").addEventListener("click", async () => {
  if (!confirm("Delete this drop-off? Items will be unassigned, not deleted.")) return;
  await fetch(`/api/events/${CURRENT_EVENT}`, { method: "DELETE" });
  closeModals();
  loadEvents();
});

// ---------- modal close ----------
function closeModals() {
  $$(".modal").forEach(m => m.classList.add("hidden"));
}
$$("[data-close]").forEach(b => b.addEventListener("click", closeModals));
$$(".modal").forEach(m => m.addEventListener("click", e => {
  if (e.target === m) closeModals();
}));

// ---------- utils ----------
function escapeHTML(s) {
  return String(s || "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

// ---------- boot ----------
loadCategories();
loadDonorsAndSettings();
let _saved = location.hash.replace(/^#/, "");
if (!$(`#view-${_saved}`)) {
  try { _saved = localStorage.getItem("dt.activeTab") || ""; } catch {}
}
if (!$(`#view-${_saved}`)) _saved = "capture";
showView(_saved);
