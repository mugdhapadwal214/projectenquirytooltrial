/*
 * script.js — UI wiring only. All calculation happens server-side in
 * calculator.py via POST /api/calculate. This file never computes a
 * weight, area, or cost itself.
 *
 * STATE
 *   rowsByType[type]   — the parts entered for each component type. Rows
 *                        persist when you switch tabs; switching just changes
 *                        which set you're looking at, it does not start over.
 *                        Every tab's rows are sent on Calculate.
 *   schemaByType[type] — cached field definitions from /api/schema.
 *
 * Column headers show the SHORT form (field.abbr); full forms go in the
 * legend under the table, built from the same schema so the two can't drift.
 */

let currentType = null;
let rowsByType = {};
let schemaByType = {};
let rowSeq = 0;
let lastResult = null;
// The last calculated layout, KEPT FOR VIEWING even after the job is booked.
// commitJob() clears lastResult so the same result cannot be booked twice -
// and once results were persisted, that null was saved too. A team lead or
// director opening the project afterwards found the Results tab empty: the
// figures they were being asked to approve existed only in the browser of
// whoever pressed Calculate.
let savedResult = null;
let lastPayloadRows = [];
let calculatedRowIds = new Set();

/* Seed rows deliberately carry NO material. blankRow() fills it with
   MATERIALS[0], which comes from database.py at page load — so renaming or
   replacing the material list can never leave a seeded row pointing at a
   material that no longer exists. That is what broke calculation before. */
const SEED = {
  "Dish End": [
    { part_no: "621010-1", head_type: "Torispherical", formula: "Standard (1.11D+2SF)", id_: 350, thk: 5, knuckle_r: 35, sf: 20, ht: 200, qty: 1, rate: 100 },
    { part_no: "",         head_type: "Torispherical", formula: "Standard (1.11D+2SF)", id_: 350, thk: 5, knuckle_r: 35, sf: 20, ht: 300, qty: 1, rate: 100 },
  ],
  "Shell": [
    { part_no: "621010-2", part_name: "shell 1", id_: 350, thk: 8, ht: 200, qty: 1, rate: 100 },
  ],
};

/* NPS is what the pipe and flange charts are keyed on. NB and DN are the same
   sizes under different names, so only the LABEL changes — the value stored on
   the row stays the NPS the chart understands. */
const NPS_TO_DN = { "1/8": 6, "1/4": 8, "3/8": 10, "1/2": 15, "3/4": 20, "1": 25,
  "1 1/4": 32, "1 1/2": 40, "2": 50, "2 1/2": 65, "3": 80, "3 1/2": 90, "4": 100,
  "5": 125, "6": 150, "8": 200, "10": 250, "12": 300, "14": 350, "16": 400,
  "18": 450, "20": 500, "22": 550, "24": 600, "26": 650, "28": 700, "30": 750,
  "32": 800, "34": 850, "36": 900, "40": 1000, "42": 1050, "48": 1200 };
function sizeLabel(nps, std) {
  const key = String(nps).replace(/["\s]+$/, "").trim();
  const dn = NPS_TO_DN[key];
  if (!std || std.indexOf("NPS") === 0 || !dn) return key + '"';
  return (std.indexOf("NB") === 0 ? "NB " : "DN ") + dn;
}
const flangeCustom = r => String(r.flange_mode || "").toLowerCase().indexOf("custom") === 0;

function schema() { return schemaByType[currentType]; }
function rows() { return rowsByType[currentType] || []; }

function blankRow(fields) {
  // NOTHING IS GUESSED.
  //
  // A new row used to arrive as the first material in the list and the first
  // option in every dropdown - so a dish end was silently Torispherical in
  // mild steel whether or not anyone had chosen that. It looked filled in, it
  // calculated, and it priced. A wrong default that quotes is worse than a
  // blank that refuses to.
  //
  // Quantity stays at 1: one of a part is the only quantity that cannot be
  // wrong in a way that matters, and "how many" is never the question a
  // drawing leaves open.
  const r = { _id: rowSeq++ };
  fields.forEach(f => { r[f.key] = f.key === "qty" ? 1 : ""; });
  return r;
}

/* Fields that must be filled for a row to count. Part number is optional
   labelling, and rate may legitimately be unknown at estimating time. */
/* Only fields the geometry actually needs. Marked per-field in meta.py —
   requiring one the blank calculation never reads (dish end Height, say)
   silently drops the row and looks like the app ignoring the part. */
function requiredKeys(sch) {
  return sch.fields.filter(f => f.required !== false).map(f => f.key);
}

function rowComplete(sch, r) {
  return requiredKeys(sch).every(k =>
    r[k] !== "" && r[k] !== undefined && r[k] !== null);
}

/* Rows that have something typed in them but aren't usable yet. These are the
   ones the user thinks they added. */
function incompleteRows(type) {
  const sch = schemaByType[type];
  if (!sch) return [];
  // Only fields the user has to type count as evidence they started the row.
  // material, qty and the dropdowns are pre-filled by blankRow, so an
  // untouched row would otherwise look half-finished.
  const typed = sch.fields.filter(f =>
    !f.options && f.key !== "material" && f.key !== "qty");
  return (rowsByType[type] || []).filter(r =>
    !rowComplete(sch, r) &&
    typed.some(f => r[f.key] !== "" && r[f.key] !== undefined && r[f.key] !== null));
}

function missingFor(sch, r) {
  return sch.fields.filter(f => f.required !== false &&
    (r[f.key] === "" || r[f.key] === undefined || r[f.key] === null))
    .map(f => f.abbr || f.label);
}

/* Rows already produced by a booked job. They stay visible as a record, but
   must never be nested again — their offcut is already on the rack, and
   re-cutting them from the very remnant they created is exactly the bug this
   guards against. */
function bookedRows(type) {
  return (rowsByType[type] || []).filter(r => r._booked);
}

function validRows(type) {
  const sch = schemaByType[type];
  if (!sch) return [];
  return (rowsByType[type] || []).filter(r => rowComplete(sch, r) && !r._booked);
}

async function switchType(type) {
  currentType = type;

  if (!schemaByType[type]) {
    const res = await fetch(`/api/schema/${encodeURIComponent(type)}`);
    schemaByType[type] = await res.json();
  }
  // Seed only the first time a tab is opened. After that the rows the user
  // typed are kept, so switching tabs never loses work.
  if (!rowsByType[type]) {
    const seed = SEED[type] || [{}];
    rowsByType[type] = seed.map(s => ({ ...blankRow(schemaByType[type].fields), ...s, _id: rowSeq++ }));
  }

  if (type === "Flange") {
    for (const r of rowsByType[type]) {
      await initializeFlangeRow(r);
    }
  }

  if (type === "Pipe") {
    for (const r of rowsByType[type]) {
      await initializePipeRow(r);
    }
  }

  document.querySelectorAll("#typeTabs span").forEach(el =>
    el.classList.toggle("on", el.dataset.type === type));
  document.getElementById("tableTitle").textContent = `2\u00a0 Enter part details \u2014 ${type}`;
  document.getElementById("tableNote").textContent = schema().note;

  renderTable();
  renderLegend();
  renderTabCounts();
}

function renderTabCounts() {
  // Count sits inline in the label. The old floating badge sat on the tab
  // corner and overlapped the next tab, which made it unreadable.
  let ready = 0, pending = 0;
  document.querySelectorAll("#typeTabs span").forEach(el => {
    const type = el.dataset.type;
    const n = validRows(type).length;
    const bad = incompleteRows(type).length;
    ready += n;
    pending += bad;
    el.textContent = type + (n > 0 ? ` (${n})` : "");
    el.classList.toggle("has-gap", bad > 0);
  });
  const tally = document.getElementById("tally");
  if (tally) {
    tally.innerHTML = ready === 0 && pending === 0 ? ""
      : `<b>${ready}</b> part${ready === 1 ? "" : "s"} ready` +
        (pending ? ` &middot; <span class="pend">${pending} row${pending === 1 ? "" : "s"} incomplete</span>` : "");
  }
}

let flashRowId = null;

// WHERE THE CURSOR WAS, SO IT CAN BE PUT BACK.
//
// updateCell() fires on CHANGE, which is blur. Clicking from one cell to the
// next therefore fires a repaint of the whole tbody - and that repaint throws
// away the very element the click had just focused. The cell lost focus, the
// row re-measured (the "needs H, Matl" note changes width), and the user had
// to click a second time to type. Every cell now carries its row id and field
// key so the same one can be found in the new table and given the cursor back.
function captureFocus() {
  const el = document.activeElement;
  if (!el || !el.dataset || el.dataset.rid === undefined) return null;
  const box = document.getElementById("partsTable");
  if (!box || !box.contains(el)) return null;
  let start = null, end = null;
  try { start = el.selectionStart; end = el.selectionEnd; } catch (e) { /* number inputs */ }
  return { rid: el.dataset.rid, key: el.dataset.key, start, end };
}

function restoreFocus(f) {
  if (!f) return;
  const el = document.querySelector(
    `#partsTable [data-rid="${f.rid}"][data-key="${f.key}"]`);
  if (!el) return;
  el.focus();
  if (f.start !== null && f.start !== undefined) {
    try { el.setSelectionRange(f.start, f.end); } catch (e) { /* number inputs */ }
  }
}

function renderTable() {
  const keep = captureFocus();
  const sch = schema();
  const thead = document.querySelector("#partsTable thead");
  thead.innerHTML = "<tr>" + sch.fields.map(f => {
    const head = f.abbr || f.label;
    const cls = f.key === "rate" ? ' class="rate-col"' : "";
    return `<th${cls}>${head}${f.unit ? `<span class="u"> (${f.unit})</span>` : ""}</th>`;
  }).join("") + `<th class="rate-col">${currentType === "Flange" ? "Purchase Cost" : "RM Cost"}</th><th></th></tr>`;

  const tbody = document.getElementById("partsBody");
  tbody.innerHTML = rows().map(r => {
    const cells = sch.fields.map(f => {
      if (f.key === "material") {
        return `<td><select data-rid="${r._id}" data-key="${f.key}" onchange="updateCell(${r._id},'${f.key}',this.value)">` +
          `<option value=""${r[f.key] ? "" : " selected"}>\u2014 choose \u2014</option>` +
          MATERIALS.map(m => `<option value="${m}" ${String(r[f.key]) === String(m) ? "selected" : ""}>${m}</option>`).join("") +
          `</select></td>`;
      }
      // A custom flange is typed in, so the chart's own fields are switched off.
      if (currentType === "Flange" && flangeCustom(r) &&
          ["pressure_class", "size_std", "nps", "flange_type"].includes(f.key)) {
        return '<td><span class="dash" title="Not used for a custom flange">\u2014</span></td>';
      }
      if (f.options) {
        const label = (f.key === "nps") ? (o => sizeLabel(o, r.size_std)) : (o => o);
        return `<td><select data-rid="${r._id}" data-key="${f.key}" onchange="updateCell(${r._id},'${f.key}',this.value)">` +
          `<option value=""${r[f.key] ? "" : " selected"}>\u2014 choose \u2014</option>` +
          f.options.map(o => `<option value="${o}" ${String(r[f.key]) === String(o) ? "selected" : ""}>${label(o)}</option>`).join("") +
          `</select></td>`;
      }
      const type = f.numeric ? "number" : "text";
      const val = r[f.key] ?? "";
      const cls = (f.key === "part_no" || f.key === "part_name") ? "part" : "rem";
      const tdCls = f.key === "rate" ? ' class="rate-col"' : "";
      return `<td${tdCls}><input class="${cls}" type="${type}" value="${val}" data-rid="${r._id}" data-key="${f.key}" onchange="updateCell(${r._id},'${f.key}',this.value)"></td>`;
    }).join("");

    const miss = missingFor(sch, r);
    const res = resultForRow(r);
    const warn = res && !res.fits_standard
      ? ' <span class="nofit" title="Blank does not fit the standard plate">!</span>' : "";
    const flash = (flashRowId != null && r._id === flashRowId) ? " flashrow" : "";
    const rowCls = r._booked
      ? ` class="booked${flash}" title="Saved with job ${r._booked}. Press reopen to edit it and include it in the next calculation."`
      : (miss.length
          ? ` class="incomplete${flash}" title="Not calculated \u2014 missing ${miss.join(", ")}"`
          : (flash ? ` class="${flash.trim()}"` : ""));
    return `<tr${rowCls}>${cells}` +
      `<td class="rate-col num rm-cost">${r._booked
          ? `<span class="bookedtag">${r._booked}</span>`
          : (miss.length
          ? `<span class="need">needs ${miss.join(", ")}</span>`
          : (currentType === "Custom"
              ? `<span class="need">not costed \u2014 free-text size</span>`
          : (res && currentType === "Flange" && res.rate > 0
              ? "\u20b9" + fmt(res.rate, 0) + "/pc \u00d7 " + res.qty + " = \u20b9" + fmt(res.purchase_cost, 0)
              : (currentType === "Pipe"
                  ? pipeCostCell(r)
                  : (res && res.rate > 0 ? "\u20b9" + fmt(res.rm_cost, 0) : "\u2014")))))}${warn}</td>` +
      `<td class="num">${r._booked
          ? `<button class="undo-btn" title="Reopen this part so it can be edited and costed again" onclick="reopenRow(${r._id})">reopen</button>` : ""}` +
      `<button class="del-btn" onclick="removeRow(${r._id})">&times;</button></td></tr>`;
  }).join("");

  restoreFocus(keep);
}

/* Match a result back to its input row by the row id the server echoes,
   so two rows sharing a part number can never be confused. */

/* Reopen a part that was saved with a job.

   Saving locks a part so a later calculation cannot nest it again into the
   offcut it just created. That protection is right, but it must not be a
   one-way door: dimensions change, and the RFQ has to be regenerated from the
   corrected figures. Reopening clears the lock and puts the part back into the
   next calculation. */
function reopenRow(id) {
  const r = rows().find(x => x._id === id);
  if (!r) return;
  delete r._booked;
  setTimeout(() => {
    renderTable();
    renderTabCounts();
    renderBomSide();
  }, 0);
  scheduleSave();
}

function resultForRow(r) {
  // Fall back to the stored layout so the parts table still shows each part's
  // cost to someone who has just opened the project. Read-only either way -
  // this only decides what a row displays, never what gets booked.
  const src = lastResult || savedResult;
  if (!src) return null;
  for (const g of (src.groups || []))
    for (const pd of (g.parts_detail || []))
      if (pd.row_id === r._id) return pd;
  for (const pd of (src.purchase_items || []))
    if (pd.row_id === r._id) return pd;
  return null;
}

function renderLegend() {
  const box = document.getElementById("legend");
  const items = schema().legend || [];
  if (!items.length) { box.innerHTML = ""; return; }
  box.innerHTML =
    `<span class="legend-title">Column key</span>` +
    items.map(i =>
      `<span class="legend-item"><b>${i.abbr}</b> \u2014 ${i.label}${i.unit ? ` (${i.unit})` : ""}</span>`
    ).join("") +
    (currentType === "Flange"
      ? `<span class="legend-item"><b>Purchase Cost</b> \u2014 quantity \u00d7 purchase rate per piece; flange is purchased finished</span>`
      : `<span class="legend-item"><b>RM Cost</b> \u2014 this part's allocated plate material cost</span>`);
}

/* Once a result is on screen, keep it in step with the table. Without this a
   part looks "added" but isn't in the numbers until Calculate is pressed
   again, which reads as the app ignoring it. */
let recalcTimer = null;
function scheduleRecalc() {
  if (!lastResult) return;              // nothing shown yet — wait for Calculate
  clearTimeout(recalcTimer);
  recalcTimer = setTimeout(() => calculate(true), 400);
}

function syncRowToBom(id, key, value) {
  // Name, number, quantity and thickness are the BOM's own columns. Typed on
  // the part form they must reach the BOM line, or the two screens show
  // different numbers for one part.
  if (!["part_no", "part_name", "qty", "thk"].includes(key)) return;
  let hit = false;
  (function walk(node) {
    (node || []).forEach(b => {
      if (b._row_id === id) { b[key] = value; hit = true; }
      walk(b.children);
    });
  })(bomTree);
  // Only repaint if the BOM is actually on screen. Rebuilding it behind the
  // part form reflowed the page under the cursor, which is what made a cell
  // shift and swallow the first click.
  if (hit && document.getElementById("bomWork").style.display !== "none") {
    renderBom();
  }
}

function updateCell(id, key, val) {
  const r = rows().find(r => r._id === id);
  if (!r) return;
  const field = schema().fields.find(f => f.key === key);
  r[key] = (field && field.numeric) ? (val === "" ? "" : parseFloat(val)) : val;
  syncRowToBom(id, key, r[key]);

  // Flange dropdowns are linked to the flange database. Changing class
  // refreshes NPS, changing NPS refreshes flange type, and the final
  // selection fills the standard flange dimensions into the row.
  if (currentType === "Flange" && key === "flange_mode") {
    if (!flangeCustom(r)) initializeFlangeRow(r).then(renderTable);   // back to the chart
    else setTimeout(renderTable, 0);                                  // OD / bore / t typed in
  }
  if (currentType === "Flange" && !flangeCustom(r) &&
      ["pressure_class", "nps", "flange_type"].includes(key)) {
    syncFlangeRow(r, key);
  }
  if ((currentType === "Flange" || currentType === "Pipe") && key === "size_std") {
    setTimeout(renderTable, 0);                    // same sizes, different names
  }

  // Pipe follows the same cascade, and material matters here too: kg/m is
  // rescaled by density, so changing the metal changes the weight and, on a
  // per-kg rate, the price.
  if (currentType === "Pipe" &&
      ["length_m", "rate", "rate_basis", "qty"].includes(key)) {
    setTimeout(renderTable, 0);
  }

  if (currentType === "Pipe" && ["nps", "schedule", "material"].includes(key)) {
    syncPipeRow(r, key).then(() => {
      renderTable();
      renderTabCounts();
      scheduleRecalc();
    });
  }

  // Deferred to the next tick on purpose. This runs from an inline onchange,
  // so the browser is still mid-blur on the input that fired it; replacing the
  // tbody underneath that throws "node to be removed is no longer a child of
  // this node". A zero-delay timeout lets the blur finish first.
  setTimeout(() => {
    renderTable();
    renderTabCounts();
  }, 0);
  scheduleRecalc();
  scheduleSave();
}

function setFlangeFieldOptions(key, options) {
  const f = schema().fields.find(x => x.key === key);
  if (f) f.options = (options || []).map(String);
}

async function syncFlangeRow(r, changedKey) {
  try {
    if (changedKey === "pressure_class") {
      const res = await fetch(`/api/flange/nps/${encodeURIComponent(r.pressure_class)}`);
      const data = await res.json();
      if (data.error || !data.nps?.length) return;

      setFlangeFieldOptions("nps", data.nps);
      r.nps = data.nps[0];

      const typeRes = await fetch(
        `/api/flange/types/${encodeURIComponent(r.pressure_class)}/${encodeURIComponent(r.nps)}`
      );
      const typeData = await typeRes.json();
      if (typeData.error || !typeData.types?.length) return;

      setFlangeFieldOptions("flange_type", typeData.types);
      r.flange_type = typeData.types[0];
      await loadFlangeDetail(r);
      return;
    }

    if (changedKey === "nps") {
      const res = await fetch(
        `/api/flange/types/${encodeURIComponent(r.pressure_class)}/${encodeURIComponent(r.nps)}`
      );
      const data = await res.json();
      if (data.error || !data.types?.length) return;

      setFlangeFieldOptions("flange_type", data.types);
      r.flange_type = data.types[0];
      await loadFlangeDetail(r);
      return;
    }

    if (changedKey === "flange_type") {
      await loadFlangeDetail(r);
    }
  } catch (e) {
    console.error("Flange database lookup failed:", e);
  }

  renderTable();
  renderTabCounts();
  scheduleRecalc();
}

async function loadFlangeDetail(r) {
  const res = await fetch("/api/flange/detail", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      pressure_class: r.pressure_class,
      nps: r.nps,
      flange_type: r.flange_type,
    }),
  });
  const data = await res.json();
  if (data.error || !data.dimensions) return;

  // These are the dimensions used by calculator.py for the material blank.
  r.od = data.dimensions.outside_d ?? "";
  r.id_ = data.dimensions.bore ?? "";
  r.thk = data.dimensions.thickness_t ?? "";
}

async function initializeFlangeRow(r) {
  if (flangeCustom(r)) return;                     // nothing to look up
  if (!r.pressure_class) {
    const classes = await (await fetch("/api/flange/classes")).json();
    r.pressure_class = classes.classes?.[0];
  }

  if (!r.pressure_class) return;

  const npsData = await (
    await fetch(`/api/flange/nps/${encodeURIComponent(r.pressure_class)}`)
  ).json();

  setFlangeFieldOptions("nps", npsData.nps);
  if (!r.nps || !npsData.nps?.includes(String(r.nps))) {
    r.nps = npsData.nps?.[0] || "";
  }

  const typeData = await (
    await fetch(
      `/api/flange/types/${encodeURIComponent(r.pressure_class)}/${encodeURIComponent(r.nps)}`
    )
  ).json();

  setFlangeFieldOptions("flange_type", typeData.types);
  if (!r.flange_type || !typeData.types?.includes(r.flange_type)) {
    r.flange_type = typeData.types?.[0] || "";
  }

  await loadFlangeDetail(r);
}


// ---- pipe schedule chart ----------------------------------------------------

/* Same cascade as the flange: choosing a size narrows the schedules, and
   choosing a schedule fills OD, wall and kg/m from ANSI B36.10 / B36.19 so
   nobody types a wall thickness by hand. */
/* Pipe cost is pure arithmetic, so it shows as soon as the numbers are in
   rather than waiting for a round trip. Which arithmetic depends on the basis:
   by the metre, or by weight. */
function pipeCostCell(r) {
  const rate = parseFloat(r.rate);
  const qty = parseInt(r.qty) || 0;
  const len = parseFloat(r.length_m) || 0;
  if (!(rate > 0)) return `<span class="need">rate required</span>`;
  const totalLen = qty * len;
  if (String(r.rate_basis || "").endsWith("/kg")) {
    const w = totalLen * (parseFloat(r.kg_m) || 0);
    if (!w) return `<span class="need">needs length</span>`;
    return `\u20b9${fmt(w * rate, 0)}<span class="sub">${fmt(w, 1)} kg</span>`;
  }
  if (!totalLen) return `<span class="need">needs length</span>`;
  return `\u20b9${fmt(totalLen * rate, 0)}<span class="sub">${fmt(totalLen, 1)} m</span>`;
}

function setPipeFieldOptions(key, options) {
  const f = schema().fields.find(x => x.key === key);
  if (f) f.options = (options || []).map(String);
}

async function loadPipeDetail(r) {
  const res = await fetch("/api/pipe/detail", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nps: r.nps, schedule: r.schedule, material: r.material }),
  });
  const data = await res.json();
  if (data.error) return;
  r.od = data.od_mm;
  r.thk = data.t_mm;
  r.id_ = data.id_mm;
  // kg/m is rescaled server-side for the chosen material — the chart itself is
  // carbon steel, so a stainless pipe must not be priced as one.
  r.kg_m = data.kg_m;
}

async function syncPipeRow(r, changedKey) {
  try {
    if (changedKey === "nps") {
      const d = await (await fetch(
        `/api/pipe/schedules/${encodeURIComponent(r.nps)}`)).json();
      if (d.error || !d.schedules?.length) return;
      setPipeFieldOptions("schedule", d.schedules);
      if (!d.schedules.includes(String(r.schedule))) r.schedule = d.schedules[0];
    }
    await loadPipeDetail(r);
  } catch (e) {
    console.error("Pipe database lookup failed:", e);
  }
}

async function initializePipeRow(r) {
  if (!r.nps) {
    const d = await (await fetch("/api/pipe/nps")).json();
    setPipeFieldOptions("nps", d.nps || []);
    r.nps = (d.nps || [])[0] || "";
  } else {
    const d = await (await fetch("/api/pipe/nps")).json();
    setPipeFieldOptions("nps", d.nps || []);
  }
  if (!r.nps) return;

  const sd = await (await fetch(
    `/api/pipe/schedules/${encodeURIComponent(r.nps)}`)).json();
  setPipeFieldOptions("schedule", sd.schedules || []);
  if (!r.schedule || !(sd.schedules || []).includes(String(r.schedule))) {
    r.schedule = (sd.schedules || [])[0] || "";
  }
  if (!r.rate_basis) r.rate_basis = "\u20b9/m";
  if (!r.length_m) r.length_m = 6;      // 6 m is the usual mill length
  await loadPipeDetail(r);
}

// No longer reachable from the page: parts are created in the BOM and opened
// with "Enter details". Kept because bomToPart() calls the same path to build
// the row a BOM line owns. It must NOT create a BOM line of its own any more -
// the BOM line is what sent us here.
function addRow() {
  const r = blankRow(schema().fields);
  rowsByType[currentType].push(r);
  scheduleSave();
  // A new database-backed row starts empty; fill it from the standard so the
  // user is choosing a size, not typing a wall thickness.
  const init = currentType === "Flange" ? initializeFlangeRow
             : currentType === "Pipe"   ? initializePipeRow : null;
  if (init) {
    init(r).then(() => { renderTable(); renderTabCounts(); scheduleRecalc(); });
  }
  renderTable();
  renderTabCounts();
}

function removeRow(id) {
  rowsByType[currentType] = rowsByType[currentType].filter(r => r._id !== id);
  // Leave the BOM line standing but unlinked. The BOM says what the assembly
  // needs; deleting a dimension sheet does not mean the part stopped being
  // part of the assembly. Pressing Enter details again rebuilds the row.
  (function unlink(node) {
    (node || []).forEach(b => {
      if (b._row_id === id) delete b._row_id;
      unlink(b.children);
    });
  })(bomTree);
  renderBom();
  scheduleSave();
  renderTable();
  renderTabCounts();
  scheduleRecalc();
}

function fmt(n, d = 2) {
  if (!isFinite(n)) return "\u2014";
  return Number(n).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: 0 });
}

// ---- plate settings ----
function plateConfig() {
  if (!document.getElementById("useStd").checked) return null;
  const w = parseFloat(document.getElementById("plateW").value);
  const l = parseFloat(document.getElementById("plateL").value);
  if (!(w > 0 && l > 0)) return null;
  return { width: w, length: l };
}

function syncPlateInputs() {
  const on = document.getElementById("useStd").checked;
  ["plateW", "plateL", "platePreset"].forEach(id =>
    document.getElementById(id).disabled = !on);
  document.getElementById("stdHint").textContent = on
    ? "You pay for whole plates, used or not."
    : "Plate cut to size \u2014 the theoretical minimum.";
}

// ---- calculate ----
// Jobs whose parts have been reopened and are about to be booked again. The
// next commit supersedes them.
let supersedeJobs = new Set();

async function calculate(auto = false) {
  const st = document.getElementById("status");
  const payloadParts = [];

  // A PART ADDED AFTER THE JOB WAS BOOKED MUST NOT NEST ALONE.
  //
  // Committing a job locks its rows, and locked rows are left out of the next
  // calculation. That is right when the next calculation is a different job.
  // It is wrong when a director or team lead asks for one more part on THIS
  // job: the new part nests by itself, buys its own plate, and produces its
  // own RM figure and its own RFQ, while the parts it should have shared a
  // plate with sit locked and invisible. The two sets then disagree about the
  // same job.
  //
  // So: if anything is locked and something new is waiting, say so and offer
  // to bring the locked parts back in. Not silent, because reopening parts
  // that were genuinely already bought would double-order them - only the
  // person knows which case this is.
  if (!auto) {
    const locked = Object.keys(rowsByType)
      .flatMap(t => (rowsByType[t] || []).filter(r => r._booked));
    const fresh = Object.keys(rowsByType)
      .flatMap(t => validRows(t)).length;
    if (locked.length && fresh) {
      const jobs = [...new Set(locked.map(r => r._booked))].join(", ");
      if (confirm(
          `${locked.length} part(s) are locked to job ${jobs} and would be left `
          + `out of this calculation.\n\n`
          + `OK \u2014 bring them back in, so everything on this job nests, `
          + `costs and quotes together.\n`
          + `Cancel \u2014 calculate only the new part(s), on their own plate.`)) {
        // Remember the jobs these parts came from. Committing the new result
        // must REPLACE those jobs, not add to them, or the same part is
        // counted twice in the summary, the RM weight and the RFQ.
        locked.forEach(r => {
          if (r._booked) supersedeJobs.add(r._booked);
          delete r._booked;
        });
        renderTable();
        renderTabCounts();
        renderBomSide();
        scheduleSave();
      }
    }
  }

  // Every tab's rows go in, not just the one on screen — that is what lets a
  // dish end and a shell of the same thickness land on one plate.
  Object.keys(rowsByType).forEach(type => {
    validRows(type).forEach(r => {
      // Strip the UI's own bookkeeping. _calc is the snapshot behind the
      // green/amber row state and _booked marks a produced part; neither is a
      // part dimension and neither belongs in the calculation payload.
      const { _id, _calc, _booked, ...rest } = r;
      const sch = schemaByType[type];
      sch.fields.forEach(f => {
        // Send numbers as numbers. The server normalises too, but a value
        // that left here as the text "3" was what split one plate into two
        // groups of the same material and thickness.
        if (f.numeric) {
          if (rest[f.key] === "" || rest[f.key] === undefined) rest[f.key] = 0;
          else if (typeof rest[f.key] === "string") {
            const n = parseFloat(rest[f.key]);
            if (!Number.isNaN(n)) rest[f.key] = n;
          }
        }
      });
      payloadParts.push({ ...rest, _type: type, _row: _id, rate: parseFloat(rest.rate) || 0 });
    });
  });

  if (!payloadParts.length) {
    st.textContent = "Fill in all fields for at least one part first.";
    return;
  }

  // Remember exactly which rows this run covered. Booking marks these as
  // produced so the next calculation leaves them alone.
  lastPayloadRows = payloadParts.map(p => p._row);
  st.textContent = auto ? "Updating\u2026" : "Calculating\u2026";
  try {
    const res = await fetch("/api/calculate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        parts: payloadParts,
        standard_plate: plateConfig(),
        auto_plate: document.getElementById("autoPlate").checked,
        plate_overrides: plateOverrides,
        combine: document.getElementById("combine").checked,
        use_remnants: document.getElementById("useRemnants").checked,
        // A SEPARATE PASS ONLY WHEN OFFCUT STOCK IS IN PLAY.
        //
        // The server estimates "remnant only" rows apart from the rest, so a
        // later offcut run cannot re-cut parts already allocated to a standard
        // plate. That is right for an offcut run and wrong for everything
        // else: every newly added row landed in this list, so a part added
        // after any calculation was estimated on its own - its own group, its
        // own plate, its own cost - even with Use offcut stock switched off.
        // Same material, same thickness, two plates. That is the split you
        // kept seeing, and why recalculating from scratch "fixed" it: a fresh
        // run has nothing marked as already calculated.
        // THIS FLAG DOES TWO JOBS, AND THEY MUST NOT BE CONFUSED.
        //
        // The server uses it for both "estimate these rows in a separate pass"
        // AND "these rows may consume offcut stock". v46 fixed the first by
        // sending an empty list unless offcut stock was ticked - and in doing
        // so broke the second, because an empty list means NO row is allowed
        // to touch the rack. Offcuts stopped being used at all: a 400x250 pad
        // with a perfectly good 260x420 offcut on the shelf bought a whole
        // 1250x2500 plate, Rs 8,586 instead of nothing.
        //
        // Sending EVERY row when the box is ticked satisfies both. All rows
        // may use the rack, and because none are left out, they are all still
        // estimated together in one pass - so the split that v46 fixed stays
        // fixed. Nothing is consumed by calculating; the rack only changes
        // when a job is booked.
        remnant_only_row_ids: document.getElementById("useRemnants").checked
          ? payloadParts.map(p => p._row)
          : [],
        cutting: {
          rate_per_m: parseFloat(document.getElementById("cutRate").value) || 0,
          pierce_charge: parseFloat(document.getElementById("pierceCharge").value) || 0,
        },
      }),
    });
    const data = await res.json();
    if (data.error) { st.textContent = "Failed: " + data.error; return; }
    lastResult = data;
    savedResult = data;
    renderPlateChoices();
    calculatedRowIds = new Set(payloadParts.map(p => String(p._row)));

    // Snapshot only on a deliberate Calculate, never on the background
    // refresh. The snapshot means "the last figures you actually asked for",
    // so an edit stays visibly an edit until it is confirmed — if the
    // auto-refresh re-snapshotted, a row could never look changed and there
    // would be nothing left to restore to.
    if (!auto) {
      const sent = new Set(payloadParts.map(p => p._row));
      Object.keys(rowsByType).forEach(type => {
        (rowsByType[type] || []).forEach(r => {
          if (sent.has(r._id)) {
            const sch = schemaByType[type];
            const snap = {};
            if (sch) sch.fields.forEach(f => { snap[f.key] = r[f.key]; });
            r._calc = snap;
          }
        });
      });
    }

    st.textContent = "";
    renderTable();
    renderBomSide();
    renderResults(data);
    // Automatic recalculation keeps the current page. Only an explicit
    // Calculate click should navigate to Results. This prevents editing a
    // part, changing a setting, or adding stock from unexpectedly switching
    // pages before the user has finished the input.
    if (!auto) showWork("resultsWork");
  } catch (e) {
    st.textContent = "Failed: " + e.message;
  }
}

/* Plates are a purchase too, and the buying team needs them as a schedule
   rather than as a plate count buried in the totals. Only newly bought plates
   appear — anything cut from the rack was paid for by an earlier job. */
function renderPlatePurchases(data) {
  let box = document.getElementById("platePurchases");
  if (!box) {
    box = document.createElement("div");
    box.id = "platePurchases";
    box.className = "tablewrap";
    document.getElementById("resultsTable").parentElement
      .insertAdjacentElement("afterend", box);
  }
  const rows = data.plates_purchased || [];
  if (!rows.length) { box.innerHTML = ""; return; }
  const qty = rows.reduce((a, r) => a + r.qty, 0);
  const wt = rows.reduce((a, r) => a + r.weight_kg, 0);
  const cost = rows.reduce((a, r) => a + r.cost, 0);
  box.innerHTML = `
    <h3 style="margin:14px 0 8px">Standard Plates Purchased</h3>
    <p class="hint">New plate bought for this run. Plate taken from offcut stock
      is excluded &mdash; it was paid for when it was first bought.</p>
    <table>
      <thead><tr><th>Material</th><th>Thickness</th><th>Plate size</th>
        <th>Plates</th><th>Weight</th><th>Cost</th></tr></thead>
      <tbody>
        ${rows.map(r => `<tr>
          <td>${r.material}</td>
          <td class="num">${fmt(r.thickness, 1)} mm</td>
          <td class="num">${r.size}</td>
          <td class="num strong">${r.qty}</td>
          <td class="num">${fmt(r.weight_kg, 1)} kg</td>
          <td class="num strong">\u20b9${fmt(r.cost, 0)}</td>
        </tr>`).join("")}
        <tr class="total"><td colspan="3">Total plates purchased</td>
          <td class="num strong">${qty}</td>
          <td class="num">${fmt(wt, 1)} kg</td>
          <td class="num strong">\u20b9${fmt(cost, 0)}</td></tr>
      </tbody></table>`;
}

function renderPurchasedItems(data) {
  let box = document.getElementById("purchaseResults");
  if (!box) {
    box = document.createElement("div");
    box.id = "purchaseResults";
    box.className = "tablewrap";
    const resultTable = document.getElementById("resultsTable").parentElement;
    resultTable.insertAdjacentElement("afterend", box);
  }

  const items = data.purchase_items || [];
  if (!items.length) {
    box.innerHTML = "";
    box.style.display = "none";
    return;
  }

  const total = items.reduce((sum, x) => sum + (x.purchase_cost || 0), 0);
  box.style.display = "";

  // One table for every bought-in type. A flange is priced per piece, a pipe
  // per metre or per kilogram, so the Size and Rate columns describe the item
  // rather than assuming everything is a flange.
  const describe = x => x.component_type === "Pipe"
    ? `NPS ${x.nps}&Prime; &middot; SCH ${x.schedule}`
    : `Class ${x.pressure_class} &middot; ${x.nps}&Prime; &middot; ${x.flange_type}`;

  const measure = x => x.component_type === "Pipe"
    ? `${fmt(x.total_length_m, 1)} m<span class="sub">${fmt(x.weight_kg, 1)} kg &middot; ${fmt(x.kg_m, 2)} kg/m</span>`
    : `${x.qty} pc<span class="sub">OD ${fmt(x.od, 1)} &times; ${fmt(x.thickness, 1)} mm</span>`;

  box.innerHTML = `
    <h3 style="margin:14px 0 8px">Purchased Components</h3>
    <p class="hint">Bought as finished items. They take no part in plate nesting
      and carry no raw-material or cutting charge.</p>
    <table>
      <thead><tr>
        <th>Part</th><th>Component</th><th>Size</th><th>Material</th>
        <th>Qty</th><th>Measure</th><th>Rate</th><th>Purchase Cost</th>
      </tr></thead>
      <tbody>
        ${items.map(x => `<tr>
          <td>${x.part_no ? `<span class="sub">${x.part_no}</span> ` : ""}${x.label}</td>
          <td><span class="ctag">${x.component_type}</span></td>
          <td>${describe(x)}</td>
          <td>${x.material}</td>
          <td class="num">${x.qty}</td>
          <td class="num">${measure(x)}</td>
          <td class="num">\u20b9${fmt(x.rate, 0)}<span class="sub">${x.unit || "Rs/pc"}</span></td>
          <td class="num strong">\u20b9${fmt(x.purchase_cost, 0)}</td>
        </tr>`).join("")}
        <tr class="total">
          <td colspan="7">Purchased total</td>
          <td class="num strong">\u20b9${fmt(total, 0)}</td>
        </tr>
      </tbody>
    </table>`;
}

function renderResults(data) {
  document.getElementById("result").style.display = "";
  const t = data.totals;
  const std = data.mode === "standard";

  const plateSummary = data.groups.length
    ? `${data.groups.length} plate group(s) \u2014 ` +
      (std ? `${t.plates_required} standard plate(s), ` : "") +
      `${fmt(t.plate_area_m2, 2)} m\u00b2 plate \u2014 ${fmt(t.used_area_m2, 2)} m\u00b2 used, ` +
      `${fmt(t.free_area_m2, 2)} m\u00b2 free, ${fmt(t.plate_weight_kg)} kg RM, ` +
      `${fmt(t.total_scrap_kg)} kg scrap (${fmt(t.scrap_pct, 1)}%)`
    : "No plate material required";
  const purchaseSummary = (t.purchase_cost || 0) > 0
    ? ` \u2014 purchased components \u20b9${fmt(t.purchase_cost, 0)}` : "";
  const plateCostSummary = t.plate_cost > 0
    ? ` \u2014 plate parts \u20b9${fmt(t.parts_cost, 0)}, `
      + `cut-to-size \u20b9${fmt(t.required_cost, 0)}, stock plate \u20b9${fmt(t.plate_cost, 0)}, `
      + `cutting \u20b9${fmt(t.cut_cost, 0)}`
    : "";
  document.getElementById("stat").textContent =
    plateSummary + plateCostSummary + purchaseSummary + ` \u2014 total job cost \u20b9${fmt(t.total_cost || 0, 0)}`;

  const flags = document.getElementById("flags");
  if (std && data.oversize.length) {
    flags.innerHTML = `<div class="flag bad"><b>${data.oversize.length} blank(s) do not fit the ${data.standard_plate.width} \u00d7 ${data.standard_plate.length} mm plate</b>` +
      data.oversize.map(o =>
        `<div>${o.component_type} \u2014 ${o.part_no ? o.part_no + " " : ""}${o.label}: blank ${o.blank_size}</div>`).join("") +
      `<div class="note">Needs a bigger plate or a seamed build. Still costed, so the total isn't understated.</div></div>`;
  } else if (std && (data.plate_rows || []).length) {
    flags.innerHTML = `<div class="flag ok">All blanks fit the ${data.standard_plate.width} \u00d7 ${data.standard_plate.length} mm standard plate.</div>`;
  } else {
    flags.innerHTML = "";
  }

  // One row per PLATE, not per group. Each plate is a self-contained costing
  // unit: its own weight, scrap, area and cost.
  document.querySelector("#resultsTable thead").innerHTML = `<tr>
      <th>Material</th><th>t (mm)</th>
      <th>${std ? "Plate" : "Plate size"}</th><th>Parts on this plate</th>
      <th>Parts cost</th><th>Plate required</th><th>Required cost</th>
      <th>Plate wt (kg)</th><th>Finished wt (kg)</th><th>Scrap wt (kg)</th>
      <th>Used area (m&sup2;)</th><th>Free area (m&sup2;)</th>
      <th>Util %</th><th>Plate cost</th>
      <th>Cut length</th><th>Pierces</th><th>Cut cost</th><th>Job cost</th></tr>`;

  const rows = data.plate_rows || [];
  const resultsWrap = document.querySelector("#resultsTable").parentElement;
  resultsWrap.style.display = rows.length ? "" : "none";
  const tbody = document.querySelector("#resultsTable tbody");
  tbody.innerHTML = rows.map(pr => {
    // group_idx ties a plate row back to the exact group that produced it.
    // Matching by material+thickness alone breaks once a locked group (parts
    // already cut) and a remnant group (newly added parts) share the same
    // material and thickness — .find() would silently grab the wrong one.
    const grp = data.groups[pr.group_idx] || {};
    const label = !pr.on_plate
      ? `<b class="warn">Does not fit</b>`
      : (pr.source === "remnant"
          ? `<b class="fromrack">From rack</b><span class="sub">${pr.remnant_id}</span>`
          : (std ? `<b>Plate ${pr.plate} of ${grp.plates_required}</b>` : `<b>${pr.spec}</b>`));

    const detail = pr.items.map(it => `
      <tr>
        <td>${it.component_type ? `<span class="ctag">${it.component_type}</span>` : ""}${it.label}${it.qty > 1 ? ` &times; ${it.qty}` : ""}</td>
        <td class="num">${it.blank_size}</td>
        <td class="num">${fmt(it.blank_weight_kg)} kg</td>
        <td class="num">${it.rate > 0 ? "\u20b9" + fmt(it.rate, 0) : "\u2014"}</td>
        <td class="num">${it.rate > 0 ? "\u20b9" + fmt(it.rm_cost, 0) : "\u2014"}</td>
      </tr>`).join("");

    return `
    <tr${!pr.on_plate ? ' class="off"' : ""}>
      <td>${pr.material}</td>
      <td class="num">${pr.thickness}</td>
      <td>${label}<span class="sub">${std ? pr.spec : "cut to size"}</span></td>
      <td>
        <table style="margin:2px 0;">
          <thead><tr>
            <th style="font-size:9.5px;">Part</th>
            <th style="font-size:9.5px;">Blank size</th>
            <th style="font-size:9.5px;">Blank weight</th>
            <th style="font-size:9.5px;">Rate</th>
            <th style="font-size:9.5px;">RM cost</th>
          </tr></thead>
          <tbody>${detail}</tbody>
        </table>
      </td>
      <td class="num">${pr.parts_cost > 0 ? "\u20b9" + fmt(pr.parts_cost, 0) : "\u2014"}</td>
      <td class="num">${pr.required_spec}
        <span class="sub">${fmt(pr.required_area_m2, 2)} m\u00b2 \u00b7 ${fmt(pr.required_weight_kg)} kg</span>
        ${std ? (pr.on_plate ? '<span class="fit yes">fits stock</span>'
                             : '<span class="fit no">too big</span>') : ""}</td>
      <td class="num">${pr.required_cost > 0 ? "\u20b9" + fmt(pr.required_cost, 0) : "\u2014"}</td>
      <td class="num">${fmt(pr.plate_weight_kg)}</td>
      <td class="num">${fmt(pr.net_weight_kg)}</td>
      <td class="num">${fmt(pr.scrap_kg)}</td>
      <td class="num">${fmt(pr.used_area_m2, 2)}<span class="sub">of ${fmt(pr.plate_area_m2, 2)}</span></td>
      <td class="num">${pr.on_plate ? fmt(pr.free_area_m2, 2) : "\u2014"}</td>
      <td class="num">${pr.on_plate ? fmt(pr.utilisation_pct, 1) + "%" : "\u2014"}</td>
      <td class="num strong">${pr.plate_cost > 0 ? "\u20b9" + fmt(pr.plate_cost, 0) : "\u2014"}</td>
      <td class="num">${fmt(pr.cut_length_m, 2)} m</td>
      <td class="num">${pr.pierces}</td>
      <td class="num">${pr.cut_cost > 0 ? "\u20b9" + fmt(pr.cut_cost, 0) : "\u2014"}</td>
      <td class="num strong">${pr.total_cost > 0 ? "\u20b9" + fmt(pr.total_cost, 0) : "\u2014"}</td>
    </tr>`;
  }).join("") + `
    <tr class="total">
      <td colspan="4">Total${std ? ` \u2014 ${t.plates_required} plate(s)` : ""}</td>
      <td class="num">${t.parts_cost > 0 ? "\u20b9" + fmt(t.parts_cost, 0) : "\u2014"}</td>
      <td class="num">${fmt(t.required_area_m2, 2)} m\u00b2<span class="sub">${fmt(t.required_weight_kg)} kg</span></td>
      <td class="num">${t.required_cost > 0 ? "\u20b9" + fmt(t.required_cost, 0) : "\u2014"}</td>
      <td class="num">${fmt(t.plate_weight_kg)}</td>
      <td class="num">${fmt(t.net_weight_kg)}</td>
      <td class="num">${fmt(t.total_scrap_kg)}</td>
      <td class="num">${fmt(t.used_area_m2, 2)}<span class="sub">of ${fmt(t.plate_area_m2, 2)}</span></td>
      <td class="num">${fmt(t.free_area_m2, 2)}</td>
      <td class="num">${fmt(t.utilisation_pct, 1)}%</td>
      <td class="num strong">${t.plate_cost > 0 ? "\u20b9" + fmt(t.plate_cost, 0) : "\u2014"}</td>
      <td class="num">${fmt(t.cut_length_m, 2)} m</td>
      <td class="num">${t.pierces}</td>
      <td class="num">${t.cut_cost > 0 ? "\u20b9" + fmt(t.cut_cost, 0) : "\u2014"}</td>
      <td class="num strong">${t.total_cost > 0 ? "\u20b9" + fmt(t.total_cost, 0) : "\u2014"}</td>
    </tr>`;

  renderPlatePurchases(data);
  renderPurchasedItems(data);

  document.getElementById("gapNote").textContent =
    `Gap between blanks: ${CUT_GAP_FACTOR} \u00d7 thickness. Flanges are purchased separately and do not use plate, RM cost or cutting.`;

  renderDiagrams(data);

  const cb = document.getElementById("commitBtn");
  const nUse = (data.remnants_used || []).length;
  const nNew = (data.harvest || []).length;
  // Booking is not only about offcut — it is what records the parts in the
  // project history. A job of purchased components moves no material at all but
  // still has to be bookable, or it would never reach the summary.
  const nLines = jobLines(data).length;
  cb.disabled = !(nUse || nNew || nLines);
  const bits = [];
  if (nUse) bits.push(`use up ${nUse} piece(s) from the rack`);
  if (nNew) bits.push(`add ${nNew} new leftover piece(s)`);
  if (nLines) bits.push(`record ${nLines} part(s) as made`);
  document.getElementById("commitMsg").textContent = cb.disabled
    ? "Nothing to save \u2014 no parts and no leftover material."
    : "This will " + bits.join(", ") + ".";

  autoStock(data);
}

// The leftover of whatever has just been calculated goes straight into the
// Store (Raw Material) of the Project Enquiry tool. Recalculating replaces the
// same project's previous leftover rather than adding to it; booking the job
// turns those rows into ordinary stock.
async function autoStock(data) {
  try {
    const r = await (await fetch("/api/remnants/auto-stock", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project: currentProject ? currentProject.id : null,
        harvest: data.harvest || [],
        note: currentProject ? (currentProject.number || currentProject.name || "") : "",
      }),
    })).json();
    const el = document.getElementById("commitMsg");
    if (el && r && r.stocked) {
      el.textContent += ` ${r.stocked} leftover piece(s) are already in the Store.`;
    }
    if (typeof loadRack === "function") loadRack();
  } catch (e) { /* the Store is optional — never block a calculation over it */ }
}

// ---- cutting-layout diagrams ----
// Draws each plate as an SVG using the exact x/y/shape data the packing
// algorithm computed — this is a picture of what the tool calculated, not
// a substitute for real nesting software's output (see the note below the
// diagrams). Circles/rings are drawn as their true shape, not just their
// bounding box, using the shape data calculator.py now carries through.

const DIAGRAM_PALETTE = ["#cfe0ef", "#f6dcae", "#cdeac4", "#e9d0e6", "#f4c9c9", "#c8e6ea"];

function componentColor(type) {
  let hash = 0;
  for (let i = 0; i < (type || "").length; i++) hash = (hash * 31 + type.charCodeAt(i)) >>> 0;
  return DIAGRAM_PALETTE[hash % DIAGRAM_PALETTE.length];
}

function escapeXml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;"
  }[c]));
}

function pieceSvg(piece) {
  const cx = piece.x + piece.w / 2, cy = piece.y + piece.l / 2;
  const fill = componentColor(piece.component_type);
  const fs = Math.max(Math.min(piece.w, piece.l) / 7, 9);
  let shape;
  if (piece.shape_kind === "circle" || piece.shape_kind === "ring") {
    const r = piece.shape_d / 2;
    shape = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${fill}" stroke="#1f4e79" stroke-width="1.5" vector-effect="non-scaling-stroke"/>`;
    if (piece.shape_kind === "ring" && piece.shape_di) {
      shape += `<circle cx="${cx}" cy="${cy}" r="${piece.shape_di / 2}" fill="#f4f6f8" stroke="#1f4e79" stroke-width="1" vector-effect="non-scaling-stroke"/>`;
    }
  } else {
    shape = `<rect x="${piece.x}" y="${piece.y}" width="${piece.w}" height="${piece.l}" fill="${fill}" stroke="#1f4e79" stroke-width="1.5" vector-effect="non-scaling-stroke"/>`;
  }
  const label = escapeXml(piece.label.length > 14 ? piece.label.slice(0, 13) + "\u2026" : piece.label);
  const text = `<text x="${cx}" y="${cy}" font-size="${fs}" text-anchor="middle" dominant-baseline="middle" fill="#1a1d21">${label}</text>`;
  return shape + text;
}

/* Twenty plates cut the same way are one drawing, not twenty. Plates whose
   layout is identical (same plate size, same pieces in the same places) are
   collapsed into one block labelled "Plate 1 x 12", with the plate numbers it
   stands for kept in the tooltip. */
function layoutKey(pr) {
  return [Math.round(pr.plate_w_mm), Math.round(pr.plate_l_mm)].join("x") + "|" +
    (pr.layout || []).map(p => [p.label, Math.round(p.x), Math.round(p.y),
      Math.round(p.w), Math.round(p.l), p.shape_kind || ""].join(",")).join(";");
}
function groupPlates(rows) {
  const seen = new Map();
  rows.forEach(pr => {
    const k = layoutKey(pr);
    if (seen.has(k)) seen.get(k).plates.push(pr.plate);
    else seen.set(k, { pr: pr, plates: [pr.plate] });
  });
  return [...seen.values()];
}
function svgForPlate(grp) {
  const pr = grp.pr || grp, n = (grp.plates || []).length || 1;
  const w = pr.plate_w_mm, l = pr.plate_l_mm;
  const pieces = pr.layout.map(pieceSvg).join("");
  const nums = (grp.plates || []).filter(x => x != null);
  const title = (pr.plate ? `Plate ${pr.plate}` : "Plate") +
    (n > 1 ? ` <span class="xn" title="Plates ${nums.join(", ")}">&times; ${n}</span>` : "");
  return `
    <div class="diagram-block"${n > 1 ? ` title="${n} plates cut this way: ${nums.join(", ")}"` : ""}>
      <div class="diagram-title">${title}<span class="sub">${fmt(w, 0)} &times; ${fmt(l, 0)} mm</span></div>
      <svg class="diagram-svg" viewBox="0 0 ${w} ${l}" preserveAspectRatio="xMidYMid meet">
        <rect x="0" y="0" width="${w}" height="${l}" fill="none" stroke="#1f4e79" stroke-width="6" vector-effect="non-scaling-stroke"/>
        ${pieces}
      </svg>
    </div>`;
}

function renderDiagrams(data) {
  const box = document.getElementById("diagrams");
  const rows = (data.plate_rows || []).filter(pr => pr.layout && pr.layout.length && pr.plate_w_mm);
  if (!rows.length) { box.innerHTML = ""; return; }
  const groups = groupPlates(rows);
  const repeats = rows.length - groups.length;
  box.innerHTML =
    `<div class="diagrams-title">Cutting layout (estimate)` +
    (repeats > 0 ? `<span class="sub"> \u2014 ${rows.length} plates, ${groups.length} different layout` +
      `${groups.length === 1 ? "" : "s"}; the repeats are shown once with their count</span>` : "") +
    `</div>` +
    `<div class="diagrams-grid">${groups.map(svgForPlate).join("")}</div>` +
    `<p class="hint">Shelf-packed layout, drawn to scale with true part shapes \u2014 not real ` +
    `nesting. A real nesting job can usually place parts more tightly, especially round or ` +
    `irregular ones, so treat this as the layout the plate/cost figures above are based on, ` +
    `not a cutting-ready drawing.</p>`;
}



// ---- offcut stock ----------------------------------------------------------

/* Estimating never touches the rack. Booking is an explicit, separate action,
   otherwise every what-if the user tried would quietly consume stock. */
async function loadRack() {
  const d = await (await fetch("/api/remnants")).json();
  const tb = document.querySelector("#rackTable tbody");
  document.getElementById("rackTotal").textContent =
    d.remnants.length
      ? `${d.remnants.length} piece(s) \u00b7 ${fmt(d.total_area_m2, 2)} m\u00b2 on the rack`
      : "Rack is empty";

  if (!d.remnants.length) {
    tb.innerHTML = `<tr><td colspan="8" class="empty">Nothing racked yet.
      Save a finished job, or add material by hand below.</td></tr>`;
    return;
  }
  tb.innerHTML = d.remnants.map(r => `
    <tr>
      <td><code>${r.id}</code></td>
      <td>${r.material}</td>
      <td class="num">${r.thickness}</td>
      <td class="num">${fmt(r.width, 0)} \u00d7 ${fmt(r.length, 0)}</td>
      <td class="num">${fmt(r.area_m2, 2)}</td>
      <td>${r.source || "\u2014"}</td>
      <td>${r.created || "\u2014"}</td>
      <td class="num"><button class="del-btn" onclick="dropRemnant('${r.id}')">&times;</button></td>
    </tr>`).join("");
}

async function dropRemnant(id) {
  const res = await fetch("/api/remnants/remove", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
  const data = await res.json();
  if (!data.removed) return;
  await loadRack();
  scheduleRecalc();
}

/* Flatten the result into one ledger line per part. Plate lines carry their
   material and cutting cost; purchased lines carry a purchase cost and no
   weight, since they never touched a plate. */
function jobLines(res) {
  const out = [];
  (res.plate_rows || []).forEach(pr => {
    (pr.items || []).forEach(it => {
      out.push({
        component_type: it.component_type || "",
        part_no: it.part_no || "",
        label: it.label || "",
        spec: it.blank_size || "",
        material: pr.material || "",
        thickness: pr.thickness || null,
        qty: it.qty || 0,
        procurement: "plate",
        weight_kg: it.blank_weight_kg || 0,
        material_cost: it.rm_cost || 0,
        cut_cost: it.cut_cost || 0,
        purchase_cost: 0,
        source_plate: pr.on_plate === false ? "oversize"
                      : (pr.source === "remnant" ? (pr.remnant_id || "rack") : "new plate"),
      });
    });
  });
  (res.purchase_items || res.purchased || []).forEach(x => {
    const isPipe = x.component_type === "Pipe";
    out.push({
      component_type: x.component_type || "",
      part_no: x.part_no || "",
      label: x.label || "",
      spec: x.spec || (isPipe
              ? `NPS ${x.nps}" SCH ${x.schedule} \u00d7 ${fmt(x.total_length_m, 1)} m`
              : `Class ${x.pressure_class} ${x.nps}" ${x.flange_type}`),
      material: x.material || "",
      thickness: isPipe ? (x.thickness || null) : null,
      // Pipe is ordered by nominal size, schedule and length. Keeping only the
      // formatted spec string meant the RFQ had nothing to print in those
      // fields and showed an empty NPS and a zero length.
      nps: isPipe ? (x.nps || "") : "",
      schedule: isPipe ? (x.schedule || "") : "",
      length_m: isPipe ? (x.length_m || 0) : 0,
      total_length_m: isPipe ? (x.total_length_m || 0) : 0,
      // Bar is quoted by stock diameter and stock length, so the drawn
      // diameter and the cut length of ONE piece both have to survive into the
      // ledger - the RFQ works the bar count out from them. Custom carries its
      // free-text dimensions and remarks for the same reason: they are the
      // only description a supplier will get.
      bar_dia: x.component_type === "Round Bar" ? (x.od || 0) : 0,
      bar_piece_mm: x.component_type === "Round Bar"
        ? Math.round((x.length_m || 0) * 1000) : 0,
      dimensions: x.component_type === "Custom" ? (x.size || "") : "",
      remarks: x.component_type === "Custom" ? (x.remarks || "") : "",
      qty: x.qty || 0,
      procurement: "purchased",
      // Pipe has a real weight even though it never saw a plate; recording it
      // keeps the project's total tonnage honest.
      weight_kg: (isPipe || x.component_type === "Round Bar")
        ? (x.weight_kg || 0) : 0,
      material_cost: 0,
      cut_cost: 0,
      purchase_cost: x.cost || 0,
      source_plate: "bought in",
    });
  });
  return out;
}

function jobTotals(res) {
  const t = res.totals || {};
  return {
    plate_cost: t.plate_cost || 0,
    cut_cost: t.cut_cost || 0,
    purchase_cost: t.purchase_cost || 0,
    total_cost: (t.total_cost || 0) + (t.purchase_cost || 0),
    plate_weight_kg: t.plate_weight_kg || 0,
    plates_bought: t.plates_required || 0,
    // The plate schedule has to be BOOKED, not just displayed. An RFQ is
    // raised against a finished project, by which time the on-screen result
    // is long gone — without this, section 2 of the RFQ comes out empty for
    // exactly the projects that need it most.
    plates: res.plates_purchased || [],
  };
}

async function commitJob() {
  if (!lastResult) return;
  const btn = document.getElementById("commitBtn");
  btn.disabled = true;
  const res = await (await fetch("/api/remnants/commit", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      source: `Job ${new Date().toISOString().slice(0, 10)}`,
      remnants_used: lastResult.remnants_used || [],
      harvest: lastResult.harvest || [],
      project: currentProject ? currentProject.id : null,
      lines: jobLines(lastResult),
      job_totals: jobTotals(lastResult),
      supersedes: [...supersedeJobs],
    }),
  })).json();
  // Mark the parts this job produced. Without this they stay live in the
  // table and the NEXT calculation nests them again — into the very offcut
  // they just created. That is the whole bug.
  // The ledger's own id (J-0007), not a date. Two commits on one day shared a
  // date string, so a row could never be traced back to the job that booked
  // it - which is why superseding was impossible before.
  const job = (res.job && res.job.id) || `Job ${new Date().toISOString().slice(0, 10)}`;
  let marked = 0;
  Object.keys(rowsByType).forEach(type => {
    (rowsByType[type] || []).forEach(r => {
      if (lastPayloadRows.includes(r._id) && !r._booked) { r._booked = job; marked++; }
    });
  });
  // The result on screen describes a job that is now closed. Clear it so the
  // next Calculate starts from only what is still outstanding.
  lastResult = null;           // cannot be booked twice
  // savedResult is deliberately NOT cleared. The job is booked, but the layout
  // it was booked from is exactly what an approver needs to look at.
  lastPayloadRows = [];
  supersedeJobs = new Set();   // consumed by this commit

  document.getElementById("commitMsg").textContent =
    `Saved \u2014 used up ${res.consumed.length} piece(s), added ` +
    `${res.added.length} new one(s), and marked ${marked} part(s) as made.`;
  renderTable();
  renderTabCounts();
  await loadRack();
  await loadSummary();
  scheduleSave();
}

/* Parts already made stay in the table, greyed out, as a record. This clears
   them off the screen once they are no longer wanted there. */
function clearBooked() {
  let n = 0;
  Object.keys(rowsByType).forEach(type => {
    const before = rowsByType[type].length;
    rowsByType[type] = rowsByType[type].filter(r => !r._booked);
    n += before - rowsByType[type].length;
  });
  renderTable();
  renderTabCounts();
  document.getElementById("commitMsg").textContent =
    `Hid ${n} part(s) that were already made.`;
}async function addRemnant() {
  const msg = document.getElementById("remMsg");
  const body = {
    material: document.getElementById("remMat").value,
    thickness: parseFloat(document.getElementById("remThk").value),
    width: parseFloat(document.getElementById("remW").value),
    length: parseFloat(document.getElementById("remL").value),
    source: "added by hand",
  };
  if (!(body.thickness > 0 && body.width > 0 && body.length > 0)) {
    msg.textContent = "Thickness, width and length are all needed.";
    return;
  }
  const res = await (await fetch("/api/remnants/add", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })).json();
  msg.textContent = res.error ? res.error : `Added ${res.added[0].id}.`;
  if (!res.error) {
    ["remThk", "remW", "remL"].forEach(id => document.getElementById(id).value = "");
    await loadRack();
    scheduleRecalc();
  }
}

// ---- project summary -------------------------------------------------------

/* The running record across every booked job, plus whatever is estimated but
   not yet booked. Read from the server, not from memory, so it survives a
   refresh — the rack persists, and a history that didn't would disagree with it. */
let lastSummary = null;   // so the clear-history warning can name a count

async function loadSummary() {
  let d;
  try {
    if (!currentProject) { d = { jobs: [], summary: {} }; }
    else d = await (await fetch(`/api/jobs?project=${currentProject.id}`)).json();
  } catch (e) {
    return;
  }
  const s = d.summary || {};
  lastSummary = s;
  const jobs = d.jobs || [];

  // Anything on screen but not yet booked is pending, and shown as such rather
  // than mixed into the produced figures.
  const pend = lastResult ? jobLines(lastResult) : [];
  const pendMade = pend.filter(l => l.procurement === "plate")
                       .reduce((a, l) => a + l.qty, 0);
  const pendBought = pend.filter(l => l.procurement === "purchased")
                         .reduce((a, l) => a + l.qty, 0);
  const pendCost = pend.reduce((a, l) =>
    a + (l.material_cost || 0) + (l.cut_cost || 0) + (l.purchase_cost || 0), 0);

  document.getElementById("sumMeta").textContent =
    `${s.jobs || 0} job(s) saved${pend.length ? ` \u00b7 ${pend.length} line(s) pending` : ""}`;

  const kpi = (v, k, cls) =>
    `<div class="kpi${cls ? " " + cls : ""}"><div class="v">${v}</div><div class="k">${k}</div></div>`;
  document.getElementById("sumKpis").innerHTML =
    kpi(s.made_qty || 0, "Parts made from plate") +
    kpi(s.bought_qty || 0, "Parts purchased") +
    kpi(fmt(s.plate_weight_kg || 0, 1) + " kg", "Plate consumed") +
    kpi(s.plates_bought || 0, "Standard plates bought") +
    kpi("\u20b9" + fmt(s.plate_cost || 0, 0), "Plate material") +
    kpi("\u20b9" + fmt(s.cut_cost || 0, 0), "Cutting") +
    kpi("\u20b9" + fmt(s.purchase_cost || 0, 0), "Purchased") +
    kpi("\u20b9" + fmt(s.grand_total || 0, 0), "Total so far", "big") +
    (pend.length
      ? kpi("\u20b9" + fmt(pendCost, 0),
            `Pending (${pendMade} made, ${pendBought} bought)`, "warn")
      : "");

  // by component
  const bt = s.by_type || {};
  const tb = document.querySelector("#sumTypeTable tbody");
  const keys = Object.keys(bt);
  tb.innerHTML = keys.length
    ? keys.map(k => `
        <tr>
          <td><span class="ctag">${k}</span></td>
          <td class="num">${bt[k].made}</td>
          <td class="num">${bt[k].bought}</td>
          <td class="num">${bt[k].made + bt[k].bought}</td>
          <td class="num strong">\u20b9${fmt(bt[k].cost, 0)}</td>
        </tr>`).join("") +
      `<tr class="total"><td>All components</td>
        <td class="num">${s.made_qty}</td><td class="num">${s.bought_qty}</td>
        <td class="num">${s.total_qty}</td>
        <td class="num strong">\u20b9${fmt(s.parts_cost || 0, 0)}</td></tr>`
    : `<tr><td colspan="5" class="empty">Nothing saved yet.</td></tr>`;

  // The per-component figures are on the PARTS basis (each part's own
  // material plus its cutting). The headline total is on the PLATE basis —
  // whole plates bought, offcut included. Spelling out the gap beats a total
  // row that silently fails to add up.
  const gap = (s.grand_total || 0) - (s.parts_cost || 0);
  document.getElementById("sumBasis").innerHTML = s.jobs
    ? `Component costs above are each part's own material plus its cutting: `
      + `<b>\u20b9${fmt(s.parts_cost || 0, 0)}</b>. `
      + `Whole plates actually bought came to <b>\u20b9${fmt(s.grand_total || 0, 0)}</b> `
      + `\u2014 the <b>\u20b9${fmt(gap, 0)}</b> difference is offcut you paid for. `
      + `Quote from the plate figure.`
    : "";

  // part by part, oldest job first, pending last
  const lt = document.querySelector("#sumLineTable tbody");
  const rows = [];
  jobs.forEach(j => (j.lines || []).forEach(l => rows.push({ job: j.id, pending: false, l })));
  pend.forEach(l => rows.push({ job: "pending", pending: true, l }));

  lt.innerHTML = rows.length
    ? rows.map(({ job, pending, l }) => {
        const cost = (l.material_cost || 0) + (l.cut_cost || 0) + (l.purchase_cost || 0);
        return `
        <tr${pending ? ' class="incomplete"' : ""}>
          <td>${pending ? '<span class="pendtag">pending</span>' : `<code>${job}</code>`}</td>
          <td><span class="ctag">${l.component_type}</span></td>
          <td>${l.part_no || "\u2014"}</td>
          <td>${l.label}${l.spec ? ` <span class="sub">${l.spec}</span>` : ""}</td>
          <td>${l.material || "\u2014"}</td>
          <td class="num">${l.thickness != null ? l.thickness : "\u2014"}</td>
          <td class="num">${l.qty}</td>
          <td>${l.procurement === "purchased"
                ? '<span class="fit no">bought</span>'
                : `<span class="fit yes">${l.source_plate}</span>`}</td>
          <td class="num">${l.weight_kg ? fmt(l.weight_kg, 2) : "\u2014"}</td>
          <td class="num strong">${cost ? "\u20b9" + fmt(cost, 0) : "\u2014"}</td>
        </tr>`;
      }).join("")
    : `<tr><td colspan="10" class="empty">No parts recorded yet. Save a finished job to start the record.</td></tr>`;
}

async function clearJobs() {
  if (!currentProject) return;
  // This used to wipe the ledger on a single click, with no confirmation and
  // no way back. Spell out what goes and what stays, because the two are not
  // the same thing and the difference is physical steel.
  const n = (lastSummary && lastSummary.jobs) || 0;
  if (!n) {
    document.getElementById("sumMsg").textContent = "There is no job history to clear.";
    return;
  }
  if (!confirm(
      `Clear the job history for this project?\n\n`
      + `${n} booked job(s) will be set aside and the summary will read zero.\n\n`
      + `What does NOT change:\n`
      + `  \u2022 offcut stock \u2014 steel that was cut stays cut, and offcuts `
      + `those jobs produced stay on the shelf\n`
      + `  \u2022 job numbers \u2014 the next job carries on from where it left `
      + `off, so an old J-number is never handed out twice\n\n`
      + `The cleared jobs are kept in the project file, but there is no undo `
      + `button in the app.`)) {
    return;
  }
  const d = await (await fetch("/api/jobs/clear", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project: currentProject.id }),
  })).json();
  if (d.error) { document.getElementById("sumMsg").textContent = d.error; return; }
  document.getElementById("sumMsg").textContent =
    `${d.cleared} job(s) set aside${d.job_ids && d.job_ids.length
      ? " (" + d.job_ids.join(", ") + ")" : ""}. `
    + `Next job will still be J-${String(d.next_job_id).padStart(4, "0")}. `
    + `${d.rack_pieces} offcut piece(s) remain in the rack, untouched.`;
  await loadSummary();
}


// ---- projects ---------------------------------------------------------------

/* A project owns the parts, the settings and the job ledger, and is saved as
   you work so closing the window loses nothing.

   It does NOT own the offcut rack. The rack is shared by every project on
   purpose: a remnant left over from last month's job is still usable steel
   this month, and locking it inside the project that produced it would strand
   it. Each remnant still records the job that created it, so the trail holds. */
let currentProject = null;
let saveTimer = null;

let bomTree = [];

function rfqWrittenTable(r) {
  return `<table class="grid" style="margin-top:12px">
    <thead><tr><th>Vendor</th><th>RFQ No.</th><th>File</th></tr></thead>
    <tbody>${r.written.map(w => `<tr><td>${escapeXml(w.vendor)}</td>
      <td><code>${escapeXml(w.rfq_no)}</code></td>
      <td>${escapeXml(w.file)}</td></tr>`).join("")}</tbody></table>
    <p class="hint">Saved in <b>${escapeXml(r.dir || "")}</b></p>`;
}

/* ---- RFQs issued -------------------------------------------------------
   The app keeps its own copy of every RFQ regardless of what the user did
   with the Save dialog, so this list is complete even for documents nobody
   saved. "Save a copy" hands one back out to a chosen location.            */

async function loadRfqArchive() {
  const box = document.getElementById("rfqArchive");
  if (!box) return;
  const q = currentProject ? `?project=${encodeURIComponent(currentProject.id)}` : "";
  let d;
  try {
    d = await (await fetch(`/api/rfq-archive${q}`)).json();
  } catch (e) { return; }

  const rows = d.rfqs || [];
  document.getElementById("rfqArchCount").textContent =
    rows.length ? `${rows.length} on this project` : "";
  if (!rows.length) {
    box.innerHTML = `<p class="hint">Nothing issued yet. Every RFQ generated
      from here is recorded automatically.</p>`;
    return;
  }
  box.innerHTML = `<div class="tablewrap"><table class="grid">
    <thead><tr><th>RFQ No.</th><th>Vendor</th><th>Issued</th>
      <th class="rate-col">Weight</th><th></th></tr></thead>
    <tbody>${rows.map(r => `<tr>
      <td>${escapeXml(r.rfq_no || "")}</td>
      <td>${escapeXml(r.vendor || "")}</td>
      <td>${escapeXml(String(r.issued_at || "").replace("T", " "))}</td>
      <td class="rate-col num">${r.weight_kg ? fmt(r.weight_kg, 1) + " kg" : ""}</td>
      <td><button class="mini" onclick="saveArchivedRfq('${escapeXml(r.id)}')">
        Save a copy</button></td></tr>`).join("")}</tbody></table></div>`;
}

async function saveArchivedRfq(id) {
  const msg = document.getElementById("rfqMsg");
  msg.textContent = "Saving\u2026";
  const d = await (await fetch(`/api/rfq-archive/${id}/save-as`,
    { method: "POST" })).json();
  msg.textContent = d.error ? d.error
    : d.cancelled ? "Save cancelled \u2014 the archived copy is untouched."
    : `Saved to ${d.path}`;
}

/* ---- expense reimbursement ---------------------------------------------
   Caps are checked on the server by reimburse.check(), the same call that
   writes the Word sheet, so what is shown here and what an approver receives
   cannot disagree. Caps apply PER DAY PER CATEGORY - two lunches on one date
   are judged together.                                                      */

const RB_CATS = ["Breakfast", "Lunch", "Dinner", "Stay", "Travel", "Other"];
let rbItems = [];

/* ---- receipt uploads ----------------------------------------------------
   Held server-side for this run only and filed nowhere until the sheet is
   generated, so an abandoned half-filled claim leaves nothing behind.      */

let rbBillToken = "";
let rbBillList = [];

async function uploadBills(files) {
  const msg = document.getElementById("rbMsg");
  const fd = new FormData();
  [...files].forEach(f => fd.append("files", f));
  if (rbBillToken) fd.append("token", rbBillToken);
  msg.textContent = "Uploading\u2026";
  let d;
  try {
    d = await (await fetch("/api/reimburse/bills",
      { method: "POST", body: fd })).json();
  } catch (e) { msg.textContent = "Upload failed."; return; }
  if (d.error) { msg.textContent = d.error; return; }
  rbBillToken = d.token;
  rbBillList = d.bills || [];
  msg.textContent = (d.rejected || []).length
    ? `${(d.rejected || []).join(", ")} skipped \u2014 PDF, JPG or PNG only.`
    : `${rbBillList.length} bill(s) attached.`;
  renderBills();
}

function renderBills() {
  const box = document.getElementById("rbBills");
  if (!rbBillList.length) { box.innerHTML = ""; return; }
  box.innerHTML = `<p class="hint"><b>Attached bills</b></p>
    <ul class="billlist">${rbBillList.map(b => `<li>${escapeXml(b.name)}
      <button class="mini" onclick="removeBill('${escapeXml(b.name)}')">&times;</button>
      </li>`).join("")}</ul>`;
}

async function removeBill(name) {
  const d = await (await fetch(
    `/api/reimburse/bills/${rbBillToken}/${encodeURIComponent(name)}`,
    { method: "DELETE" })).json();
  rbBillList = d.bills || [];
  renderBills();
}

function rbBlank() {
  return { date: "", description: "", vendor: "", category: "Other",
           bill_no: "", cost: "" };
}

function rbPayload() {
  return {
    bills_token: rbBillToken,
    sheet_no: val("rbSheetNo"), period_from: val("rbFrom"), period_to: val("rbTo"),
    employee: val("rbEmployee"), employee_id: val("rbEmpId"),
    department: val("rbDept"), advance: val("rbAdvance"),
    notes: val("rbNotes"), items: rbItems,
  };
  function val(id) { return (document.getElementById(id).value || "").trim(); }
}

function renderReimb() {
  const tb = document.querySelector("#rbTable tbody");
  tb.innerHTML = rbItems.map((it, i) => `<tr>
    <td class="num">${i + 1}</td>
    <td><input type="date" value="${escapeXml(it.date)}"
        onchange="rbSet(${i},'date',this.value)"></td>
    <td><input type="text" value="${escapeXml(it.description)}"
        onchange="rbSet(${i},'description',this.value)"></td>
    <td><input type="text" list="rbVendorList" value="${escapeXml(it.vendor)}"
        onchange="rbSet(${i},'vendor',this.value)"></td>
    <td><select onchange="rbSet(${i},'category',this.value)">${
      RB_CATS.map(c => `<option${c === it.category ? " selected" : ""}>${c}</option>`).join("")
    }</select></td>
    <td><input type="text" value="${escapeXml(it.bill_no)}"
        onchange="rbSet(${i},'bill_no',this.value)"></td>
    <td class="rate-col"><input type="number" min="0" step="0.01"
        value="${escapeXml(it.cost)}" onchange="rbSet(${i},'cost',this.value)"></td>
    <td><button class="rowx" onclick="rbDel(${i})">&times;</button></td></tr>`).join("");
  rbRecalc();
}

function rbSet(i, key, v) { rbItems[i][key] = v; rbRecalc(); }
function rbDel(i) { rbItems.splice(i, 1); renderReimb(); }

let rbTimer = null;
function rbRecalc() {
  clearTimeout(rbTimer);
  rbTimer = setTimeout(async () => {
    const p = rbPayload();
    let d;
    try {
      d = await (await fetch("/api/reimburse/check", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(p),
      })).json();
    } catch (e) { return; }

    const adv = Number(p.advance) || 0;
    document.getElementById("rbTotals").innerHTML = `
      <div class="rbrow"><span>Subtotal</span><b>${fmtRs(d.subtotal)}</b></div>
      ${d.over_cap ? `<div class="rbrow over"><span>Over cap, not payable</span>
        <b>&minus; ${fmtRs(d.over_cap)}</b></div>` : ""}
      <div class="rbrow"><span>Advance payment</span><b>&minus; ${fmtRs(adv)}</b></div>
      <div class="rbrow tot"><span>Total reimbursement</span>
        <b>${fmtRs((d.payable || 0) - adv)}</b></div>`;

    const flags = [];
    if ((d.exceptions || []).length) {
      flags.push(`<div class="bomissue"><b>Over the daily cap.</b> These are
        trimmed to the cap on the sheet, so the claim will not pay in full.
        <ul>${d.exceptions.map(e => `<li>${escapeXml(e.text)}</li>`).join("")}</ul></div>`);
    }
    if ((d.missing_bill || []).length) {
      flags.push(`<div class="bomissue warn"><b>No bill number</b> on line(s)
        ${d.missing_bill.join(", ")}. Printed or handwritten receipts and GST
        bills only &mdash; UPI is not accepted.</div>`);
    }
    if ((d.outside_period || []).length) {
      flags.push(`<div class="bomissue warn"><b>Dated outside the claim
        period</b>: line(s) ${d.outside_period.join(", ")}.</div>`);
    }
    document.getElementById("rbFlags").innerHTML = flags.join("");
  }, 250);
}

function fmtRs(v) {
  return "\u20b9" + (Number(v) || 0).toLocaleString("en-IN",
    { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

async function loadReimbPage() {
  if (!rbItems.length) { for (let i = 0; i < 5; i++) rbItems.push(rbBlank()); }
  renderReimb();
  await loadReimbVendors();
}

/* The Vendor Name cell offers everyone on the Vendor List, and still accepts
   anything typed. A hard dropdown would be wrong here: the vendor list is
   suppliers - steel, machining, heat treatment - while an expense claim names
   a hotel, a canteen or a bus company. Locking the field to registered
   suppliers would block most genuine receipts. So the list is a suggestion,
   not a restriction. */
async function loadReimbVendors() {
  const dl = document.getElementById("rbVendorList");
  if (!dl) return;
  try {
    const d = await (await fetch("/api/vendors")).json();
    const names = (d.vendors || [])
      .map(v => ((v.company || {}).name || "").trim())
      .filter(Boolean)
      .sort((a, b) => a.localeCompare(b));
    dl.innerHTML = names.map(n => `<option value="${escapeXml(n)}"></option>`).join("");
  } catch (e) { /* the field still works as free text */ }
}

async function reimbDocx() {
  const msg = document.getElementById("rbMsg");
  const p = rbPayload();
  p.items = rbItems.filter(x => x.date || x.description || x.cost);
  msg.textContent = "Building\u2026";
  const d = await (await fetch("/api/reimburse/docx", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(p),
  })).json();
  msg.textContent = d.error ? d.error
    : d.cancelled ? "Save cancelled — nothing was written."
    : `Saved to ${d.path}`;
}

/* ---- project status board ----------------------------------------------
   Phases 1-3 are ONE activity each, derived from state the app already holds:
   the BOM, the parts table, the last calculation. Phases 4 and 5 stay split,
   because Project Engineer / Team Lead / Director complete at different times
   and collapsing them would hide exactly where a request is stuck.

   Derived here in the browser rather than on the server, so the board always
   describes what is on screen. Computing it server-side would have shown the
   last saved state, which is not the same thing while someone is mid-edit.  */

let activities = [];          // the manual ones; the auto ones are computed
let actEditing = null;
let autoEditing = null;
let actAddPhase = null;
let statusExtra = { vendors: 0, jobs: 0, plates: 0, lastJob: "" };

const PHASES = [
  { id: 1, name: "Setup & BOM" },
  { id: 2, name: "Part details" },
  { id: 3, name: "Nesting & cost" },
  { id: 4, name: "Approval" },
  { id: 5, name: "RFQ" },
];

const ST_LABEL = { done: "Completed", prog: "In progress",
                   todo: "Not started", hold: "On hold" };

function actOverdue(a) {
  if (a.status === "done" || !a.due) return false;
  const d = new Date(a.due + "T23:59:59");
  return d < new Date();
}

function daysLate(due) {
  return Math.floor((Date.now() - new Date(due + "T23:59:59")) / 86400000);
}

function autoPhase1() {
  const flat = [];
  (function walk(n) { (n || []).forEach(r => { flat.push(r); walk(r.children); }); })(bomTree);
  if (!flat.length) {
    return { name: "Setup & BOM", auto: true, status: "todo",
             facts: "No BOM lines yet." };
  }
  const untyped = flat.filter(r => !r.type).length;
  return {
    name: "Setup & BOM", auto: true,
    status: untyped ? "prog" : "done",
    facts: `<b>${flat.length}</b> BOM line(s) entered<br>`
         + `<b>${untyped}</b> line(s) without a component type`,
    blocked: untyped ? `${untyped} line(s) have no component type, so they are `
                     + `not costed. Set a type on the BOM tab.` : "",
  };
}

function autoPhase2() {
  const flat = [];
  (function walk(n) { (n || []).forEach(r => { flat.push(r); walk(r.children); }); })(bomTree);
  const real = flat.filter(r => r.type && r.type !== "__sub");
  const done = real.filter(bomLineDone).length;
  const mats = [...new Set(Object.values(rowsByType).flat()
    .map(r => r.material).filter(Boolean))];
  if (!real.length) {
    return { name: "Part details", auto: true, status: "todo",
             facts: "Nothing to dimension yet." };
  }
  return {
    name: "Part details", auto: true,
    status: done >= real.length ? "done" : done ? "prog" : "todo",
    facts: `<b>${done} of ${real.length}</b> BOM line(s) dimensioned`
         + (mats.length ? `<br>Materials: ${mats.map(escapeXml).join(", ")}` : ""),
  };
}

function autoPhase3() {
  if (!lastResult) {
    // Nothing calculated in THIS session. lastResult is cleared on project
    // load and on commit, so a project with plates already booked was coming
    // back reading "not calculated yet" - the board was describing the browser
    // session rather than the project. Fall back to what is actually booked.
    if (statusExtra.jobs) {
      return {
        name: "Nesting & cost", auto: true, status: "done",
        facts: `<b>${statusExtra.plates}</b> plate(s) across `
             + `<b>${statusExtra.jobs}</b> booked job(s)`
             + (statusExtra.lastJob
                 ? `<br>Last booked ${escapeXml(String(statusExtra.lastJob)).slice(0, 10)}` : "")
             + `<br>Press Calculate to see the current layout.`,
      };
    }
    return { name: "Nesting & cost", auto: true, status: "todo",
             facts: "Not calculated yet." };
  }
  // plate_rows sits at the TOP of the result, not inside each group. Reading
  // it from the groups returned an empty list every time, so a calculated job
  // reported "Not calculated yet" - which is exactly what you saw.
  const rows = lastResult.plate_rows || [];
  const plates = rows.length;
  const util = plates
    ? rows.reduce((a, r) => a + (r.utilisation_pct || 0), 0) / plates : 0;
  const bought = (lastResult.plates_purchased || [])
    .reduce((a, p) => a + (Number(p.qty) || 0), 0);
  const rem = (lastResult.remnants_used || []).length;
  const sizes = [...new Set(rows.map(r => r.spec).filter(Boolean))];
  const spare = (lastResult.harvest || []).length;
  return {
    name: "Nesting & cost", auto: true, status: plates ? "done" : "todo",
    facts: `<b>${plates}</b> plate(s) laid out &middot; <b>${util.toFixed(1)}%</b> average utilisation<br>`
         + `<b>${bought}</b> new plate(s) to buy`
         + (rem ? ` &middot; <b>${rem}</b> from the rack` : "")
         + (sizes.length > 1 ? `<br>Sizes: ${sizes.map(escapeXml).join(", ")}` : "")
         + (spare ? `<br><b>${spare}</b> offcut(s) worth keeping` : ""),
    blocked: (lastResult.oversize || []).length
      ? `${(lastResult.oversize || []).length} blank(s) fit no stock plate.` : "",
  };
}

function autoPhase4() {
  const rec = latestRequest();
  if (!rec) {
    return [{ name: "Not sent for approval", auto: true, status: "todo",
              facts: "Send the RFQ for approval from the RFQ tab." }];
  }
  return (rec.steps || []).map((st, i) => {
    const prior = (rec.steps || []).slice(0, i);
    const waiting = st.state === "pending" && prior.every(p => p.state === "approved");
    return {
      name: st.designation, auto: true,
      status: st.state === "approved" ? "done"
            : st.state === "rejected" ? "hold"
            : waiting ? "prog" : "todo",
      who: st.by || "",
      when: (st.at || "").slice(0, 10),
      remark: st.comment || "",
      facts: `${rec.id}`,
      blocked: waiting && !st.by && !statusHasUser(st.designation)
        ? `Nobody is registered as ${st.designation}, so this cannot be `
        + `signed. ${rec.id} is stuck here.` : "",
    };
  });
}

/* The newest request raised against the open project. loadApprovals() already
   asks for ?project=<id>, so apState.project is only this project's. */
function latestRequest() {
  return (apState && apState.project || [])
    .slice()
    .sort((a, b) => (b.created || "").localeCompare(a.created || ""))[0] || null;
}

/* Who actually holds a role. The approvals API sends this precisely so an
   unregistered approver shows as a gap now, instead of being discovered when
   the request stalls on them. */
function statusHasUser(designation) {
  return ((apState && apState.holders || {})[designation] || []).length > 0;
}

function statusPeople() {
  const h = (apState && apState.holders) || {};
  return Object.entries(h).flatMap(([desig, list]) =>
    list.map(u => ({ name: u.name, designation: desig })));
}

function autoPhase5() {
  const rec = latestRequest();
  const kg = rec ? (rec.snapshot || {}).total_kg : 0;
  const n = statusExtra.vendors;
  const out = [{
    name: "Vendors selected", auto: true,
    status: n ? "done" : "todo",
    facts: n ? `<b>${n}</b> raw material vendor(s) ticked` : "None ticked yet",
  }];
  const issued = rec && rec.issued_at;
  out.push({
    name: "RFQ issued", auto: true,
    status: issued ? "done" : "todo",
    when: issued ? String(issued).slice(0, 10) : "",
    facts: rec ? `${rec.id}${kg ? ` &middot; <b>${fmt(kg, 0)} kg</b>` : ""}`
               : "Not submitted",
    blocked: (!issued && rec && rec.status !== "approved")
      ? `Blocked: the chain has not signed off.` : "",
  });
  return out;
}

/* An auto card's status is computed and stays computed. What a person can add
   is ownership: who is on it, and a note. Without that the board says the work
   is done but never who did it, which is the one thing a status board is asked
   at a standup. Name and status remain the app's, so nobody can mark
   "Nesting & cost" complete by hand and have the board claim work that has not
   happened. */
let autoMeta = {};

function applyAutoMeta(a) {
  const m = autoMeta[a.key] || {};
  if (m.who) a.who = m.who;
  if (m.remark) a.remark = m.remark;
  if (m.due) a.due = m.due;
  if (m.status && m.status !== a.status) {
    // Overriding is allowed, but never silent. An unlabelled override would
    // let the board claim work the app can see has not happened.
    a.status = m.status;
    a.overridden = true;
  }
  return a;
}

function openAutoActivity(key) {
  autoEditing = key;
  actEditing = null;
  const m = autoMeta[key] || {};
  const who = document.getElementById("actWho");
  const people = statusPeople();
  who.innerHTML = `<option value="">Unassigned</option>`
    + people.map(p => `<option value="${escapeXml(p.name)}">${escapeXml(p.name)}
        \u2014 ${escapeXml(p.designation || "")}</option>`).join("");
  who.value = m.who || "";

  document.getElementById("actModalTitle").textContent = "Assign activity";
  const nm = document.getElementById("actName");
  nm.value = key.split("|").slice(1).join(" ") || key;
  nm.disabled = true;
  const stt = document.getElementById("actStatus");
  stt.disabled = false;
  stt.value = m.status || "todo";
  document.getElementById("actDue").value = m.due || "";
  document.getElementById("actRemark").value = m.remark || "";
  document.getElementById("actModalErr").textContent =
    "The app works this status out for itself. Change it only to override that "
    + "\u2014 the card will show it was set by hand.";
  document.getElementById("actModal").style.display = "";
}

function autoFor(phaseId) {
  const list = phaseId === 1 ? [autoPhase1()]
             : phaseId === 2 ? [autoPhase2()]
             : phaseId === 3 ? [autoPhase3()]
             : phaseId === 4 ? autoPhase4() : autoPhase5();
  // Keyed by phase and name, not by position, so adding a phase later does
  // not silently reassign somebody else's activity to a different card.
  return list.map(a => applyAutoMeta(
    Object.assign(a, { key: `${phaseId}|${a.name}` })));
}

function actCard(a, idx) {
  const late = actOverdue(a);
  const cls = late ? "late" : a.status === "done" ? "done"
            : a.status === "prog" ? "prog" : a.status === "hold" ? "hold" : "";
  const pill = late ? '<span class="pill p-late">Overdue</span>'
    : `<span class="pill p-${a.status || "todo"}">${ST_LABEL[a.status] || "Not started"}</span>`;
  const who = a.who
    ? `<span class="who"><span class="avm">${escapeXml(String(a.who)[0] || "?").toUpperCase()}</span>${escapeXml(a.who)}</span>`
    : "";
  const when = a.due || a.when || "";
  const meta = (who || when)
    ? `<div class="meta">${who}${who && when ? " &middot; " : ""}${
        when ? `<span class="due${late ? " late" : ""}">${escapeXml(when)}${
          late ? ` &middot; ${daysLate(a.due)} day(s) late` : ""}</span>` : ""}</div>`
    : "";
  return `<div class="act ${cls}">
    <div class="nm">${escapeXml(a.name)}</div>
    ${pill}${a.auto ? ' <span class="pill p-auto">auto</span>' : ""}${
      a.overridden ? ' <span class="pill p-hold">set by hand</span>' : ""}
    ${meta}
    ${a.facts ? `<div class="facts">${a.facts}</div>` : ""}
    ${a.blocked ? `<div class="blocked">${escapeXml(a.blocked)}</div>` : ""}
    ${a.remark ? `<div class="rem">${escapeXml(a.remark)}</div>` : ""}
    <div class="btns">
      <button class="mini" onclick="${a.auto
        ? `openAutoActivity('${escapeXml(a.key)}')` : `openActivity(${idx})`}">Edit</button>
      ${a.auto ? "" : `<button class="mini" onclick="removeActivity(${idx})">&times;</button>`}
    </div>
  </div>`;
}

function renderStatus() {
  const board = document.getElementById("statusBoard");
  const all = [];

  board.innerHTML = PHASES.map(ph => {
    const auto = autoFor(ph.id);
    const mine = activities
      .map((a, i) => ({ a, i }))
      .filter(x => Number(x.a.phase) === ph.id);
    auto.forEach(a => all.push(a));
    mine.forEach(x => all.push(x.a));

    const cards = auto.map(a => actCard(a, -1))
      .concat(mine.map(x => actCard(x.a, x.i))).join("");
    const set = auto.concat(mine.map(x => x.a));
    const done = set.filter(a => a.status === "done").length;
    const pct = set.length ? Math.round(done / set.length * 100) : 0;
    const anyLate = set.some(actOverdue);

    return `<div class="phase">
      <h3><span>${ph.id} &middot; ${escapeXml(ph.name)}</span>
        <span class="pct">${done}/${set.length} &middot; ${pct}%</span></h3>
      <div class="pbar"><i class="${anyLate ? "r" : pct === 100 ? "g" : ""}"
        style="width:${pct}%"></i></div>
      <div class="acts">${cards}
        <div class="addact" onclick="openActivity(null, ${ph.id})">+ Add activity</div>
      </div></div>`;
  }).join("");

  const n = all.length;
  const cnt = k => all.filter(k).length;
  document.getElementById("statusStats").innerHTML = `
    <div class="stat"><div class="n">${n}</div><div class="l">Total activities</div></div>
    <div class="stat done"><div class="n">${cnt(a => a.status === "done")}</div>
      <div class="l">Completed</div></div>
    <div class="stat prog"><div class="n">${cnt(a => a.status === "prog" && !actOverdue(a))}</div>
      <div class="l">In progress</div></div>
    <div class="stat late"><div class="n">${cnt(actOverdue)}</div>
      <div class="l">Overdue</div></div>
    <div class="stat wait"><div class="n">${cnt(a => a.status === "todo" && !actOverdue(a))}</div>
      <div class="l">Not started</div></div>`;
  document.getElementById("statusMeta").textContent = currentProject
    ? `${currentProject.number || currentProject.id} \u00b7 ${currentProject.name} \u2014 ${n} activities`
    : "No project open";
}

async function loadStatusPage() {
  if (!currentProject) { showPage("projectPage"); return; }
  await loadApprovals();
  try {
    const p = await (await fetch(`/api/projects/${currentProject.id}/progress`)).json();
    statusExtra.jobs = p.jobs || 0;
    statusExtra.plates = p.plates || 0;
    statusExtra.lastJob = p.last_job || "";
  } catch (e) { statusExtra.jobs = 0; }
  const ticked = document.querySelectorAll("#rfqVendors input:checked").length;
  const rec = latestRequest();
  statusExtra.vendors = ticked
    || ((rec && rec.snapshot && rec.snapshot.vendors) || []).length;
  renderStatus();
}

/* ---- add / edit ---------------------------------------------------------
   No Activity ID field and no Phase field, as asked. The phase comes from
   whichever column's "+ Add activity" was pressed, which does mean an
   activity cannot be moved to another phase afterwards - delete and re-add. */
function openActivity(idx, phase) {
  autoEditing = null;
  document.getElementById("actName").disabled = false;
  document.getElementById("actStatus").disabled = false;
  actEditing = (idx === null || idx === undefined || idx < 0) ? null : idx;
  actAddPhase = phase || (actEditing !== null ? activities[actEditing].phase : 1);
  const a = actEditing !== null ? activities[actEditing] : {};

  const who = document.getElementById("actWho");
  const people = statusPeople();
  who.innerHTML = `<option value="">Unassigned</option>`
    + people.map(p => `<option value="${escapeXml(p.name)}">${escapeXml(p.name)}
        \u2014 ${escapeXml(p.designation || "")}</option>`).join("");
  who.value = a.who || "";

  document.getElementById("actModalTitle").textContent =
    actEditing !== null ? "Edit activity" : "Add activity";
  document.getElementById("actName").value = a.name || "";
  document.getElementById("actDue").value = a.due || "";
  document.getElementById("actStatus").value = a.status || "todo";
  document.getElementById("actRemark").value = a.remark || "";
  document.getElementById("actModalErr").textContent = "";
  document.getElementById("actModal").style.display = "";
}

function closeActivity() {
  document.getElementById("actModal").style.display = "none";
  // Re-enable, or the next plain Add activity opens a form it cannot type in.
  document.getElementById("actName").disabled = false;
  document.getElementById("actStatus").disabled = false;
  autoEditing = null;
}

function saveActivity() {
  const err = document.getElementById("actModalErr");
  const name = document.getElementById("actName").value.trim();
  const due = document.getElementById("actDue").value;

  if (autoEditing) {
    autoMeta[autoEditing] = {
      who: document.getElementById("actWho").value,
      due: due,
      remark: document.getElementById("actRemark").value.trim(),
      status: document.getElementById("actStatus").value,
    };
    closeActivity();
    renderStatus();
    scheduleSave();
    return;
  }

  if (!name) { err.textContent = "Enter an activity name."; return; }
  if (!due) { err.textContent = "Enter a due date."; return; }

  const rec = {
    name, due,
    who: document.getElementById("actWho").value,
    status: document.getElementById("actStatus").value,
    remark: document.getElementById("actRemark").value.trim(),
    phase: Number(actAddPhase) || 1,
  };
  if (actEditing !== null) activities[actEditing] = rec;
  else activities.push(rec);
  closeActivity();
  renderStatus();
  scheduleSave();
}

function removeActivity(idx) {
  if (!confirm(`Delete "${activities[idx].name}"?`)) return;
  activities.splice(idx, 1);
  renderStatus();
  scheduleSave();
}

/* ---- plate size per material and thickness ------------------------------
   Right-sizing minimises the area bought, which is only the right answer when
   the offcut is worth something. A 3 mm sheet for one bracket in a grade
   nothing else uses leaves a remnant that sits in the rack until it is
   scrapped, so smallest really is best. A 5 mm sheet in a grade used on every
   job is the reverse: a bigger sheet costs more today and hands back a remnant
   the next job will cut from. The app cannot know which is which - only you
   know what the rack turns over - so the choice lives here.               */

let plateOverrides = {};

function renderPlateChoices() {
  const box = document.getElementById("plateChoices");
  if (!box) return;
  const groups = (lastResult && lastResult.groups) || [];
  if (!groups.length) {
    box.innerHTML = `<p class="hint">Calculate once and each material and
      thickness will be listed here with its own plate size.</p>`;
    return;
  }
  box.innerHTML = `<table class="platetable"><thead><tr>
      <th>Material</th><th>Thk</th><th>Plate size</th>
      <th class="num">Plates</th><th class="num">Cost</th></tr></thead><tbody>`
    + groups.map(g => {
        const k = g.plate_key || "";
        const cur = plateOverrides[k];
        const sel = cur ? `${cur.width}x${cur.length}` : "";
        return `<tr>
          <td>${escapeXml(g.material)}</td>
          <td class="num">${g.thickness} mm</td>
          <td><select onchange="setPlateChoice('${escapeXml(k)}', this.value)">
            <option value=""${sel ? "" : " selected"}>Automatic \u2014 smallest that fits</option>
            ${STANDARD_PLATES.map(p => {
              const v = `${p.width}x${p.length}`;
              return `<option value="${v}"${v === sel ? " selected" : ""}>${escapeXml(p.label)} mm</option>`;
            }).join("")}
          </select>
          ${g.plate_note ? `<div class="platewarn">${escapeXml(g.plate_note)}</div>` : ""}</td>
          <td class="num">${g.plates_required}</td>
          <td class="num">\u20b9${fmt(g.total_cost, 0)}</td></tr>`;
      }).join("")
    + `</tbody></table>
       <p class="hint">A size is refused if any blank will not fit on it, and the
       automatic size is used instead \u2014 nesting onto a sheet the parts do not
       fit would produce a cutting plan that cannot be cut.</p>`;
}

function setPlateChoice(key, value) {
  if (!value) delete plateOverrides[key];
  else {
    const [w, l] = value.split("x").map(Number);
    plateOverrides[key] = { width: w, length: l };
  }
  scheduleSave();
  calculate(true);
}

function projectState() {
  return {
    rows_by_type: rowsByType,
    bom: bomTree,
    activities: activities,
    auto_meta: autoMeta,
    last_result: savedResult,
    last_payload_rows: lastPayloadRows,
    plate_overrides: plateOverrides,
    settings: {
      useStd: document.getElementById("useStd").checked,
      // Restored on open but never written here, so the setting did not
      // survive closing the project. Whichever way it was left, reopening
      // silently went back to the HTML default.
      autoPlate: document.getElementById("autoPlate").checked,
      autoPlate: document.getElementById("autoPlate").checked,
      combine: document.getElementById("combine").checked,
      useRemnants: document.getElementById("useRemnants").checked,
      plateW: document.getElementById("plateW").value,
      plateL: document.getElementById("plateL").value,
      cutRate: document.getElementById("cutRate").value,
      pierceCharge: document.getElementById("pierceCharge").value,
      rowSeq: rowSeq,
    },
  };
}

// False while a project is being opened. Every edit calls scheduleSave(), and
// a save that fired mid-load wrote the half-built state back over the good one
// - the BOM emptied, the parts emptied, the results gone. That is the "going
// back erases everything" fault: nothing was erasing anything, an empty state
// was being saved on top of a full one.
let stateReady = false;

function scheduleSave() {
  if (!currentProject || !stateReady) return;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try {
      await fetch(`/api/projects/${currentProject.id}/save`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state: projectState() }),
      });
      setProjBar("saved");
    } catch (e) { setProjBar("save failed"); }
  }, 600);
}

function setProjBar(status) {
  const bar = document.getElementById("projBar");
  if (!currentProject) {
    bar.innerHTML = `<span class="pb-none">No project open &mdash;
      <a href="#" class="golink" data-page="projectPage">choose or create one</a></span>`;
    return;
  }
  bar.innerHTML = `<span class="pb-name">${currentProject.name}</span>` +
    (currentProject.client ? `<span class="pb-sub">${currentProject.client}</span>` : "") +
    `<span class="pb-id">${currentProject.id}</span>` +
    (status ? `<span class="pb-status">${status}</span>` : "");
}

async function loadProjects() {
  const d = await (await fetch("/api/projects")).json();
  const list = d.projects || [];
  document.getElementById("projMeta").textContent =
    list.length === 1 ? "1 project" : `${list.length} projects`;

  const box = document.getElementById("projList");
  if (!list.length) {
    box.innerHTML = `<p class="empty-note">No projects yet. Press
      <b>+ Add Project</b> to create one, or open a .rmp file you saved earlier.</p>`;
    return;
  }
  box.innerHTML = list.map(p => `
    <div class="proj-card${currentProject && currentProject.id === p.id ? " open" : ""}">
      <div class="proj-info">
        <div class="proj-num">${p.number || p.id}</div>
        <div class="proj-name">${p.name}</div>
        <div class="proj-meta">
          ${p.client ? `Customer: ${p.client} &nbsp;&middot;&nbsp; ` : ""}
          Updated: ${prettyDate(p.modified)}
          &nbsp;&middot;&nbsp; ${p.parts} part(s), ${p.jobs} job(s)
        </div>
      </div>
      <div class="proj-btns">
        <button onclick="openProject('${p.id}')">Open Project</button>
        <button class="sec" onclick="saveProjectFile('${p.id}')">Save as file</button>
        <button class="sec danger" onclick="deleteProject('${p.id}')">Delete</button>
      </div>
    </div>`).join("");
}

function prettyDate(iso) {
  if (!iso) return "\u2014";
  const d = new Date(iso);
  if (isNaN(d)) return String(iso).replace("T", " ");
  return d.toLocaleDateString(undefined,
    { year: "numeric", month: "short", day: "2-digit" });
}

// ---- create dialog ----
function openProjModal() {
  ["newProjNumber", "newProjName", "newProjClient", "newProjNotes"]
    .forEach(id => document.getElementById(id).value = "");
  document.getElementById("projModalErr").textContent = "";
  document.getElementById("projModal").style.display = "";
  document.getElementById("newProjNumber").focus();
}

function closeProjModal() {
  document.getElementById("projModal").style.display = "none";
}

async function createProject() {
  const number = document.getElementById("newProjNumber").value.trim();
  const name = document.getElementById("newProjName").value.trim();
  const client = document.getElementById("newProjClient").value.trim();
  const notes = document.getElementById("newProjNotes").value.trim();
  const err = document.getElementById("projModalErr");

  // Both starred fields are required. Saying which one is missing beats a
  // generic complaint the user has to go hunting through.
  const missing = [];
  if (!number) missing.push("Project Number");
  if (!name) missing.push("Project Name");
  if (missing.length) {
    err.textContent = `${missing.join(" and ")} ${missing.length > 1 ? "are" : "is"} required.`;
    return;
  }

  const d = await (await fetch("/api/projects/create", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ number, name, client, notes }),
  })).json();
  if (d.error) { err.textContent = d.error; return; }
  closeProjModal();
  await loadProjects();
  await openProject(d.project.id);
}

/* Saving goes through the server, not a browser download. Inside the desktop
   window there is no download manager, so window.location to an attachment
   silently does nothing — which is exactly why "Save as file" appeared dead. */
async function saveProjectFile(pid) {
  const msg = document.getElementById("projMsg");
  msg.textContent = "Saving\u2026";
  try {
    const r = await (await fetch(`/api/projects/${pid}/save-file`,
      { method: "POST" })).json();
    if (r.cancelled) { msg.textContent = "Save cancelled."; return; }
    if (r.error) { msg.textContent = r.error; return; }
    msg.innerHTML = `Saved to <b>${r.path}</b> ` +
      `<a href="#" class="golink" onclick="revealPath('${r.path.replace(/\\/g, "\\\\")}');return false">open folder</a>`;
  } catch (e) {
    msg.textContent = "Save failed: " + e.message;
  }
}

async function revealPath(path) {
  await fetch("/api/reveal", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
}

function clearStaleProjectPanels() {
  // Everything on screen that belongs to ONE project and would otherwise sit
  // there after a different project is opened.
  //
  // Clearing the result TABLES was not enough. The cutting diagrams live in
  // their own div, the purchased-components block is inserted after the
  // result table, and the summary writes into four more containers - none of
  // them a tbody. So a brand new project showed the previous project's plates,
  // its KPI tiles and its part-by-part ledger. Every one of these is a render
  // of something else that is already stored; blanking it loses nothing and
  // the next Calculate or Summary visit fills it back in.
  // "hidden" panels are ones the page keeps display:none until they have
  // something to say. "empty" ones are always in the layout and are filled by
  // whatever renders them - hiding those would leave the summary blank for
  // good, because nothing sets their display back.
  [["bomImportReport", "hidden"], ["rfqResult", "hidden"],
   ["rbBills", "hidden"], ["purchaseResults", "hidden"],
   ["diagrams", "empty"], ["sumKpis", "empty"], ["sumBasis", "empty"],
   ["bomMsg", "text"], ["rfqMsg", "text"], ["sumMsg", "text"],
   ["status", "text"]].forEach(([id, how]) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (how === "hidden") { el.innerHTML = ""; el.style.display = "none"; }
    else if (how === "empty") { el.innerHTML = ""; }
    else el.textContent = "";
  });
  // Summary and results tables, which are filled row by row rather than as a
  // block. Emptying the body leaves the headings, which is what an unvisited
  // page should look like.
  document.querySelectorAll("#summaryWork table tbody, #resultsWork table tbody")
    .forEach(tb => { tb.innerHTML = ""; });
  lastSummary = null;
}

async function openProject(pid) {
  stateReady = false;              // no saving until everything is in place
  clearStaleProjectPanels();
  const d = await (await fetch(`/api/projects/${pid}`)).json();
  if (d.error) {
    stateReady = true;
    document.getElementById("projMsg").textContent = d.error;
    return;
  }
  currentProject = d.project;

  const st = d.state || {};
  rowsByType = st.rows_by_type || {};
  // Mutated in place, NOT reassigned. bomNodeAt() hands out references into
  // this array; swapping the array for a new one would leave any edit made
  // while the project was still loading writing into an orphan that is then
  // thrown away.
  bomTree.length = 0;
  (st.bom || []).forEach(x => bomTree.push(x));
  bomPath = [];
  activities = Array.isArray(st.activities) ? st.activities : [];
  autoMeta = (st.auto_meta && typeof st.auto_meta === "object") ? st.auto_meta : {};
  plateOverrides = (st.plate_overrides && typeof st.plate_overrides === "object")
    ? st.plate_overrides : {};
  // Row ids must not restart, or a restored row could collide with a new one
  // and the result lookup would attach to the wrong line.
  const maxId = Math.max(-1, ...Object.values(rowsByType)
    .flat().map(r => (typeof r._id === "number" ? r._id : -1)));
  rowSeq = Math.max(Number(st.settings?.rowSeq) || 0, maxId + 1);

  const g = st.settings || {};
  const set = (id, v, check) => {
    const el = document.getElementById(id);
    if (!el || v === undefined || v === null) return;
    if (check) el.checked = !!v; else el.value = v;
  };
  set("useStd", g.useStd, true);
  set("autoPlate", g.autoPlate, true);
  set("combine", g.combine, true);
  set("useRemnants", g.useRemnants, true);
  set("plateW", g.plateW); set("plateL", g.plateL);
  set("cutRate", g.cutRate); set("pierceCharge", g.pierceCharge);

  // Results used to live only in memory, so reopening a project showed no
  // layout, no plates and no cost until Calculate was pressed again - and the
  // status board reported the job as never started.
  savedResult = st.last_result || null;
  // lastResult stays NULL on open, and that is the whole point of the split.
  // loadSummary() treats anything in lastResult as a calculated-but-unbooked
  // job and lists it as "pending" - so restoring it here made every booked
  // part appear twice, once under J-0001 and once as pending, and the project
  // total counted both. An approver opening the project is reviewing what has
  // already been booked; they are not holding an unbooked estimate.
  lastResult = null;
  lastPayloadRows = Array.isArray(st.last_payload_rows) ? st.last_payload_rows : [];
  // Clearing the table bodies was not enough. #result also holds the summary
  // strip, the Standard Plates Purchased block and the cutting-layout
  // diagrams, none of which live in a tbody - so opening a new project left
  // the previous project's plate count, job cost and cutting layouts on
  // screen under a new project's name. Hide the whole block; renderResults()
  // shows it again if this project actually has a stored layout.
  document.getElementById("resultsWork").querySelectorAll("table tbody")
    .forEach(tb => { tb.innerHTML = ""; });
  const resBlock = document.getElementById("result");
  if (resBlock) resBlock.style.display = "none";

  // Same for the summary. loadSummary() only runs when the Summary tab is
  // opened, so until then the figures on screen were the last project's.
  ["sumKpis"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = "";
  });
  ["sumMeta", "sumBasis"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = "";
  });
  ["sumTypeTable", "sumLineTable"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.querySelectorAll("tbody").forEach(tb => { tb.innerHTML = ""; });
  });
  lastSummary = null;

  renderBom();
  setProjBar("opened");
  await loadProjects();
  await switchType(currentType || document.querySelector("#typeTabs span").dataset.type);
  // Repaint the stored layout, so plates, costs and the cutting diagram are
  // there on open instead of only after another Calculate.
  if (savedResult) {
    try { renderResults(savedResult); } catch (e) { /* older saved shape */ }
  }
  showWork("bomWork");
  stateReady = true;               // safe to save from here on
}

async function deleteProject(pid) {
  await fetch(`/api/projects/${pid}/delete`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  if (currentProject && currentProject.id === pid) {
    currentProject = null;
    setProjBar();
  }
  await loadProjects();
}

async function importProject(file) {
  const msg = document.getElementById("projMsg");
  try {
    const doc = JSON.parse(await file.text());
    const d = await (await fetch("/api/projects/import", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file: doc }),
    })).json();
    if (d.error) { msg.textContent = d.error; return; }
    msg.textContent = `Opened as ${d.project.id}.`;
    await loadProjects();
    await openProject(d.project.id);
  } catch (e) {
    msg.textContent = "That file could not be read as a project.";
  }
}

/* The RFQ is reviewed before it is written. Building only renders it; Save is
   a separate, deliberate step — an RFQ goes to a supplier, and a document that
   appears on disk the instant a button is pressed invites sending the wrong one. */
/* The Summary page's button just moves to the RFQ page, which is where the
   raw material enquiry is now raised — one document per vendor. */
function generateRfq() {
  if (!currentProject) {
    alert("Open a project first \u2014 an RFQ is raised against a project.");
    showPage("projectPage");
    return;
  }
  showPage("rfqPage");
}








// ---- BOM -------------------------------------------------------------------

/* The BOM is the first thing entered against a project: what the assembly is
   made of, before any dimension is typed.
   
   A line is either a PART, in which case choosing its component type opens
   that component's input, or a SUB-ASSEMBLY, which opens its own BOM one level
   down. `bomPath` is the trail of sub-assemblies currently open, which is what
   makes the breadcrumb and the Back button work. */
let bomPath = [];

/* Manufacturing processes, from Manufacturing_Processes.xlsx. The code is what
   goes on the BOM sheet; the full name is the tooltip. */
const MFG_PROCESSES = [
  ["A", "Laser Cutting"], ["B", "Rolling"], ["C", "Spinning"],
  ["D", "Machining"], ["E", "Solution Annealing"], ["F", "Hardening"],
  ["G", "Welding"], ["H", "Forming"], ["I", "Bending"], ["J", "Drilling"],
  ["K", "Grinding"], ["M", "Pickling"], ["N", "Passivation"],
  ["O", "Painting / Coating"], ["Q", "NDT"], ["R", "Assembly Part"],
];

/* A BOM line counts as done once its part actually has a costed row in the
   parts table. That is the check the user asked to see in green here - on the
   BOM, where the assembly is listed, not on the part form. */
function bomLineDone(r) {
  if (!r.type || r.type === "__sub") return false;
  const rows = rowsByType[r.type] || [];
  return rows.some(x =>
    ((r.part_no && String(x.part_no) === String(r.part_no)) ||
     (r.part_name && String(x.part_name) === String(r.part_name))) &&
    (x._booked || (lastResult && resultForRow(x))));
}

function bomProcSet(r) {
  if (!Array.isArray(r.processes)) r.processes = [];
  return r.processes;
}

function toggleBomProc(i, code, on) {
  const rows = bomNodeAt(bomPath);
  const set = bomProcSet(rows[i]);
  const at = set.indexOf(code);
  if (on && at < 0) set.push(code);
  if (!on && at >= 0) set.splice(at, 1);
  scheduleSave();
  renderBomSide();
}

function bomNodeAt(path) {
  // The BOM is part of project state, so it saves and loads with everything
  // else rather than living only on screen.
  let node = bomTree;
  for (const idx of path) {
    if (!node[idx].children) node[idx].children = [];
    node = node[idx].children;
  }
  return node;
}

function renderBom() {
  renderBomSide();

  // Process codes head their own columns, under one grouped heading. Built
  // here rather than in the template so the list stays in one place.
  const ph = document.getElementById("procHeadRow");
  if (ph && !ph.dataset.built) {
    ph.innerHTML = MFG_PROCESSES
      .map(([code, name]) => `<th class="proccol" title="${name}">${code}</th>`).join("");
    document.getElementById("procGroupHead").colSpan = MFG_PROCESSES.length;
    ph.dataset.built = "1";

    // Spell the codes out under the table. The column heads only have room
    // for a letter, and nobody should have to hover sixteen of them to find
    // out which is which.
    const pl = document.getElementById("procLegend");
    if (pl) {
      pl.innerHTML = `<span class="legend-title">Manufacturing process codes</span>` +
        MFG_PROCESSES.map(([code, name]) =>
          `<span class="pitem"><span class="pcode">${code}</span> \u2014 ${name}</span>`).join("");
    }
  }
  const rows = bomNodeAt(bomPath);
  const tb = document.querySelector("#bomTable tbody");
  const types = COMPONENT_TYPES.slice();

  tb.innerHTML = rows.length ? rows.map((r, i) => {
    const done = bomLineDone(r);
    const procs = bomProcSet(r);
    return `
    <tr${done ? ' class="bomdone" title="Costed \u2014 this part has been calculated"' : ""}>
      <td><input class="part" value="${r.part_no || ""}"
           onchange="bomSet(${i},'part_no',this.value)"></td>
      <td><input class="part" value="${r.part_name || ""}"
           onchange="bomSet(${i},'part_name',this.value)"></td>
      <td><input class="rem" type="number" value="${r.qty ?? 1}"
           onchange="bomSet(${i},'qty',this.value)"></td>
      <td><input class="rem" type="number" value="${r.thk ?? ""}"
           onchange="bomSet(${i},'thk',this.value)"></td>
      <td>
        <select onchange="bomSet(${i},'type',this.value)">
          <option value="">\u2014 choose \u2014</option>
          <option value="__sub" ${r.type === "__sub" ? "selected" : ""}>Sub-assembly</option>
          ${types.map(t => `<option value="${t}" ${r.type === t ? "selected" : ""}>${t}</option>`).join("")}
        </select>
      </td>
      ${MFG_PROCESSES.map(([code, name]) => `
        <td class="proccell" title="${name}">
          <input type="checkbox" ${procs.includes(code) ? "checked" : ""}
            onchange="toggleBomProc(${i},'${code}',this.checked)"></td>`).join("")}
      <td class="num">${bomAction(r, i)}</td>
      <td class="num"><button class="del-btn" onclick="bomRemove(${i})">&times;</button></td>
    </tr>`;
  }).join("")
    : `<tr><td colspan="${7 + MFG_PROCESSES.length}" class="empty">No BOM lines yet. Press
       <b>+ Add BOM line</b>.</td></tr>`;

  const crumb = document.getElementById("bomCrumb");
  if (!bomPath.length) {
    crumb.innerHTML = "";
  } else {
    const names = [];
    let node = bomTree;
    for (const idx of bomPath) {
      names.push(node[idx].part_name || node[idx].part_no || "sub-assembly");
      node = node[idx].children || [];
    }
    crumb.innerHTML = `<b>Inside:</b> Top level &rsaquo; ${names.join(" &rsaquo; ")}
      &nbsp; <button class="sec" onclick="bomUp()">Back</button>`;
  }
  document.getElementById("bomMeta").textContent =
    `${rows.length} line(s)${bomPath.length ? " at this level" : ""}`;
}

/* ---- BOM import from Excel ---------------------------------------------
   The designer's own datasheet, read straight into the BOM table so the same
   list is not typed twice. Anything the parser could not place is shown below
   the table rather than quietly dropped or guessed at - a line that arrives
   with the wrong component type becomes a wrong blank, a wrong nest and a
   wrong price, and nobody looks at the BOM again to find out why.            */

async function importBomExcel(file) {
  const msg = document.getElementById("bomMsg");
  const box = document.getElementById("bomImportReport");
  msg.textContent = "Reading " + file.name + "\u2026";
  box.style.display = "none";

  const fd = new FormData();
  fd.append("file", file);
  fd.append("codes", MFG_PROCESSES.map(p => p[0]).join(","));

  let d;
  try {
    d = await (await fetch("/api/bom/import", { method: "POST", body: fd })).json();
  } catch (err) {
    msg.textContent = "Could not read that file.";
    return;
  }
  if (d.error) { msg.textContent = d.error; return; }
  if (!d.lines.length) {
    msg.textContent = "No BOM lines found in that sheet.";
    return;
  }

  // Import happens at the top level, not inside whichever sub-assembly is
  // open. A spreadsheet describes the whole assembly, and dropping it into a
  // sub-assembly the user happened to have open would bury it.
  bomPath = [];
  const existing = bomTree.length;
  let mode = "replace";
  if (existing) {
    mode = confirm(`The BOM already has ${existing} line(s).\n\n`
      + `OK — replace them with the ${d.lines.length} line(s) from the file.\n`
      + `Cancel — keep them and add the imported lines underneath.`)
      ? "replace" : "append";
  }
  bomTree = mode === "replace" ? d.lines : bomTree.concat(d.lines);

  renderBom();
  scheduleSave();
  msg.textContent = `${d.lines.length} line(s) imported from ${d.sheet}`
    + (mode === "append" ? ` and added to the existing ${existing}.` : ".");
  renderBomImportReport(d);
}

function renderBomImportReport(d) {
  const box = document.getElementById("bomImportReport");
  const parts = [];

  parts.push(`<p class="hint">Read <b>${escapeXml(d.sheet)}</b>, header on row
    ${d.header_row}. Columns used:
    ${Object.entries(d.columns).map(([k, v]) =>
      `<code>${escapeXml(v)}</code>`).join(", ")}${
    d.process_columns.length
      ? ` \u00b7 process columns ${d.process_columns.join(", ")}` : ""}.</p>`);

  if (d.unknown_types.length) {
    parts.push(`<div class="bomissue">
      <b>${d.unknown_types.length} line(s) have no component type this app
      knows.</b> They were imported with the Type column left blank \u2014 pick
      a type for each, or delete the line. They are not costed until you do.
      <ul>${d.unknown_types.map(u => `<li>Row ${u.row}:
        ${escapeXml(u.part) || "(unnamed)"} \u2014 sheet says
        <code>${escapeXml(u.text)}</code></li>`).join("")}</ul></div>`);
  }
  if ((d.dimension_types || []).length) {
    parts.push(`<div class="bomissue ok">
      <b>${d.dimension_types.length} line(s) were typed from their dimensions.</b>
      OD with a bore is a Ring, OD on its own is a Circular Plate, length with
      a width is a Plate. The figures were carried into the part form too.
      <ul>${d.dimension_types.map(u => `<li>Row ${u.row}:
        ${escapeXml(u.part)} \u2014 ${escapeXml(u.why)} \u2192
        ${escapeXml(u.guess)}</li>`).join("")}</ul></div>`);
  }
  if (d.guessed_types.length) {
    parts.push(`<div class="bomissue warn">
      <b>${d.guessed_types.length} line(s) had no type column, so the type was
      taken from the part name.</b> Check these before calculating.
      <ul>${d.guessed_types.map(u => `<li>Row ${u.row}:
        ${escapeXml(u.part)} \u2192 ${escapeXml(u.guess)}</li>`).join("")}</ul></div>`);
  }
  if (d.bad_qty.length) {
    parts.push(`<div class="bomissue warn">
      <b>${d.bad_qty.length} quantity value(s) were not numbers</b>, so those
      lines came in as qty 1.
      <ul>${d.bad_qty.map(u => `<li>Row ${u.row}: ${escapeXml(u.part)} \u2014
        <code>${escapeXml(u.text)}</code></li>`).join("")}</ul></div>`);
  }
  if (d.unknown_codes.length) {
    parts.push(`<div class="bomissue warn">
      <b>Unrecognised process code(s), ignored.</b> Valid codes are
      ${MFG_PROCESSES.map(p => p[0]).join(", ")}.
      <ul>${d.unknown_codes.map(u => `<li>Row ${u.row}: ${escapeXml(u.part)}
        \u2014 <code>${escapeXml(u.code)}</code></li>`).join("")}</ul></div>`);
  }
  if (parts.length === 1) {
    parts.push(`<div class="bomissue ok">Every line was matched to a component
      type. Nothing needs attention.</div>`);
  }
  box.innerHTML = parts.join("");
  box.style.display = "";
}

function bomAction(r, i) {
  if (r.type === "__sub") {
    const n = (r.children || []).length;
    return `<button class="sec" onclick="bomOpen(${i})">Open (${n})</button>`;
  }
  if (r.type) {
    return `<button class="sec" onclick="bomToPart(${i})">Enter details</button>`;
  }
  return "";
}

function bomSet(i, key, val) {
  const rows = bomNodeAt(bomPath);
  rows[i][key] = (key === "qty" || key === "thk")
    ? (val === "" ? "" : parseFloat(val)) : val;
  if (key === "type" && val === "__sub" && !rows[i].children) rows[i].children = [];
  setTimeout(renderBom, 0);   // deferred: we are inside an onchange blur
  scheduleSave();
}

function bomAdd() {
  bomNodeAt(bomPath).push({ part_no: "", part_name: "", qty: 1, thk: "", type: "" });
  renderBom();
  scheduleSave();
}

function bomRemove(i) {
  bomNodeAt(bomPath).splice(i, 1);
  renderBom();
  scheduleSave();
}

function bomOpen(i) { bomPath.push(i); renderBom(); }
function bomUp() { bomPath.pop(); renderBom(); }

/* Carry the BOM line into the component's own input table, so the part number,
   name, quantity and thickness are not typed twice. */
async function bomToPart(i) {
  const r = bomNodeAt(bomPath)[i];
  if (!r.type || r.type === "__sub") return;
  await switchType(r.type);
  const rows = rowsByType[r.type];

  // THE BOM LINE OWNS ITS ROW.
  //
  // This used to grab "the first row with no part number or name", which is
  // right exactly once. After the row was filled in there was no blank left,
  // so the next press created another one - press Enter details three times
  // and you had three rows for one BOM line, none of them calculable and all
  // of them counted. Worse, a genuinely blank row belonging to a DIFFERENT
  // BOM line was fair game, so two lines could quietly seize the same row.
  //
  // The line now remembers which row is its own and goes back to it.
  let target = (r._row_id != null)
    ? rows.find(x => x._id === r._row_id) : null;

  // Not linked yet, or the row was deleted from the parts table. Match on
  // part number or name before creating anything, so BOMs built before this
  // version - and rows typed by hand - adopt the row already there instead of
  // duplicating it.
  if (!target) {
    target = rows.find(x =>
      (r.part_no && String(x.part_no) === String(r.part_no)) ||
      (r.part_name && x.part_name && String(x.part_name) === String(r.part_name)));
  }

  const fresh = !target;
  if (fresh) {
    const blank = rows.find(x => !x.part_no && !x.part_name && x._row_taken !== true);
    target = blank || (rows.push(blankRow(schema().fields)), rows[rows.length - 1]);
  }
  target._row_taken = true;
  r._row_id = target._id;

  // Only a brand new row is filled from the BOM. Coming back to an existing
  // row must not overwrite what was typed on the part form - that would throw
  // away the very work the button exists to let you do.
  if (fresh) {
    if (r.part_no) target.part_no = r.part_no;
    if (r.part_name && "part_name" in target) target.part_name = r.part_name;
    if (r.qty) target.qty = r.qty;
    if (r.thk !== "" && r.thk != null && "thk" in target) target.thk = r.thk;
    // Dimensions read off the imported spreadsheet. Only fields this component
    // type actually has are filled, so an OD from the sheet lands on a Ring or
    // a Circular Plate and is ignored by a Shell that has no such column.
    // "dia" is Round Bar's name for the same number.
    ["od", "id_", "length", "width", "dia"].forEach(k => {
      const v = k === "dia" ? r.od : r[k];
      if (v !== "" && v != null && k in target) target[k] = v;
    });
  }
  flashRowId = target._id;

  // Pipe and flange take their dimensions from a standard, not from typing.
  // A row created here skipped the tab's normal setup, so the lookup has to
  // be run explicitly — otherwise OD, wall thickness and kg/m stay blank and
  // the row can never be completed.
  const init = fresh && r.type === "Pipe" ? initializePipeRow
             : fresh && r.type === "Flange" ? initializeFlangeRow : null;
  if (init) await init(target);

  renderTable();
  renderTabCounts();
  renderBomSide();
  showWork("inputWork");
  scheduleSave();

  // Point at the row, then stop pointing. Without this, pressing Enter details
  // on a line whose row is already filled looks like nothing happened.
  const el = document.querySelector("#partsTable tbody tr.flashrow");
  if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
  setTimeout(() => { flashRowId = null; renderTable(); }, 2200);
}


/* The assembly tree, always on the left while a project is open.

   It is a reference, not a second place to edit: the user can see the whole
   structure and where they are in it while typing dimensions on another tab,
   without having to go back to the BOM to remember what a part was called.
   Clicking a line navigates to it. */
function renderBomSide() {
  const side = document.getElementById("bomSide");
  if (!side) return;
  if (!currentProject) {
    side.style.display = "none";
    document.body.classList.remove("has-bomside");
    return;
  }
  side.style.display = "";
  document.body.classList.add("has-bomside");

  let parts = 0, subs = 0;
  const walk = (rows, path, depth) => rows.map((r, i) => {
    const here = path.concat(i);
    const label = r.part_name || r.part_no || "(unnamed)";
    const isSub = r.type === "__sub";
    if (isSub) subs++; else if (r.type) parts++;

    // A line is "done" once its component actually has a row in the parts
    // table, so the tree doubles as a checklist of what still needs entering.
    const entered = !isSub && r.type && (rowsByType[r.type] || [])
      .some(x => (r.part_no && x.part_no === r.part_no) ||
                 (r.part_name && x.part_name === r.part_name));

    const open = bomPath.length > depth && bomPath[depth] === i;
    return `
      <div class="bnode${open ? " on" : ""}" style="padding-left:${8 + depth * 14}px">
        <span class="bicon">${isSub ? "\u25be" : (entered ? "\u2713" : "\u25aa")}</span>
        <span class="bname" onclick="bomJump(${JSON.stringify(here).replace(/"/g, "&quot;")})">${label}</span>
        ${r.qty > 1 ? `<span class="bqty">\u00d7${r.qty}</span>` : ""}
        ${r.type && !isSub ? `<span class="btype">${r.type}</span>` : ""}
      </div>
      ${isSub ? walk(r.children || [], here, depth + 1) : ""}`;
  }).join("");

  const html = walk(bomTree, [], 0);
  document.getElementById("bomSideTree").innerHTML =
    html || `<p class="bempty">Nothing yet. Add BOM lines to build the
      assembly tree.</p>`;
  document.getElementById("bomSideMeta").textContent =
    parts || subs ? `${parts} part(s), ${subs} sub-assembly(ies)` : "";
}

/* Click a tree line: go to its own BOM level if it is a sub-assembly,
   otherwise open the BOM containing it. */
function bomJump(path) {
  const idx = path[path.length - 1];
  const parent = path.slice(0, -1);
  let node = bomTree;
  for (const i of parent) node = node[i].children || [];
  const row = node[idx];
  bomPath = row && row.type === "__sub" ? path.slice() : parent;
  showWork("bomWork");
}

// ---- vendors ---------------------------------------------------------------

let vendorCats = [];
let editingVendor = null;

async function loadVendors() {
  const d = await (await fetch("/api/vendors")).json();
  vendorCats = d.categories || [];
  const list = d.vendors || [];
  document.getElementById("vendorMeta").textContent =
    list.length === 1 ? "1 vendor" : `${list.length} vendors`;

  const box = document.getElementById("vendorList");
  if (!list.length) {
    box.innerHTML = `<p class="empty-note">No vendors yet. Press
      <b>+ Add Vendor</b> to add one.</p>`;
    return;
  }

  // Grouped by what they supply, because that is how an RFQ finds them.
  const groups = {};
  list.forEach(v => (v.categories.length ? v.categories : ["Uncategorised"])
    .forEach(c => (groups[c] = groups[c] || []).push(v)));

  box.innerHTML = Object.keys(groups).sort().map(cat => `
    <h3 class="sub-h">${cat} <span class="catcount">${groups[cat].length}</span></h3>
    ${groups[cat].map(v => {
      const c = (v.contacts || []).find(x => x.name || x.email) || {};
      return `
      <div class="proj-card">
        <div class="proj-info">
          <div class="proj-name">${v.company.name}</div>
          <div class="proj-meta">
            ${v.company.city ? v.company.city + " &middot; " : ""}
            ${c.name ? c.name + " &middot; " : ""}
            ${c.email || ""} ${c.phone ? "&middot; " + c.phone : ""}
          </div>
          ${v.company.products ? `<div class="proj-meta">${v.company.products}</div>` : ""}
        </div>
        <div class="proj-btns">
          <button class="sec" onclick="editVendor('${v.id}')">Edit</button>
          <button class="sec danger" onclick="removeVendor('${v.id}')">Delete</button>
        </div>
      </div>`;
    }).join("")}`).join("");
}

function vendorForm(v) {
  const c = v ? v.company : {};
  const ct = (v && v.contacts) || [{}, {}, {}];
  const set = (id, val) => document.getElementById(id).value = val || "";
  set("vCompany", c.name); set("vCity", c.city); set("vAddress", c.address);
  set("vProducts", c.products); set("vWebsite", c.website);
  set("vPortfolio", c.portfolio);
  [1, 2, 3].forEach(i => {
    const x = ct[i - 1] || {};
    set(`vP${i}Name`, x.name); set(`vP${i}Email`, x.email); set(`vP${i}Phone`, x.phone);
  });
  set("vGst", v && v.gst); set("vPay", v && v.payment_terms);
  set("vNotes", v && v.notes);

  const chosen = (v && v.categories) || [];
  document.getElementById("vCats").innerHTML = vendorCats.map(cat => `
    <label class="catbox"><input type="checkbox" value="${cat}"
      ${chosen.includes(cat) ? "checked" : ""}> ${cat}</label>`).join("");
  document.getElementById("vendorModalErr").textContent = "";
  document.getElementById("vendorModalTitle").textContent =
    v ? "Edit Vendor" : "Add Vendor";
}

function openVendorModal(v) {
  editingVendor = v || null;
  vendorForm(v);
  document.getElementById("vendorModal").style.display = "";
  document.getElementById("vCompany").focus();
}

function closeVendorModal() {
  document.getElementById("vendorModal").style.display = "none";
  editingVendor = null;
}

async function editVendor(vid) {
  const d = await (await fetch("/api/vendors")).json();
  vendorCats = d.categories || [];
  openVendorModal((d.vendors || []).find(v => v.id === vid));
}

async function saveVendor() {
  const val = id => document.getElementById(id).value.trim();
  const cats = [...document.querySelectorAll("#vCats input:checked")]
    .map(x => x.value);
  const err = document.getElementById("vendorModalErr");

  if (!val("vCompany")) { err.textContent = "Company Name is required."; return; }
  // Without a category the vendor can never be found by an RFQ, so it is
  // required rather than optional.
  if (!cats.length) { err.textContent = "Choose at least one category \u2014 an RFQ finds vendors by what they supply."; return; }

  const payload = {
    id: editingVendor ? editingVendor.id : null,
    company: { name: val("vCompany"), city: val("vCity"), address: val("vAddress"),
               products: val("vProducts"), website: val("vWebsite"),
               portfolio: val("vPortfolio") },
    contacts: [1, 2, 3].map(i => ({ name: val(`vP${i}Name`),
                                    email: val(`vP${i}Email`),
                                    phone: val(`vP${i}Phone`) })),
    categories: cats, gst: val("vGst"),
    payment_terms: val("vPay"), notes: val("vNotes"),
  };
  const r = await (await fetch("/api/vendors/save", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })).json();
  if (r.error) { err.textContent = r.error; return; }
  closeVendorModal();
  await loadVendors();
}

async function removeVendor(vid) {
  await fetch("/api/vendors/delete", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: vid }),
  });
  await loadVendors();
}


// ---- approvals --------------------------------------------------------------

/* An RFQ is signed off in turn before it can be issued. The frozen snapshot
   taken at submission is what everyone reviews and what the documents are
   built from, so an approval always refers to something specific. */
let apState = null;

function apStatusTag(st) {
  const label = { pending: "in review", approved: "approved",
                  rejected: "rejected", issued: "issued", stale: "out of date",
                  draft: "draft" }[st] || st;
  return `<span class="apstat ap-${st}">${label}</span>`;
}

/* Each step shows the role AND who holds it: the person who signed off, or
   the person it is waiting on. A chain of bare job titles does not tell you
   whose desk it is sitting on. */
function apChainStrip(rec) {
  const holders = (apState && apState.holders) || {};
  return `<div class="apchain">` + (rec.steps || []).map(s => {
    const who = holders[s.designation] || [];
    const names = who.map(h => h.name).join(", ");
    let sub;
    if (s.by) sub = s.by;                                  // who actually acted
    else if (!names) sub = "nobody registered";            // a real gap
    else sub = names;
    return `
    <span class="apstep ap-${s.state}${!s.by && !names ? " ap-gap" : ""}"
          title="${s.at ? s.at : "not yet"}">
      <b>${s.designation}</b>${s.state === "approved" ? " \u2713"
        : s.state === "rejected" ? " \u2717" : ""}
      <span class="apwho">${sub}</span></span>`;
  }).join("") + `</div>`;
}

function apCard(rec, actionable) {
  const snap = rec.snapshot || {};
  const items = snap.items || [];
  const vendors = snap.vendors || [];
  return `
  <div class="apcard">
    <div class="aphead">
      <div>
        <code>${rec.id}</code> ${apStatusTag(rec.status)}
        <div class="apname">${rec.project_number} \u00b7 ${rec.project_name}</div>
        <div class="apmeta">Raised by ${rec.submitted_by?.name || "\u2014"}
          (${rec.submitted_by?.designation || "\u2014"}) on ${rec.created || ""}
          ${rec.note ? ` \u2014 \u201c${rec.note}\u201d` : ""}</div>
      </div>
    </div>
    ${apChainStrip(rec)}
    <div class="apbody">
      <b>${items.length} line(s)</b>, ${fmt(snap.total_kg || 0, 0)} kg &nbsp;\u00b7&nbsp;
      to ${vendors.map(v => v.name).join(", ") || "no vendor"}
      <div class="tablewrap" style="margin-top:8px;max-height:30vh">
        <table><thead><tr><th>Item</th><th>Material</th><th>Size</th>
          <th>Thickness</th><th>Qty</th><th>Weight</th></tr></thead>
        <tbody>${items.map(i => `<tr>
          <td>${i.description}</td><td>${i.material}</td><td>${i.size}</td>
          <td>${i.thickness}${i.length ? `<span class="sub">${i.length}</span>` : ""}</td>
          <td class="num">${i.qty}</td>
          <td class="num">${fmt(i.weight_kg, 0)} kg</td></tr>`).join("")}</tbody></table>
      </div>
    </div>
    ${rec.status === "stale" ? `<p class="hint warnhint">The project changed
      after this was submitted, so the approvals no longer describe what would
      be sent. Withdraw it and submit again.</p>` : ""}
    <div class="aprow">
      <button class="sec" onclick="viewApprovalRfq('${rec.id}')">View RFQ</button>
      <span class="hint">Opens the document exactly as the vendor will receive it.</span>
    </div>
    ${actionable ? `
      <div class="aprow">
        <input type="text" class="apnote" id="apNote-${rec.id}"
               placeholder="Comment (optional)">
        <button onclick="apAct('${rec.id}','approve')">Approve</button>
        <button class="sec danger" onclick="apAct('${rec.id}','reject')">Reject</button>
      </div>` : ""}
    ${rec.status === "approved" ? `
      <div class="aprow">
        <button onclick="apIssue('${rec.id}')">Generate &amp; send RFQ</button>
        <span class="hint">Documents are written per vendor, and emailed if
          mail is set up.</span>
      </div>` : ""}
    ${(rec.status === "pending" || rec.status === "stale") ? `
      <div class="aprow">
        <button class="sec" onclick="apAct('${rec.id}','withdraw')">Withdraw</button>
      </div>` : ""}
    ${rec.issued_at ? `<p class="hint okhint">Issued ${rec.issued_at} \u2014 sent to ${(rec.issued_to || []).join(", ") || "vendors"}</p>` : ""}
  </div>`;
}

async function loadApprovals() {
  const pid = currentProject ? `?project=${currentProject.id}` : "";
  const d = await (await fetch("/api/approvals" + pid)).json();
  apState = d;

  const mine = d.mine || [];
  document.getElementById("apMeta").textContent = d.my_role
    ? `You are ${d.my_role}` : "";
  document.getElementById("apMine").innerHTML = mine.length
    ? mine.map(r => apCard(r, true)).join("")
    : `<p class="empty-note">Nothing is waiting for your approval.</p>`;

  const warn = document.getElementById("apMailWarn");
  warn.className = d.mail_configured ? "hint okhint" : "hint warnhint";
  warn.textContent = d.mail_configured
    ? "Mail is configured \u2014 approvers and vendors will be emailed."
    : "Mail is not configured. Everything still works, and every message is "
      + "recorded in the outbox below, but nothing is actually sent. "
      + "Fill in data/mail.json to enable it.";

  const all = d.project || [];
  document.getElementById("apAllMeta").textContent = currentProject
    ? `${currentProject.number || currentProject.id} \u00b7 ${all.length} request(s)`
    : "Open a project to see its requests";
  document.getElementById("apAll").innerHTML = all.length
    ? all.map(r => apCard(r, false)).join("")
    : `<p class="empty-note">No requests raised for this project yet.</p>`;

  await loadOutbox();
  apBadge();
}

function apBadge() {
  const n = (apState?.mine || []).length;
  const el = document.getElementById("apBadge");
  if (!el) return;
  if (el) el.textContent = n ? ` (${n})` : "";
}

async function loadOutbox() {
  try {
    const d = await (await fetch("/api/outbox")).json();
    const rows = d.messages || [];
    document.getElementById("apOutMeta").textContent =
      rows.length ? `${rows.length} message(s)` : "";
    document.querySelector("#apOutbox tbody").innerHTML = rows.length
      ? rows.map(m => `<tr>
          <td>${m.at || ""}</td><td>${(m.to || []).join(", ")}</td>
          <td>${m.subject || ""}</td>
          <td>${m.sent ? '<span class="fit yes">sent</span>'
                       : `<span class="fit no">${m.error || "not sent"}</span>`}</td>
        </tr>`).join("")
      : `<tr><td colspan="4" class="empty">Nothing yet.</td></tr>`;
  } catch (e) { /* outbox is informational */ }
}

async function apAct(rid, action) {
  const note = document.getElementById("apNote-" + rid);
  // Approving hands the request to the next reviewer. Naming them makes the
  // handover explicit rather than something the user has to infer.
  const r = await (await fetch(`/api/approvals/${rid}/act`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, comment: note ? note.value : "" }),
  })).json();
  if (r.error) { alert(r.error); return; }
  await loadApprovals();
}

/* Issuing runs through the normal RFQ route, which carries the approval gate
   and the staleness check - there is deliberately no second path that could
   skip them. */
async function apIssue(rid) {
  const rec = (apState?.mine || []).concat(apState?.project || [])
    .find(r => r.id === rid);
  if (!rec) return;
  if (!currentProject || currentProject.id !== rec.project_id) {
    alert("Open " + rec.project_number + " first \u2014 the documents are "
          + "generated from that project.");
    showPage("projectPage");
    return;
  }
  const ids = (rec.snapshot?.vendors || []).map(v => v.id);
  const plates = (lastResult && lastResult.plates_purchased) || [];
  const r = await (await fetch(`/api/projects/${rec.project_id}/rfq-rm`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ vendor_ids: ids, plates }),
  })).json();
  if (r.error) { alert(r.error); return; }
  if (r.cancelled) return;
  alert(`${r.written.length} RFQ(s) written to ${r.folder}`
    + (r.mailed?.length ? `\n\nEmail: ` + r.mailed
        .map(m => `${m.vendor} \u2014 ${m.status}`).join("\n") : ""));
  await loadApprovals();
}

/* ---- RFQ preview -------------------------------------------------------
   Renders rfq_rm.content() as the Word document lays it out: the two-column
   header block, one boxed table per item, then the standing clauses. Same
   dict the .docx is built from, so the two cannot say different things.      */

let rfqPreviewDocs = [], rfqPreviewIdx = 0, rfqPreviewOnConfirm = null;

function rfqDocHtml(c) {
  return `
    <div class="rfqhead">
      ${c.header.map(([l, r]) => `<div class="rfqhrow">
        <div>${escapeXml(l)}</div><div>${escapeXml(r)}</div></div>`).join("")}
    </div>
    <h3 class="rfqtitle">REQUEST FOR QUOTATION</h3>
    <p class="rfqno">RFQ No: ${escapeXml(c.rfq_no)}</p>
    <h4 class="rfqsec">${escapeXml(c.section_title)}</h4>
    ${c.items.length ? c.items.map(b => `
      <div class="rfqitem">
        ${b.rows.map((r, i) => `<div class="rfqirow${i === 0 ? " lead" : ""}">
          <span class="k">${i === 0 ? b.n + ") " : ""}${escapeXml(r[0])}:</span>
          <span class="v">${escapeXml(r[1])}</span></div>`).join("")}
      </div>`).join("")
    : `<p class="hint">${escapeXml(c.empty_note)}</p>`}
    <p class="rfqtotal">${escapeXml(c.total_text)}</p>
    ${c.sections.map(sec => `
      <h4 class="rfqsec">${escapeXml(sec.heading)}</h4>
      ${sec.style === "bullets"
        ? `<ul class="rfqlist">${sec.lines.map(l => `<li>${escapeXml(l)}</li>`).join("")}</ul>`
        : sec.lines.map(l => `<p class="rfqline">${escapeXml(l)}</p>`).join("")}
    `).join("")}`;
}

function rfqPreviewShow(i) {
  rfqPreviewIdx = i;
  document.getElementById("rfqPreviewBody").innerHTML =
    rfqDocHtml(rfqPreviewDocs[i]);
  [...document.querySelectorAll("#rfqPreviewTabs button")].forEach((b, n) =>
    b.classList.toggle("on", n === i));
}

function openRfqPreview(docs, opts) {
  opts = opts || {};
  rfqPreviewDocs = docs;
  rfqPreviewOnConfirm = opts.onConfirm || null;
  document.getElementById("rfqPreviewTitle").textContent =
    opts.title || "RFQ Preview";
  document.getElementById("rfqPreviewSub").textContent = opts.subtitle || "";
  document.getElementById("rfqPreviewErr").textContent = "";

  const tabs = document.getElementById("rfqPreviewTabs");
  tabs.innerHTML = docs.length > 1
    ? docs.map((d, i) => `<button type="button" onclick="rfqPreviewShow(${i})">${
        escapeXml(d.vendor_name || "Vendor " + (i + 1))}</button>`).join("")
    : "";

  const btn = document.getElementById("rfqPreviewConfirm");
  btn.style.display = rfqPreviewOnConfirm ? "" : "none";
  btn.textContent = opts.confirmLabel || "Send for Approval";
  document.getElementById("rfqPreviewModal").style.display = "";
  rfqPreviewShow(0);
}

function closeRfqPreview() {
  document.getElementById("rfqPreviewModal").style.display = "none";
  rfqPreviewOnConfirm = null;
}

/* Approver's copy: built from the frozen snapshot, so it shows the figures
   that were actually submitted rather than the project as it stands now. */
async function viewApprovalRfq(rid) {
  const d = await (await fetch(`/api/approvals/${rid}/rfq-preview`)).json();
  if (d.error) { alert(d.error); return; }
  openRfqPreview(d.docs, {
    title: `RFQ for ${rid}`,
    subtitle: d.docs.length > 1
      ? `${d.docs.length} documents \u2014 one per vendor. This is what will be sent.`
      : "This is what will be sent to the vendor.",
  });
}

/* Raise a request from the RFQ page: the items and vendors on screen are sent
   once and frozen. */
async function submitForApproval() {
  if (!currentProject) { showPage("projectPage"); return; }
  const msg = document.getElementById("rfqMsg");
  const ids = [...document.querySelectorAll("#rfqVendors input:checked")].map(x => x.value);
  const plates = (lastResult && lastResult.plates_purchased) || [];

  // Show it before it is committed. Once this is submitted the figures are
  // frozen and the chain starts, so the last chance to notice a wrong
  // thickness or the wrong vendor is here, not after three people have signed.
  msg.textContent = "Building preview\u2026";
  const pv = await (await fetch(`/api/projects/${currentProject.id}/rfq-preview`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ vendor_ids: ids, plates }),
  })).json();
  if (pv.error) { msg.textContent = pv.error; return; }
  msg.textContent = "";
  openRfqPreview(pv.docs, {
    title: "Review before sending",
    subtitle: pv.docs.length > 1
      ? `${pv.docs.length} documents \u2014 one per vendor. Check before submitting.`
      : "Check this before it goes for approval.",
    confirmLabel: "Send for Approval",
    onConfirm: () => reallySubmitForApproval(ids, plates),
  });
}

async function reallySubmitForApproval(ids, plates) {
  const msg = document.getElementById("rfqMsg");
  msg.textContent = "Submitting\u2026";
  const r = await (await fetch(`/api/projects/${currentProject.id}/submit-approval`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ vendor_ids: ids, plates,
                           note: document.getElementById("rfqNote")?.value || "" }),
  })).json();
  if (r.error) { msg.textContent = r.error; return; }
  msg.textContent = `Sent for approval as ${r.request.id}. `
    + `Waiting on ${r.request.steps.find(s => s.state === "pending")?.designation || "review"}.`;
  showPage("approvalPage");
}

// ---- raw material RFQ ------------------------------------------------------

async function loadRfqPage() {
  const meta = document.getElementById("rfqMeta");
  const tb = document.querySelector("#rfqItemTable tbody");
  const vbox = document.getElementById("rfqVendors");

  if (!currentProject) {
    meta.textContent = "No project open";
    tb.innerHTML = `<tr><td colspan="7" class="empty">Open a project first \u2014
      an RFQ is raised against a project.</td></tr>`;
    vbox.innerHTML = "";
    return;
  }
  meta.textContent = `${currentProject.number || currentProject.id} \u00b7 ${currentProject.name}`;

  const plates = (lastResult && lastResult.plates_purchased) || [];
  let d = { items: [], total_kg: 0 };
  try {
    const res = await fetch(`/api/projects/${currentProject.id}/rm-items`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plates }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    d = await res.json();
  } catch (e) {
    // Refresh must say when it fails. Silently leaving the old table on screen
    // is what made the button look dead.
    tb.innerHTML = `<tr><td colspan="7" class="empty">Could not load the
      material list \u2014 ${e.message}</td></tr>`;
    vbox.innerHTML = "";
    return;
  }

  const items = d.items || [];
  tb.innerHTML = items.length
    ? items.map((it, i) => `
      <tr>
        <td class="num">${i + 1}</td>
        <td>${it.description}</td>
        <td>${it.material}</td>
        <td>${it.size}</td>
        <td>${it.thickness}${it.length ? `<span class="sub">${it.length}</span>` : ""}</td>
        <td class="num">${it.qty}</td>
        <td class="num">${fmt(it.weight_kg, 0)} kg</td>
      </tr>`).join("") +
      `<tr class="total"><td colspan="6">Total approximate weight</td>
        <td class="num strong">${fmt(d.total_kg, 0)} kg</td></tr>`
    : `<tr><td colspan="7" class="empty">Nothing to quote yet. Calculate and
       save the finished job so the plate to be bought is known.</td></tr>`;

  const v = await (await fetch("/api/vendors")).json();
  const rm = (v.vendors || []).filter(x => (x.categories || []).includes("Raw Material"));
  vbox.innerHTML = rm.length
    ? rm.map(x => `<label class="catbox"><input type="checkbox" value="${x.id}" checked>
        ${x.company.name}${x.company.city ? ` <span class="sub">${x.company.city}</span>` : ""}</label>`).join("")
    : `<p class="hint warnhint">No raw material vendors yet. Add one on the
       Vendor List page and tick <b>Raw Material</b>.</p>`;
}

async function generateVendorRfqs() {
  if (!currentProject) return;
  const msg = document.getElementById("rfqMsg");
  const ids = [...document.querySelectorAll("#rfqVendors input:checked")]
    .map(x => x.value);
  msg.textContent = "Generating\u2026";
  const plates = (lastResult && lastResult.plates_purchased) || [];
  try {
    const res = await fetch(`/api/projects/${currentProject.id}/rfq-rm`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vendor_ids: ids, plates }),
    });
    // A refusal carries a reason worth reading - "not approved yet", "the
    // project changed since it was signed off". A crash returns an HTML page
    // with no JSON at all. Try for the reason, and only fall back to the bare
    // status code when there genuinely isn't one.
    if (!res.ok) {
      let reason = "";
      try { reason = (await res.json()).error || ""; } catch (e) { /* not JSON */ }
      msg.textContent = reason
        || `The server failed while building the RFQ (HTTP ${res.status}). `
           + "Nothing was saved. Check the console window for the traceback.";
      document.getElementById("rfqResult").innerHTML = "";
      return;
    }
    const r = await res.json();
    if (r.error) { msg.textContent = r.error; return; }
    // The files are built before the folder is asked for, so there is always
    // something to report. If the user cancelled the picker they stayed in the
    // app's own rfq folder rather than being lost.
    msg.textContent = `${r.written.length} RFQ(s) written`
      + (r.moved ? " to the folder you chose." : " to the app's rfq folder.");
    // Held back on purpose: the box was ticked before anyone had opened the
    // document that is about to reach a supplier. The files exist and are
    // archived; only the sending waits for the next press.
    if (r.held) {
      document.getElementById("rfqResult").innerHTML =
        `<div class="bomissue warn"><b>Not sent yet.</b> ${escapeXml(r.held)}</div>`
        + rfqWrittenTable(r);
      loadRfqArchive();
      return;
    }
    document.getElementById("rfqResult").innerHTML = `
      <table class="grid" style="margin-top:12px">
        <thead><tr><th>Vendor</th><th>RFQ No.</th><th>File</th></tr></thead>
        <tbody>${r.written.map(w => `
          <tr><td>${w.vendor}</td><td><code>${w.rfq_no}</code></td>
          <td>${w.file}</td></tr>`).join("")}</tbody>
      </table>
      <p class="hint">Saved in <b>${r.folder}</b>
        <a href="#" class="golink" onclick="revealPath('${r.folder.replace(/\\/g, "\\\\")}');return false">open folder</a></p>`
      + (r.moved ? "" : `<p class="hint">Press <b>Generate</b> again if you meant
         to put them somewhere else \u2014 the folder picker was cancelled or
         unavailable.</p>`);
  } catch (e) {
    msg.textContent = "Failed: " + e.message;
  }
}

// ---- page navigation -----------------------------------------------------
/* Four top-level pages. The project workspace (BOM, part details, results,
   summary) lives inside Projects and is switched with showWork(), so the top
   bar stays short and a job never appears to exist outside a project. */
function showPage(pageId) {
  if (pageId === "projectPage") loadProjects();
  if (pageId === "vendorPage") loadVendors();
  if (pageId === "rfqPage") { loadRfqPage(); loadRfqArchive(); }
  if (pageId === "statusPage") loadStatusPage();
  if (pageId === "reimbPage") loadReimbPage();
  if (pageId === "approvalPage") loadApprovals();

  document.querySelectorAll(".app-page").forEach(p => {
    p.style.display = p.id === pageId ? "" : "none";
  });
  document.querySelectorAll(".nav-btn").forEach(b => {
    b.classList.toggle("active", b.dataset.page === pageId);
  });
  // The workspace bar belongs to Projects only.
  const wb = document.getElementById("workBar");
  if (wb) wb.style.display = "none";
  const bs = document.getElementById("bomSide");
  if (bs) bs.style.display = "none";     // the tree belongs to a project
  document.body.classList.remove("has-bomside");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* Inside an open project. */
function showWork(workId) {
  if (workId === "summaryWork") loadSummary();
  if (workId === "bomWork") renderBom();
  // Repaint from the stored layout if the panel is empty - which it is for
  // anyone who did not press Calculate in this session, such as the team lead
  // or director being asked to approve it.
  if (workId === "resultsWork" && savedResult) {
    const body = document.querySelector("#resultsWork table tbody");
    if (!body || !body.children.length) {
      try { renderResults(savedResult); } catch (e) { /* older saved shape */ }
    }
  }

  document.querySelectorAll(".app-page").forEach(p => {
    p.style.display = p.id === workId ? "" : "none";
  });
  document.querySelectorAll(".work-btn").forEach(b => {
    b.classList.toggle("active", b.dataset.work === workId);
  });
  document.querySelectorAll(".nav-btn").forEach(b => {
    b.classList.toggle("active", b.dataset.page === "projectPage");
  });
  renderBomSide();
  const wb = document.getElementById("workBar");
  if (wb) {
    wb.style.display = "";
    document.getElementById("workProj").textContent = currentProject
      ? `${currentProject.number || currentProject.id} \u00b7 ${currentProject.name}` : "";
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// ---- init ----
document.getElementById("typeTabs").addEventListener("click", e => {
  const el = e.target.closest("span[data-type]");
  if (el) switchType(el.dataset.type);
});
// Wrapped, not passed directly. addEventListener hands the click Event in as
// the first argument, so `calculate` would receive auto=Event — truthy — and
// the "explicit click navigates to Results" branch could never fire.
document.getElementById("calcBtn").addEventListener("click", () => calculate(false));
document.getElementById("useStd").addEventListener("change", () => {
  scheduleRecalc();
});
document.getElementById("combine").addEventListener("change", () => {
  scheduleRecalc(); scheduleSave();
});
["plateW", "plateL"].forEach(id =>
  document.getElementById(id).addEventListener("change", scheduleRecalc));
document.getElementById("platePreset").addEventListener("change", e => {
  if (e.target.value === "custom") {
    const pw = document.getElementById("plateW");
    pw.focus();
    pw.select();
    return;
  }
  const p = STANDARD_PLATES[e.target.value];
  if (p) {
    document.getElementById("plateW").value = p.width;
    document.getElementById("plateL").value = p.length;
    scheduleRecalc();
  }
});
document.getElementById("useRemnants").addEventListener("change", async function () {
  // Offcut stock is optional. If the user chooses it at the beginning of a
  // job and the rack is empty, warn immediately instead of silently falling
  // back to standard plate calculation.
  if (this.checked) {
    try {
      const res = await fetch("/api/remnants");
      const data = await res.json();
      const available = (data.remnants || []).filter(r => r.status === "available");
      if (!available.length) {
        alert("Offcut stock is empty. No offcut is available for this job. Please add stock or continue with standard plate.");
        this.checked = false;
        return;
      }
    } catch (e) {
      alert("Could not check offcut stock. Please try again.");
      this.checked = false;
      return;
    }
  }
  scheduleRecalc();
});
["cutRate", "pierceCharge"].forEach(id =>
  document.getElementById(id).addEventListener("change", () => {
    scheduleRecalc(); scheduleSave();
  }));
document.getElementById("commitBtn").addEventListener("click", commitJob);
document.getElementById("clearBookedBtn").addEventListener("click", clearBooked);
document.getElementById("clearJobsBtn").addEventListener("click", clearJobs);
document.getElementById("remAddBtn").addEventListener("click", addRemnant);
document.querySelectorAll("[data-page]").forEach(btn =>
  btn.addEventListener("click", () => showPage(btn.dataset.page)));
document.getElementById("backToInputBtn").addEventListener("click", () => showWork("inputWork"));
// Offcut stock moved to the Store section of the Project Enquiry tool.
const _toStock = document.getElementById("resultsToStockBtn");
if (_toStock) _toStock.addEventListener("click", () => showPage("stockPage"));
document.getElementById("stockToResultsBtn").addEventListener("click", () => showWork("resultsWork"));

// --- projects ---
// Bound exactly once, here. These three were previously spliced into the body
// of openProject(), so every time a project was opened they were bound again —
// after three opens, one click on Generate RFQ fired three times.
document.getElementById("newProjBtn").addEventListener("click", openProjModal);
document.getElementById("projModalCreate").addEventListener("click", createProject);
document.getElementById("projModalCancel").addEventListener("click", closeProjModal);
document.getElementById("projModalX").addEventListener("click", closeProjModal);
document.getElementById("projModal").addEventListener("click", e => {
  if (e.target.id === "projModal") closeProjModal();   // click the backdrop
});
document.addEventListener("keydown", e => {
  const open = document.getElementById("projModal").style.display !== "none";
  if (!open) return;
  if (e.key === "Escape") closeProjModal();
  if (e.key === "Enter" && e.target.tagName !== "TEXTAREA") createProject();
});
document.getElementById("rfqBtn").addEventListener("click", generateRfq);
document.getElementById("bomImportFile").addEventListener("change", e => {
  if (e.target.files[0]) importBomExcel(e.target.files[0]);
  e.target.value = "";       // so re-picking the same file still fires change
});

document.getElementById("importFile").addEventListener("change", e => {
  if (e.target.files[0]) importProject(e.target.files[0]);
  e.target.value = "";       // so re-picking the same file still fires change
});

document.getElementById("remMat").innerHTML =
  MATERIALS.map(m => `<option value="${m}">${m}</option>`).join("");

// Open on Projects. A job belongs to a project, and starting anywhere else
// invites entering parts that then have nowhere to be saved.
showPage("projectPage");
setProjBar();
loadRack();

(async () => {
  await loadProjects();
  // Launched by double-clicking a .rmp? Open that project straight away rather
  // than making the user hunt for it in the list.
  try {
    const st = await (await fetch("/api/startup")).json();
    if (st.open_project) await openProject(st.open_project);
  } catch (e) {
    /* the startup hint is optional — the list is already usable without it */
  }
})();

// --- project workspace sub-tabs ---
document.getElementById("workBar").addEventListener("click", e => {
  const b = e.target.closest("button[data-work]");
  if (b) showWork(b.dataset.work);
});

// --- BOM ---
document.getElementById("bomAddBtn").addEventListener("click", bomAdd);

// --- vendors ---
document.getElementById("vendorAddBtn").addEventListener("click", () => openVendorModal(null));
document.getElementById("rbAddRow").addEventListener("click", () => {
  rbItems.push(rbBlank()); renderReimb();
});
document.getElementById("rbDocx").addEventListener("click", reimbDocx);
document.getElementById("rbBillFile").addEventListener("change", e => {
  if (e.target.files.length) uploadBills(e.target.files);
  e.target.value = "";     // so the same file can be re-picked
});
["rbFrom", "rbTo", "rbAdvance"].forEach(id =>
  document.getElementById(id).addEventListener("change", rbRecalc));

document.getElementById("actModalSave").addEventListener("click", saveActivity);
document.getElementById("actModalCancel").addEventListener("click", closeActivity);
document.getElementById("actModalX").addEventListener("click", closeActivity);
document.getElementById("actModal").addEventListener("click", e => {
  if (e.target.id === "actModal") closeActivity();
});

document.getElementById("rfqPreviewCancel").addEventListener("click", closeRfqPreview);
document.getElementById("rfqPreviewX").addEventListener("click", closeRfqPreview);
document.getElementById("rfqPreviewModal").addEventListener("click", e => {
  if (e.target.id === "rfqPreviewModal") closeRfqPreview();
});
document.getElementById("rfqPreviewConfirm").addEventListener("click", () => {
  const go = rfqPreviewOnConfirm;
  closeRfqPreview();
  if (go) go();
});

document.getElementById("vendorModalSave").addEventListener("click", saveVendor);
document.getElementById("vendorModalCancel").addEventListener("click", closeVendorModal);
document.getElementById("vendorModalX").addEventListener("click", closeVendorModal);
document.getElementById("vendorModal").addEventListener("click", e => {
  if (e.target.id === "vendorModal") closeVendorModal();
});

// --- raw material RFQ ---
document.getElementById("rfqGenBtn").addEventListener("click", generateVendorRfqs);
document.getElementById("rfqSubmitBtn").addEventListener("click", submitForApproval);
document.getElementById("rfqRefreshBtn").addEventListener("click", loadRfqPage);

const firstType = document.querySelector("#typeTabs span")?.dataset.type;
if (firstType) switchType(firstType);
