import re, pathlib
j = pathlib.Path("static/sim.js").read_text(encoding="utf-8")
print("emitC line   :", [l.strip() for l in j.splitlines() if "emitC" in l])
print("unit applied :", [l.strip() for l in j.splitlines() if "* unit" in l])

for secs, label in ((300, "5 min (old default)"), (7200, "2 h"), (108000, "30 h")):
    emit = min(600, max(10, secs / 600.0))
    print("%-20s emit %6.1f s -> ~%5d frames" % (label, emit, secs / emit))
