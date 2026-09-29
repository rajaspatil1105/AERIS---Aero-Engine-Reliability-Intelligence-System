import time, requests
B = "http://localhost:8000"
body = {"engine_serial": "RTX915-0001", "throttle_pct": 100.0,
        "altitude_ft": 1000.0, "oat_c": 38.0, "duration_s": 108000.0,
        "stress_enabled": True, "emit_cruise_s": 180.0,
        "emit_event_s": 1.0, "speed": 0}
sid = requests.post(B + "/sim/run", json=body, timeout=60).json()["session_id"]
while True:
    time.sleep(5.0)
    s = requests.get("%s/sim/run/%s" % (B, sid), timeout=600).json()
    if s.get("done") or s.get("error"):
        break
rows = requests.get(B + "/frames", params={"session_id": sid, "limit": 1000},
                    timeout=120).json()["frames"]
rows.sort(key=lambda x: x["seq"])
last = rows[-1]["seq"]
def hrs(x): return x["seq"] / last * 30.0
g = l = None
for x in rows:
    if g is None and (x.get("p_anom") or 0) > 0.5: g = hrs(x)
    lb = x.get("fault_label") or ""
    if l is None and lb and lb != "HEALTHY": l = hrs(x)
print("session %s  gate %s  label %s  lead %s"
      % (sid, "%.1f h" % g if g else "never", "%.1f h" % l if l else "never",
         "%.1f h" % (l - g) if (g and l) else "--"))
print("\n  hour   p_anom  status     rul_sm")
for x in rows[::40]:
    print("  %5.1f  %6.3f  %-9s  %s" % (hrs(x), x.get("p_anom") or 0, x.get("status"),
        ("%.1f" % x["rul_smoothed"]) if x.get("rul_smoothed") is not None else "--"))
