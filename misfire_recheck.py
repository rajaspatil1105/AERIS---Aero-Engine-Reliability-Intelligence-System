import requests, time
B = "http://localhost:8000"
body = dict(engine_serial="RTX915-0003", fault="misfire", fault_severity="severe",
            fault_at_s=600, fault_clear_s=1200, duration_s=1500,
            throttle_pct=80, altitude_ft=6000, oat_c=10,
            emit_cruise_s=60, emit_event_s=60, speed=0)
sid = requests.post(B + "/sim/run", json=body, timeout=30).json()["session_id"]
while not requests.get("%s/sim/run/%d" % (B, sid), timeout=10).json()["done"]:
    time.sleep(1)
rows = requests.get(B + "/frames", params={"session_id": sid, "limit": 1000},
                    timeout=30).json()
rows = rows.get("frames", rows) if isinstance(rows, dict) else rows
for r in sorted(rows, key=lambda x: x["seq"]):
    d = requests.get("%s/frames/%d" % (B, r["seq"]),
                     params={"session_id": sid}, timeout=10).json()
    fr = d.get("frame", d); g = fr.get("residuals", {})
    print("seq %-3d EGT %7.1f  dEGT %+8.3f  dFF %+.4f  p %.4f  %-7s %s"
          % (r["seq"], fr["features"]["EGT_mean_C"], g.get("EGT_mean_C", 0.0),
             g.get("fuelflow_kgh", 0.0), r["p_anom"], r["status"],
             r["fault_label"] or ""))
