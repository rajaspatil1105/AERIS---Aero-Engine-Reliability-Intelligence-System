import io, shutil
P = r"static\sim.js"
src = io.open(P, encoding="utf-8-sig").read().replace("\r\n", "\n")
old = '''    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      {maxZoom: 17, attribution: "OpenStreetMap"}).addTo(map);'''
new = '''    // Imagery is the default on purpose: boundary depiction is disputed
    // between states and no tile source matches every official view.
    // Terrain carries no such claim, and a sortie planner needs terrain.
    var imagery = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/" +
      "World_Imagery/MapServer/tile/{z}/{y}/{x}",
      {maxZoom: 17, attribution: "Esri, Maxar, Earthstar Geographics"});
    var streets = L.tileLayer(
      "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      {maxZoom: 17, attribution: "OpenStreetMap"});
    imagery.addTo(map);
    L.control.layers({"satellite": imagery, "streets (OSM)": streets},
                     null, {position: "topright"}).addTo(map);'''
if "World_Imagery" in src:
    print("ok    imagery already default")
elif old in src:
    shutil.copy2(P, P + ".bak_tiles")
    src = src.replace(old, new, 1)
    io.open(P, "w", encoding="utf-8", newline="\n").write(src)
    print("ok    satellite default, OSM on a layer switch")
else:
    print("MISS  tileLayer block")
