import pathlib
p = pathlib.Path("generate_mvem_dataset.py")
s = p.read_text(encoding="utf-8")
old = "ALT_MIN, ALT_MAX = 0.0, 35000.0"
new = ("ALT_MIN, ALT_MAX = 0.0, 22800.0   # Rotax 915iS published ceiling is\n"
       "# 23000 ft [A] and mvem.solve() refuses above it. The Heron Mk II airframe\n"
       "# ceiling is 35000 ft, so high-altitude loiter is OUT OF SCOPE for scoring.")
if old not in s: raise SystemExit("NOT FOUND")
s = s.replace(old, new, 1)
compile(s.lstrip("\ufeff"), str(p), "exec")
p.write_text(s, encoding="utf-8")
print("patched, syntax OK")
