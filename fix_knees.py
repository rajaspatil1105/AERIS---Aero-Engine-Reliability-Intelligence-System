import re, pathlib, shutil

P = pathlib.Path("shared/mission_engine.py")
src = P.read_text(encoding="utf-8")
shutil.copy(P, pathlib.Path("shared/mission_engine.py.bak_knee"))

NEW = {
    "THERMAL_FULL_S": (36000.0, "40 h hot WOT -> ~0.79 cooling damage"),
    "OIL_KNEE_C":     (94.0,    "cruise oil is regulated at 90 C; hot WOT reaches 98.8"),
    "OIL_FULL_S":     (130000.0,"4.8 C over knee for 40 h -> ~0.53 oil damage"),
}
for name, (val, why) in NEW.items():
    pat = re.compile(r"^" + name + r"\s*=\s*([0-9._eE+-]+)[^\n]*", re.M)
    m = pat.search(src)
    if not m:
        raise SystemExit(name + " not found")
    old = m.group(1)
    src = src[:m.start()] + "%s = %r  # was %s; %s" % (name, val, old, why) + src[m.end():]
    print("%-16s %s -> %g" % (name, old, val))

P.write_text(src, encoding="utf-8")
print("patched")
