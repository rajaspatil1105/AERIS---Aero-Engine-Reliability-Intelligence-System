import time, json, requests
B = "http://localhost:8000"
body = {"engine_serial": "RTX915-0001", "throttle_pct": 100.0,
        "altitude_ft": 1000.0, "oat_c": 38.0, "duration_s": 7200.0,
        "stress_enabled": True, "emit_cruise_s": 180.0,
        "emit_event_s": 1.0, "speed": 0}
sid = requests.post(B + "/sim/run", json=body, timeout=60).json()["session_id"]
while True:
    time.sleep(3.0)
    s = requests.get("%s/sim/run/%s" % (B, sid), timeout=600).json()
    if s.get("done"):
        break
print(json.dumps(s, indent=1)[:600])
import pathlib
w = pathlib.Path.home() / ".aeris" / "fleet_wear.json"
print("wear file exists:", w.exists(), "->", w.read_text() if w.exists() else "")
