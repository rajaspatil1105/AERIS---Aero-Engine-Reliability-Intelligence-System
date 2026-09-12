import joblib, pathlib, numpy as np, pandas as pd, sys
sys.path.insert(0, ".")
from node2_twin_core.residual_calc import FEATURE_ORDER
ENV=["rpm","throttle_pct","altitude_ft","ambient_temperature_C"]
RAW=["EGT_mean_C","coolant_temp_C","oil_pressure_bar","oil_temperature_C","fuelflow_kgh"]
df=pd.read_parquet("C:/aeris_data/datasets/mvem_v3.parquet")
for ch in RAW:
    m=joblib.load(pathlib.Path("models/baseline")/(ch+"_baseline.pkl"))
    df["delta_"+ch]=df[ch]-m.predict(df[ENV].to_numpy(float))
eng=np.sort(df.engine_id.unique())
t=df[df.engine_id.isin(set(eng[np.random.default_rng(42).permutation(len(eng))[:10]]))]
g=joblib.load("models/classifier/fault_classifier.pkl")
p=g.predict_proba(t[list(FEATURE_ORDER)].to_numpy(float))[:,1]
h=(t.fault_type=="healthy").to_numpy(); c=(t.fault_type=="cooling_degradation").to_numpy()
print(" thr    FA%   recall  cooling  other4")
for thr in (0.41,0.50,0.60,0.70,0.80,0.90):
    d=p>=thr
    print(" %.2f  %5.2f%%  %.4f   %.4f   %.4f"%(thr,100*d[h].mean(),d[~h].mean(),
          d[c].mean(),d[~h&~c].mean()))
