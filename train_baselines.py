"""Refit the five healthy baselines on the corrected Stage-6 dataset.

Input order is fixed by physics_deck.BASELINE_INPUT_ORDER and must not
change. Fitted on a numpy array, not a DataFrame, so feature_names_in_
stays absent exactly as the deck expects.
"""
import json, os, time
from pathlib import Path
import numpy as np, pandas as pd, joblib
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import r2_score, mean_absolute_error

IN_ORDER = ("rpm", "throttle_pct", "altitude_ft", "ambient_temperature_C")
TARGETS  = ("EGT_mean_C", "coolant_temp_C", "oil_pressure_bar",
            "oil_temperature_C", "fuelflow_kgh")

ENV = {"rpm": (3000.0, 5800.0), "throttle_pct": (56.5, 100.0),
       "altitude_ft": (0.0, 21709.34086551268),
       "ambient_temperature_C": (-27.98449491371509, 30.0)}

SRC = os.environ.get("AERIS_HEALTHY_CSV")
if not SRC:
    raise SystemExit("set AERIS_HEALTHY_CSV first")
OUT = Path("models/baseline"); OUT.mkdir(parents=True, exist_ok=True)
CFG = Path("models/configs/reconstruction_config.json")
N_TARGET = 400_000
rng = np.random.default_rng(42)

cols = list(IN_ORDER) + list(TARGETS) + ["engine_id"]
parts, seen, kept = [], 0, 0
for ch in pd.read_csv(SRC, usecols=cols, chunksize=400_000, low_memory=False):
    seen += len(ch)
    m = np.ones(len(ch), dtype=bool)
    for k, (lo, hi) in ENV.items():
        m &= ch[k].between(lo, hi).to_numpy()
    ch = ch[m]
    kept += len(ch)
    ch = ch[rng.random(len(ch)) < 0.30]
    parts.append(ch.astype({c: "float32" for c in IN_ORDER + TARGETS}))
    print(f"\rread {seen:,}  in-envelope {kept:,}", end="")

df = pd.concat(parts, ignore_index=True); del parts
print(f"\npool {len(df):,}  ({kept/seen*100:.1f}% of rows were in envelope)")

# even coverage of the envelope, not cruise-dominated
b = (pd.cut(df.rpm, 8, labels=False).astype(str) + "_" +
     pd.cut(df.throttle_pct, 6, labels=False).astype(str) + "_" +
     pd.cut(df.altitude_ft, 6, labels=False).astype(str))
ncell = b.nunique()
cap = max(200, N_TARGET // ncell)
take = []
for _, idx in b.groupby(b, observed=True).groups.items():
    arr = np.asarray(idx)
    if len(arr) > cap:
        arr = rng.choice(arr, cap, replace=False)
    take.append(arr)
sel = np.concatenate(take)
df = df.iloc[sel].reset_index(drop=True)
print(f"stratified to {len(df):,} rows over {ncell} cells, cap {cap}")

X = df[list(IN_ORDER)].to_numpy(dtype=float)
Xtr, Xte, itr, ite = train_test_split(X, np.arange(len(df)),
                                      test_size=0.15, random_state=42)
stats, report = {}, []
for t in TARGETS:
    y = df[t].to_numpy(dtype=float)
    m = RandomForestRegressor(n_estimators=60, max_depth=14,
                              min_samples_leaf=25, n_jobs=6,
                              random_state=42)
    t0 = time.time()
    m.fit(Xtr, y[itr])
    pred = m.predict(Xte)
    r2 = r2_score(y[ite], pred); mae = mean_absolute_error(y[ite], pred)
    m.n_jobs = 1                      # deck requires single-thread pickles
    f = OUT / f"{t}_baseline.pkl"
    joblib.dump(m, f, compress=3)
    mb = f.stat().st_size / 1e6
    report.append((t, r2, mae, mb, time.time() - t0))
    stats[t] = {"rows_used": int(len(df)),
                "mean": float(y.mean()), "std": float(y.std()),
                "min": float(y.min()), "max": float(y.max()),
                "operating_range": {k: [float(a), float(b)] for k, (a, b) in ENV.items()}}
    print(f"  {t:<20} R2 {r2:7.4f}  MAE {mae:8.3f}  {mb:5.1f} MB  {time.time()-t0:5.1f}s")

cfg = json.loads(CFG.read_text(encoding="utf-8"))
cfg["baseline_stats"] = stats
cfg["refit_utc"] = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
cfg["refit_source"] = str(SRC)
CFG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

print("\nSANITY at rpm 5000 / thr 80 / 6000 ft / OAT 10")
x = np.asarray([[5000.0, 80.0, 6000.0, 10.0]])
for t in TARGETS:
    v = joblib.load(OUT / f"{t}_baseline.pkl").predict(x)[0]
    print(f"  {t:<20} {v:10.3f}")
print("\nexpect coolant 88-92, EGT 700-790, oil temp 95-115, oil press 2-5")
