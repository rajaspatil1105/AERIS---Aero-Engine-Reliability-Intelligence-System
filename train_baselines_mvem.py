"""Refit the five twin baselines on MVEM healthy rows (mvem_v3.parquet)."""
import time
import numpy as np, pandas as pd, joblib
from pathlib import Path
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import r2_score, mean_absolute_error

SRC = "C:/aeris_data/datasets/mvem_v3.parquet"
IN_ORDER = ["rpm", "throttle_pct", "altitude_ft", "ambient_temperature_C"]
TARGETS = ["EGT_mean_C", "coolant_temp_C", "oil_pressure_bar",
           "oil_temperature_C", "fuelflow_kgh"]
OUT = Path("models/baseline")

df = pd.read_parquet(SRC)
h = df[df.fault_type == "healthy"].reset_index(drop=True)
print(f"healthy rows {len(h):,}  (of {len(df):,})")

X = h[IN_ORDER].to_numpy(float)
Xtr, Xte, itr, ite = train_test_split(X, np.arange(len(h)), test_size=0.15,
                                      random_state=42)
stats = {}
for t in TARGETS:
    y = h[t].to_numpy(float)
    t0 = time.time()
    m = RandomForestRegressor(n_estimators=60, max_depth=14, min_samples_leaf=25,
                              n_jobs=6, random_state=42).fit(Xtr, y[itr])
    pred = m.predict(Xte)
    f = OUT / f"{t}_baseline.pkl"
    joblib.dump(m, f, compress=3)
    mb = f.stat().st_size / 1e6
    print(f"  {t:<20} R2 {r2_score(y[ite], pred):7.4f}  "
          f"MAE {mean_absolute_error(y[ite], pred):8.3f}  {mb:5.1f} MB  "
          f"{time.time()-t0:5.1f}s")
    stats[t] = {"rows_used": len(h), "mean": float(y.mean()), "std": float(y.std()),
                "min": float(y.min()), "max": float(y.max())}

print("\nSANITY at rpm 5000 / thr 80 / 6000 ft / OAT 10")
q = np.array([[5000.0, 80.0, 6000.0, 10.0]])
for t in TARGETS:
    v = joblib.load(OUT / f"{t}_baseline.pkl").predict(q)[0]
    print(f"  {t:<20} {v:9.3f}")
print("\nMVEM ground truth at the same point:")
import shared.engine_mvem as mvem
o = mvem.solve(throttle_pct=80.0, altitude_ft=6000.0, oat_c=10.0)
print(f"  EGT {o.egt_mean_c:.2f}  coolant {o.coolant_temp_out_c:.2f}  "
      f"oilP {o.oil_pressure_bar:.2f}  oilT {o.oil_temp_c:.2f}  "
      f"fuel {o.fuel_flow_kgh:.2f}")

import json
cfg = Path("models/configs/reconstruction_config.json")
if cfg.exists():
    c = json.loads(cfg.read_text(encoding="utf-8"))
    c["baseline_stats"] = stats
    c["baseline_provenance"] = ("refit 2026-09-11 on shared/engine_mvem.py, "
                                "630k rows, 60 virtual engines, envelope "
                                "0-22800 ft / 20-100% throttle / -39.5..+49.5 C")
    cfg.write_text(json.dumps(c, indent=2), encoding="utf-8")
    print("\nreconstruction_config.json updated")
