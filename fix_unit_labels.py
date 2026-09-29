import pathlib, shutil
H = pathlib.Path("static/index.html"); J = pathlib.Path("static/sim.js")
shutil.copy(H, pathlib.Path("static/index.html.bak_lbl"))
shutil.copy(J, pathlib.Path("static/sim.js.bak_lbl"))

h = H.read_text(encoding="utf-8")
for a, b in ((">mission length (s)<", ' id="sm-durlbl">mission length (s)<'),
             (">onset (s)<",          ' id="sm-onsetlbl">onset (s)<'),
             (">clears after (s)<",   ' id="sm-clearlbl">clears after (s)<')):
    if a not in h:
        raise SystemExit("label not found: " + a)
    h = h.replace(a, b, 1)
H.write_text(h, encoding="utf-8")

j = J.read_text(encoding="utf-8")
OLD = '  smShow();\n  smEngines();'
NEW = ('  var du = document.getElementById("sm-durunit");\n'
       '  if (du) du.onchange = smUnits;\n'
       '  smUnits();\n'
       '  smShow();\n'
       '  smEngines();')
FN = ('function smUnits() {\n'
      '  var el = document.getElementById("sm-durunit");\n'
      '  var u = el ? el.options[el.selectedIndex].text : "sec";\n'
      '  var m = {"sm-durlbl":"mission length (", "sm-onsetlbl":"onset (",\n'
      '           "sm-clearlbl":"clears after ("};\n'
      '  for (var k in m) {\n'
      '    var t = document.getElementById(k);\n'
      '    if (t) t.textContent = m[k] + u + ")";\n'
      '  }\n'
      '}\n\n')
if OLD not in j:
    raise SystemExit("wire anchor not found")
j = j.replace(OLD, NEW, 1).replace("(function smWire() {", FN + "(function smWire() {", 1)
J.write_text(j, encoding="utf-8")
print("patched -- labels follow the unit selector")
