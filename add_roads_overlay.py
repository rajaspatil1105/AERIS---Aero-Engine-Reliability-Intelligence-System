import io, shutil
P = r"static\sim.js"
src = io.open(P, encoding="utf-8-sig").read().replace("\r\n", "\n")
if "World_Transportation" in src:
    raise SystemExit("ok    already wired")
lines = src.split("\n")
i = next((k for k, l in enumerate(lines) if "imagery.addTo(map);" in l), -1)
if i < 0:
    raise SystemExit("MISS  imagery.addTo line")
pad = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
blk = '''var E = "https://server.arcgisonline.com/ArcGIS/rest/services/";
// Roads, rail and place names with NO political boundaries on them,
// so the only border drawn anywhere is the one we ship ourselves.
var roads = L.tileLayer(E + "Reference/World_Transportation/MapServer/" +
  "tile/{z}/{y}/{x}", {maxZoom: 17, pane: "shadowPane"});
var terrain = L.tileLayer(E + "World_Terrain_Base/MapServer/tile/{z}/{y}/{x}",
  {maxZoom: 13, attribution: "Esri"});
imagery.addTo(map);
roads.addTo(map);'''
shutil.copy2(P, P + ".bak_roads")
lines[i:i + 1] = [pad + x if x else "" for x in blk.split("\n")]
src = "\n".join(lines)
src = src.replace('L.control.layers({"satellite": imagery, "streets (OSM)": streets},\n',
                  'L.control.layers({"satellite": imagery, "terrain": terrain,\n'
                  '                  "streets (OSM)": streets},\n', 1)
src = src.replace('{"india boundary": bnd},',
                  '{"roads and names": roads, "india boundary": bnd},', 1)
io.open(P, "w", encoding="utf-8", newline="\n").write(src)
print("ok    roads/labels overlay + terrain option")

H = r"static\index.html"
h = io.open(H, encoding="utf-8-sig").read().replace("\r\n", "\n")
io.open(H, "w", encoding="utf-8", newline="\n").write(h.replace("sim.js?v=19", "sim.js?v=20", 1))
print("ok    v=20")
