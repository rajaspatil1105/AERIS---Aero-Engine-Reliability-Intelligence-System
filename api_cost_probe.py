import sys, pathlib, time
sys.path.insert(0, str(pathlib.Path(".").resolve()))
import requests

B = "http://localhost:8000"
body = {"engine_serial": "RTX915-0001", "throttle_pct": 100.0,
        "altitude_ft": 1000.0, "oat_c": 38.0,
        "duration_s": 7200.0, "stress_enabled": True,
        "emit_cruise_s": 300.0, "emit_event_s": 1.0}
t0 = time.time()
r = requests.post(B + "/sim/run", json=body, timeout=30)
print(r.status_code, r.text[:200])
sid = r.json().get("session_id")
while True:
    time.sleep(2.0)
    s = requests.get("%s/sim/run/%s" % (B, sid), timeout=30).json()
    if s.get("done"):
        break
print("2 h mission: %.1f s wall, frames=%s" % (time.time() - t0, s.get("frames")))
