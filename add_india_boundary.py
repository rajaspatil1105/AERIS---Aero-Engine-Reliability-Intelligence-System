import io, shutil
P = r"static\sim.js"
src = io.open(P, encoding="utf-8-sig").read().replace("\r\n", "\n")
old = """    // KNOWN ISSUE: OSM draws Kashmir per its own convention, which is not
    // the Survey of India depiction this project needs. Fix is planned:
    // imagery base plus an India boundary layer we ship ourselves.
    streets.addTo(map);
    L.control.layers({"streets (OSM)": streets, "satellite": imagery},
                     null, {position: "topright"}).addTo(map);"""
new = """    // Imagery draws no borders anywhere, so nothing contradicts the
    // boundary we ship. OSM streets stay available for road detail, but
    // its Kashmir depiction is not the one this project uses.
    imagery.addTo(map);
    var bnd = L.layerGroup().addTo(map);
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
                     {"india boundary": bnd}, {position: "topright"}).addTo(map);"""
if "india-boundary.geojson" in src:
    print("ok    boundary layer already wired")
elif old in src:
    shutil.copy2(P, P + ".bak_bnd")
    io.open(P, "w", encoding="utf-8", newline="\n").write(src.replace(old, new, 1))
    print("ok    imagery base + india boundary overlay")
else:
    print("MISS  layer block -- paste the lines around addTo(map)")

H = r"static\index.html"
h = io.open(H, encoding="utf-8-sig").read().replace("\r\n", "\n")
io.open(H, "w", encoding="utf-8", newline="\n").write(h.replace("sim.js?v=18", "sim.js?v=19", 1))
print("ok    v=19")
