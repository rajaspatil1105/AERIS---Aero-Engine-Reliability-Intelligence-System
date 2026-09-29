import io, shutil
P = r"static\sim.js"
src = io.open(P, encoding="utf-8-sig").read().replace("\r\n", "\n")
if "india-boundary.geojson" in src:
    raise SystemExit("ok    already wired")

lines = src.split("\n")
i = next((k for k, l in enumerate(lines) if "L.control.layers(" in l), -1)
if i < 0:
    raise SystemExit("MISS  no L.control.layers call")
j = i
while j < len(lines) and ".addTo(map);" not in lines[j]:
    j += 1
pad = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
print("replacing lines %d-%d" % (i + 1, j + 1))

blk = '''var bnd = L.layerGroup().addTo(map);
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
L.control.layers({"satellite": imagery, "streets (OSM)": streets},
                 {"india boundary": bnd},
                 {position: "topright"}).addTo(map);'''
shutil.copy2(P, P + ".bak_bnd")
lines[i:j + 1] = [pad + x if x else "" for x in blk.split("\n")]
io.open(P, "w", encoding="utf-8", newline="\n").write("\n".join(lines))
print("ok    india boundary overlay wired")
