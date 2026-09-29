import requests
B = "http://localhost:8000"
r = requests.get(B + "/frames", params={"session_id": 71, "limit": 1000}, timeout=120)
rows = r.json()["frames"]
rows.sort(key=lambda x: x["seq"])          # oldest first
print("rows:", len(rows), "seq", rows[0]["seq"], "->", rows[-1]["seq"])

HOURS = 30.0
last = rows[-1]["seq"]
def hrs(x): return x["seq"] / last * HOURS

gate = lab = crit = None
for x in rows:
    if gate is None and (x.get("p_anom") or 0) > 0.5:
        gate = hrs(x)
    l = x.get("fault_label") or ""
    if lab is None and l and l != "HEALTHY":
        lab = hrs(x)
    if crit is None and x.get("status") in ("FAULT", "CRITICAL"):
        crit = hrs(x)
print("gate p_anom > 0.5 first at   %s" % ("%.1f h" % gate if gate else "never"))
print("label names a fault at       %s" % ("%.1f h" % lab if lab else "never"))
print("status FAULT/CRITICAL at     %s" % ("%.1f h" % crit if crit else "never"))
if gate and lab:
    print("lead time gate -> label: %.1f h" % (lab - gate))

print("\n  hour   p_anom  status     label                 rul_sm")
for x in rows[::40]:
    print("  %5.1f  %6.3f  %-9s  %-20s  %s"
          % (hrs(x), x.get("p_anom") or 0, x.get("status"),
             (x.get("fault_label") or "")[:20],
             ("%.1f" % x["rul_smoothed"]) if x.get("rul_smoothed") is not None else "--"))
