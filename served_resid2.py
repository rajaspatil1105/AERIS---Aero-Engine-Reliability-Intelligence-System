import requests
B = "http://localhost:8000"
SID = 25
rows = requests.get(B + "/frames", params={"session_id": SID, "limit": 1000},
                    timeout=30).json()
rows = rows.get("frames", rows) if isinstance(rows, dict) else rows
rows = sorted(rows, key=lambda r: r["seq"])
for r in rows:
    d = requests.get("%s/frames/%d" % (B, r["seq"]),
                     params={"session_id": SID}, timeout=10).json()
    fr = d.get("frame", d)
    f = fr.get("features", {}); e = fr.get("expected", {}); g = fr.get("residuals", {})
    print("seq %-3d rpm %7.1f  oilP %.3f  exp %.3f  resid %+.4f  p %.4f  %-7s refus=%s meaning=%s"
          % (r["seq"], f.get("rpm", float("nan")),
             f.get("oil_pressure_bar", float("nan")),
             e.get("oil_pressure_bar", float("nan")),
             g.get("oil_pressure_bar", float("nan")),
             r["p_anom"] if r["p_anom"] is not None else float("nan"),
             r["status"], r["refusal_class"], r["meaningful"]))
