import io, shutil
H = r"static\index.html"
h = io.open(H, encoding="utf-8-sig").read().replace("\r\n", "\n")
old = '      <div id="sm-map-fallback" class="dim"></div>'
new = '''      <div id="sm-map-fallback" class="dim"
           style="display:flex;flex-wrap:wrap;gap:10px;align-items:center;
                  margin:6px 0;font-size:11px">
        <span>takeoff <input id="sm-tk-lat" placeholder="lat" style="width:74px">
          <input id="sm-tk-lon" placeholder="lon" style="width:74px"></span>
        <span>landing <input id="sm-ld-lat" placeholder="lat" style="width:74px">
          <input id="sm-ld-lon" placeholder="lon" style="width:74px"></span>
        <span>area <input id="sm-ac-lat" placeholder="lat" style="width:74px">
          <input id="sm-ac-lon" placeholder="lon" style="width:74px"></span>
        <span class="dim">click the map or type</span>
      </div>'''
if "sm-tk-lat" in h:
    print("ok    coord inputs already present")
elif old in h:
    shutil.copy2(H, H + ".bak_coords")
    h = h.replace(old, new, 1).replace("sim.js?v=16", "sim.js?v=17", 1)
    io.open(H, "w", encoding="utf-8", newline="\n").write(h)
    print("ok    coord inputs, v=17")
else:
    print("MISS  fallback div")

P = r"static\sim.js"
src = io.open(P, encoding="utf-8-sig").read().replace("\r\n", "\n")
n = 0
oldr = '''  function readout() {
    var names = ["takeoff", "landing", "area centre"], t = [];
    for (var i = 0; i < pts.length; i++)
      t.push(names[i] + "  " + pts[i][0].toFixed(4) + ", " + pts[i][1].toFixed(4));
    while (t.length < 3) t.push(names[t.length] + "  -- click the map");
    var f = el("sm-map-fallback");
    if (f) f.innerHTML = t.join("<br>");
  }'''
newr = '''  var IDS = [["sm-tk-lat", "sm-tk-lon"], ["sm-ld-lat", "sm-ld-lon"],
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
  }'''
if "fromBoxes" in src:
    print("ok    js already two-way")
elif oldr in src:
    shutil.copy2(P, P + ".bak_coords")
    src = src.replace(oldr, newr, 1); n += 1
    src = src.replace('    if (ev.target.id === "sm-plan") planIt();',
                      '    if (ev.target.id === "sm-plan") { fromBoxes(); planIt(); }', 1)
    src = src.replace('''  document.addEventListener("click", function (ev) {
    if (ev.target.id === "sm-plan")''',
                      '''  document.addEventListener("change", function (ev) {
    var id = ev.target.id || "";
    if (id.indexOf("sm-tk-") === 0 || id.indexOf("sm-ld-") === 0 ||
        id.indexOf("sm-ac-") === 0) { if (fromBoxes()) { plan = null; redraw(); } }
  });

  document.addEventListener("click", function (ev) {
    if (ev.target.id === "sm-plan")''', 1)
    io.open(P, "w", encoding="utf-8", newline="\n").write(src)
    print("ok    boxes and map stay in sync")
else:
    print("MISS  readout function")
