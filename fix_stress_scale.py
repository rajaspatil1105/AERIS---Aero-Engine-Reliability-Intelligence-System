import re, pathlib, shutil

P = pathlib.Path("shared/mission_engine.py")
src = P.read_text(encoding="utf-8")
shutil.copy(P, pathlib.Path("shared/mission_engine.py.bak_scale"))

FACTORS = {"THERMAL_FULL_S": 50.0, "POWER_FULL_S": 50.0, "OIL_FULL_S": 10.0}
rep = []
for name, f in FACTORS.items():
    pat = re.compile(r"^" + name + r"\s*=\s*([0-9._eE+-]+)[^\n]*", re.M)
    m = pat.search(src)
    if not m:
        raise SystemExit(name + " not found -- paste its line")
    old = float(m.group(1).replace("_", ""))
    new = old * f
    src = src[:m.start()] + "%s = %r  # was %r, x%g so a 40 h abusive mission degrades, not kills" % (name, new, old, f) + src[m.end():]
    rep.append((name, old, new))

P.write_text(src, encoding="utf-8")
for n, o, w in rep:
    print("%-16s %g -> %g" % (n, o, w))
print("patched")
