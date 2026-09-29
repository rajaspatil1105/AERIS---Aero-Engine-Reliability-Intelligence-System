import time, sys, pathlib
sys.path.insert(0, str(pathlib.Path(".").resolve()))
from shared import mission_engine as me

eng = [e for e in me.fleet_listing() if e["serial"] == "RTX915-0001"][0]
eng = me.FleetEngine(**{k: v for k, v in eng.items()
                        if k in me.FleetEngine.__dataclass_fields__})

HOURS = 40.0
prof = [me.Setpoint(t_s=0.0, throttle_pct=100.0, altitude_ft=1000.0, oat_c=38.0),
        me.Setpoint(t_s=HOURS*3600.0, throttle_pct=100.0, altitude_ft=1000.0, oat_c=38.0)]

t0 = time.time()
frames = 0
last = None
for rec in me.run_mission(prof, eng, dt_s=1.0, stress_enabled=True,
                          emit_cruise_s=300.0, emit_event_s=1.0):
    frames += 1
    last = rec
print("%.0f h  frames=%d  wall=%.1f s" % (HOURS, frames, time.time() - t0))
print("last frame keys sample:", sorted(last)[:12])
