import io, shutil
P = r"static\sim.js"
shutil.copy2(P, P + ".bak_map")
src = io.open(P, encoding="utf-8-sig").read().replace("\r\n", "\n")
n = 0

pairs = [
 ('      "full surveillance sortie -- not built yet"]',
  '      "route, weather and a 20-30 h sortie flown from the map"]'),
 ('    var fault = el("sm-card-fault"), age = el("sm-card-age");',
  '    var fault = el("sm-card-fault"), age = el("sm-card-age"),\n'
  '        mapc = el("sm-card-map");\n'
  '    if (mapc) mapc.style.display = (mode === "mission") ? "" : "none";'),
 ('    } else {\n      if (fault) fault.style.display = "";\n'
  '      if (age) age.style.display = "none";\n    }',
  '    } else if (mode === "mission") {\n'
  '      if (fault) fault.style.display = "none";\n'
  '      if (age) age.style.display = "none";\n'
  '    } else {\n      if (fault) fault.style.display = "";\n'
  '      if (age) age.style.display = "none";\n    }'),
 ('    if (mode === "mission" && typeof smLog === "function")\n'
  '      smLog("mission simulator is not built yet -- running as a plain sortie");',
  '    if (mode === "mission" && window.smMapInit) window.smMapInit();'),
]
for old, new in pairs:
    if old in src:
        src = src.replace(old, new, 1); n += 1
        print("ok    " + old.strip().split("\n")[0][:52])
    else:
        print("MISS  " + old.strip().split("\n")[0][:52])

mod = r'''

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

  function readout() {
    var names = ["takeoff", "landing", "area centre"], t = [];
    for (var i = 0; i < pts.length; i++)
      t.push(names[i] + "  " + pts[i][0].toFixed(4) + ", " + pts[i][1].toFixed(4));
    while (t.length < 3) t.push(names[t.length] + "  -- click the map");
    var f = el("sm-map-fallback");
    if (f) f.innerHTML = t.join("<br>");
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
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      {maxZoom: 17, attribution: "OpenStreetMap"}).addTo(map);
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
            "&target_h=" + (el("sm-target").value || 30) +
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
        plan = o.j; redraw();
        if (map) map.fitBounds(L.polyline(plan.route).getBounds().pad(0.2));
        var w = plan.weather || {}, h = "";
        h += "<div><b>" + plan.total_h + " h aloft &middot; transit " +
             plan.transit_km + " km &middot; loiter " + plan.loiter_h +
             " h &middot; " + plan.tasking + "</b></div>";
        h += "<div>weather " + (w.sources || ["?"]).join(", ") +
             (plan.date ? " for " + plan.date : " (no date, ISA)") + "</div>";
        for (var i = 0; i < (plan.phases || []).length; i++) {
          var p = plan.phases[i], bits = [];
          for (var k in p) if (p.hasOwnProperty(k)) bits.push(k + " " + p[k]);
          h += "<div>" + bits.join("  &middot;  ") + "</div>";
        }
        if ((plan.clamped || []).length)
          h += "<div>clamped: " + plan.clamped.join("; ") + "</div>";
        if (w.single_hour_caveat) h += "<div>" + w.single_hour_caveat + "</div>";
        h += "<div>" + (plan.caveat || "") + "</div>";
        out.innerHTML = h;
      })
      .catch(function (e) { out.textContent = "plan failed: " + e; });
  }

  document.addEventListener("click", function (ev) {
    if (ev.target.id === "sm-plan") planIt();
    if (ev.target.id === "sm-map-clear") {
      pts = []; plan = null; redraw();
      el("sm-plan-out").textContent = "cleared";
    }
  });
})();
'''
src += mod
io.open(P, "w", encoding="utf-8", newline="\n").write(src)
print("%d edits, map module appended -> %s" % (n, P))
