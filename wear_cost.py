import sys, pathlib
sys.path.insert(0, str(pathlib.Path(".").resolve()))
from shared import mission_engine as me
e = [x for x in me.fleet_listing() if x["serial"] == "RTX915-0001"][0]
e = me.FleetEngine(**{k: v for k, v in e.items()
                      if k in me.FleetEngine.__dataclass_fields__})
prof = [me.Setpoint(t_s=0.0, throttle_pct=100.0, altitude_ft=1000.0, oat_c=38.0),
        me.Setpoint(t_s=30*3600.0, throttle_pct=100.0, altitude_ft=1000.0, oat_c=38.0)]
last = None
for rec in me.run_mission(prof, e, dt_s=1.0, stress_enabled=True, emit_cruise_s=180.0):
    last = rec
s = last["stress"]
print("after one abusive 30 h mission:")
print("  thermal %.4f -> coolant health -%.1f%%" % (s["thermal"], 30.0 * min(1, s["thermal"])))
print("  oil     %.4f -> oil health     -%.1f%%" % (s["oil"], 25.0 * min(1, s["oil"])))
print("  power   %.4f -> bearing wear   +%.1f%%" % (s["power"], 35.0 * min(1, s["power"])))
print("  label:", last["fault_label"])
