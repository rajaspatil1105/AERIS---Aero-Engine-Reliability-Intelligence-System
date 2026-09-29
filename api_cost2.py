import time, requests
B = "http://localhost:8000"
body = {"engine_serial": "RTX915-0001", "throttle_pct": 100.0,
        "altitude_ft": 1000.0, "oat_c": 38.0,
        "duration_s": 7200.0, "stress_enabled": True,
        "emit_cruise_s": 300.0, "emit_event_s": 1.0}

t0 = time.time()
r = requests.post(B + "/sim/run", json=body, timeout=120)
print(r.status_code, r.text[:120])
sid = r.json()["session_id"]

worst = 0.0
while True:
    time.sleep(3.0)
    t1 = time.time()
    s = requests.get("%s/sim/run/%s" % (B, sid), timeout=600).json()
    lat = time.time() - t1
    worst = max(worst, lat)
    print("  poll %.1fs latency  done=%s frames=%s"
          % (lat, s.get("done"), s.get("frames")))
    if s.get("done"):
        break

w = time.time() - t0
print("2 h: %.1f s wall, %s frames, worst poll latency %.1f s"
      % (w, s.get("frames"), worst))
print("extrapolated 30 h at same emit rate: %.1f min" % (w * 15.0 / 60.0))
