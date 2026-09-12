import json, pathlib, pandas as pd
IN = ["rpm", "throttle_pct", "altitude_ft", "ambient_temperature_C"]
df = pd.read_parquet("C:/aeris_data/datasets/mvem_v3.parquet",
                     columns=IN + ["fault_type"])
h = df[df.fault_type == "healthy"]
rng = {k: [float(h[k].min()), float(h[k].max())] for k in IN}
print("operating_range from the rows the baselines were actually fit on:")
for k, (lo, hi) in rng.items():
    print("  %-22s %10.2f .. %10.2f" % (k, lo, hi))

p = pathlib.Path("models/configs/reconstruction_config.json")
pathlib.Path(str(p) + ".envbak").write_text(p.read_text(encoding="utf-8"),
                                            encoding="utf-8")
d = json.loads(p.read_text(encoding="utf-8"))
if "operating_coordinates" in d:
    print("\nexisting operating_coordinates:", json.dumps(d["operating_coordinates"])[:300])
for ch, st in d["baseline_stats"].items():
    st["operating_range"] = {k: list(v) for k, v in rng.items()}
    st["operating_range_source"] = ("healthy rows of mvem_v3.parquet, the same "
        "rows these forests were fit on. Random forests extrapolate flat, so "
        "check_envelope() refuses anything outside. NOTE: ambient tops out at "
        "39.9 C not the 49.5 C clamp - ISA 15 C plus max deviation 25 C was the "
        "hottest air ever sampled.")
d["baseline_provenance"] = (d.get("baseline_provenance", "") +
    " | envelope restored 2026-09-11 from data min/max per channel.")
p.write_text(json.dumps(d, indent=2), encoding="utf-8")
print("\nwrote operating_range into all 5 baseline_stats channels")
