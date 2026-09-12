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
test=set(eng[np.random.default_rng(42).permutation(len(eng))[:10]])
t=df[df.engine_id.isin(test)]
g=joblib.load("models/classifier/fault_classifier.pkl")
p=g.predict_proba(t[list(FEATURE_ORDER)].to_numpy(float))[:,1]
THR=0.42
h=(t.fault_type=="healthy").to_numpy()
ld=((t.throttle_pct.to_numpy(float)>=72.)&(t.altitude_ft.to_numpy(float)<=9000.))
print("healthy false alarm  loaded %.4f (n=%d)   rest %.4f (n=%d)"
      %((p[h&ld]>=THR).mean(),(h&ld).sum(),(p[h&~ld]>=THR).mean(),(h&~ld).sum()))
c=(t.fault_type=="cooling_degradation").to_numpy()
print("\ncooling detection vs whether the coolant residual is actually visible:")
for sev in ("mild","moderate","severe"):
    k=c&(t.fault_severity.to_numpy()==sev)
    vis=t.coolant_temp_C.to_numpy(float)>92.
    for lbl,kk in (("visible",k&vis),("hidden",k&~vis)):
        if kk.sum(): print("  %-9s %-8s det %.4f  (n=%d)"%(sev,lbl,(p[kk]>=THR).mean(),kk.sum()))
