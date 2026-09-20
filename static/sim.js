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
// Presets now only JUMP the sliders; the sliders are the source of truth.
var SM_PRESET = {
  climb:  { thr:95, alt:6000, oat:10 },
  cruise: { thr:80, alt:6000, oat:10 },
  econ:   { thr:70, alt:8000, oat:6  }
};

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
  var fk = document.getElementById("sm-fault").value;
  var fault = SM_FAULT[fk] || null;
  var base = { throttle_pct: smVal("sm-thr", 80),
               altitude_ft:  smVal("sm-alt", 6000),
               oat_c:        smVal("sm-oat", 10) };
  var pk = base.throttle_pct + "% " + base.altitude_ft + "ft";
  // Duration, onset and clear all read in the selected unit.
  var uEl = document.getElementById("sm-durunit");
  var unit = uEl ? (parseFloat(uEl.value) || 1) : 1;
  var dur = smVal("sm-dur", 300) * unit;
  // Keep emitted frames near 600 whatever the mission length:
  // 10 s emit over 30 h would be 10800 scored frames (~1 h of work).
  var emitC = Math.min(600, Math.max(10, dur / 600));
  var onset = smVal("sm-onset", 0) * unit;
  var clear = smVal("sm-clear", 0) * unit;

  var body = {
    engine_serial: document.getElementById("sm-eng").value || "RTX915-0003",
    throttle_pct: base.throttle_pct,
    altitude_ft: base.altitude_ft,
    oat_c: base.oat_c,
    duration_s: dur,
    emit_cruise_s: emitC,
    emit_event_s: 1.0,
    // Long missions ignore playback pacing. The server sleeps
    // min(sim_dt,60)/speed per frame, so 1x over 30 h with a 180 s
    // emit rate would sleep 60 s x 600 frames = 10 hours.
    speed: (dur > 3600 ? 0 : smVal("sm-rate", 1))
  };
  if (fault) {
    body.fault = fault;
    body.fault_severity = document.getElementById("sm-sev").value || "severe";
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

  if (dur > 3600)
    smLog("mission is " + (dur/3600).toFixed(1) + " h -- playback "
          + "pacing disabled, running as fast as possible");
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

function smShow() {
  document.getElementById("sm-thr-v").textContent = smVal("sm-thr", 80) + " %";
  document.getElementById("sm-alt-v").textContent = smVal("sm-alt", 6000) + " ft";
  document.getElementById("sm-oat-v").textContent = smVal("sm-oat", 10) + " C";
  // Honest warnings rather than silent bad demos.
  var f = document.getElementById("sm-fault").value;
  var thr = smVal("sm-thr", 80), w = "";
  if (f === "cooling")
    w = "cooling_degradation is detected at only 0.19 overall: the engine is " +
        "thermostatted, so a weak pump hides at low load. Needs high throttle " +
        "and warm air to show at all.";
  else if (f === "misfire")
    w = "misfire is detected reliably but usually LABELLED fuel_pressure_dev " +
        "-- the two are the same point under mean-value sensors.";
  else if (f === "fuel")
    w = "fuel_pressure_dev recall is 0.73, the lowest of the four strong " +
        "classes, for the same reason.";
  if (f === "cooling" && thr < 70)
    w += " At " + thr + "% throttle it will almost certainly not be detected.";
  document.getElementById("sm-warn").innerHTML = w;
}

async function smEngines() {
  var sel = document.getElementById("sm-eng");
  try {
    var r = await fetch("/sim/fleet");
    var d = await r.json();
    var list = d.fleet || d.engines || d;
    list.forEach(function (e) {
      var o = document.createElement("option");
      o.value = e.serial;
      o.textContent = e.serial + " -- " + Number(e.hours).toFixed(0) +
        " h" + (e.note ? ", " + e.note : "");
      if (e.serial === "RTX915-0003") o.selected = true;
      sel.appendChild(o);
    });
  } catch (err) {
    var o = document.createElement("option");
    o.value = "RTX915-0003"; o.textContent = "RTX915-0003 (fleet list unavailable)";
    sel.appendChild(o);
  }
}

function smUnits() {
  var el = document.getElementById("sm-durunit");
  var u = el ? el.options[el.selectedIndex].text : "sec";
  var m = {"sm-durlbl":"mission length (", "sm-onsetlbl":"onset (",
           "sm-clearlbl":"clears after ("};
  for (var k in m) {
    var t = document.getElementById(k);
    if (t) t.textContent = m[k] + u + ")";
  }
}

(function smWire() {
  document.getElementById("sm-start").onclick = smStart;
  document.getElementById("sm-stop").onclick = function () { smStop(); };
  ["sm-thr", "sm-alt", "sm-oat", "sm-fault"].forEach(function (id) {
    document.getElementById(id).oninput = smShow;
    document.getElementById(id).onchange = smShow;
  });
  document.getElementById("sm-preset").onchange = function () {
    var p = SM_PRESET[this.value];
    if (!p) return;
    document.getElementById("sm-thr").value = p.thr;
    document.getElementById("sm-alt").value = p.alt;
    document.getElementById("sm-oat").value = p.oat;
    smShow();
  };
  var du = document.getElementById("sm-durunit");
  if (du) du.onchange = smUnits;
  smUnits();
  smShow();
  smEngines();
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


// ---- simulator mode chooser (fault / condition / mission) ----------------
(function () {
  var MODES = {
    fault: ["FAULT SIMULATOR",
      "you choose the failure; the engine shows how it behaves under it"],
    condition: ["CONDITION SIMULATOR",
      "no fault is injected; wear and failures emerge from the conditions"],
    mission: ["MISSION SIMULATOR",
      "route, weather and a 20-30 h sortie flown from the map"]
  };
  function el(id) { return document.getElementById(id); }

  function showFleetAge() {
    var sel = el("sm-eng"), out = el("sm-age-now");
    if (!sel || !out || !sel.value) return;
    fetch("/sim/fleet").then(function (r) { return r.json(); }).then(function (j) {
      var list = Array.isArray(j) ? j : (j.fleet || j.engines || j.items || []);
      for (var i = 0; i < list.length; i++) {
        var e = list[i];
        if (e.serial !== sel.value) continue;
        out.textContent = e.serial + " -- " + e.hours.toFixed(0) + " h" +
          "  oil " + e.oil_pump_health.toFixed(3) +
          "  coolant " + e.coolant_pump_health.toFixed(3) +
          "  bearing " + e.bearing_wear.toFixed(3);
        return;
      }
    }).catch(function () { out.textContent = "fleet unavailable"; });
  }

  function enter(mode) {
    var m = MODES[mode] || MODES.fault;
    el("sm-choose").style.display = "none";
    el("sm-workspace").style.display = "";
    el("sm-mode-title").textContent = m[0];
    el("sm-mode-sub").textContent = m[1];
    var fault = el("sm-card-fault"), age = el("sm-card-age"),
        mapc = el("sm-card-map");
    if (mapc) mapc.style.display = (mode === "mission") ? "" : "none";
    if (window.smOpRows) window.smOpRows(mode === "mission");
    if (mode === "condition") {
      if (fault) fault.style.display = "none";
      if (age) age.style.display = "";
      var f = el("sm-fault");            // sim.js still reads this
      if (f) f.value = "none";
      showFleetAge();
    } else if (mode === "mission") {
      if (fault) fault.style.display = "none";
      if (age) age.style.display = "none";
    } else {
      if (fault) fault.style.display = "";
      if (age) age.style.display = "none";
    }
    if (mode === "mission" && window.smMapInit) window.smMapInit();
  }

  document.addEventListener("click", function (ev) {
    var box = ev.target.closest && ev.target.closest(".sm-box");
    if (box) { enter(box.getAttribute("data-mode")); return; }
    if (ev.target.id === "sm-back") {
      el("sm-workspace").style.display = "none";
      el("sm-choose").style.display = "";
    }
  });
  document.addEventListener("change", function (ev) {
    if (ev.target.id === "sm-eng") showFleetAge();
  });
})();


// ---- pre-age controls on the condition simulator ------------------------
(function () {
  function el(id) { return document.getElementById(id); }
  function log(msg) { var o = el("sm-age-log"); if (o) o.textContent = msg; }
  function refresh() {
    document.getElementById("sm-eng")
      .dispatchEvent(new Event("change", { bubbles: true }));
  }

  document.addEventListener("click", function (ev) {
    var serial = (el("sm-eng") || {}).value;
    if (!serial) return;

    if (ev.target.id === "sm-age-go") {
      var h = parseFloat((el("sm-agehrs") || {}).value || "0");
      if (!(h > 0)) { log("enter a positive number of hours"); return; }
      log("aging " + serial + " by " + h + " h ...");
      fetch("/sim/age?serial=" + encodeURIComponent(serial) + "&hours=" + h,
            { method: "POST" })
        .then(function (r) { return r.json().then(function (j) {
          return { ok: r.ok, j: j }; }); })
        .then(function (res) {
          if (!res.ok) { log("refused: " + (res.j.detail || "error")); return; }
          var b = res.j.before, a = res.j.after;
          log("+" + h + " h  oil " + b.oil_pump_health.toFixed(4) + " -> " +
              a.oil_pump_health.toFixed(4) + "   bearing " +
              b.bearing_wear.toFixed(4) + " -> " + a.bearing_wear.toFixed(4));
          refresh();
        })
        .catch(function (e) { log("failed: " + e); });
    }

    if (ev.target.id === "sm-age-reset") {
      log("restoring " + serial + " to factory ...");
      fetch("/sim/age/reset?serial=" + encodeURIComponent(serial),
            { method: "POST" })
        .then(function (r) { return r.json(); })
        .then(function () { log("back to factory"); refresh(); })
        .catch(function (e) { log("failed: " + e); });
    }
  });
})();


/* ---- mission map, box 3 ------------------------------------------- */
(function () {
  var map = null, layer = null, pts = [], plan = null;
  function el(id) { return document.getElementById(id); }

  function vendorPath() {
    var s = document.getElementsByTagName("script");
    for (var i = 0; i < s.length; i++) {
      var u = s[i].src || "";
      if (u.indexOf("leaflet.js") >= 0) return u.replace(/leaflet\.js.*$/, "");
    }
    return "vendor/";
  }

  var IDS = [["sm-tk-lat", "sm-tk-lon"], ["sm-ld-lat", "sm-ld-lon"],
             ["sm-ac-lat", "sm-ac-lon"]];

  function readout() {                      // pts -> boxes
    for (var i = 0; i < 3; i++) {
      var la = el(IDS[i][0]), lo = el(IDS[i][1]);
      if (!la || !lo) continue;
      la.value = pts[i] ? pts[i][0].toFixed(4) : "";
      lo.value = pts[i] ? pts[i][1].toFixed(4) : "";
    }
  }

  function fromBoxes() {                    // boxes -> pts
    var got = [];
    for (var i = 0; i < 3; i++) {
      var la = el(IDS[i][0]), lo = el(IDS[i][1]);
      if (!la || !lo) return false;
      var a = parseFloat(la.value), b = parseFloat(lo.value);
      if (isNaN(a) || isNaN(b)) break;
      if (a < -90 || a > 90 || b < -180 || b > 180) {
        el("sm-plan-out").textContent =
          "coordinates out of range: lat -90..90, lon -180..180";
        return false;
      }
      got.push([a, b]);
    }
    pts = got;
    return true;
  }

  function redraw() {
    if (!map) { readout(); return; }
    layer.clearLayers();
    for (var i = 0; i < pts.length; i++) L.marker(pts[i]).addTo(layer);
    if (plan) {
      L.polyline(plan.route, {color: "#4af", weight: 2}).addTo(layer);
      L.circle([plan.orbit.lat, plan.orbit.lon],
        {radius: plan.orbit.radius_km * 1000, color: "#fa4",
         weight: 1, fill: false}).addTo(layer);
    }
    readout();
  }

  window.smMapInit = function () {
    var d = el("sm-date");
    if (d && !d.max) {
      var mx = new Date(Date.now() + 13 * 864e5);
      d.max = mx.toISOString().slice(0, 10);
    }
    if (!window.L) {
      el("sm-map").innerHTML = "<div class='dim' style='padding:8px'>" +
        "leaflet did not load -- coordinates below still work</div>";
      readout(); return;
    }
    if (map) { setTimeout(function () { map.invalidateSize(); }, 60); return; }
    L.Icon.Default.imagePath = vendorPath();
    map = L.map("sm-map").setView([26.9, 72.0], 7);
    // Imagery is the default on purpose: boundary depiction is disputed
    // between states and no tile source matches every official view.
    // Terrain carries no such claim, and a sortie planner needs terrain.
    var imagery = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/" +
      "World_Imagery/MapServer/tile/{z}/{y}/{x}",
      {maxZoom: 17, attribution: "Esri, Maxar, Earthstar Geographics"});
    var streets = L.tileLayer(
      "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      {maxZoom: 17, attribution: "OpenStreetMap"});
    var E = "https://server.arcgisonline.com/ArcGIS/rest/services/";
    // Roads, rail and place names with NO political boundaries on them,
    // so the only border drawn anywhere is the one we ship ourselves.
    var roads = L.tileLayer(E + "Reference/World_Transportation/MapServer/" +
      "tile/{z}/{y}/{x}", {maxZoom: 17, pane: "shadowPane"});
    var terrain = L.tileLayer(E + "World_Terrain_Base/MapServer/tile/{z}/{y}/{x}",
      {maxZoom: 13, attribution: "Esri"});
    streets.addTo(map);
    roads.addTo(map);
    // KNOWN ISSUE: the OSM base draws Kashmir per its own
    // convention, not the Survey of India depiction this
    // project needs. The boundary overlay below is correct
    // and can be switched on; deferred, not forgotten.
    var bnd = L.layerGroup();
    fetch("vendor/india-boundary.geojson")
      .then(function (r) { return r.json(); })
      .then(function (gj) {
        L.geoJSON(gj, {style: {color: "#ff9a3c", weight: 1.6,
                               opacity: 0.95, fill: false},
                       interactive: false}).addTo(bnd);
      })
      .catch(function () {
        var o = document.getElementById("sm-plan-out");
        if (o) o.textContent = "india boundary layer failed to load";
      });
    L.control.layers({"streets (OSM)": streets, "satellite": imagery,
                  "terrain": terrain},
                     {"roads and names": roads, "india boundary": bnd},
                     {position: "topright"}).addTo(map);
    layer = L.layerGroup().addTo(map);
    map.on("click", function (e) {
      if (pts.length >= 3) { pts = []; plan = null; }
      pts.push([e.latlng.lat, e.latlng.lng]);
      redraw();
    });
    setTimeout(function () { map.invalidateSize(); }, 60);
    readout();
  };

  function planIt() {
    var out = el("sm-plan-out");
    if (pts.length < 3) {
      out.textContent = "click three points first: takeoff, landing, area centre";
      return;
    }
    var q = "tk_lat=" + pts[0][0] + "&tk_lon=" + pts[0][1] +
            "&ld_lat=" + pts[1][0] + "&ld_lon=" + pts[1][1] +
            "&ac_lat=" + pts[2][0] + "&ac_lon=" + pts[2][1] +
            "&area_radius_km=" + (el("sm-radius").value || 40) +
            "&target_h=0" +
            "&tasking=" + el("sm-tasking").value;
    var dv = el("sm-date").value;
    if (dv) q += "&date=" + dv;
    out.textContent = "planning -- a first weather fetch can take a few seconds";
    fetch("/sim/mission/plan?" + q, {method: "POST"})
      .then(function (r) {
        return r.json().then(function (j) { return {s: r.status, j: j}; }); })
      .then(function (o) {
        if (o.s !== 200) {
          out.textContent = "plan refused (" + o.s + "): " +
            (o.j.detail || JSON.stringify(o.j)); return;
        }
        plan = o.j; window.smLastPlan = plan; redraw();
        if (map) map.fitBounds(L.polyline(plan.route).getBounds().pad(0.2));
        var w = plan.weather || {}, h = "";
        h += "<div><b>" + plan.total_h + " h aloft &middot; transit " +
             plan.transit_km + " km &middot; loiter " + plan.loiter_h +
             " h &middot; " + plan.tasking + "</b></div>";
        var en = plan.endurance || {};
        if (en.name) h += "<div>" + en.name + " &middot; " + en.hours +
          " h (" + en.source + ") &middot; transit " + en.transit_h +
          " h at " + en.transit_kt + " kt</div>";
        h += "<div>weather " + (w.sources || ["?"]).join(", ") +
             (plan.date ? " for " + plan.date : " (no date, ISA)") + "</div>";
        for (var i = 0; i < (plan.phases || []).length; i++) {
          var p = plan.phases[i], bits = [];
          for (var k in p) if (p.hasOwnProperty(k)) bits.push(k + " " + p[k]);
          h += "<div>" + bits.join("  &middot;  ") + "</div>";
        }
        if ((plan.clamped || []).length)
          h += "<div>planner notes: " + plan.clamped.join("; ") + "</div>";
        if (w.single_hour_caveat) h += "<div>" + w.single_hour_caveat + "</div>";
        h += "<div>" + (plan.caveat || "") + "</div>";
        out.innerHTML = h;
      })
      .catch(function (e) { out.textContent = "plan failed: " + e; });
  }

  document.addEventListener("change", function (ev) {
    var id = ev.target.id || "";
    if (id.indexOf("sm-tk-") === 0 || id.indexOf("sm-ld-") === 0 ||
        id.indexOf("sm-ac-") === 0) { if (fromBoxes()) { plan = null; redraw(); } }
  });

  document.addEventListener("click", function (ev) {
    if (ev.target.id === "sm-plan") { fromBoxes(); planIt(); }
    if (ev.target.id === "sm-map-clear") {
      pts = []; plan = null; redraw();
      el("sm-plan-out").textContent = "cleared";
    }
  });
})();


/* hide the operating-point rows that mission mode does not use */
window.smOpRows = function (hide) {
  var card = document.getElementById("sm-eng");
  card = card && card.closest ? card.closest(".rep-card") : null;
  if (!card) return;
  ["sm-thr", "sm-alt", "sm-oat", "sm-preset", "sm-dur", "sm-rate"]
    .forEach(function (id) {
      var node = document.getElementById(id);
      while (node && node.parentNode !== card) node = node.parentNode;
      if (node) node.style.display = hide ? "none" : "";
    });
};
