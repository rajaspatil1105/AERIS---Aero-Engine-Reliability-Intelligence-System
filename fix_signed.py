import ast, pathlib
p = pathlib.Path("node2_twin_core/residual_calc.py")
s = p.read_text(encoding="utf-8")

edits = [
 ('RESIDUAL_MODE = "ABSOLUTE"',
  'RESIDUAL_MODE = "SIGNED"   # changed 2026-09-12: the MVEM refit trains on\n'
  '# signed (measured - expected) deltas (train_classifiers_mvem.py L95-96).\n'
  '# Serving in ABSOLUTE mode fed the gate +0.91 bar for a 0.91 bar oil\n'
  '# pressure COLLAPSE, scoring it 0.3532 (healthy) against 0.9999 offline.\n'
  '# Verified on mvem_v3.parquet: oil_pressure residuals 53.6% negative,\n'
  '# min -1.17 bar; EGT -110..+130 C. Signed is mandatory for these artifacts.'),
 ('residuals = {c: abs(signed[c]) for c in MEASURED_CHANNELS}',
  'residuals = dict(signed)      # SIGNED: direction reaches the classifier'),
]
for old, new in edits:
    if s.count(old) != 1:
        raise SystemExit("anchor not unique/found (%d): %r" % (s.count(old), old[:60]))
    s = s.replace(old, new)

ast.parse(s)
p.write_text(s, encoding="utf-8")
print("residual_calc -> SIGNED, syntax OK")
