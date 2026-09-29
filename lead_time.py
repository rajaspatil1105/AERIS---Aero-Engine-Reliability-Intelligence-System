import requests
B = "http://localhost:8000"
d = requests.get(B + "/frames", params={"session_id": 71, "limit": 5000}, timeout=120).json()
rows = d if isinstance(d, list) else d.get("frames", d.get("rows", []))
print("rows:", len(rows), "keys:", sorted(rows[0])[:14] if rows else None)
first_f = first_l = None
for r in rows:
    h = (r.get("t_s") or 0) / 3600.0
    if first_f is None and (r.get("p_anom") or 0) > 0.5:
        first_f = h
    lab = (r.get("fault_label") or "")
    if first_l is None and lab and lab != "HEALTHY":
        first_l = h
print("gate first exceeds 0.5 at  %.1f h" % first_f if first_f else "gate never fired")
print("label first names fault at %.1f h" % first_l if first_l else "label never fired")
if first_f and first_l:
    print("predictive lead time: %.1f h" % (first_l - first_f))
