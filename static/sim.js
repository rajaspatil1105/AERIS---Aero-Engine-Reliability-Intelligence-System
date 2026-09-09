"use strict";
// AERIS SIMULATION -- posts preset operating points to /frames at a chosen
// rate. Deck values, not MVEM: these are the points the twin was trained on.
var SM = { timer:null, t:0, dur:0, ses:null, sent:0 };

var SM_PRESET = {
  climb:  { throttle_pct:95, altitude_ft:6000, ambient_temperature_C:10,
            rpm:5600, fuelflow_kgh:20.041, coolant_temp_C:76.983,
            EGT_mean_C:564.866, oil_pressure_bar:3.890,
            oil_temperature_C:76.092 },
  cruise: { throttle_pct:80, altitude_ft:6000, ambient_temperature_C:10,
            rpm:5000, fuelflow_kgh:10.580, coolant_temp_C:66.619,
            EGT_mean_C:456.197, oil_pressure_bar:3.162,
            oil_temperature_C:70.843 },
  econ:   { throttle_pct:70, altitude_ft:8000, ambient_temperature_C:6,
            rpm:4600, fuelflow_kgh:6.540, coolant_temp_C:63.618,
            EGT_mean_C:406.706, oil_pressure_bar:2.827,
            oil_temperature_C:69.580 }
};

var SM_FAULT = {
  none:        null,
  cooling:     { ch:"coolant_temp_C",    mag:6.0,  ramp:120 },
  lubrication: { ch:"oil_temperature_C", mag:5.0,  ramp:120 },
  fuel:        { ch:"fuelflow_kgh",      mag:0.42, ramp:90  }
};

function smLog(s) { document.getElementById("sm-log").innerHTML = s; }

function smFrac(t, onset, ramp, clear) {
  if (t < onset) return 0;
  if (clear > 0 && t >= onset + clear)
    return Math.max(0, 1 - (t - onset - clear) / ramp);
  return Math.min(1, (t - onset) / ramp);
}

async function smPost() {
  var base = SM_PRESET[document.getElementById("sm-preset").value];
  var fk = document.getElementById("sm-fault").value;
  var spec = SM_FAULT[fk];
  var onset = parseFloat(document.getElementById("sm-onset").value) || 0;
  var clear = parseFloat(document.getElementById("sm-clear").value) || 0;

  var body = {}, k;
  for (k in base) body[k] = base[k];
  var off = 0;
  if (spec) {
    off = spec.mag * smFrac(SM.t, onset, spec.ramp, clear);
    body[spec.ch] = body[spec.ch] + off;
  }

  try {
    var r = await fetch("/frames", { method:"POST",
      headers:{ "Content-Type":"application/json" },
      body: JSON.stringify(body) });
    var d = await r.json();
    SM.sent++;
    smLog("t=" + SM.t.toFixed(1) + "s / " + SM.dur + "s &middot; frames " +
      SM.sent + " &middot; " + (d.status || "?") +
      (d.anomaly_probability === null || d.anomaly_probability === undefined
        ? "" : " p=" + Number(d.anomaly_probability).toFixed(4)) +
      (spec ? " &middot; " + spec.ch + " +" + off.toFixed(2) : ""));
  } catch (e) { smLog("post failed: " + e.message); }

  SM.t += 0.5;
  if (SM.t > SM.dur) smStop("mission complete, " + SM.sent + " frames");
}

async function smStart() {
  if (SM.timer) return;
  SM.t = 0; SM.sent = 0;
  SM.dur = parseFloat(document.getElementById("sm-dur").value) || 300;
  var rate = parseFloat(document.getElementById("sm-rate").value) || 1;
  try {
    var r = await fetch("/sessions", { method:"POST",
      headers:{ "Content-Type":"application/json" },
      body: JSON.stringify({ note:"UI simulation " +
        document.getElementById("sm-preset").value + " / " +
        document.getElementById("sm-fault").value }) });
    var d = await r.json();
    SM.ses = d.session_id || d.id || null;
  } catch (e) { smLog("session open failed: " + e.message); return; }
  smLog("running, session " + SM.ses);
  SM.timer = setInterval(smPost, 500 / rate);
}

function smStop(msg) {
  if (SM.timer) { clearInterval(SM.timer); SM.timer = null; }
  smLog(msg || ("stopped at t=" + SM.t.toFixed(1) + "s, " + SM.sent + " frames"));
}

(function smWire() {
  document.getElementById("sm-start").onclick = smStart;
  document.getElementById("sm-stop").onclick = function () { smStop(); };
})();
// ---------------------------------------------------------------- //
// FAULT ALARM -- watches the verdict panel and beeps on the
// HEALTHY -> FAULT transition. Deliberately not inside render():
// this reads the DOM, so it cannot affect the scoring path.
// ---------------------------------------------------------------- //
var SM_ALARM = { last:null, ac:null };

function smBeep(times) {
  try {
    if (!SM_ALARM.ac)
      SM_ALARM.ac = new (window.AudioContext || window.webkitAudioContext)();
    var ac = SM_ALARM.ac, i;
    for (i = 0; i < times; i++) {
      var o = ac.createOscillator(), g = ac.createGain();
      var t0 = ac.currentTime + i * 0.28;
      o.type = "square";
      o.frequency.setValueAtTime(920, t0);
      g.gain.setValueAtTime(0.0001, t0);
      g.gain.exponentialRampToValueAtTime(0.18, t0 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.22);
      o.connect(g); g.connect(ac.destination);
      o.start(t0); o.stop(t0 + 0.24);
    }
  } catch (e) {}
}

(function smAlarmWire() {
  var el = document.getElementById("p-status");
  if (!el || !window.MutationObserver) return;
  new MutationObserver(function () {
    var txt = (el.textContent || "").trim().toUpperCase();
    var now = txt.indexOf("FAULT") === 0 ? "FAULT"
            : txt.indexOf("HEALTHY") === 0 ? "HEALTHY" : null;
    if (now === null) return;
    if (SM_ALARM.last === "HEALTHY" && now === "FAULT") smBeep(3);
    if (SM_ALARM.last === "FAULT" && now === "HEALTHY") smBeep(1);
    SM_ALARM.last = now;
  }).observe(el, { childList:true, subtree:true, characterData:true });
})();
