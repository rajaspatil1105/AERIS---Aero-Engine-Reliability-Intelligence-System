import io, re, shutil
P = r"static\index.html"
src = io.open(P, encoding="utf-8-sig").read()
n = 0

m = re.search(r'<script[^>]*src=["\']([^"\']*sim\.js[^"\']*)["\'][^>]*>', src, re.I)
if not m:
    print("MISS  no sim.js tag. script tags found:")
    for t in re.findall(r'<script[^>]*>', src, re.I):
        print("   ", t.strip())
    raise SystemExit(1)
url = m.group(1)
pref = url[:url.rfind("/") + 1] if "/" in url else ""
print("sim.js tag = %s   prefix = %r" % (url, pref))

shutil.copy2(P, P + ".bak_map")

if "leaflet.css" not in src:
    src = src.replace("</head>",
        '  <link rel="stylesheet" href="' + pref + 'vendor/leaflet.css">\n</head>',
        1); n += 1
    print("ok    leaflet css")

if "vendor/leaflet.js" not in src:
    src = src.replace(m.group(0),
        '<script src="' + pref + 'vendor/leaflet.js"></script>\n  ' + m.group(0),
        1); n += 1
    print("ok    leaflet js before sim.js")

card = '''    <div class="rep-card" id="sm-card-map" style="display:none">
      <h3>MISSION MAP</h3>
      <div class="dim">click sets TAKEOFF, then LANDING, then the
        SURVEILLANCE CENTRE. a fourth click starts over.</div>
      <div id="sm-map" style="height:340px;margin:6px 0;background:#111"></div>
      <div id="sm-map-fallback" class="dim"></div>
      <div style="margin:4px 0">
        <label>tasking
          <select id="sm-tasking">
            <option value="high_surveillance">high surveillance</option>
            <option value="low_patrol">low patrol</option>
            <option value="contested">contested</option>
          </select></label>
        <label>date <input type="date" id="sm-date" style="width:140px"></label>
        <label>radius <input type="number" id="sm-radius" value="40"
               min="5" max="200" step="5" style="width:60px"> km</label>
        <label>hours <input type="number" id="sm-target" value="30"
               min="1" max="40" step="1" style="width:60px"></label>
      </div>
      <div style="margin:4px 0">
        <button id="sm-plan">PLAN</button>
        <button id="sm-map-clear">CLEAR</button>
        <button id="sm-map-fly" disabled title="no run endpoint yet">FLY IT</button>
      </div>
      <div id="sm-plan-out" class="dim">no plan yet</div>
    </div>
'''
anchor = '    <div class="rep-card" id="sm-card-fault">'
if "sm-card-map" in src:
    print("ok    map card already present")
elif anchor in src:
    src = src.replace(anchor, card + anchor, 1); n += 1
    print("ok    map card")
else:
    print("MISS  card anchor -- paste line 184 verbatim")

io.open(P, "w", encoding="utf-8", newline="\n").write(src)
print("%d edits -> %s" % (n, P))
