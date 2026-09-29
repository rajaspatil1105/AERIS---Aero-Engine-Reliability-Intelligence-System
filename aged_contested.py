import sys, pathlib, requests
sys.path.insert(0, str(pathlib.Path(".").resolve()))
from shared import mission_engine as me, mission_profile as mp, mission_weather as mw

B, S = "http://localhost:8000", "RTX915-0007"
TO, LD, AC = (26.251, 73.049), (26.889, 70.865), (27.200, 70.200)

for age_h in (0, 600, 1500):
    requests.post(B + "/sim/age/reset", params={"serial": S}, timeout=30)
    if age_h:
        requests.post(B + "/sim/age", params={"serial": S, "hours": age_h},
                      timeout=300)
    eng = me.engine_now(S)
    plan = mp.build(TO, LD, AC, target_h=30.0, tasking="contested")
    mw.apply_to_plan(plan, [TO, AC, AC, LD], "2025-06-15")
    mid = last = None
    for rec in me.run_mission(plan.profile, eng, dt_s=1.0, stress_enabled=True,
                              emit_cruise_s=300.0, emit_event_s=1.0):
        last = rec
        if mid is None and rec["t_s"] >= 15 * 3600.0:
            mid = rec
    f, s = mid["frame"], last["stress"]
    print("+%4d h  coolant %.3f oil %.3f -> loiter oil %5.1fC coolant %5.1fC "
          "| wear t %.3f o %.3f p %.3f  %s"
          % (age_h, eng.coolant_pump_health, eng.oil_pump_health,
             f["oil_temperature_C"], f["coolant_temp_C"],
             s["thermal"], s["oil"], s["power"], last["fault_label"]))

requests.post(B + "/sim/age/reset", params={"serial": S}, timeout=30)
print("reset to factory")
