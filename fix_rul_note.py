import io, shutil
P = r"node2_twin_core\rul_engine.py"
shutil.copy2(P, P + ".bak_note")
src = io.open(P, encoding="utf-8").read()

old = """NOTE: smoothing makes the number stable, not correct. Units are
      unknown and must not be labelled 'hours' on the dashboard."""
new = """NOTE: units are hours. R2 +0.842 / MAE 93 h on four held-out
      engines, but 0.938 of the signal is the oil pressure residual
      and labels share the wear model that made the histories.
      Reads about +30 h late at true end of life."""

if old in src:
    src = src.replace(old, new, 1)
    io.open(P, "w", encoding="utf-8", newline="\n").write(src)
    print("ok -- closing NOTE rewritten")
else:
    print("MISS -- find it with the grep below and paste the exact lines")
