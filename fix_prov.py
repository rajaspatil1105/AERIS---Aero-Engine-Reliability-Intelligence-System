import json, pathlib
SRC = ("MVEM mean-value engine model (shared/engine_mvem.py), synthetic. "
       "Calibrated to manufacturer data at 5800 rpm WOT sea level (AVL Boost "
       "paper Table 4): fuel 33.8 L/h, 104.4 kW, BSFC 243 g/kWh all match to "
       "0.0%. NO OTHER POINT IN THE ENVELOPE IS VALIDATED - no public "
       "part-throttle or altitude test data exists for the Rotax 915iS. "
       "Dataset mvem_v3.parquet, 629,441 rows, 60 virtual engines.")

p = pathlib.Path("build_manifest.py")
s = p.read_text(encoding="utf-8")
old = '            "source": "Cantera simulation, synthetic",'
new = '            "source": %s,' % json.dumps(SRC)
if old not in s: raise SystemExit("NOT FOUND: build_manifest source")
s = s.replace(old, new, 1)
old2 = ('            "note": "no artifact trained on this data may be presented as a "\n'
        '                    "validated engine model; all outputs are pipeline demonstrations.",')
new2 = ('            "note": "no artifact trained on this data may be presented as a "\n'
        '                    "validated engine model; all outputs are pipeline demonstrations. "\n'
        '                    "Known model weaknesses: oil temperature barely responds to "\n'
        '                    "oil_pump_health, thermal time constants are UNVERIFIED, rpm is "\n'
        '                    "a linear function of throttle with no propeller load model.",')
if old2 in s: s = s.replace(old2, new2, 1)
else: print("WARNING: provenance note not matched, source still updated")
compile(s.lstrip("\ufeff"), str(p), "exec")
p.write_text(s, encoding="utf-8")
print("build_manifest.py provenance updated")

c = pathlib.Path("contract/caveats.json")
d = json.loads(c.read_text(encoding="utf-8"))
d["data_provenance"] = SRC
c.write_text(json.dumps(d, indent=2), encoding="utf-8")
print("contract/caveats.json provenance updated")
