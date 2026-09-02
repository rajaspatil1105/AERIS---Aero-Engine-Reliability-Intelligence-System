from __future__ import annotations
import json, pathlib, urllib.error, urllib.request

BASE = "http://127.0.0.1:8000"
OUT = pathlib.Path("contract"); OUT.mkdir(exist_ok=True)

HEALTHY = {"altitude_ft": 6000.0, "ambient_temperature_C": 10.0, "throttle_pct": 80.0,
           "rpm": 5000.0, "fuelflow_kgh": 10.5801, "coolant_temp_C": 66.6186,
           "EGT_mean_C": 456.1973, "oil_pressure_bar": 3.1616, "oil_temperature_C": 70.8429}

def call(method, path, payload=None):
    url = BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")
    except Exception as e:
        return 0, {"transport_error": repr(e)}

def frame(name, **over):
    p = dict(HEALTHY); p.update(over)
    code, body = call("POST", "/frames", p)
    (OUT / ("frame_%s.json" % name)).write_text(
        json.dumps({"request": p, "http_status": code, "response": body}, indent=2),
        encoding="utf-8")
    print("%-14s http=%s status=%-11s healthy=%-5s p_anom=%-8s label=%-24s keys=%d" % (
        name, code, body.get("status"), body.get("is_healthy"),
        (round(body["anomaly_probability"], 4) if isinstance(body.get("anomaly_probability"), (int, float)) else None),
        body.get("fault_label"), len(body)))
    for k in ("rul", "rul_raw", "rul_trusted", "rul_units", "safety_alert", "in_envelope"):
        if k in body:
            print("      %-14s %s" % (k, body[k]))
    v = body.get("envelope_violations")
    if v: print("      violations     %s" % (v,))
    return body

print("-- four wire payloads --")
frame("healthy")
frame("advisory", oil_pressure_bar=2.1616)
frame("fault_oilhot", oil_temperature_C=85.8429)
frame("declined_envelope", throttle_pct=40.0)

print("\n-- /explain with explain=False --")
code, body = call("POST", "/explain?top_n=5", HEALTHY)
(OUT / "explain.json").write_text(json.dumps({"http_status": code, "response": body}, indent=2), encoding="utf-8")
print("http=%s keys=%s" % (code, list(body)[:12]))
print("explanation=%r  error=%r" % (body.get("explanation"), body.get("error")))

print("\n-- /caveats shape --")
cav = json.loads((OUT / "caveats.json").read_text(encoding="utf-8-sig"))
print("top-level: %s" % (list(cav) if isinstance(cav, dict) else "FLAT LIST len=%d" % len(cav)))

print("\n-- /summary, /events, /sessions --")
for p in ("/summary", "/events?limit=3", "/sessions?limit=3"):
    code, body = call("GET", p)
    print("%-18s http=%s -> %s" % (p, code, list(body)[:10] if isinstance(body, dict) else "list len=%d" % len(body)))
