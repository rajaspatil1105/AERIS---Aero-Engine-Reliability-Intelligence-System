import re, pathlib, shutil
P = pathlib.Path("shared/mission_engine.py")
src = P.read_text(encoding="utf-8")
shutil.copy(P, pathlib.Path("shared/mission_engine.py.bak_t80k"))
m = re.search(r"^THERMAL_FULL_S\s*=\s*([0-9._eE+-]+)[^\n]*", src, re.M)
if not m:
    raise SystemExit("THERMAL_FULL_S not found")
src = (src[:m.start()]
       + "THERMAL_FULL_S = 80000.0  # was %s, calibrated when DEGRADE_ONSET\n"
         "# still had a 0.35 dead band. With onset at 0.0 that value cooked the\n"
         "# engine by 12 h; 80000 saturates at 26.4 h so a 30 h abusive mission\n"
         "# is a climb throughout with only a short tail." % m.group(1)
       + src[m.end():])
P.write_text(src, encoding="utf-8")
print("patched -- THERMAL_FULL_S %s -> 80000" % m.group(1))
