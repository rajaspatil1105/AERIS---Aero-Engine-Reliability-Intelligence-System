import requests, json
B = "http://localhost:8000"
body = dict(engine_serial="RTX915-0003", fault="lubrication_degradation",
            fault_severity="severe", fault_at_s=600, fault_clear_s=1200,
            duration_s=1500, throttle_pct=80, altitude_ft=6000, oat_c=10,
            emit_cruise_s=60, emit_event_s=60, speed=0)
r = requests.post(B + "/sim/run", json=body, timeout=30)
print("POST /sim/run ->", r.status_code)
print(r.text[:1200])
print("\nhealth:", requests.get(B + "/health", timeout=10).status_code)
