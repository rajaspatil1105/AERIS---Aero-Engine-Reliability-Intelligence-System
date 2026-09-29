"""Build RUL training data as RESIDUAL VECTORS, matching the artifact contract.

The existing rul_regressor takes one frame's 14-element residual vector, so
health knobs cannot be swapped in. This replays the aged states from
rul_histories.csv, solves a frame at several operating points, and labels
each residual vector with that state's remaining hours.
"""
import csv, sys, pathlib, dataclasses, time
sys.path.insert(0, str(pathlib.Path(".").resolve()))
from shared import mission_engine as me
from node2_twin_core.residual_calc import ResidualCalculator, FEATURE_ORDER, ResidualError

calc = ResidualCalculator()
SRC = pathlib.Path("data/rul_histories.csv")
OUT = pathlib.Path("data/rul_residuals.csv")

# Sweep the envelope so the model is not valid at one cruise point only.
OPS = [(70.0, 8000.0,  6.0),
       (80.0, 6000.0, 15.0),
       (90.0, 4000.0, 28.0),
       (100.0, 1000.0, 38.0)]

rows = list(csv.DictReader(SRC.open(encoding="utf-8")))
print("replaying %d states x %d operating points" % (len(rows), len(OPS)))

base = me.FLEET_BY_SERIAL["RTX915-0001"]
out, skipped = [], 0
t0 = time.time()
for i, r in enumerate(rows):
    e = dataclasses.replace(base, serial=r["serial"],
                            oil_pump_health=float(r["oil"]),
                            coolant_pump_health=float(r["coolant"]),
                            bearing_wear=float(r["bearing"]))
    for thr, alt, oat in OPS:
        prof = [me.Setpoint(t_s=0.0, throttle_pct=thr, altitude_ft=alt, oat_c=oat),
                me.Setpoint(t_s=120.0, throttle_pct=thr, altitude_ft=alt, oat_c=oat)]
        last = None
        for rec in me.run_mission(prof, e, dt_s=1.0, stress_enabled=False,
                                  emit_cruise_s=60.0):
            last = rec
        try:
            rs = calc.compute(last["frame"])
        except ResidualError:
            skipped += 1
            continue
        d = {"serial": r["serial"], "rul_h": float(r["rul_h"]),
             "thr": thr, "alt": alt, "oat": oat}
        for name, val in zip(FEATURE_ORDER, rs.vector):
            d[name] = float(val)
        out.append(d)
    if i % 200 == 0:
        print("  %d/%d  %.0fs" % (i, len(rows), time.time() - t0))

with OUT.open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0]))
    w.writeheader(); w.writerows(out)
print("\n%d vectors -> %s   (%d skipped out-of-envelope)  %.0fs"
      % (len(out), OUT, skipped, time.time() - t0))
