import importlib, sys, pathlib
sys.path.insert(0, str(pathlib.Path(".").resolve()))
from shared import mission_engine as me

def trial(full_s):
    me.THERMAL_FULL_S = full_s
    e = [x for x in me.fleet_listing() if x["serial"] == "RTX915-0001"][0]
    e = me.FleetEngine(**{k: v for k, v in e.items()
                          if k in me.FleetEngine.__dataclass_fields__})
    prof = [me.Setpoint(t_s=0.0, throttle_pct=100.0, altitude_ft=1000.0, oat_c=38.0),
            me.Setpoint(t_s=30*3600.0, throttle_pct=100.0, altitude_ft=1000.0, oat_c=38.0)]
    hit = None
    endv = 0.0
    for rec in me.run_mission(prof, e, dt_s=1.0, stress_enabled=True, emit_cruise_s=180.0):
        v = rec["stress"]["thermal"]
        endv = v
        if hit is None and v >= 1.0:
            hit = rec["t_s"] / 3600.0
    print("THERMAL_FULL_S %7d  saturates %s  final %.2f"
          % (full_s, ("%.1f h" % hit) if hit else "never", endv))

for f in (36000, 80000, 120000, 160000, 220000):
    trial(f)
