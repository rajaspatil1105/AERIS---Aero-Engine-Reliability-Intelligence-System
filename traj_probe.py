import sys, pathlib
sys.path.insert(0, str(pathlib.Path(".").resolve()))
from shared import mission_engine as me
e = [x for x in me.fleet_listing() if x["serial"] == "RTX915-0001"][0]
e = me.FleetEngine(**{k: v for k, v in e.items()
                      if k in me.FleetEngine.__dataclass_fields__})
prof = [me.Setpoint(t_s=0.0, throttle_pct=100.0, altitude_ft=1000.0, oat_c=38.0),
        me.Setpoint(t_s=40*3600.0, throttle_pct=100.0, altitude_ft=1000.0, oat_c=38.0)]
nxt = 0.0
for rec in me.run_mission(prof, e, dt_s=1.0, stress_enabled=True, emit_cruise_s=300.0):
    h = rec["t_s"] / 3600.0
    if h >= nxt:
        s = rec["stress"]
        print("%5.1f h  thermal %6.3f  oil %5.3f  power %5.3f  %s"
              % (h, s["thermal"], s["oil"], s["power"], rec["fault_label"]))
        nxt += 2.0
