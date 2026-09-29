import sys, pathlib
sys.path.insert(0, str(pathlib.Path(".").resolve()))
from shared import mission_engine as me
e = [x for x in me.fleet_listing() if x["serial"] == "RTX915-0001"][0]
e = me.FleetEngine(**{k: v for k, v in e.items()
                      if k in me.FleetEngine.__dataclass_fields__})
prof = [me.Setpoint(t_s=0.0, throttle_pct=75.0, altitude_ft=6000.0, oat_c=15.0),
        me.Setpoint(t_s=40*3600.0, throttle_pct=75.0, altitude_ft=6000.0, oat_c=15.0)]
last = None
for rec in me.run_mission(prof, e, dt_s=1.0, stress_enabled=True, emit_cruise_s=300.0):
    last = rec
print("40 h gentle cruise:", last["stress"], last["fault_label"])
