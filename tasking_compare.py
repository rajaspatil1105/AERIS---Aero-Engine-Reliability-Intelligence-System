import sys, pathlib
sys.path.insert(0, str(pathlib.Path(".").resolve()))
from shared import mission_engine as me, mission_profile as mp, mission_weather as mw

TO, LD, AC = (26.251, 73.049), (26.889, 70.865), (27.200, 70.200)
eng = me.engine_now("RTX915-0007")
print("engine %s  %.0f h  oil %.4f\n" % (eng.serial, eng.hours, eng.oil_pump_health))

for tk in ("high_surveillance", "low_patrol", "contested"):
    for tag, date in (("Dec", "2025-12-15"), ("Jun", "2025-06-15")):
        plan = mp.build(TO, LD, AC, target_h=30.0, tasking=tk)
        info = mw.apply_to_plan(plan, [TO, AC, AC, LD], date)
        last, po, pe, bad = None, 0.0, 0.0, 0
        for rec in me.run_mission(plan.profile, eng, dt_s=1.0,
                                  stress_enabled=True, emit_cruise_s=300.0,
                                  emit_event_s=1.0):
            last = rec
            f = rec["frame"]
            po = max(po, f.get("oil_temperature_C", 0.0))
            pe = max(pe, f.get("EGT_mean_C", 0.0))
            if not rec["scoreable"]:
                bad += 1
        s = last["stress"]
        print("%-18s %s  oil %5.1fC  EGT %6.1fC  thermal %.3f oil %.3f "
              "power %.3f  unscored %d  %s"
              % (tk, tag, po, pe, s["thermal"], s["oil"], s["power"], bad,
                 last["fault_label"]))
        if info["clamped"]:
            print("%22s clamped: %s" % ("", info["clamped"][0]))
    print("")
