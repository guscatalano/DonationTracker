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
  if (name === "events") { loadEvents(); loadCash(); }
  if (name === "donors") loadDonors();
}

// ---------- Add-tab mode toggle: Item vs Cash ----------
function setAddMode(mode) {
  if (mode !== "item" && mode !== "cash") mode = "item";
  $$(".seg-toggle .seg").forEach(x => x.classList.toggle("active", x.dataset.mode === mode));
  $("#addModeItem").classList.toggle("hidden", mode !== "item");
  $("#addModeCash").classList.toggle("hidden", mode !== "cash");
  try { localStorage.setItem("dt.addMode", mode); } catch {}
}
// Event delegation: catches the click whether the user hit the button or its emoji/text child.
$(".seg-toggle").addEventListener("click", e => {
  const b = e.target.closest(".seg");
  if (b) setAddMode(b.dataset.mode);
});
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
  for (const sel of [$("#defaultDonor"),
                     $("#itemForm select[name=donor_id]"),
                     $("#cashEditForm select[name=donor_id]")]) {
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
  list.innerHTML = DONORS.map(d => {
    const parts = [];
    if (d.item_count) parts.push(`${d.item_count} item${d.item_count===1?"":"s"} · ${fmt(d.items_value)}`);
    if (d.cash_count) parts.push(`${d.cash_count} cash gift${d.cash_count===1?"":"s"} · ${fmt(d.cash_value)}`);
    const breakdown = parts.length ? parts.join(" • ") : "No donations yet";
    return `
    <div class="donor-item" data-id="${d.id}">
      <div>
        <div class="name">${escapeHTML(d.name)}</div>
        <div class="stats">${breakdown}</div>
        ${parts.length > 1 ? `<div class="stats" style="margin-top:.15rem"><b style="color:var(--accent-2)">Total: ${fmt(d.total_value)}</b></div>` : ""}
      </div>
      <div style="display:flex;gap:.4rem">
        <button class="rename">Rename</button>
        <button class="danger del">Delete</button>
      </div>
    </div>`;
  }).join("") ||
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

// ---------- cash donations ----------
async function loadCash() {
  const r = await fetch("/api/cash");
  const list = await r.json();
  const total = list.reduce((s, c) => s + (c.amount || 0), 0);
  $("#cashTotal").textContent = `${list.length} donations · ${fmt(total)} total`;
  const el = $("#cashList");
  el.innerHTML = list.map(c => {
    const tags = [
      `<span class="tag">${escapeHTML(c.donation_date)}</span>`,
      `<span class="tag">${escapeHTML(c.payment_method || "—")}</span>`,
      c.donor_name ? `<span class="donor">👤 ${escapeHTML(c.donor_name)}</span>` : "",
      c.notes ? `<span title="${escapeHTML(c.notes)}">📝 ${escapeHTML(c.notes.length > 40 ? c.notes.slice(0,40)+"…" : c.notes)}</span>` : "",
    ].filter(Boolean).join("");
    const actions = [
      `<button type="button" class="edit" data-id="${c.id}">Edit</button>`,
      c.receipt_url ? `<a href="${escapeHTML(c.receipt_url)}" target="_blank" rel="noopener">📎 View receipt</a>` : "",
      `<button type="button" class="del" data-id="${c.id}">Delete</button>`,
    ].filter(Boolean).join("");
    return `
      <div class="cash-row">
        <div class="who">${escapeHTML(c.charity_name)}</div>
        <div class="amt">${fmt(c.amount)}</div>
        <div class="meta-line">${tags}</div>
        <div class="actions">${actions}</div>
      </div>`;
  }).join("") ||
    `<p class="hint">No cash donations yet.</p>`;
  el.querySelectorAll(".cash-row .del").forEach(btn => {
    btn.addEventListener("click", async e => {
      e.stopPropagation();
      const id = Number(btn.dataset.id);
      const c = list.find(x => x.id === id);
      if (!confirm(`Delete the ${fmt(c.amount)} gift to ${c.charity_name}?`)) return;
      await fetch(`/api/cash/${id}`, { method: "DELETE" });
      loadCash();
    });
  });
  el.querySelectorAll(".cash-row .edit").forEach(btn => {
    btn.addEventListener("click", e => {
      e.stopPropagation();
      const id = Number(btn.dataset.id);
      openCashEdit(list.find(x => x.id === id));
    });
  });
}

function openCashEdit(c) {
  const f = $("#cashEditForm");
  f.elements.id.value = c.id;
  f.elements.charity_name.value = c.charity_name || "";
  f.elements.amount.value = c.amount ?? "";
  f.elements.donation_date.value = c.donation_date || "";
  f.elements.payment_method.value = c.payment_method || "cash";
  f.elements.donor_id.value = c.donor_id || "";
  f.elements.charity_address.value = c.charity_address || "";
  f.elements.notes.value = c.notes || "";
  const rc = $("#cashEditReceiptCurrent");
  if (c.receipt_url) {
    const isImg = /\.(jpg|jpeg|png|webp|gif|heic)$/i.test(c.receipt_url);
    rc.classList.remove("empty");
    rc.innerHTML = (isImg
      ? `<img class="thumb" src="${escapeHTML(c.receipt_url)}" alt="receipt">`
      : `<span style="font-size:1.4rem">📄</span>`)
      + `<span>Current receipt — <a href="${escapeHTML(c.receipt_url)}" target="_blank">open</a></span>`;
  } else {
    rc.classList.add("empty");
    rc.textContent = "No receipt attached yet.";
  }
  // Reset file picker and the "remove" checkbox each time the modal opens
  f.elements.receipt.value = "";
  f.elements.remove_receipt.checked = false;
  $("#cashRemoveReceiptWrap").style.display = c.receipt_url ? "" : "none";
  $("#cashEditModal").classList.remove("hidden");
}

$("#cashEditForm").addEventListener("submit", async e => {
  e.preventDefault();
  const f = e.target;
  const id = f.elements.id.value;
  const fd = new FormData();
  for (const k of ["charity_name","amount","donation_date","payment_method",
                   "donor_id","charity_address","notes"]) {
    fd.append(k, f.elements[k].value);
  }
  // Receipt file (only if user picked one)
  if (f.elements.receipt.files && f.elements.receipt.files[0]) {
    fd.append("receipt", f.elements.receipt.files[0]);
  }
  if (f.elements.remove_receipt.checked) {
    fd.append("remove_receipt", "true");
  }
  const r = await fetch(`/api/cash/${id}`, { method: "PATCH", body: fd });
  if (!r.ok) { alert("Save failed: " + await r.text()); return; }
  closeModals();
  loadCash();
});

$("#cashEditDelete").addEventListener("click", async () => {
  const id = $("#cashEditForm").elements.id.value;
  if (!confirm("Delete this cash gift?")) return;
  await fetch(`/api/cash/${id}`, { method: "DELETE" });
  closeModals();
  loadCash();
});

$("#cashForm").addEventListener("submit", async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  if (!fd.get("donation_date")) fd.set("donation_date", new Date().toISOString().slice(0, 10));
  // Use the per-device "Donating as" picker from the top of the Add tab
  const def = $("#defaultDonor").value || getDefaultDonorId();
  if (def) fd.set("donor_id", def);
  const status = $("#cashStatus");
  status.classList.remove("hidden", "error");
  status.textContent = "Saving…";
  const r = await fetch("/api/cash", { method: "POST", body: fd });
  if (!r.ok) {
    status.classList.add("error");
    status.textContent = "Failed: " + await r.text();
    return;
  }
  status.textContent = "Saved.";
  e.target.reset();
  loadCash();
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
  f.elements.value_overridden.value = it.value_overridden ? "true" : "false";
  applyValueModeUI();
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

  const isOverride = $("#itemForm").elements.value_overridden.value === "true";
  const overrideLine = isOverride
    ? `<div class="row2"><span style="color:var(--accent)">Manual override</span><span><b>${fmt(perUnit)}</b> per unit</span></div>`
    : `<div class="row2"><span style="color:var(--accent-2)">Auto from category + condition</span><span><b>${fmt(tableValue || 0)}</b> per unit</span></div>`;

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
    const name = e.target.name;
    if (!["category_key","condition","quantity","estimated_value"].includes(name)) return;
    // In auto mode, recompute the live value when category/condition change.
    if ($("#itemForm").elements.value_overridden.value !== "true"
        && ["category_key","condition"].includes(name)) {
      syncAutoValue();
    }
    updateCalcBox();
    if (name === "category_key") updateFmvHint();
  });
});

function applyValueModeUI() {
  const f = $("#itemForm");
  const overridden = f.elements.value_overridden.value === "true";
  const inp = f.elements.estimated_value;
  const badge = $("#valueBadge");
  if (overridden) {
    inp.disabled = false;
    inp.classList.remove("locked");
    inp.removeAttribute("title");
    if (badge) { badge.textContent = "manual · click for auto"; badge.className = "value-badge manual"; }
  } else {
    inp.disabled = true;
    inp.classList.add("locked");
    inp.setAttribute("title", "Auto-calculated. Click the badge to override.");
    if (badge) { badge.textContent = "auto · click to override"; badge.className = "value-badge auto"; }
    syncAutoValue();
  }
}

// Clicking the badge toggles auto / manual.
$("#valueBadge").addEventListener("click", () => {
  const f = $("#itemForm");
  const cur = f.elements.value_overridden.value === "true";
  f.elements.value_overridden.value = cur ? "false" : "true";
  applyValueModeUI();
  // Going auto -> manual on an empty field: seed with the auto value.
  if (!cur && !f.elements.estimated_value.value) syncAutoValue();
  updateCalcBox();
});

function syncAutoValue() {
  const f = $("#itemForm");
  const cat = CATEGORIES.find(c => c.key === f.elements.category_key.value);
  const cond = f.elements.condition.value;
  if (!cat) return;
  const v = cond === "excellent" ? cat.high : cond === "fair" ? cat.low : cat.median;
  if (v != null) f.elements.estimated_value.value = v;
}

$("#itemForm").addEventListener("submit", async e => {
  e.preventDefault();
  const f = e.target;
  const id = f.elements.id.value;
  const fd = new FormData();
  for (const k of ["description", "category_key", "condition", "quantity", "notes", "donor_id"]) {
    fd.append(k, f.elements[k].value);
  }
  // Only send estimated_value when in override mode; disabled fields are skipped
  // by FormData anyway, but be explicit.
  const overridden = f.elements.value_overridden.value === "true";
  fd.append("value_overridden", overridden ? "true" : "false");
  if (overridden) fd.append("estimated_value", f.elements.estimated_value.value);
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

$("#reanalyzeItem").addEventListener("click", async () => {
  const id = Number($("#itemForm").elements.id.value);
  if (!confirm("Re-run the AI on this photo? Your current description, category, condition, and value will be overwritten.")) return;
  const r = await fetch(`/api/items/${id}/reanalyze`, { method: "POST" });
  if (!r.ok) { alert("Failed: " + await r.text()); return; }
  PENDING.add(id);
  if (RECENT.has(id)) { const it = RECENT.get(id); it.status = "analyzing"; }
  closeModals();
  startPolling();
  if (!$("#view-items").classList.contains("hidden")) loadItems();
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
  // Refresh the underlying drop-off list in the background so totals are
  // up-to-date the moment the user closes the modal.
  loadEvents();
  const [evRes, unRes] = await Promise.all([
    fetch(`/api/events/${id}`),
    fetch(`/api/items?unassigned=true`),
  ]);
  const ev = await evRes.json();
  const unassigned = await unRes.json();

  $("#evTitle").textContent = ev.charity_name;
  const dateLabel = formatLongDate(ev.donation_date);
  const parts = [dateLabel];
  if (ev.charity_address) parts.push(ev.charity_address);
  $("#evMeta").textContent = parts.join(" · ");
  const itemWord = ev.item_count === 1 ? "item" : "items";
  $("#evTotal").textContent = `${ev.item_count} ${itemWord} · ${fmt(ev.total_value)} total`;

  // In-drop-off items: tap to remove instantly.
  const evItemsEl = $("#evItems");
  evItemsEl.dataset.empty = "Nothing in this drop-off yet — tap a card below to add it.";
  evItemsEl.innerHTML = ev.items.map(renderCard).join("");
  evItemsEl.querySelectorAll(".card").forEach(c => {
    c.addEventListener("click", async () => {
      c.classList.add("removing");
      const fd = new FormData(); fd.append("item_ids", c.dataset.id);
      const r = await fetch(`/api/events/${id}/unassign`, { method: "POST", body: fd });
      if (!r.ok) { alert("Failed: " + await r.text()); c.classList.remove("removing"); return; }
      openEvent(id);
    });
  });

  // Available items: tap to add instantly.
  const unEl = $("#evUnassigned");
  unEl.dataset.empty = "Everything's been assigned somewhere — snap more photos on the Add tab.";
  unEl.innerHTML = unassigned.map(renderCard).join("");
  unEl.querySelectorAll(".card").forEach(c => {
    c.addEventListener("click", async () => {
      c.classList.add("removing");
      const fd = new FormData(); fd.append("item_ids", c.dataset.id);
      const r = await fetch(`/api/events/${id}/assign`, { method: "POST", body: fd });
      if (!r.ok) { alert("Failed: " + await r.text()); c.classList.remove("removing"); return; }
      openEvent(id);
    });
  });

  // Hide the "Add all" button if there's nothing to add.
  $("#donateAllBtn").style.display = unassigned.length ? "" : "none";

  $("#receiptLink").href = `/receipt/${id}`;
  $("#eventModal").classList.remove("hidden");
}

function formatLongDate(s) {
  if (!s) return "";
  const d = new Date(s + "T00:00:00");
  if (isNaN(d)) return s;
  return d.toLocaleDateString(undefined, { weekday: "short", month: "long", day: "numeric", year: "numeric" });
}

$("#donateAllBtn").addEventListener("click", async () => {
  const r = await fetch(`/api/events/${CURRENT_EVENT}/assign_all_unassigned`, { method: "POST" });
  if (!r.ok) { alert("Failed: " + await r.text()); return; }
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
// Restore Add-tab mode (item vs cash) from last session
try { setAddMode(localStorage.getItem("dt.addMode") || "item"); } catch {}
let _saved = location.hash.replace(/^#/, "");
if (!$(`#view-${_saved}`)) {
  try { _saved = localStorage.getItem("dt.activeTab") || ""; } catch {}
}
if (!$(`#view-${_saved}`)) _saved = "capture";
showView(_saved);
