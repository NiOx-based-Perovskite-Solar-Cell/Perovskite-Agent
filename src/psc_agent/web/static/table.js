"use strict";
const JOB = window.TABLE_JOB;
const SHEET_ORDER = ["Layer", "Vender", "Materials", "Process", "Properties", "Stability"];
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const CHECK_KEY = `psc-checked:${JOB}`;

const st = {
  kind: "extract",
  rows: [],
  sheet: "",
  device: "",
  filter: "", // decision/status or "__unchecked"
  sel: null,
  view: null,
  size: null,
  checked: new Set(JSON.parse(localStorage.getItem(CHECK_KEY) || "[]")),
};

const tag = (r) => (st.kind === "recheck" ? r.status : r.decision);

function saveChecked() {
  localStorage.setItem(CHECK_KEY, JSON.stringify([...st.checked]));
  updateProgress();
}

function updateProgress() {
  const total = st.rows.length;
  const done = st.rows.filter((r) => st.checked.has(r.row_id)).length;
  $("#checked-progress").textContent = `Checked ${done} / ${total}`;
}

function parseValues(raw) {
  if (raw && typeof raw === "object" && !Array.isArray(raw)) return raw;
  if (typeof raw !== "string" || !raw.trim().startsWith("{")) return null;
  try {
    const obj = JSON.parse(raw);
    return obj && typeof obj === "object" && !Array.isArray(obj) ? obj : null;
  } catch {
    return null;
  }
}

function sheetColumns(sheet, rows) {
  const base = (window.SHEET_HEADERS && window.SHEET_HEADERS[sheet]) || [];
  const cols = [...base];
  for (const r of rows) {
    const obj = parseValues(r.values);
    if (!obj) continue;
    for (const k of Object.keys(obj)) if (!cols.includes(k)) cols.push(k);
  }
  return cols;
}

const sample = (d) => String(d ?? "").replace(/^.*_(Sample\s*\d+)$/i, "$1");

function attnClass(r) {
  const t = tag(r);
  if (t === "revise" || t === "mismatch") return "attn-revise";
  if (t === "verify_more" || t === "unit_mismatch" || t === "review") return "attn-verify";
  if (t === "human_read" || t === "missing_in_record") return "attn-human";
  return "";
}

async function getJson(url) {
  const r = await fetch(url);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `${r.status}`);
  return d;
}

function baseFiltered() {
  const q = $("#q").value.trim().toLowerCase();
  return st.rows.filter((r) => {
    if (st.device && r.device !== st.device) return false;
    if (st.filter === "__unchecked") { if (st.checked.has(r.row_id)) return false; }
    else if (st.filter && tag(r) !== st.filter) return false;
    if (q && !JSON.stringify(r).toLowerCase().includes(q)) return false;
    return true;
  });
}

function orderedSheets(rows) {
  const sheets = [...new Set(rows.map((r) => r.sheet || "Other"))];
  sheets.sort((a, b) => (SHEET_ORDER.indexOf(a) + 1 || 99) - (SHEET_ORDER.indexOf(b) + 1 || 99));
  return sheets;
}

function drawToolbar() {
  const devices = [...new Set(st.rows.map((r) => r.device).filter(Boolean))];
  const devSel = $("#device-filter");
  if (!devSel.options.length) {
    devSel.innerHTML = `<option value="">All devices (${devices.length})</option>` +
      devices.map((d) => `<option value="${esc(d)}">${esc(sample(d))} · ${esc(d)}</option>`).join("");
    devSel.hidden = devices.length <= 1;
  }

  const order = st.kind === "recheck"
    ? ["mismatch", "unit_mismatch", "missing_in_record", "review", "needs_si", "not_found", "not_checked", "paper_more_detail", "match"]
    : ["revise", "verify_more", "human_read", "accept"];
  const counts = {};
  for (const r of st.rows) counts[tag(r) || ""] = (counts[tag(r) || ""] || 0) + 1;
  const unchecked = st.rows.filter((r) => !st.checked.has(r.row_id)).length;
  const chip = (f, label, n) => `<button type="button" class="chip ${st.filter === f ? "on" : ""}" data-f="${esc(f)}">${esc(label)} ${n}</button>`;
  $("#decision-chips").innerHTML = chip("", "all", st.rows.length)
    + order.filter((k) => counts[k] || (st.kind === "extract" && (k === "verify_more" || k === "human_read"))).map((k) => chip(k, k, counts[k] || 0)).join("")
    + chip("__unchecked", "unchecked", unchecked);

  const afterBasic = baseFiltered();
  const sheets = orderedSheets(afterBasic);
  const sc = {}, sa = {};
  for (const r of afterBasic) {
    const s = r.sheet || "Other";
    sc[s] = (sc[s] || 0) + 1;
    if (["revise", "verify_more", "human_read", "mismatch", "unit_mismatch", "review"].includes(tag(r))) sa[s] = (sa[s] || 0) + 1;
  }
  const stab = (s, label, n, warn) => `<button type="button" class="stab ${st.sheet === s ? "on" : ""}" data-s="${esc(s)}">${esc(label)} (${n})${warn ? `<span class="badge verify_more">${warn} check</span>` : ""}</button>`;
  $("#sheet-tabs").innerHTML = sheets.map((s) => stab(s, s, sc[s] || 0, sa[s] || 0)).join("")
    + (sheets.length > 1 ? stab("", "All sheets", afterBasic.length, 0) : "");
}

function drawTables() {
  const afterBasic = baseFiltered();
  if (st.kind === "extract" && st.sheet && afterBasic.length && !afterBasic.some((r) => (r.sheet || "Other") === st.sheet)) {
    st.sheet = orderedSheets(afterBasic)[0] || "";
  }
  drawToolbar();
  const rows = afterBasic.filter((r) => !st.sheet || (r.sheet || "Other") === st.sheet);
  $("#shown-count").textContent = `Showing ${rows.length} of ${st.rows.length}`;
  const pane = $("#tables-pane");
  pane.classList.toggle("single-sheet", Boolean(rows.length && (st.kind === "recheck" || st.sheet !== "")));
  if (!rows.length) {
    pane.innerHTML = `<div class="sheet-card" style="padding:2rem;text-align:center"><span class="mute">No rows match this filter.</span></div>`;
    return;
  }

  if (st.kind === "recheck") {
    pane.innerHTML = `<div class="sheet-card">
      <div class="sheet-head"><h2>Findings</h2><span class="mute">${rows.length} cells</span></div>
      <div class="sheet-scroll"><table>
        <thead><tr>
          <th class="col-chk" title="Checked"></th><th>Status</th><th>Device</th><th>Sheet · Row</th><th>Column</th><th>Keys</th>
          <th>Recorded (Excel)</th><th>Paper (Agent)</th><th>Auditor</th><th>Detail · Evidence</th><th class="col-act">Verify</th>
        </tr></thead>
        <tbody>${rows.map((r) => `<tr class="${attnClass(r)} ${st.sel === r.row_id ? "is-sel" : ""} ${st.checked.has(r.row_id) ? "is-checked" : ""}" data-row="${esc(r.row_id)}">
          <td class="col-chk"><input type="checkbox" data-chk="${esc(r.row_id)}" ${st.checked.has(r.row_id) ? "checked" : ""}></td>
          <td><span class="badge ${esc(r.status)}">${esc(r.status)}</span></td>
          <td title="${esc(r.device)}">${esc(sample(r.device))}</td>
          <td>${esc(r.sheet)} · ${esc(r.excel_row)}</td>
          <td><strong>${esc(r.column)}</strong></td>
          <td class="mute">${esc(r.keys)}</td>
          <td class="num">${esc(r.recorded)} ${esc(r.recorded_unit)}</td>
          <td class="num"><strong>${esc(r.paper_value)} ${esc(r.paper_unit)}</strong></td>
          <td>${r.audit ? `${esc(r.audit)} (${Math.round(Number(r.audit_p || 0) * 100)}%)` : `<span class="empty-cell">–</span>`}</td>
          <td>${esc(r.detail)}<div class="ev-quote mute">${r.page ? `p${esc(r.page)} ` : ""}${esc(r.evidence)}</div></td>
          <td class="col-act">
            ${r.page ? `<button type="button" class="btn" data-pdf="${esc(r.row_id)}">PDF p${esc(r.page)}</button>` : `<span class="empty-cell">no page</span>`}
            <a class="btn" href="/review/${encodeURIComponent(JOB)}?row=${encodeURIComponent(r.row_id)}" target="_blank" rel="noopener" title="Open side-by-side review">Open</a>
          </td>
        </tr>`).join("")}</tbody>
      </table></div>
    </div>`;
    return;
  }

  const bySheet = new Map();
  for (const r of rows) {
    const s = r.sheet || "Other";
    if (!bySheet.has(s)) bySheet.set(s, []);
    bySheet.get(s).push(r);
  }
  const sheets = [...bySheet.keys()].sort((a, b) => (SHEET_ORDER.indexOf(a) + 1 || 99) - (SHEET_ORDER.indexOf(b) + 1 || 99));

  $("#tables-pane").innerHTML = sheets.map((sheet) => {
    const sRows = bySheet.get(sheet);
    const cols = sheetColumns(sheet, sRows);
    return `<div class="sheet-card">
      <div class="sheet-head"><h2>${esc(sheet)}</h2><span class="mute">${sRows.length} rows · ${cols.length} columns</span></div>
      <div class="sheet-scroll"><table>
        <thead><tr>
          <th class="col-chk" title="Checked"></th><th>Decision</th><th>Device</th>
          ${cols.map((c) => `<th>${esc(c)}</th>`).join("")}
          <th>Source & Evidence</th><th>Reason / Note</th><th class="col-act">Verify</th>
        </tr></thead>
        <tbody>${sRows.map((r) => {
          const obj = parseValues(r.values) || {};
          return `<tr class="${attnClass(r)} ${st.sel === r.row_id ? "is-sel" : ""} ${st.checked.has(r.row_id) ? "is-checked" : ""}" data-row="${esc(r.row_id)}">
            <td class="col-chk"><input type="checkbox" data-chk="${esc(r.row_id)}" ${st.checked.has(r.row_id) ? "checked" : ""}></td>
            <td><span class="badge ${esc(r.decision)}">${esc(r.decision || "none")}</span>${r.p ? `<div class="mute">p=${esc(Number(r.p).toFixed(2))}</div>` : ""}</td>
            <td title="${esc(r.device)}"><strong>${esc(sample(r.device))}</strong></td>
            ${cols.map((c) => {
              const v = obj[c];
              return v !== undefined && v !== null && v !== "" ? `<td class="num">${esc(v)}</td>` : `<td><span class="empty-cell">–</span></td>`;
            }).join("")}
            <td style="min-width:14rem;max-width:22rem">
              <div class="mute">${r.page ? `p${esc(r.page)} ` : ""}${r.source ? `[${esc(r.source)}] ` : ""}· quote: ${esc(r.quote_ok || "–")}</div>
              ${r.evidence ? `<div class="ev-quote">${esc(r.evidence)}</div>` : ""}
            </td>
            <td style="min-width:10rem">${esc(r.reason)}${r.note ? `<div class="mute">Note: ${esc(r.note)}</div>` : ""}</td>
            <td class="col-act">
              ${r.page ? `<button type="button" class="btn" data-pdf="${esc(r.row_id)}">PDF p${esc(r.page)}</button>` : `<span class="empty-cell">no page</span>`}
              <a class="btn" href="/review/${encodeURIComponent(JOB)}?row=${encodeURIComponent(r.row_id)}" target="_blank" rel="noopener" title="Open side-by-side review">Open</a>
            </td>
          </tr>`;
        }).join("")}</tbody>
      </table></div>
    </div>`;
  }).join("");
}

const HOW = {
  quote: ["found", "quote found on this page"],
  "part of the quote": ["found", "part of the quote found"],
  value: ["weak", "quote not found: number highlighted instead"],
  "": ["notfound", "quote not found on this page or adjacent pages"],
};

function drawPage(v, rects = [], how = "") {
  st.view = v;
  const img = $("#d-pageimg"), box = $("#d-page");
  box.querySelectorAll(".hl").forEach((e) => e.remove());
  $("#d-pagebox").hidden = false;
  const q = v.paper ? `?paper=${encodeURIComponent(v.paper)}` : "";
  const base = `/api/jobs/${encodeURIComponent(JOB)}/source/${v.source}`;
  $("#d-where").innerHTML = `<strong>${v.is_si ? "SI" : "Paper"} · p${v.page}</strong> <span class="mute">${esc(v.file)}</span> · <a href="${base}${q}#page=${v.page}" target="_blank" rel="noopener">open PDF</a>`;
  const place = () => {
    const w = st.size.width, h = st.size.height;
    rects.forEach(([x0, y0, x1, y1]) => {
      const d = document.createElement("div");
      d.className = "hl" + (how === "value" ? " value" : "");
      Object.assign(d.style, { left: `${(x0 / w) * 100}%`, top: `${(y0 / h) * 100}%`, width: `${((x1 - x0) / w) * 100}%`, height: `${((y1 - y0) / h) * 100}%` });
      box.appendChild(d);
    });
    const first = box.querySelector(".hl");
    if (first) first.scrollIntoView({ block: "center", behavior: "smooth" });
    else $("#d-pagebox").scrollTop = 0;
  };
  img.onload = place;
  img.src = `${base}/page/${v.page}.png?dpi=130${v.paper ? `&paper=${encodeURIComponent(v.paper)}` : ""}`;
  if (img.complete && img.naturalWidth) place();
}

function turn(step) {
  if (!st.view) return;
  const page = st.view.page + step;
  if (page < 1 || page > st.view.pages) return;
  drawPage({ ...st.view, page });
}

async function openPdf(rowId) {
  const r = st.rows.find((x) => x.row_id === rowId);
  if (!r) return;
  st.sel = rowId;
  $("#drawer").hidden = false;
  document.querySelectorAll("#tables-pane tr[data-row]").forEach((tr) => tr.classList.toggle("is-sel", tr.dataset.row === rowId));
  $("#d-title").textContent = `${sample(r.device)} · ${r.sheet || ""}`;
  $("#d-mark-next").textContent = st.checked.has(rowId) ? "Checked" : "Mark checked";
  const obj = parseValues(r.values);
  const kvHtml = obj
    ? `<table id="d-kv"><tbody>${Object.entries(obj).map(([k, v]) => `<tr><th style="width:40%">${esc(k)}</th><td>${v !== null && v !== "" ? esc(v) : "–"}</td></tr>`).join("")}</tbody></table>`
    : (r.column ? `<div><b>${esc(r.column)}:</b> Recorded <b>${esc(r.recorded)}</b> → Paper <b>${esc(r.paper_value)}</b></div>` : "");

  $("#d-facts").innerHTML = `
    <div id="d-where"><span class="mute">Finding quote in PDF…</span></div>
    <div id="d-hit" style="margin-top:.2rem"></div>
    <div style="margin-top:.35rem"><span class="badge ${esc(tag(r))}">${esc(tag(r))}</span> <span class="mute">${esc(r.reason || r.detail || "")}</span></div>
    ${kvHtml}
    ${r.evidence ? `<blockquote>${esc(r.evidence)}</blockquote>` : ""}
    <div id="d-pager">
      <button type="button" class="btn" id="d-prev">‹ Page</button>
      <button type="button" class="btn" id="d-next">Page ›</button>
      <span class="mute">Left/Right keys turn pages · Esc close</span>
    </div>`;
  $("#d-prev").onclick = () => turn(-1);
  $("#d-next").onclick = () => turn(1);

  try {
    const hit = await getJson(`/api/jobs/${encodeURIComponent(JOB)}/locate?row=${encodeURIComponent(rowId)}`);
    if (st.sel !== rowId) return;
    if (hit.source == null) {
      $("#d-where").innerHTML = `<span class="mute">No page cited.</span>`;
      $("#d-pagebox").hidden = true;
      return;
    }
    st.size = { width: hit.width, height: hit.height };
    const [cls, text] = HOW[hit.how] || HOW[""];
    $("#d-hit").innerHTML = `<span class="${cls}">${esc(text)}</span>`;
    drawPage(hit, hit.rects, hit.how);
  } catch (e) {
    $("#d-where").innerHTML = `<span class="notfound">${esc(e.message)}</span>`;
  }
}

function setRowChecked(rid, isChecked) {
  if (isChecked) st.checked.add(rid);
  else st.checked.delete(rid);
  saveChecked();
  if (st.filter === "__unchecked") {
    drawTables();
    return;
  }
  document.querySelectorAll(`#tables-pane tr[data-row]`).forEach((tr) => {
    if (tr.dataset.row !== rid) return;
    tr.classList.toggle("is-checked", isChecked);
    const cb = tr.querySelector(`[data-chk]`);
    if (cb) cb.checked = isChecked;
  });
  drawToolbar();
}

async function init() {
  const id = encodeURIComponent(JOB);
  try {
    const job = await getJson(`/api/jobs/${id}`);
    st.kind = job.kind === "recheck" ? "recheck" : "extract";
    const rows = (await getJson(`/api/jobs/${id}/${st.kind === "recheck" ? "findings" : "review"}`)).rows;
    st.rows = st.kind === "recheck" ? rows.map((r, i) => ({ ...r, row_id: `f${i}` })) : rows;
  } catch (e) {
    $("#tables-pane").innerHTML = `<div class="sheet-card" style="padding:2rem"><span class="notfound">${esc(e.message)}</span></div>`;
    return;
  }
  document.title = st.kind === "recheck" ? `Check findings · ${JOB}` : `Check extracted tables · ${JOB}`;
  $("#title").textContent = st.kind === "recheck" ? "Check recheck findings" : "Check extracted tables";
  $("#meta").textContent = `job ${JOB} · ${st.rows.length} rows`;
  if (st.kind === "recheck") $("#csv-link").href = `/api/jobs/${id}/files/findings.csv?download=1`;
  st.sheet = st.kind === "extract" ? (orderedSheets(st.rows)[0] || "") : "";
  updateProgress();
  drawTables();

  $("#sheet-tabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-s]");
    if (b) { st.sheet = b.dataset.s; drawTables(); }
  });
  $("#decision-chips").addEventListener("click", (e) => {
    const b = e.target.closest("[data-f]");
    if (b) { st.filter = b.dataset.f; drawTables(); }
  });
  $("#device-filter").addEventListener("change", (e) => { st.device = e.target.value; drawTables(); });
  $("#q").addEventListener("input", drawTables);
  $("#clear-checks").addEventListener("click", () => { st.checked.clear(); saveChecked(); drawTables(); });
  $("#d-close").addEventListener("click", () => {
    $("#drawer").hidden = true;
    st.sel = null;
    document.querySelectorAll("#tables-pane tr.is-sel").forEach((tr) => tr.classList.remove("is-sel"));
  });
  $("#d-mark-next").addEventListener("click", () => {
    if (!st.sel) return;
    const next = !st.checked.has(st.sel);
    setRowChecked(st.sel, next);
    $("#d-mark-next").textContent = next ? "Checked" : "Mark checked";
  });
  $("#tables-pane").addEventListener("change", (e) => {
    const chk = e.target.closest("[data-chk]");
    if (!chk) return;
    setRowChecked(chk.dataset.chk, chk.checked);
    if (st.sel === chk.dataset.chk) $("#d-mark-next").textContent = chk.checked ? "Checked" : "Mark checked";
  });
  $("#tables-pane").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-pdf]");
    if (btn) openPdf(btn.dataset.pdf);
  });
  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
    if (e.key === "Escape" && !$("#drawer").hidden) {
      $("#drawer").hidden = true;
      st.sel = null;
      document.querySelectorAll("#tables-pane tr.is-sel").forEach((tr) => tr.classList.remove("is-sel"));
    }
    if (!$("#drawer").hidden && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
      e.preventDefault();
      turn(e.key === "ArrowLeft" ? -1 : 1);
    }
  });
}

init();
