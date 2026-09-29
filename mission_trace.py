import sys, pathlib
sys.path.insert(0, str(pathlib.Path(".").resolve()))
from shared import mission_engine as me, mission_profile as mp

plan = mp.build((26.251, 73.049), (26.889, 70.865), (27.200, 70.200), target_h=30.0)
eng = me.engine_now("RTX915-0007")

K = ("throttle_pct", "altitude_ft", "ambient_temperature_C", "rpm",
     "oil_pressure_bar", "oil_temperature_C", "coolant_temp_C",
     "EGT_mean_C", "fuelflow_kgh")
print("  hour  " + "".join("%9s" % k[:8] for k in K) + "  score")

nxt, bad, fuel, prev_t = 0.0, 0, 0.0, None
for rec in me.run_mission(plan.profile, eng, dt_s=1.0, stress_enabled=True,
                          emit_cruise_s=300.0, emit_event_s=1.0):
    f, h = rec["frame"], rec["t_s"] / 3600.0
    if prev_t is not None:
        fuel += f.get("fuelflow_kgh", 0.0) * (rec["t_s"] - prev_t) / 3600.0
    prev_t = rec["t_s"]
    if not rec["scoreable"]:
        bad += 1
    if h >= nxt:
        print("%6.2f  " % h
              + "".join("%9.1f" % f.get(k, float("nan")) for k in K)
              + ("  ok" if rec["scoreable"] else "  REFUSED"))
        nxt += 2.0

print("\nunscoreable frames: %d" % bad)
print("fuel burned: %.1f kg over %.1f h" % (fuel, plan.total_h))
print("final stress:", rec["stress"], rec["fault_label"])
