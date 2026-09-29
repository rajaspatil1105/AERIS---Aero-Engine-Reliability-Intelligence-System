import io, shutil
P = r"static\sim.js"
src = io.open(P, encoding="utf-8-sig").read().replace("\r\n", "\n")
n = 0
pairs = [
 ("imagery.addTo(map);\n", "streets.addTo(map);\n"),
 ('L.control.layers({"satellite": imagery, "terrain": terrain,\n',
  'L.control.layers({"streets (OSM)": streets, "satellite": imagery,\n'),
 ('                  "streets (OSM)": streets},\n',
  '                  "terrain": terrain},\n'),
]
for old, new in pairs:
    if old in src:
        src = src.replace(old, new, 1); n += 1
    else:
        print("MISS  " + old.strip()[:44])
src = src.replace("var bnd = L.layerGroup().addTo(map);",
                  "// KNOWN ISSUE: the OSM base draws Kashmir per its own\n"
                  "    // convention, not the Survey of India depiction this\n"
                  "    // project needs. The boundary overlay below is correct\n"
                  "    // and can be switched on; deferred, not forgotten.\n"
                  "    var bnd = L.layerGroup();", 1)
shutil.copy2(P, P + ".bak_revert")
io.open(P, "w", encoding="utf-8", newline="\n").write(src)
print("%d edits -- streets default, boundary off by default" % n)

H = r"static\index.html"
h = io.open(H, encoding="utf-8-sig").read().replace("\r\n", "\n")
io.open(H, "w", encoding="utf-8", newline="\n").write(h.replace("sim.js?v=20", "sim.js?v=21", 1))
print("ok    v=21")
