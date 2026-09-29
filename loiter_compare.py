import sys, pathlib
sys.path.insert(0, str(pathlib.Path(".").resolve()))
from shared import mission_engine as me, mission_profile as mp, mission_weather as mw

TO, LD, AC = (26.251, 73.049), (26.889, 70.865), (27.200, 70.200)
eng = me.engine_now("RTX915-0007")
print("engine %s  %.0f h  oil %.4f" % (eng.serial, eng.hours, eng.oil_pump_health))
print("knees: coolant %.0f  oil %.0f\n" % (me.COOLANT_KNEE_C, me.OIL_KNEE_C))

for tk in ("high_surveillance", "low_patrol", "contested"):
    for tag, date in (("Dec", "2025-12-15"), ("Jun", "2025-06-15")):
        plan = mp.build(TO, LD, AC, target_h=30.0, tasking=tk)
        mw.apply_to_plan(plan, [TO, AC, AC, LD], date)
        mid, last = None, None
        for rec in me.run_mission(plan.profile, eng, dt_s=1.0,
                                  stress_enabled=True, emit_cruise_s=300.0,
                                  emit_event_s=1.0):
            last = rec
            if mid is None and rec["t_s"] >= 15 * 3600.0:
                mid = rec
        f, s = mid["frame"], last["stress"]
        print("%-18s %s  LOITER thr %4.1f%% alt %5.0fft oat %5.1fC -> "
              "oil %5.1fC coolant %5.1fC EGT %6.1fC | wear t %.3f o %.3f p %.3f"
              % (tk, tag, f["throttle_pct"], f["altitude_ft"],
                 f["ambient_temperature_C"], f["oil_temperature_C"],
                 f["coolant_temp_C"], f["EGT_mean_C"],
                 s["thermal"], s["oil"], s["power"]))
    print("")
