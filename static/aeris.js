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
    return "<tr><td>" + k.replace(/_/g, " ") + "</td><td>" + num(m, 2) +
      "</td><td>" + num(e, 2) + '</td><td class="' + cls + '">' +
      (isNum(d) ? (d > 0 ? "+" : "") + d.toFixed(2) : "--") +
      "</td><td>" + num(res[k], 2) + "</td></tr>";
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
    const dead = k === "fuel_pressure_dev";
    const w = (p[k] * 100).toFixed(1);
    return '<div class="pr' + (dead ? " ph" : "") + '"><span>' +
      k.replace(/_/g, " ") + (dead ? ' <span class="tag">DEAD CLASS</span>' : "") +
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
    '<div class="dim">trend ' + num(f.rul_trend_per_minute, 4) + "/min" +
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
  $("h-ses").textContent = frames;
  $("h-utc").textContent = utc(f.timestamp);

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

  const x = f.features || {};
  $("b-thr").textContent = num(x.throttle_pct, 1) + " %";
  $("b-rpm").textContent = num(x.rpm, 0);
  $("b-alt").textContent = num(x.altitude_ft, 0) + " ft";
  $("b-oat").textContent = num(x.ambient_temperature_C, 1) + " C";
  $("b-cnt").textContent = frames;
}

function link(up, why) {
  const el = $("link");
  el.className = up ? "up" : "down";
  el.textContent = up ? "LINK OK" : (why || "NO LINK");
}

async function tick() {
  let f;
  try {
    const r = await fetch("/live", { cache: "no-store" });
    if (!r.ok) { link(false, "HTTP " + r.status); return; }
    f = await r.json();
  } catch (e) {
    if (++misses > 2) { link(false); document.body.classList.add("stale"); }
    return;
  }
  if (!f || !f.status) { link(false, "NO FRAME"); return; }
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