import requests, time
B = "http://localhost:8000"
body = dict(engine_serial="RTX915-0003", fault="lubrication_degradation",
            fault_severity="severe", fault_at_s=600, fault_clear_s=1200,
            duration_s=1500, throttle_pct=80, altitude_ft=6000, oat_c=10,
            emit_cruise_s=60, emit_event_s=60, speed=0)
sid = requests.post(B + "/sim/run", json=body, timeout=30).json()["session_id"]
while not requests.get("%s/sim/run/%d" % (B, sid), timeout=10).json()["done"]:
    time.sleep(1)
rows = requests.get(B + "/frames", params={"session_id": sid, "limit": 1000},
                    timeout=30).json()
rows = rows.get("frames", rows) if isinstance(rows, dict) else rows
print("session", sid, "frames", len(rows))
for r in rows:
    fid = r.get("id")
    d = requests.get("%s/frames/%d" % (B, fid), params={"session_id": sid},
                     timeout=10).json()
    fr = d.get("frame", d)
    f, e, g = fr["features"], fr["expected"], fr["residuals"]
    print("seq %-3s rpm %7.1f  oilP meas %.3f  exp %.3f  resid %+.4f  p %.4f  %s"
          % (r.get("seq"), f["rpm"], f["oil_pressure_bar"],
             e["oil_pressure_bar"], g["oil_pressure_bar"],
             fr["anomaly_probability"], r.get("status")))
