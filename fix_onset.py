import re, pathlib, shutil
P = pathlib.Path("shared/mission_engine.py")
src = P.read_text(encoding="utf-8")
shutil.copy(P, pathlib.Path("shared/mission_engine.py.bak_onset"))

pat = re.compile(r"^DEGRADE_ONSET\s*=\s*([0-9._eE+-]+)[^\n]*", re.M)
m = pat.search(src)
if not m:
    raise SystemExit("DEGRADE_ONSET not found")
src = (src[:m.start()]
       + "DEGRADE_ONSET = 0.0  # was %s. A dead band meant the engine was\n"
         "# physically identical to a new one until the counter crossed it, so\n"
         "# nothing was predictable from sensors -- p_anom sat at 0.000 for 17 h\n"
         "# and then the gate tripped 0.6 h after health finally moved. Health\n"
         "# now walks with accumulated stress from the first hour; the fault is\n"
         "# still only NAMED at DEGRADE_ANNOUNCE, so the gate leads the label." % m.group(1)
       + src[m.end():])
P.write_text(src, encoding="utf-8")
print("patched -- DEGRADE_ONSET %s -> 0.0" % m.group(1))
