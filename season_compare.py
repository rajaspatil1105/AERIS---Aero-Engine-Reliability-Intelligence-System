import sys, pathlib
sys.path.insert(0, str(pathlib.Path(".").resolve()))
from shared import mission_engine as me, mission_profile as mp, mission_weather as mw

TO, LD, AC = (26.251, 73.049), (26.889, 70.865), (27.200, 70.200)
eng = me.engine_now("RTX915-0007")
print("engine %s  %.0f h  oil %.4f\n" % (eng.serial, eng.hours, eng.oil_pump_health))

for tag, date in (("December", "2025-12-15"), ("June", "2025-06-15")):
    plan = mp.build(TO, LD, AC, target_h=30.0)
    info = mw.apply_to_plan(plan, [TO, AC, AC, LD], date)
    last, peak_oil, peak_egt = None, 0.0, 0.0
    for rec in me.run_mission(plan.profile, eng, dt_s=1.0, stress_enabled=True,
                              emit_cruise_s=300.0, emit_event_s=1.0):
        last = rec
        f = rec["frame"]
        peak_oil = max(peak_oil, f.get("oil_temperature_C", 0.0))
        peak_egt = max(peak_egt, f.get("EGT_mean_C", 0.0))
    s = last["stress"]
    print("%-9s %s  peak oil %5.1f C  peak EGT %6.1f C" % (tag, date, peak_oil, peak_egt))
    print("          thermal %.4f  oil %.4f  power %.4f  %s"
          % (s["thermal"], s["oil"], s["power"], last["fault_label"]))
    if info["clamped"]:
        print("          clamped: %s" % info["clamped"][0])
