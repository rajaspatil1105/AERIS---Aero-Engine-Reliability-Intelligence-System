from __future__ import annotations
import datetime, importlib, pathlib

SOURCES = [
    ("node1_ingestion.adapter",  "adapter_caveats",      "Node 1 ingestion adapter - input assumptions"),
    ("node1_ingestion.adapter",  "adapter_declarations", "Node 1 ingestion adapter - scope declarations"),
    ("shared.atmosphere",        "atmosphere_caveats",   "Shared - atmosphere / density altitude"),
    ("shared.stress_sim",        "stress_caveats",       "Shared - envelope stress sweep"),
    ("shared.throttle_dynamics", "dynamics_caveats",     "Shared - throttle dynamics / admission gate"),
    ("shared.fault_injection",   "injection_caveats",    "Shared - synthetic fault injection"),
]

out, missing, total, unver = [], [], 0, 0
out += ["# AERIS - declared caveats", "",
        "Generated %s by make_caveats.py. Do not edit by hand." %
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), ""]

for mod, fn, title in SOURCES:
    try:
        m = importlib.import_module(mod)
    except Exception as e:
        out += ["## " + title, "", "- MODULE IMPORT FAILED: %r" % (e,), ""]
        continue
    f = getattr(m, fn, None)
    if f is None:
        missing.append("%s.%s" % (mod, fn))
        continue
    out += ["## " + title, "", "Source: `%s.%s()`" % (mod, fn), ""]
    for item in f():
        d = item if isinstance(item, dict) else getattr(item, "__dict__", {"value": repr(item)})
        ver = d.get("verified", None)
        flag = "VERIFIED" if ver is True else ("UNVERIFIED" if ver is False else "n/a")
        total += 1
        unver += 1 if ver is False else 0
        out.append("### %s  [%s]" % (d.get("id", "(no id)"), flag))
        for k, v in d.items():
            if k in ("id", "verified"):
                continue
            out.append("- **%s**: %s" % (k, v))
        out.append("")

out += ["## Summary", "",
        "- %d declared caveats, %d marked UNVERIFIED." % (total, unver),
        "- Regression invariant: p_anom = 0.5443998040908319 at rpm 5000, throttle 80 %, 6000 ft, 10 C.",
        "- Fault gate threshold 0.65 is UNTRUSTED pre-retrain.",
        "- Multiclass label 'fuel_pressure_dev' is a dead class and is never predicted.",
        "- RUL is emitted with rul_trusted = False and rul_units = 'unknown'.",
        "- Transient frames are not scored by design; they surface as UNAVAILABLE with a reason.", ""]
if missing:
    out += ["## Caveat functions not found (rename in make_caveats.py if they exist)", ""] + \
           ["- " + n for n in missing] + [""]

pathlib.Path("CAVEATS.md").write_text("\n".join(out), encoding="utf-8")
print("wrote CAVEATS.md -- %d caveats, %d unverified" % (total, unver))
if missing:
    print("not found: " + ", ".join(missing))
