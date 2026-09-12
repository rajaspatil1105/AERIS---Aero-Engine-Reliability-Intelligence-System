import pathlib, re
p = pathlib.Path("generate_mvem_dataset.py")
s = p.read_text(encoding="utf-8")

old = '''def isa_oat(alt_ft: float) -> float:
    return 15.0 - 1.98 * alt_ft / 1000.0'''
new = '''# Certified ambient range is (-40, 50) C [A] and mvem.solve() refuses outside
# it. ISA at 22,800 ft is about -30 C, so a large negative deviation falls off
# the bottom; the sampled value is clamped rather than the deviation narrowed,
# which keeps full weather variety at the low altitudes that matter.
OAT_MIN, OAT_MAX = -39.5, 49.5


def isa_oat(alt_ft: float) -> float:
    return 15.0 - 1.98 * alt_ft / 1000.0


def sample_oat(alt_ft: float, rng) -> float:
    dev = float(rng.uniform(ISA_DEV_MIN, ISA_DEV_MAX))
    return float(min(OAT_MAX, max(OAT_MIN, isa_oat(alt_ft) + dev)))'''
if old not in s: raise SystemExit("NOT FOUND - isa_oat")
s = s.replace(old, new, 1)

n = len(re.findall(r"oat = isa_oat\(alt\) \+ float\(rng\.uniform\(ISA_DEV_MIN, ISA_DEV_MAX\)\)", s))
if n != 2: raise SystemExit(f"expected 2 oat lines, found {n}")
s = s.replace("oat = isa_oat(alt) + float(rng.uniform(ISA_DEV_MIN, ISA_DEV_MAX))",
              "oat = sample_oat(alt, rng)")
compile(s.lstrip("\ufeff"), str(p), "exec")
p.write_text(s, encoding="utf-8")
print("patched both call sites, syntax OK")
