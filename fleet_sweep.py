import requests, time
B = "http://localhost:8000"
fleet = requests.get(B + "/sim/fleet", timeout=10).json()
fleet = fleet.get("engines") or fleet.get("fleet") or fleet
print("%-14s %6s %8s %9s %10s %s" % ("serial","hours","p_anom","res_oilP","status","margin"))
for e in fleet:
    body = {"engine_serial": e["serial"], "duration_s": 120, "speed": 0,
            "emit_cruise_s": 30.0, "emit_event_s": 5.0,
            "throttle_pct": 80.0, "altitude_ft": 6000.0, "oat_c": 10.0}
    r = requests.post(B + "/sim/run", json=body, timeout=60)
    if r.status_code >= 300:
        print("%-14s  POST %s %s" % (e["serial"], r.status_code, r.text[:80])); continue
    sid = r.json().get("session_id")
    for _ in range(40):
        st = requests.get("%s/sim/run/%s" % (B, sid), timeout=10).json()
        if st.get("done"): break
        time.sleep(0.25)
    rows = requests.get(B + "/frames", params={"session_id": sid, "limit": 5},
                        timeout=20).json()
    rows = rows.get("frames", rows) if isinstance(rows, dict) else rows
    if not rows:
        print("%-14s  no frames" % e["serial"]); continue
    r0 = sorted(rows, key=lambda x: x["seq"])[-1]
    d = requests.get("%s/frames/%d" % (B, r0["seq"]),
                     params={"session_id": sid}, timeout=10).json()
    d = d.get("frame", d)
    p = r0["p_anom"] or 0.0
    print("%-14s %6.0f %8.4f %+9.4f %10s %s"
          % (e["serial"], e["hours"], p,
             d.get("residuals", {}).get("oil_pressure_bar", float("nan")),
             r0["status"], "CROSSES" if p >= 0.50 else "%+.3f" % (0.50 - p)))
