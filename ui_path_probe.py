import time, requests
B = "http://localhost:8000"
body = {"engine_serial": "RTX915-0001", "throttle_pct": 100.0,
        "altitude_ft": 1000.0, "oat_c": 38.0,
        "duration_s": 108000.0, "stress_enabled": True,
        "emit_cruise_s": 180.0, "emit_event_s": 1.0, "speed": 0}
t0 = time.time()
r = requests.post(B + "/sim/run", json=body, timeout=60)
print(r.status_code, r.text[:140])
sid = r.json()["session_id"]
while True:
    time.sleep(5.0)
    s = requests.get("%s/sim/run/%s" % (B, sid), timeout=600).json()
    print("  frames=%s faults=%s done=%s" % (s.get("frames"), s.get("faults"), s.get("done")))
    if s.get("done") or s.get("error"):
        break
print("30 h via API: %.1f s, frames %s, error %s"
      % (time.time() - t0, s.get("frames"), s.get("error")))
