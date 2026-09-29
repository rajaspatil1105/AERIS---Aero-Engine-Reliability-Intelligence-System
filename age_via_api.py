import time, requests
B = "http://localhost:8000"

def rows():
    d = requests.get(B + "/sim/fleet", timeout=30).json()
    if isinstance(d, dict):
        for k in ("fleet", "engines", "items"):
            if isinstance(d.get(k), list):
                return d[k]
        raise SystemExit("unexpected shape: " + str(list(d))[:200])
    return d

def state(tag):
    f = [e for e in rows() if e["serial"] == "RTX915-0001"][0]
    print("%-10s hours %6.1f  coolant %.4f  oil %.4f  bearing %.4f  %s"
          % (tag, f["hours"], f["coolant_pump_health"],
             f["oil_pump_health"], f["bearing_wear"], f.get("note", "")))

state("before")
body = {"engine_serial": "RTX915-0001", "throttle_pct": 100.0,
        "altitude_ft": 1000.0, "oat_c": 38.0, "duration_s": 108000.0,
        "stress_enabled": True, "emit_cruise_s": 180.0,
        "emit_event_s": 1.0, "speed": 0}
sid = requests.post(B + "/sim/run", json=body, timeout=60).json()["session_id"]
while True:
    time.sleep(5.0)
    s = requests.get("%s/sim/run/%s" % (B, sid), timeout=600).json()
    if s.get("done"):
        break
print("aged_h:", s.get("aged_h"), " wear_error:", s.get("wear_error"))
state("after")
