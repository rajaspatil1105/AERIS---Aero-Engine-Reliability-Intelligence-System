"""Retrain gate + multiclass on the corrected master dataset.

Feature order, class indices and gate polarity are fixed by
node2_twin_core/predictor.py. Do not change them here.
"""
import json, os, time
from pathlib import Path
import numpy as np, pandas as pd, joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import (f1_score, precision_score, recall_score,
                             average_precision_score, confusion_matrix,
                             classification_report)
from node2_twin_core.physics_deck import BaselineDeck, BASELINE_TARGETS

RAW = ["altitude_ft","ambient_temperature_C","throttle_pct","rpm","fuelflow_kgh",
       "coolant_temp_C","EGT_mean_C","oil_pressure_bar","oil_temperature_C"]
FEATS = RAW + ["delta_EGT_mean_C","delta_coolant_temp_C","delta_oil_pressure_bar",
               "delta_oil_temperature_C","delta_fuelflow_kgh"]
CLASS_INDEX = {"cooling_degradation":0, "fuel_pressure_deviation":1,
               "lubrication_degradation":2, "misfire":3, "sensor_drift":4}
NAMES = ["cooling_degradation","fuel_pressure_dev","lubrication_degradation",
         "misfire","sensor_drift"]
ENV = {"rpm":(3000.,5800.), "throttle_pct":(56.5,100.),
       "altitude_ft":(0.,21709.34086551268),
       "ambient_temperature_C":(-27.98449491371509,30.)}
PER_CLASS  = 60_000       # per fault class, in-window rows only
HEALTHY_N  = 300_000      # matches total faulted -> balanced gate
DEADBAND_S = 5.0          # drop first N s after onset from TRAIN only
rng = np.random.default_rng(42)

SRC = os.environ.get("AERIS_MASTER_CSV") or exit("set AERIS_MASTER_CSV")
OUT = Path("models/classifier")

def eng(s): return s.str.slice(4).astype(int)

print("reading", SRC)
KEEP = 0.25
LBL  = ["engine_id","fault_type","fault_severity","time_s",
        "fault_start_time_s","fault_end_time_s"]
parts, seen = [], 0
for ch in pd.read_csv(SRC, usecols=LBL+RAW,
                      chunksize=400_000, low_memory=False):
    seen += len(ch)
    m = np.ones(len(ch), bool)
    for k,(lo,hi) in ENV.items(): m &= ch[k].between(lo,hi).to_numpy()
    ch = ch[m]
    parts.append(ch[rng.random(len(ch)) < KEEP])
    print(f"\rread {seen:,}  pool {sum(len(x) for x in parts):,}", end="")

pool = pd.concat(parts, ignore_index=True); del parts
inj = pool.fault_type.ne("healthy").to_numpy()
tt  = pool.time_s.to_numpy(float)
ss  = pool.fault_start_time_s.to_numpy(float)
ee  = pool.fault_end_time_s.to_numpy(float)
inw = inj & (tt >= ss) & (tt <= ee)
pool["label"] = np.where(inw, pool.fault_type, "healthy")
pool["sev"]   = np.where(inw, pool.fault_severity, "none")
pool["db"]    = inj & (tt >= ss) & (tt < ss + DEADBAND_S)
print(f"\nrelabel: {inj.sum():,} injection rows -> {inw.sum():,} in-window, "
      f"{(inj & ~inw).sum():,} moved to healthy")
idx = []
for k, g in pool.groupby("label", observed=True):
    cap = HEALTHY_N if k == "healthy" else PER_CLASS
    idx.append(g.sample(n=min(cap, len(g)), random_state=42).index)
df = pool.loc[np.concatenate(idx)].reset_index(drop=True)
del pool
print(f"\nsampled {len(df):,}")
print(df.label.value_counts().to_string())
nn = eng(df.engine_id)
print("healthy engines", nn[df.label=="healthy"].min(), "-", nn[df.label=="healthy"].max())
print("faulted engines", nn[df.label!="healthy"].min(), "-", nn[df.label!="healthy"].max())

print("\ncomputing deltas through the baseline deck ...")
deck = BaselineDeck()
for m in deck.models.values(): m.n_jobs = 6
X4 = df[["rpm","throttle_pct","altitude_ft","ambient_temperature_C"]].to_numpy(float)
t0 = time.time()
for t in BASELINE_TARGETS:
    df["delta_"+t] = df[t].to_numpy(float) - deck.models[t].predict(X4)
    print(f"  delta_{t:<20} {time.time()-t0:6.1f}s")

n = eng(df.engine_id)
faulted = df.label != "healthy"
test = np.asarray(n.between(241,250) | n.between(285,290))
val  = np.asarray(n.between(226,240) | n.between(279,284))
dbm  = df.db.to_numpy()
tr   = ~(test | val) & ~dbm
val  = val & ~dbm
print(f"\ntrain {tr.sum():,}   val {val.sum():,}   test {test.sum():,}")
for lbl, s in (("train",tr),("val",val),("test",test)):
    h = (s & ~faulted).sum(); f = (s & faulted).sum()
    print(f"  {lbl:<6} healthy {h:>8,}   faulted {f:>8,}")
assert (test & ~faulted).sum() > 1000, "test set has no healthy rows - split broken"
assert (test & faulted).sum() > 1000, "test set has no faulted rows - split broken"

X = df[FEATS].to_numpy(np.float32)
yg = faulted.to_numpy().astype(int)          # 1 = anomaly, required polarity

print("\n=== GATE ===")
gate = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.08,
        max_leaf_nodes=63, early_stopping=True, validation_fraction=0.12,
        random_state=42).fit(X[tr], yg[tr])
p = gate.predict_proba(X[test])[:, list(gate.classes_).index(1)]
from sklearn.metrics import balanced_accuracy_score, roc_auc_score
grid   = np.arange(0.20, 0.91, 0.01)
bas    = [balanced_accuracy_score(yg[test], p >= t) for t in grid]
best_t = float(grid[int(np.argmax(bas))])
gate_ba = float(max(bas))
base = f1_score(yg[test], np.ones(int(test.sum()), int))
best = (float(f1_score(yg[test], p >= best_t)), best_t)
print(f"  test positives {yg[test].mean():.1%}   ROC-AUC "
      f"{roc_auc_score(yg[test], p):.4f}  (prevalence-free)")
for t in (0.65, best_t):
    pr = p >= t
    tn, fp, fn, tp = confusion_matrix(yg[test], pr, labels=[0,1]).ravel()
    r = tp/max(tp+fn,1); s = tn/max(tn+fp,1)
    print(f"  thr {t:.2f}  recall {r:.4f}  specificity {s:.4f}  "
          f"bal_acc {(r+s)/2:.4f}  P {precision_score(yg[test],pr):.4f}  "
          f"F1 {f1_score(yg[test],pr):.4f}")
print(f"  always-fault bal_acc 0.5000  -> "
      f"{'PASS' if gate_ba > 0.75 else 'WEAK'}  (gate {gate_ba:.4f})")
print("  confusion at best thr (rows=true 0/1):\n",
      confusion_matrix(yg[test], p >= best_t, labels=[0,1]))
sev_te = df.sev.to_numpy()[np.asarray(test)]
pr_te  = p >= best_t
print("  detection rate by severity:")
for s_ in ['mild','moderate','severe']:
    k = sev_te == s_
    if k.sum(): print(f"    {s_:<9} {pr_te[k].mean():.4f}  (n={int(k.sum()):,})")
k = sev_te == 'none'
if k.sum(): print(f"    healthy   false-alarm {pr_te[k].mean():.4f}  (n={int(k.sum()):,})")

print("\n=== MULTICLASS ===")
fm = faulted.to_numpy()
ym = df.label.map(CLASS_INDEX).to_numpy()
mtr, mte = np.asarray(tr) & fm, np.asarray(test) & fm
mc = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.08,
        max_leaf_nodes=63, early_stopping=True, validation_fraction=0.12,
        random_state=42).fit(X[mtr], ym[mtr])
pred = mc.predict(X[mte])
print(classification_report(ym[mte], pred, target_names=NAMES, digits=4))
rec = recall_score(ym[mte], pred, average=None, labels=[0,1,2,3,4])
_sev = df.sev.to_numpy()[mte]
print("  accuracy by severity:")
for s_ in ['mild','moderate','severe']:
    k = _sev == s_
    if k.sum():
        print(f"    {s_:<9} {(pred[k]==ym[mte][k]).mean():.4f}  (n={int(k.sum()):,})")
dead = [NAMES[i] for i,r in enumerate(rec) if r == 0]
print("  dead classes:", dead if dead else "none - PASS")

joblib.dump(gate, OUT/"fault_classifier.pkl", compress=3)
joblib.dump(mc, OUT/"fault_classifier_multiclass.pkl", compress=3)
json.dump({"gate_best_threshold": float(best[1]), "gate_f1": float(best[0]),
           "always_fault_baseline_f1": float(base),
           "per_class_recall": {NAMES[i]: float(r) for i,r in enumerate(rec)},
           "rows": int(len(df)), "trained_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
          open("models/classifier/retrain_metrics.json","w"), indent=2)
print(f"\nwritten. recommended gate threshold {best[1]:.2f} (config currently 0.65)")
