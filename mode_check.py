import sys, joblib, numpy as np, pandas as pd, pathlib
sys.path.insert(0, ".")
df = pd.read_parquet("C:/aeris_data/datasets/mvem_v3.parquet").sample(20000, random_state=0)
env = ["rpm", "throttle_pct", "altitude_ft", "ambient_temperature_C"]
for ch in ["oil_pressure_bar", "EGT_mean_C"]:
    m = joblib.load(pathlib.Path("models/baseline") / (ch + "_baseline.pkl"))
    d = df[ch].to_numpy(float) - m.predict(df[env].to_numpy(float))
    print("%-20s min %+.4f  max %+.4f  pct_negative %.1f%%"
          % (ch, d.min(), d.max(), 100.0 * (d < 0).mean()))
