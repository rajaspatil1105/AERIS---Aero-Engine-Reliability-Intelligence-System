import sys, pathlib, time
sys.path.insert(0, str(pathlib.Path(".").resolve()))
from shared import mission_engine as me, mission_profile as mp

plan = mp.build((26.251, 73.049), (26.889, 70.865), (27.200, 70.200), target_h=30.0)
eng = me.engine_now("RTX915-0007")
print("engine %s  %.0f h  oil %.4f" % (eng.serial, eng.hours, eng.oil_pump_health))

t0, last, n = time.time(), None, 0
for rec in me.run_mission(plan.profile, eng, dt_s=1.0, stress_enabled=True,
                          emit_cruise_s=300.0, emit_event_s=1.0):
    last, n = rec, n + 1
print("%d frames in %.1f s" % (n, time.time() - t0))
print("stress:", last["stress"])
f = last["frame"]
for k in ("oil_pressure_bar", "oil_temp_c", "coolant_temp_out_c", "egt_mean_c",
          "fuel_flow_kgh", "fault_label"):
    if k in f:
        print("  %-20s %s" % (k, f[k]))
