import sys, pathlib, time
sys.path.insert(0, str(pathlib.Path(".").resolve()))
from shared import mission_engine as me

def run(hours, dt, cruise):
    e = [x for x in me.fleet_listing() if x["serial"] == "RTX915-0001"][0]
    e = me.FleetEngine(**{k: v for k, v in e.items()
                          if k in me.FleetEngine.__dataclass_fields__})
    prof = [me.Setpoint(t_s=0.0, throttle_pct=100.0, altitude_ft=1000.0, oat_c=38.0),
            me.Setpoint(t_s=hours*3600.0, throttle_pct=100.0, altitude_ft=1000.0, oat_c=38.0)]
    last = None
    t0 = time.time()
    for rec in me.run_mission(prof, e, dt_s=dt, stress_enabled=True,
                              emit_cruise_s=cruise, emit_event_s=1.0):
        last = rec
    return last, time.time() - t0

last, w = run(40.0, 1.0, 300.0)
print("40 h stress:", last["stress"], " labels:", last["fault_label"], " %.1f s" % w)

a, wa = run(2.0, 1.0, 300.0)      # adaptive (coarse kicks in >1 h)
b, wb = run(0.9, 1.0, 300.0)      # under 1 h -> forced all-fine, reference
print("2.0 h adaptive:", a["stress"], "%.1f s" % wa)
print("0.9 h all-fine:", b["stress"], "%.1f s" % wb)
