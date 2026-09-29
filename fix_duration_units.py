import pathlib, shutil

H = pathlib.Path("static/index.html")
J = pathlib.Path("static/sim.js")
shutil.copy(H, pathlib.Path("static/index.html.bak_dur"))
shutil.copy(J, pathlib.Path("static/sim.js.bak_dur"))

h = H.read_text(encoding="utf-8")
OLD_IN = '<input id="sm-dur" type="number" value="300" min="30" step="10">'
NEW_IN = ('<input id="sm-dur" type="number" value="300" min="1" step="10">'
          '<select id="sm-durunit">'
          '<option value="1" selected>sec</option>'
          '<option value="60">min</option>'
          '<option value="3600">hours</option></select>')
if OLD_IN not in h:
    raise SystemExit("duration input not found")
h = h.replace(OLD_IN, NEW_IN, 1)
H.write_text(h, encoding="utf-8")

j = J.read_text(encoding="utf-8")
OLD_DUR = '  var dur = smVal("sm-dur", 300);'
NEW_DUR = ('  // Duration, onset and clear all read in the selected unit.\n'
           '  var uEl = document.getElementById("sm-durunit");\n'
           '  var unit = uEl ? (parseFloat(uEl.value) || 1) : 1;\n'
           '  var dur = smVal("sm-dur", 300) * unit;\n'
           '  // Keep emitted frames near 600 whatever the mission length:\n'
           '  // 10 s emit over 30 h would be 10800 scored frames (~1 h of work).\n'
           '  var emitC = Math.min(600, Math.max(10, dur / 600));')
if OLD_DUR not in j:
    raise SystemExit("dur anchor not found")
j = j.replace(OLD_DUR, NEW_DUR, 1)

for a, b in (('  var onset = smVal("sm-onset", 0);',
              '  var onset = smVal("sm-onset", 0) * unit;'),
             ('  var clear = smVal("sm-clear", 0);',
              '  var clear = smVal("sm-clear", 0) * unit;'),
             ('    emit_cruise_s: 10.0,', '    emit_cruise_s: emitC,')):
    if a not in j:
        raise SystemExit("anchor not found: " + a.strip())
    j = j.replace(a, b, 1)

J.write_text(j, encoding="utf-8")
print("patched -- duration units + scaled emit rate")
