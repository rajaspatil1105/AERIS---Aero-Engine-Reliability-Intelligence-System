import re, pathlib, shutil
P = pathlib.Path("shared/mission_engine.py")
src = P.read_text(encoding="utf-8")
shutil.copy(P, pathlib.Path("shared/mission_engine.py.bak_slow"))

NEW = {"THERMAL_FULL_S": 1150000.0,
       "OIL_FULL_S":      430000.0,
       "POWER_FULL_S":   1180000.0}
NOTE = ("  # Rescaled for cumulative wear: one punishing 30 h mission should\n"
        "# cost a few percent of health, not saturate the knob. Engines age\n"
        "# across missions, not within one flight.\n")
for name, val in NEW.items():
    m = re.search(r"^" + name + r"\s*=\s*([0-9._eE+-]+)[^\n]*", src, re.M)
    if not m:
        raise SystemExit(name + " not found")
    src = src[:m.start()] + "%s = %r%s" % (name, val, NOTE if name == "THERMAL_FULL_S" else "  # see THERMAL_FULL_S note\n") + src[m.end()+1:]
    print("%-16s %s -> %g" % (name, m.group(1), val))
P.write_text(src, encoding="utf-8")
print("patched")
