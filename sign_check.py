import sys, joblib, numpy as np, requests, pandas as pd, pathlib
sys.path.insert(0, ".")
from node2_twin_core.residual_calc import FEATURE_ORDER, DELTA_FEATURES, DELTA_TARGETS

f = requests.get("http://localhost:8000/frames/15",
                 params={"session_id": 18}, timeout=10).json()
feat = f["frame"]["features"]
g = joblib.load("models/classifier/fault_classifier.pkl")

v = np.array([[float(feat[n]) for n in FEATURE_ORDER]])
idx = [FEATURE_ORDER.index(n) for n in DELTA_FEATURES]
w = v.copy(); w[0, idx] *= -1.0
print("served as-is      p_anom %.4f" % g.predict_proba(v)[0, 1])
print("deltas negated    p_anom %.4f" % g.predict_proba(w)[0, 1])
print("served delta signs:")
for n in DELTA_FEATURES:
    print("   %-26s %+.4f" % (n, feat[n]))

df = pd.read_parquet("C:/aeris_data/datasets/mvem_v3.parquet")
lub = df[(df.fault_type == "lubrication_degradation") &
         (df.fault_severity == "severe")].sample(2000, random_state=0)
env = ["rpm", "throttle_pct", "altitude_ft", "ambient_temperature_C"]
print("training-side SIGNED mean residual, lubrication severe:")
for ch in DELTA_TARGETS:
    m = joblib.load(pathlib.Path("models/baseline") / (ch + "_baseline.pkl"))
    exp = m.predict(lub[env].to_numpy())
    print("   %-22s measured-expected %+.4f" % (ch, float((lub[ch].to_numpy() - exp).mean())))
