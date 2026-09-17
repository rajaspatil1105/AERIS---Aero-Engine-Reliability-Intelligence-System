"use strict";
// AERIS SIMULATION -- drives the SERVER-SIDE mission engine via POST /sim/run.
//
// Rewritten 2026-09-12. The previous version posted hand-copied channel values
// straight to /frames and injected faults as client-side additive offsets.
// Both were wrong after the MVEM refit: the cruise preset carried EGT 456.2 C
// where MVEM solves 740.8, so a "healthy" mission scored FAULT 0.9999 on every
// frame. And an offset on one sensor channel is sensor_drift, not a failing
// pump -- it bypassed the engine model completely.
//
// Now the server owns the physics. This file sends an operating point and a
// fault name; shared/mission_engine.py solves MVEM, applies FORCED_FAULTS,
// runs the thermal lags and feeds each frame through the same scoring path.
var SM = { poll:null, ses:null };

// Operating points only. No channel values -- the server solves them, so these
// cannot drift out of sync with the engine model again.
var SM_PRESET = {
  climb:  { throttle_pct:95, altitude_ft:6000, oat_c:10 },
  cruise: { throttle_pct:80, altitude_ft:6000, oat_c:10 },
  econ:   { throttle_pct:70, altitude_ft:8000, oat_c:6  }
};

// Maps the existing select values to mission_engine FORCED_FAULTS keys.
// sensor_drift is absent by design: it is a measurement offset, not a physical
// degradation, so the mission engine does not forge one.
var SM_FAULT = {
  none:        null,
  cooling:     "cooling_degradation",
  lubrication: "lubrication_degradation",
  fuel:        "fuel_pressure_dev",
  misfire:     "misfire"
};

function smLog(s) { document.getElementById("sm-log").innerHTML = s; }
function smVal(id, dflt) {
  var el = document.getElementById(id);
  var v = el ? parseFloat(el.value) : NaN;
  return isNaN(v) ? dflt : v;
}

async function smStart() {
  if (SM.poll) return;
  var pk = document.getElementById("sm-preset").value;
  var fk = document.getElementById("sm-fault").value;
  var base = SM_PRESET[pk];
  var fault = SM_FAULT[fk] || null;
  var dur = smVal("sm-dur", 300);
  var onset = smVal("sm-onset", 0);
  var clear = smVal("sm-clear", 0);

  var body = {
    engine_serial: "RTX915-0003",
    throttle_pct: base.throttle_pct,
    altitude_ft: base.altitude_ft,
    oat_c: base.oat_c,
    duration_s: dur,
    emit_cruise_s: 10.0,
    emit_event_s: 1.0,
    speed: smVal("sm-rate", 1)
  };
  if (fault) {
    body.fault = fault;
    body.fault_severity = "severe";
    body.fault_at_s = onset;
    // The form asks for a DURATION after onset; the route wants an absolute
    // mission time. 0 means "never clear".
    if (clear > 0) body.fault_clear_s = onset + clear;
  }

  try {
    var r = await fetch("/sim/run", { method:"POST",
      headers:{ "Content-Type":"application/json" },
      body: JSON.stringify(body) });
    var d = await r.json();
    if (!r.ok || d.session_id === undefined) {
      smLog("run rejected: " + (d.detail ? JSON.stringify(d.detail) : r.status));
      return;
    }
    SM.ses = d.session_id;
  } catch (e) { smLog("run failed: " + e.message); return; }

  smLog("running server-side, session " + SM.ses + " &middot; " + pk +
        (fault ? " &middot; " + fault + " @ " + onset + "s" : " &middot; healthy"));
  SM.poll = setInterval(smTick, 1000);
}

async function smTick() {
  try {
    var r = await fetch("/sim/run/" + SM.ses);
    var d = await r.json();
    smLog("session " + SM.ses + " &middot; frames " + (d.frames || 0) +
          " &middot; faults " + (d.faults || 0) +
          (d.error ? " &middot; ERROR " + d.error : "") +
          (d.done ? " &middot; complete" : " &middot; running"));
    if (d.done || d.error) smStop(null, true);
  } catch (e) { smLog("poll failed: " + e.message); smStop(null, true); }
}

function smStop(msg, quiet) {
  if (SM.poll) { clearInterval(SM.poll); SM.poll = null; }
  if (!quiet && SM.ses !== null) {
    // Ask the server to abort; the mission loop checks a cancel flag.
    fetch("/sim/run/" + SM.ses, { method:"DELETE" }).catch(function () {});
    smLog("cancel requested for session " + SM.ses);
  } else if (msg) { smLog(msg); }
}

(function smWire() {
  document.getElementById("sm-start").onclick = smStart;
  document.getElementById("sm-stop").onclick = function () { smStop(); };
})();
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
