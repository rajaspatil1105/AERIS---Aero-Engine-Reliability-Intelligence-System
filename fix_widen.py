import pathlib
p = pathlib.Path("generate_mvem_dataset.py")
s = p.read_text(encoding="utf-8")
reps = [
 ('                    alt = float(rng.uniform(ALT_MIN, 9000.0))',
  '                    alt = float(rng.uniform(ALT_MIN, 16000.0))'),
 ('                                    + float(rng.uniform(12.0, ISA_DEV_MAX)))))',
  '                                    + float(rng.uniform(0.0, ISA_DEV_MAX)))))'),
 ('                    thr = float(rng.uniform(72.0, THR_MAX))',
  '                    thr = float(rng.uniform(55.0, THR_MAX))'),
]
for old, new in reps:
    if old not in s: raise SystemExit("NOT FOUND: " + old.strip())
    s = s.replace(old, new, 1)
s = s.replace("# is CONDITIONAL ON THERMAL LOAD, not envelope-wide.",
  "# is CONDITIONAL ON THERMAL LOAD, not envelope-wide. The window\n"
  "                    # must stay WIDE: a tight corner (thr>=72, alt<=9000,\n"
  "                    # ISA+12) made the operating point itself the label -\n"
  "                    # the gate hit 0.98 on cooling rows with no coolant\n"
  "                    # signal and 21% false alarm on healthy cruise.", 1)
compile(s.lstrip("\ufeff"), str(p), "exec")
p.write_text(s, encoding="utf-8")
print("injection window widened (thr>=55, alt<=16000, ISA+0), syntax OK")
