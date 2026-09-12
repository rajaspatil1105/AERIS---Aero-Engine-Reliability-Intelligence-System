"""tab3_probe.py - forced-fault injection end to end.

Flat cruise, fault forced at 300 s, cleared at 900 s, 1800 s total. Every
emitted frame is posted to the twin so we see the gate react and then recover
through the thermal lags (coolant 15 s, oil 25 s).
"""
import sys, requests
sys.path.insert(0, ".")
from shared import mission_engine as me
from shared import engine_mvem as mvem

URL = "http://127.0.0.1:8000/frames"
eng = me.FleetEngine(**{k: v for k, v in me.fleet_listing()[2].items()
                        if k in ("serial","uav_tail","hours","coolant_pump_health",
                                 "oil_pump_health","bearing_wear","note")})
profile = [me.Setpoint(t_s=0.0,    throttle_pct=80.0, altitude_ft=6000.0, oat_c=10.0),
           me.Setpoint(t_s=1800.0, throttle_pct=80.0, altitude_ft=6000.0, oat_c=10.0)]

fs = mvem.FaultState()
fs.cylinder_fuel_trim = [0.72, 1.0, 1.0, 1.0]      # one cylinder lean = misfire
fs.label = "misfire"
fs.validate()

# One session per run. POST /sessions closes the current one and opens a
# fresh row, so replay never mixes two sim runs (or a run with the
# smoke-test frames that preceded it).
sid = requests.post("http://127.0.0.1:8000/sessions",
                    json={"note": "tab3 forced %s on %s"
                          % (fs.label, eng.serial)}, timeout=10
                    ).json()["session_id"]
print("session %d" % sid)
print("engine %s (%s, %.0f h, %s)" % (eng.serial, eng.uav_tail, eng.hours, eng.note))
print("misfire forced 300-900 s of a 1800 s cruise at 80%% / 6000 ft / 10 C\n")
print("   t_s   EGT  coolant  oilP  |  p_anom  status    label")
sess, n_hit, n_frames = requests.Session(), 0, 0
for rec in me.run_mission(profile, eng, dt_s=1.0, stress_enabled=True,
                          forced_fault=fs, forced_at_s=300.0,
                          forced_clear_s=900.0,
                          emit_cruise_s=30.0, emit_event_s=5.0):
    f = rec["frame"]
    n_frames += 1
    try:
        r = sess.post(URL, json=f, timeout=10).json()
    except Exception as exc:
        print("POST failed at t=%.0f: %s" % (rec["t_s"], exc)); break
    if "anomaly_probability" not in r:
        print("service said:", r); break
    p, st = r["anomaly_probability"], r.get("status")
    if st == "FAULT": n_hit += 1
    if n_frames % 3 == 0 or 295 <= rec["t_s"] <= 360 or 895 <= rec["t_s"] <= 980:
        print("  %5.0f %5.0f  %6.2f  %4.2f  |  %.4f  %-8s  %s"
              % (rec["t_s"], f["EGT_mean_C"], f["coolant_temp_C"],
                 f["oil_pressure_bar"], p, st, r.get("fault_label") or "-"))
print("\n%d frames posted, %d flagged FAULT" % (n_frames, n_hit))
summ = sess.get("http://127.0.0.1:8000/summary",
                params={"session_id": sid}, timeout=10).json()
print("session %d summary: %s" % (sid, summ))
print("replay:  http://127.0.0.1:8000/frames?session_id=%d" % sid)
print("report:  http://127.0.0.1:8000/report/%d.csv" % sid)
