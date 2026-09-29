import io, shutil
P = r"node2_twin_core\rul_engine.py"
shutil.copy2(P, P + ".bak_note2")
src = io.open(P, encoding="utf-8").read()
pairs = [
 ('print("NOTE: smoothing makes the number stable, not correct. Units are")',
  'print("NOTE: units are hours. R2 +0.842 / MAE 93 h on four held-out")'),
 ('print("      unknown and must not be labelled \'hours\' on the dashboard.")',
  'print("      engines; 0.938 of the signal is the oil pressure residual.")'),
]
for old, new in pairs:
    print(("ok    " if old in src else "MISS  ") + old[:60])
    src = src.replace(old, new, 1)
io.open(P, "w", encoding="utf-8", newline="\n").write(src)
