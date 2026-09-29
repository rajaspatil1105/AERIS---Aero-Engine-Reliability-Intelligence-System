import pathlib, shutil
OLD = """Under injected faults, FORCED_FAULTS overwrites oil_pump_health rather than
composing with each engine's wear, so a fresh engine and a worn engine both
report 2.24 bar. Engine selection therefore has no effect once a fault is
active."""
NEW = """Injected faults compose multiplicatively with each engine's wear, so the
engine selector still matters under fault: severe lubrication at cruise gives
2.2400 bar on RTX915-0001 (fresh), 2.2189 on RTX915-0007 and 2.1654 on
RTX915-0015, against healthy values of 3.2000, 3.1699 and 3.0934. Bearing wear
composes additively. The two fuel-trim faults (misfire, fuel_pressure_dev) do
not compose, because the fleet carries no per-cylinder trim to compose with.
A composed fault on a worn engine is deeper than the fault depths the
classifier was trained on, so the type label is less certain there even though
detection is not."""
p = pathlib.Path("README.md")
s = p.read_text(encoding="utf-8-sig")
if OLD not in s:
    print("README anchor not found -- edit by hand")
else:
    shutil.copy(p, p.with_suffix(".md.bak"))
    p.write_text(s.replace(OLD, NEW, 1), encoding="utf-8")
    print("README updated")
