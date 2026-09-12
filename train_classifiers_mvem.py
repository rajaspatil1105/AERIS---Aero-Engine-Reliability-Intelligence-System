"""train_classifiers_mvem.py
Gate + multiclass trained on mvem_v3.parquet.

Labels come straight from fault_type - the generator writes one label per row,
so all the in-window relabel logic in train_classifiers.py is dead here. No
fault_start_time_s, no pre-onset rows, no post-end rows.

Whole engines are held out. Threshold chosen on validation, reported on test.
"""
import json, os, pathlib, sys, time
import numpy as np
import pandas as pd
import joblib
import pyarrow.parquet as pq
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import (accuracy_score, balanced_accuracy_score,
                             classification_report, confusion_matrix, f1_score,
                             precision_score, recall_score, roc_auc_score)

ROOT = pathlib.Path(__file__).resolve().parent
SRC  = pathlib.Path(os.environ.get("AERIS_MVEM_PARQUET",
                                   "C:/aeris_data/datasets/mvem_v3.parquet"))
BASE = ROOT / "models" / "baseline"
OUT  = ROOT / "models" / "classifier"
OUT.mkdir(parents=True, exist_ok=True)

ENV   = ["rpm", "throttle_pct", "altitude_ft", "ambient_temperature_C"]
RAW   = ["EGT_mean_C", "coolant_temp_C", "oil_pressure_bar",
         "oil_temperature_C", "fuelflow_kgh"]
DELTA = ["delta_" + c for c in RAW]
# Do NOT restate the order here. node2_twin_core.residual_calc owns the
# 14-column contract and feature_names.json mirrors it; a local list
# silently disagreed once and the gate scored permuted columns (0.513 on
# an exact-ground-truth frame). Import it so that cannot recur.
import sys as _sys; _sys.path.insert(0, str(ROOT))
from node2_twin_core.residual_calc import FEATURE_ORDER as FEATURES
FEATURES = list(FEATURES)
assert sorted(FEATURES) == sorted(ENV + RAW + DELTA), \
    'contract columns differ from the 14 this script can build'
NAMES = ["cooling_degradation", "fuel_pressure_dev", "lubrication_degradation",
         "misfire", "sensor_drift"]
CLASS_INDEX = {n: i for i, n in enumerate(NAMES)}
ALIAS = {"fuel_pressure_deviation": "fuel_pressure_dev"}

HP = dict(max_iter=300, learning_rate=0.08, max_leaf_nodes=63,
          early_stopping=True, validation_fraction=0.12, random_state=42)
N_TEST_ENG, N_VAL_ENG, SEED = 10, 8, 42
T0 = time.time()

def tick(msg):
    print("  [%6.1fs] %s" % (time.time() - T0, msg), flush=True)

# ---------------------------------------------------------------- baselines
def unwrap(obj, path):
    if hasattr(obj, "predict"):
        return obj
    if isinstance(obj, dict):
        for v in obj.values():
            if hasattr(v, "predict"):
                return v
    raise SystemExit("no estimator with .predict found inside " + str(path))

models = {}
for ch in RAW:
    p = BASE / (ch + "_baseline.pkl")
    if not p.exists():
        raise SystemExit("missing baseline " + str(p))
    m = unwrap(joblib.load(p), p)
    n = getattr(m, "n_features_in_", None)
    if n is not None and n != len(ENV):
        raise SystemExit("%s expects %d features, ENV has %d - baseline was fit "
                         "on a different feature set" % (ch, n, len(ENV)))
    models[ch] = m
tick("loaded 5 baseline regressors from " + str(BASE))

# ---------------------------------------------------------------- load data
have = set(pq.ParquetFile(SRC).schema.names)
need = ["engine_id", "fault_type"] + ENV + RAW
missing = [c for c in need if c not in have]
if missing:
    raise SystemExit("parquet is missing columns: " + ", ".join(missing))
sev_col  = next((c for c in ("fault_severity", "severity") if c in have), None)
tran_col = next((c for c in have if "transient" in c.lower()), None)
cols = need + [c for c in (sev_col, tran_col) if c]

df = pd.read_parquet(SRC, columns=cols)
df["fault_type"] = df.fault_type.astype(str).replace(ALIAS)
bad = sorted(set(df.fault_type) - set(NAMES) - {"healthy"})
if bad:
    raise SystemExit("unexpected fault_type values: " + ", ".join(bad))
tick("read %s rows, %d engines" % (f"{len(df):,}", df.engine_id.nunique()))
print(df.fault_type.value_counts().to_string())

for ch in RAW:
    df["delta_" + ch] = (df[ch].to_numpy(float)
                         - models[ch].predict(df[ENV].to_numpy(float)))
tick("computed 5 residual channels")

h = df.fault_type == "healthy"
print("\nhealthy residual floor (mean abs, this is the detection floor):")
for ch in RAW:
    print("  %-20s %.4f" % (ch, df.loc[h, "delta_" + ch].abs().mean()))

# ------------------------------------------------------------ engine split
eng  = np.sort(df.engine_id.unique())
perm = np.random.default_rng(SEED).permutation(len(eng))
test_e = set(eng[perm[:N_TEST_ENG]])
val_e  = set(eng[perm[N_TEST_ENG:N_TEST_ENG + N_VAL_ENG]])
mte = df.engine_id.isin(test_e).to_numpy()
mva = df.engine_id.isin(val_e).to_numpy()
mtr = ~(mte | mva)

X = df[FEATURES].to_numpy(float)
yg = (~h).to_numpy().astype(int)
print("\nsplit by whole engines (no engine appears in two sets)")
for lbl, m in (("train", mtr), ("val", mva), ("test", mte)):
    print("  %-5s %8s rows  healthy %7s  faulted %7s  engines %d"
          % (lbl, f"{m.sum():,}", f"{(m & ~yg.astype(bool)).sum():,}",
             f"{(m & yg.astype(bool)).sum():,}", df.engine_id[m].nunique()))
assert (mte & ~yg.astype(bool)).sum() > 1000, "test set has too few healthy rows"
assert (mte &  yg.astype(bool)).sum() > 1000, "test set has too few faulted rows"

# -------------------------------------------------------------------- gate
print("\n=== GATE ===")
gate = HistGradientBoostingClassifier(**HP).fit(X[mtr], yg[mtr])
tick("gate fit")
pv, pt = gate.predict_proba(X[mva])[:, 1], gate.predict_proba(X[mte])[:, 1]

grid = np.arange(0.20, 0.81, 0.01)
bal  = [balanced_accuracy_score(yg[mva], (pv >= t).astype(int)) for t in grid]
THR  = float(grid[int(np.argmax(bal))])
print("threshold %.2f chosen on validation (bal_acc %.4f there)"
      % (THR, max(bal)))

pred = (pt >= THR).astype(int)
tn, fp, fn, tp = confusion_matrix(yg[mte], pred).ravel()
gm = dict(precision=round(float(precision_score(yg[mte], pred)), 4),
          recall=round(float(recall_score(yg[mte], pred)), 4),
          f1=round(float(f1_score(yg[mte], pred)), 4),
          specificity=round(float(tn / (tn + fp)), 4),
          balanced_accuracy=round(float(balanced_accuracy_score(yg[mte], pred)), 4),
          roc_auc=round(float(roc_auc_score(yg[mte], pt)), 4),
          threshold=THR,
          test_positive_prevalence=round(float(yg[mte].mean()), 4))
for k, v in gm.items():
    print("  %-26s %s" % (k, v))
print("  confusion  true0 [%d %d]  true1 [%d %d]" % (tn, fp, fn, tp))

print("\ngate detection rate by fault type (test engines):")
det_by_fault = {}
for n in NAMES:
    m = mte & (df.fault_type == n).to_numpy()
    if m.sum():
        r = float(pred[np.flatnonzero(mte)[np.isin(np.flatnonzero(mte),
              np.flatnonzero(m))].searchsorted(np.flatnonzero(m))].mean()) \
            if False else float((pt[(df.fault_type[mte] == n).to_numpy()] >= THR).mean())
        det_by_fault[n] = round(r, 4)
        print("  %-24s %.4f  (n=%s)" % (n, r, f"{m.sum():,}"))
fa = float((pt[(df.fault_type[mte] == "healthy").to_numpy()] >= THR).mean())
gm["healthy_false_alarm_rate"] = round(fa, 4)
print("  %-24s %.4f  (n=%s)" % ("healthy false alarm", fa,
      f"{(mte & ~yg.astype(bool)).sum():,}"))

det_by_sev = {}
if sev_col:
    print("\ngate detection rate by severity:")
    sub = df.loc[mte, sev_col].astype(str).to_numpy()
    for s in sorted(set(sub[(df.fault_type[mte] != "healthy").to_numpy()])):
        k = sub == s
        if k.sum():
            r = float((pt[k] >= THR).mean())
            det_by_sev[s] = round(r, 4)
            print("  %-10s %.4f  (n=%s)" % (s, r, f"{k.sum():,}"))

if tran_col:
    tr = df.loc[mte, tran_col].astype(bool).to_numpy()
    hh = (df.fault_type[mte] == "healthy").to_numpy()
    for lbl, k in (("transient", hh & tr), ("steady", hh & ~tr)):
        if k.sum():
            print("  healthy FA %-10s %.4f  (n=%s)"
                  % (lbl, float((pt[k] >= THR).mean()), f"{k.sum():,}"))

# -------------------------------------------------------------- multiclass
print("\n=== MULTICLASS (faulted rows only) ===")
ym = df.fault_type.map(CLASS_INDEX).to_numpy()
ftr, fte = mtr & (~h).to_numpy(), mte & (~h).to_numpy()
mc = HistGradientBoostingClassifier(**HP).fit(X[ftr], ym[ftr])
tick("multiclass fit")
mp = mc.predict(X[fte])
print(classification_report(ym[fte], mp, labels=list(range(5)),
                            target_names=NAMES, digits=4, zero_division=0))
rec = recall_score(ym[fte], mp, average=None, labels=list(range(5)),
                   zero_division=0)
mm = dict(accuracy=round(float(accuracy_score(ym[fte], mp)), 4),
          macro_f1=round(float(f1_score(ym[fte], mp, average="macro",
                                        zero_division=0)), 4),
          per_class_recall={NAMES[i]: round(float(r), 4)
                            for i, r in enumerate(rec)})
dead = [NAMES[i] for i in range(5) if (mp == i).sum() == 0]
print("dead classes:", dead if dead else "none - PASS")
if sev_col:
    print("multiclass accuracy by severity:")
    sub = df.loc[fte, sev_col].astype(str).to_numpy()
    for s in sorted(set(sub)):
        k = sub == s
        if k.sum():
            print("  %-10s %.4f  (n=%s)"
                  % (s, float((mp[k] == ym[fte][k]).mean()), f"{k.sum():,}"))

# -------------------------------------------------------------------- save
PROV = ("train_classifiers_mvem.py 2026-09-11 on mvem_v3.parquet (%s rows, "
        "%d virtual engines, envelope 0-22800 ft / 20-100%% throttle / "
        "-39.5..+49.5 C). Labels are per-row from the generator. Held-out "
        "engines: %s (%s rows)."
        % (f"{len(df):,}", len(eng), ",".join(map(str, sorted(test_e))),
           f"{mte.sum():,}"))

def save(path, est, metrics, extra):
    old = joblib.load(path) if path.exists() else None
    if old is not None:
        joblib.dump(old, path.with_suffix(".pkl.prevbak"))
        print("  old keys:", sorted(old.keys())
              if isinstance(old, dict) else type(old).__name__)
    b = dict(old) if isinstance(old, dict) else {}
    mk = next((k for k, v in b.items() if hasattr(v, "predict")), "model")
    b[mk] = est
    b["feature_names"] = FEATURES
    b["metrics"] = metrics
    b["provenance"] = PROV
    b["trained_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    b.update(extra)
    joblib.dump(b, path)
    print("  wrote", path, "(model key '%s')" % mk)

gp = OUT / "fault_classifier.pkl"
if gp.exists():
    prev = joblib.load(gp)
    if isinstance(prev, dict) and prev.get("feature_names"):
        if list(prev["feature_names"]) != FEATURES:
            raise SystemExit("FEATURE ORDER CHANGED - predictor.py would break.\n"
                             "  old: %s\n  new: %s"
                             % (list(prev["feature_names"]), FEATURES))
        print("\nfeature order matches existing artifact - predictor.py unchanged")

save(gp, gate, gm, dict(role="anomaly_gate", decision_threshold=THR,
     trusted=True, dead_classes=[],
     caveats=["Trained on MVEM-generated data; MVEM itself is validated only at "
              "5800 rpm WOT sea level.",
              "Steady-state baselines do not track throttle transients."]))
save(OUT / "fault_classifier_multiclass.pkl", mc, mm,
     dict(role="fault_classifier", label_map=CLASS_INDEX, class_names=NAMES,
          trusted=bool(not dead and mm["accuracy"] > 0.80), dead_classes=dead,
          caveats=["fuel_pressure_dev is injected via a global fuel trim proxy; "
                   "fuel_rail_bar is fixed at 3.0 bar in MVEM."]))

rep = dict(source=str(SRC), rows=int(len(df)), engines=int(len(eng)),
           test_engines=sorted(map(str, test_e)), features=FEATURES,
           gate=gm, gate_detection_by_fault=det_by_fault,
           gate_detection_by_severity=det_by_sev, multiclass=mm,
           dead_classes=dead, provenance=PROV)
(OUT / "retrain_metrics_mvem.json").write_text(json.dumps(rep, indent=2),
                                               encoding="utf-8")
print("\nwrote", OUT / "retrain_metrics_mvem.json")
tick("done")
