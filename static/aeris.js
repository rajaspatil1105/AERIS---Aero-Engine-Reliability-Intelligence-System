"use strict";
const POLL_MS = 500, SETTLE_S = 100;
const $ = (id) => document.getElementById(id);
let frames = 0, misses = 0, logged = false;

// Display range only -- NOT a limit and NOT a healthy band. Bars are for
// glanceable movement; the residual table is what carries a verdict.
const RANGE = {
  throttle_pct:[0,100,"%",0], rpm:[0,8000,"rpm",0],
  altitude_ft:[0,20000,"ft",0], ambient_temperature_C:[-30,50,"C",1],
  fuelflow_kgh:[0,40,"kg/h",2], coolant_temp_C:[0,150,"C",1],
  EGT_mean_C:[0,1000,"C",0], oil_pressure_bar:[0,8,"bar",2],
  oil_temperature_C:[0,150,"C",1],
};
const ORDER = Object.keys(RANGE);

const isNum = (v) => typeof v === "number" && isFinite(v);
const num = (v, d = 1) => isNum(v) ? v.toFixed(d) : "--";
// Residual precision matters: the gate fires on ~0.002 units (predictor CASE
// 5), which two decimals renders as 0.00 -- the panel that carries the verdict
// must not hide the quantity that caused it.
const fine = (v) => !isNum(v) ? "--" : v === 0 ? "0" :
  Math.abs(v) < 0.01 ? v.toExponential(1) : v.toFixed(3);
const signed = (v) => !isNum(v) ? "--" : (v > 0 ? "+" : "") + fine(v);
const esc = (s) => String(s).replace(/[&<>]/g, (c) =>
  ({ "&":"&amp;", "<":"&lt;", ">":"&gt;" }[c]));

function statusClass(f) {
  if (f.status === "UNAVAILABLE" && f.refusal_class === "transient")
    return "s-TRANSIENT";
  if (f.safety_alert === true) return "s-CRITICAL";
  return "s-" + (f.status || "UNAVAILABLE");
}
// timestamp is an epoch float on /live, not an ISO string.
function utc(t) {
  if (isNum(t)) return new Date(t * 1000).toISOString().slice(0, 19).replace("T", " ");
  if (typeof t === "string") return t.replace("T", " ").slice(0, 19);
  return "--";
}
function flagged(ch, f) {
  return (f.advisories || []).some((a) => a.indexOf(ch) === 0) ||
         (f.safety_breaches || []).some((b) => String(b).indexOf(ch) >= 0);
}

function gauges(f) {
  const x = f.features || {};
  return ORDER.map((k) => {
    const [lo, hi, unit, dp] = RANGE[k];
    const v = x[k];
    const pct = isNum(v) ? Math.max(0, Math.min(100, (v - lo) / (hi - lo) * 100)) : 0;
    const bad = flagged(k, f);
    return '<div class="g' + (bad ? " g-bad" : "") + '">' +
      '<div class="g-h"><span>' + k.replace(/_/g, " ") + "</span>" +
      "<b>" + num(v, dp) + ' <i>' + unit + "</i></b></div>" +
      '<div class="g-t"><div class="g-f" style="width:' + pct.toFixed(1) + '%"></div></div>' +
      "</div>";
  }).join("");
}

function residuals(f) {
  const meas = f.features || {}, exp = f.expected || {}, res = f.residuals || {};
    const keys = Object.keys(exp).sort();
  if (!keys.length) return '<span class="dim">no residuals on this frame</span>';
  // The deck extrapolates outside its trained range, so expected values on a
  // refused frame are not a baseline. Show them marked, never as evidence.
  const void_ = f.in_envelope === false || f.refusal_class !== null;

  const rows = keys.map((k) => {
    const m = meas[k], e = exp[k];
    // residuals are UNSIGNED upstream; sign is recovered here so the operator
    // can tell hot from cold. Never infer direction from res[k] alone.
    const d = (isNum(m) && isNum(e)) ? m - e : null;
    const cls = flagged(k, f) ? "s-ADVISORY" : "s-HEALTHY";
    return "<tr><td>" + k.replace(/_/g, " ") + "</td><td>" + num(m, 3) +
      "</td><td>" + num(e, 3) + '</td><td class="' + cls + '">' +
      signed(d) +
      "</td><td>" + fine(res[k]) + "</td></tr>";
  }).join("");
    return (void_ ? '<div class="warn">frame refused: these physics values are ' +
    'extrapolated outside the trained deck range and are NOT a baseline. ' +
    'The deltas below carry no meaning.</div>' : "") +
    '<table class="t' + (void_ ? " ph" : "") + '"><thead><tr><th>channel</th><th>measured</th>' +

    "<th>physics</th><th>delta</th><th>|res|</th></tr></thead><tbody>" +
    rows + "</tbody></table>";
}

function diagnosis(f) {
  if (f.ml_evaluated !== true)
    return '<span class="dim">not scored &mdash; ' +
      esc(f.admit_reason || "frame not admitted") + "</span>";
    const p = f.fault_probabilities || {};
    const top = Math.max.apply(null, Object.values(p).concat([0]));
    const flat = top < 0.35;

  const bars = Object.keys(p).sort((a, b) => p[b] - p[a]).map((k) => {
    // NOT a dead class: it is the argmax at near-zero residual (predictor
    // CASE 5), never under large injection offsets. Tagged, not hidden.
    const nearZero = k === "fuel_pressure_dev";
    const w = (p[k] * 100).toFixed(1);
    return '<div class="pr' + (nearZero ? " ph" : "") + '"><span>' +
      k.replace(/_/g, " ") + (nearZero ? ' <span class="tag">NEAR-ZERO ARGMAX</span>' : "") +
      "</span><b>" + w + "%</b>" +
      '<div class="g-t"><div class="g-f" style="width:' + w + '%"></div></div></div>';
  }).join("");
  const g = (f.caveats && f.caveats.gate) || {};
  return '<div class="big ' + statusClass(f) + '">' +
        esc(f.fault_label || "no label") +
    (flat ? ' <span class="tag">NEAR CHANCE</span>' : "") + "</div>" +
    (flat ? '<div class="warn">top class ' + (top * 100).toFixed(1) +
      "% against 20% chance across 5 classes: this label is not a diagnosis" +
      "</div>" : "") +

    '<div class="dim">confidence ' + num(f.fault_confidence * 100, 1) +
    "% &middot; p_anom " + num(f.anomaly_probability, 4) +
    " vs gate " + num(f.gate_threshold, 2) + "</div>" +
    bars + '<div class="warn">' + esc(g.reason || "gate untrusted") + "</div>";
}

function rulBlock(f) {
  const c = (f.caveats && f.caveats.rul) || {}, m = c.metrics || {};
  return '<div class="big s-UNAVAILABLE">' + num(f.rul_raw, 1) + "</div>" +
    '<div class="dim">rul_raw &middot; units ' + esc(f.rul_units || "unknown") +
    " &middot; ordering only, not a time</div>" +
    '<div class="dim" style="margin-top:6px">smoothed ' + num(f.rul, 1) +
    " (EWMA, lags on one frame)</div>" +
    '<div class="dim">trend ' + fine(f.rul_trend_per_minute) + "/min" +
          " &middot; to zero " + (isNum(f.rul_minutes_to_zero) &&
      Math.abs(f.rul_minutes_to_zero) < 1e6 ?
      num(f.rul_minutes_to_zero, 1) + " min" : "n/a (trend ~0)") + "</div>" +

    '<div class="warn">rul_trusted=' + String(f.rul_trusted) +
    " &middot; R2 " + num(m.r2, 3) + " / MAE " + num(m.mae, 0) +
    " &mdash; " + esc(c.reason || "untrusted") + "</div>";
}

function admission(f) {
  if (f.refusal_class === null || f.refusal_class === undefined) {
    return '<div class="s-HEALTHY">ADMITTED</div>' +
      '<div class="dim">steady-state rules satisfied: throttle rate within ' +
      "0.5 %/s and past the settling window</div>";
  }
    // admit_reason carries transient text only; envelope refusals put their
  // reason in envelope_violations. Fall back so the line is never blank.
  const cls = f.refusal_class;
  const reason = f.admit_reason ||
    (f.envelope_violations || []).join("; ") || "(no reason given)";

  let bar = "";
  const mm = /([\d.]+)s of ([\d.]+)s/.exec(reason);
  if (cls === "transient" && mm) {
    const done = parseFloat(mm[1]), need = parseFloat(mm[2]) || SETTLE_S;
    const w = Math.max(0, Math.min(100, done / need * 100));
    bar = '<div class="g-t" style="margin-top:7px"><div class="g-f tran" style="width:' +
      w.toFixed(1) + '%"></div></div><div class="dim">settling ' +
      done.toFixed(1) + "s of " + need.toFixed(0) + "s</div>";
  }
  const label = { transient: "TRANSIENT &mdash; wait, not a fault",
    envelope_recoverable: "OUTSIDE ENVELOPE &mdash; will score again on return",
    envelope_persistent: "OUTSIDE ENVELOPE &mdash; will not recover this flight",
    telemetry_unusable: "TELEMETRY UNUSABLE" }[cls] || esc(cls);
  return '<div class="' + (cls === "transient" ? "s-TRANSIENT" : "s-UNAVAILABLE") +
    '">' + label + "</div>" +
    '<div class="dim" style="margin-top:4px">' + esc(reason) + "</div>" + bar +
    '<div class="dim" style="margin-top:6px">refusal_class=' + esc(cls) +
    " &mdash; no claim is made about the engine on a refused frame</div>";
}

function render(f) {
  const cls = statusClass(f);
  $("h-sys").textContent = f.status || "--";
  $("h-sys").className = cls;
  $("h-lat").textContent = num(f.latency_ms, 2) + " ms";
  // replay owns this field while it is driving; stored rows carry no session_id
  $("h-utc").textContent = utc(f.timestamp);
  if (!replayOn()) $("h-ses").textContent = isNum(f.session_id) ? f.session_id : "--";

  const adv = (f.advisories || []).map((a) =>
    '<li class="s-ADVISORY">' + esc(a) + "</li>").join("");
  const brk = (f.safety_breaches || []).map((b) =>
    '<li class="s-CRITICAL">' + esc(b) + "</li>").join("");
  const env = (f.envelope_violations || []).map((v) =>
    '<li class="s-UNAVAILABLE">' + esc(v) + "</li>").join("");
  $("p-status").innerHTML =
    '<div class="big ' + cls + '">' + (f.status || "UNAVAILABLE") + "</div>" +
    '<div class="dim">' + esc(f.headline || "no headline") + "</div>" +
    '<div class="dim" style="margin-top:5px">in_envelope=' + String(f.in_envelope) +
    " &middot; ml_evaluated=" + String(f.ml_evaluated) +
    " &middot; safety_alert=" + String(f.safety_alert) + "</div>" +
    (adv + brk + env ? '<ul class="ev">' + brk + env + adv + "</ul>" : "");

  $("p-gauges").innerHTML = gauges(f);
  $("p-resid").innerHTML = residuals(f);
  $("p-diag").innerHTML = diagnosis(f);
  $("p-rul").innerHTML = rulBlock(f);
  $("p-refuse").innerHTML = admission(f);
  paintSchematic(f);

  const x = f.features || {};
  $("b-thr").textContent = num(x.throttle_pct, 1) + " %";
  $("b-rpm").textContent = num(x.rpm, 0);
  $("b-alt").textContent = num(x.altitude_ft, 0) + " ft";
  $("b-oat").textContent = num(x.ambient_temperature_C, 1) + " C";
  $("b-cnt").textContent = frames;
}

// ---- COLD STATE ------------------------------------------------------
// No engine, no frame, server down: the shell STAYS. An operator navigates
// by panel position, so panels never collapse to a sentence -- every row
// keeps its label and unit and reads "--". No invented values anywhere.
const RESID_CHANNELS = ["EGT_mean_C", "coolant_temp_C", "fuelflow_kgh",
                        "oil_pressure_bar", "oil_temperature_C"];
const FAULT_CLASSES = ["cooling_degradation", "fuel_pressure_dev",
                       "lubrication_degradation", "misfire", "sensor_drift"];

function coldTable() {
  const rows = RESID_CHANNELS.map((k) =>
    "<tr><td>" + k.replace(/_/g, " ") +
    "</td><td>--</td><td>--</td><td>--</td><td>--</td></tr>").join("");
  return '<table class="t ph"><thead><tr><th>channel</th><th>measured</th>' +
    "<th>physics</th><th>delta</th><th>|res|</th></tr></thead><tbody>" +
    rows + "</tbody></table>";
}

function coldBars() {
  return FAULT_CLASSES.map((k) =>
    '<div class="pr ph"><span>' + k.replace(/_/g, " ") + "</span><b>--</b>" +
    '<div class="g-t"><div class="g-f" style="width:0%"></div></div></div>'
  ).join("");
}

// Single owner for the SESSION header: replay writes it while scrubbing,
// live and cold-state write it the rest of the time. Two writers = "--" wins.
function replayOn() {
  const b = document.getElementById("rp-bar");
  return !!b && b.style.display !== "none";
}

function renderCold(why) {
  $("p-status").innerHTML =
    '<div class="big s-UNAVAILABLE">NO ENGINE</div>' +
    '<div class="dim">' + esc(why || "no telemetry source connected") +
    "</div>" +
    '<div class="dim" style="margin-top:5px">in_envelope=-- &middot; ' +
    "ml_evaluated=-- &middot; safety_alert=--</div>";
  $("p-gauges").innerHTML = gauges({});
  $("p-resid").innerHTML = coldTable();
  $("p-diag").innerHTML =
    '<div class="big s-UNAVAILABLE">--</div>' +
    '<div class="dim">no frame scored</div>' + coldBars();
  $("p-rul").innerHTML =
    '<div class="big s-UNAVAILABLE">--</div>' +
    '<div class="dim">rul_raw &middot; units unknown &middot; ordering only, ' +
    "not a time</div>" +
    '<div class="dim">smoothed -- &middot; trend --/min</div>';
  $("p-refuse").innerHTML =
    '<div class="s-UNAVAILABLE">NOT EVALUATED</div>' +
    '<div class="dim">the admission gate runs on the first frame</div>';
  paintSchematic({});
  $("h-sys").textContent = "--";
  $("h-lat").textContent = "-- ms";
  $("h-utc").textContent = "--";
  if (!replayOn()) $("h-ses").textContent = "--";
  ["b-thr", "b-rpm", "b-alt", "b-oat"].forEach((k) => {
    const el = $(k); if (el) el.textContent = "--";
  });
}

function link(up, why) {
  const el = $("link");
  el.className = up ? "up" : "down";
  el.textContent = up ? "LINK OK" : (why || "NO LINK");
}

async function tick() {
  if (typeof RP !== "undefined" && RP.frozen) return;   // replay owns the panels
  let f;
  try {
    const r = await fetch("/live", { cache: "no-store" });
    if (!r.ok) { link(false, "HTTP " + r.status); renderCold("no frame processed yet (HTTP " + r.status + ")"); return; }
    f = await r.json();
  } catch (e) {
    if (++misses > 2) { link(false); document.body.classList.add("stale"); renderCold("service unreachable"); }
    return;
  }
  if (!f || !f.status) { link(false, "NO FRAME"); renderCold("no frame yet"); return; }
  if (!logged) { console.log("/live keys:", Object.keys(f).length, Object.keys(f).sort()); logged = true; }
  frames++; misses = 0;
  document.body.classList.remove("stale");
  link(true);
  // A render fault must be visible, not swallowed into a green link.
  try { render(f); }
  catch (e) { link(false, "RENDER ERR"); console.error("render failed:", e); }
}

document.querySelectorAll(".tab:not(.ph)").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("on"));
    b.classList.add("on");
    $("h-mode").textContent = b.dataset.view.toUpperCase();
  });
});

tick();
setInterval(tick, POLL_MS);

// ---- view switching -------------------------------------------------
// Tabs previously only relabelled the header, so REPORT rendered the
// tactical grid with the replay bar still open. One body class now owns
// which root is visible; replay is force-exited on the way out.
function showView(v) {
  document.body.classList.remove("view-report", "view-config", "view-sim");
  if (v === "report" || v === "config" || v === "sim") document.body.classList.add("view-" + v);
  const hm = document.getElementById("h-mode");
  if (hm) hm.textContent = v.toUpperCase();
}

document.querySelectorAll(".tab[data-view]").forEach((b) => {
  b.addEventListener("click", () => {
    const v = (b.dataset.view || "tactical").toLowerCase();
    if (v !== "replay" && typeof RP !== "undefined" && RP.on
        && typeof rpExit === "function") rpExit();
    showView(v);
    if (v === "report" && typeof repLoad === "function") repLoad();
    if (v === "config" && typeof cfgLoad === "function") cfgLoad();
  });
});

// ---- report + config views -----------------------------------------
// Downloads are plain anchors: the browser streams the file straight from
// the API. href="#" would make `download` save this page instead, which is
// exactly what happened before these were wired.
var REP = { ses: null, loaded: false };

function repRow(k, v) {
  return '<div class="kv"><i>' + esc(k) + "</i><b>" + esc(String(v)) + "</b></div>";
}

async function repRender(sid) {
  REP.ses = sid;
  $("rep-csv").href = "/report/" + sid + ".csv";
  $("rep-pdf").href = "/report/" + sid + ".pdf";
  var b = $("rep-body");
  try {
    var r = await fetch("/summary?session_id=" + sid, { cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    var s = await r.json();
    var sc = s.status_counts || {}, ec = s.event_counts || {};
    var tot = s.frames || 0, h = "", k;

    $("rep-sub").textContent =
      "session " + sid + "  \u00b7  " + tot + " frames  \u00b7  " +
      String(s.started_utc || "--").slice(0, 19).replace("T", " ") +
      (s.ended_utc ? "" : "  (open)");

    h += '<div class="rep-grid">';
    h += '<div class="rep-card"><h3>FRAME DISPOSITION</h3>';
    for (k in sc) h += repRow(k, sc[k] + "  (" +
      (tot ? (sc[k] / tot * 100).toFixed(1) : "0.0") + "%)");
    if (!Object.keys(sc).length) h += '<div class="dim">no frames</div>';
    h += "</div>";

    h += '<div class="rep-card"><h3>EVENTS</h3>';
    for (k in ec) h += repRow(k, ec[k]);
    if (!Object.keys(ec).length) h += '<div class="dim">none recorded</div>';
    h += "</div>";

    h += '<div class="rep-card"><h3>PROVENANCE</h3>';
    h += repRow("data", s.data_provenance || "--");
    h += repRow("models_trusted", String(!!s.models_trusted));
    h += repRow("sklearn", s.sklearn_version || "--");
    h += repRow("manifest", String(s.manifest_sha256 || "--").slice(0, 16) + "...");
    h += "</div></div>";

    h += '<div class="warn" style="margin-top:10px">Exports carry the same ' +
         'caveats as the live panels. The PDF states on its cover that these ' +
         'models are unvalidated placeholders.</div>';
    b.innerHTML = h;
  } catch (e) {
    b.innerHTML = '<div class="warn">summary failed: ' + esc(e.message) + "</div>";
  }
}

async function repLoad() {
  var sel = $("rep-ses");
  try {
    var r = await fetch("/sessions", { cache: "no-store" });
    var d = await r.json(), list = d.sessions || [], h = "", i, s;
    if (!list.length) {
      $("rep-body").innerHTML = '<div class="dim">no sessions recorded yet</div>';
      $("rep-sub").textContent = "nothing to report";
      return;
    }
    for (i = 0; i < list.length; i++) {
      s = list[i];
      h += '<option value="' + s.id + '">' + s.id + "  " +
           String(s.started_utc).slice(0, 19).replace("T", " ") +
           (s.ended_utc ? "" : "  (open)") + "</option>";
    }
    sel.innerHTML = h;
    if (!REP.loaded) {
      sel.onchange = function () { repRender(parseInt(this.value, 10)); };
      REP.loaded = true;
    }
    await repRender(parseInt(sel.value, 10));
  } catch (e) {
    $("rep-body").innerHTML = '<div class="warn">/sessions failed: ' +
      esc(e.message) + "</div>";
  }
}

async function cfgLoad() {
  var b = $("cfg-body");
  try {
    var m = await (await fetch("/manifest", { cache: "no-store" })).json();
    var c = await (await fetch("/caveats", { cache: "no-store" })).json();
    var h = '<div class="rep-grid">';
    h += '<div class="rep-card"><h3>BUILD</h3>';
    h += repRow("manifest version", m.version || m.manifest_version || "--");
    h += repRow("models_trusted", String(!!m.models_trusted));
    h += repRow("sklearn", m.sklearn_version || "--");
    h += "</div>";
    h += '<div class="rep-card"><h3>TRAINED ENVELOPE</h3>';
    var env = m.envelope || (m.baseline && m.baseline.envelope) || {};
    for (var k in env) h += repRow(k, "[" + env[k][0] + ", " + env[k][1] + "]");
    if (!Object.keys(env).length)
      h += '<div class="dim">not exposed by /manifest</div>';
    h += "</div></div>";
    h += '<div class="rep-card" style="margin-top:10px"><h3>CAVEATS</h3>';
    var cav = c.caveats || c;
    for (var q in cav)
      h += '<div style="margin-bottom:7px"><b>' + esc(q) + "</b><br>" +
           '<span class="dim">' + esc(String(cav[q]).slice(0, 600)) + "</span></div>";
    h += "</div>";
    b.innerHTML = h;
  } catch (e) {
    b.innerHTML = '<div class="warn">config load failed: ' + esc(e.message) + "</div>";
  }
}