import sys, pathlib
sys.path.insert(0, str(pathlib.Path(".").resolve()))
from shared import mission_engine as me

me.reset_wear()
def show(tag):
    e = me.engine_now("RTX915-0001")
    print("%-10s hours %6.1f  coolant %.4f  oil %.4f  bearing %.4f"
          % (tag, e.hours, e.coolant_pump_health, e.oil_pump_health, e.bearing_wear))

show("factory")
for n in range(1, 4):
    e = me.engine_now("RTX915-0001")
    prof = [me.Setpoint(t_s=0.0, throttle_pct=100.0, altitude_ft=1000.0, oat_c=38.0),
            me.Setpoint(t_s=30*3600.0, throttle_pct=100.0, altitude_ft=1000.0, oat_c=38.0)]
    last = None
    for rec in me.run_mission(prof, e, dt_s=1.0, stress_enabled=True, emit_cruise_s=180.0):
        last = rec
    st = me.StressState(**{k: last["stress"][k] for k in ("thermal","oil","power","cycles")},
                        triggered=list(last["stress"]["triggered"]))
    me.record_wear("RTX915-0001", st, 30.0)
    show("after %d" % n)
print("\nwear file:", me._WEAR_PATH.read_text(encoding="utf-8"))
