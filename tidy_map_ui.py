import io, shutil
H = r"static\index.html"
src = io.open(H, encoding="utf-8-sig").read().replace("\r\n", "\n")
lines = src.split("\n")
a = next((i for i, l in enumerate(lines) if 'id="sm-map-fallback"' in l), -1)
b = next((i for i, l in enumerate(lines) if 'id="sm-plan-out"' in l), -1)
if a < 0 or b < 0 or b <= a:
    raise SystemExit("MISS  map card controls (a=%s b=%s)" % (a, b))
row = '''      <div style="display:flex;flex-wrap:nowrap;align-items:center;gap:10px;margin:6px 0;white-space:nowrap">
        <label>tasking <select id="sm-tasking">
          <option value="high_surveillance">high surveillance</option>
          <option value="low_patrol">low patrol</option>
          <option value="contested">contested</option></select></label>
        <label>date <input type="date" id="sm-date" style="width:132px"></label>
        <label>radius <input type="number" id="sm-radius" value="40" min="5"
               max="200" step="5" style="width:54px"> km</label>
        <span class="dim">duration: auto (Heron Mk II)</span>
        <button id="sm-plan">PLAN</button>
        <button id="sm-map-clear">CLEAR</button>
        <button id="sm-map-fly" disabled title="no run endpoint yet">FLY IT</button>
      </div>'''.split("\n")
shutil.copy2(H, H + ".bak_row")
lines[a + 1:b] = row
io.open(H, "w", encoding="utf-8", newline="\n").write("\n".join(lines))
src2 = io.open(H, encoding="utf-8-sig").read()
io.open(H, "w", encoding="utf-8", newline="\n").write(
    src2.replace("sim.js?v=10", "sim.js?v=11", 1))
print("ok    one control line, hours box gone, v=11")

J = r"static\sim.js"
shutil.copy2(J, J + ".bak_row")
js = io.open(J, encoding="utf-8-sig").read().replace("\r\n", "\n")
n = 0
pairs = [
 ('"&target_h=" + (el("sm-target").value || 30) +', '"&target_h=0" +'),
 ('    if (mapc) mapc.style.display = (mode === "mission") ? "" : "none";',
  '    if (mapc) mapc.style.display = (mode === "mission") ? "" : "none";\n'
  '    if (window.smOpRows) window.smOpRows(mode === "mission");'),
 ('        h += "<div>weather " + (w.sources || ["?"]).join(", ") +',
  '        var en = plan.endurance || {};\n'
  '        if (en.name) h += "<div>" + en.name + " &middot; " + en.hours +\n'
  '          " h (" + en.source + ") &middot; transit " + en.transit_h +\n'
  '          " h at " + en.transit_kt + " kt</div>";\n'
  '        h += "<div>weather " + (w.sources || ["?"]).join(", ") +'),
]
for old, new in pairs:
    if old in js:
        js = js.replace(old, new, 1); n += 1; print("ok    " + old.strip()[:46])
    else:
        print("MISS  " + old.strip()[:46])

js += r'''

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
'''
io.open(J, "w", encoding="utf-8", newline="\n").write(js)
print("%d js edits" % n)
