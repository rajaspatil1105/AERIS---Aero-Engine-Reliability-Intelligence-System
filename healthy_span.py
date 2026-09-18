import pickle, joblib, numpy as np, pandas as pd
RAW = ["EGT_mean_C","coolant_temp_C","oil_pressure_bar","oil_temperature_C","fuelflow_kgh"]
ENV = ["rpm", "throttle_pct", "altitude_ft", "ambient_temperature_C"]

def load(c):
    p = "models/baseline/%s_baseline.pkl" % c
    try:
        return joblib.load(p)
    except Exception:
        with open(p, "rb") as f:
            return pickle.load(f)

models = {c: load(c) for c in RAW}
m = models["oil_pressure_bar"]
print("artifact type", type(m).__name__, "| has predict:", hasattr(m, "predict"))

df = pd.read_parquet("C:/aeris_data/datasets/mvem_v3.parquet")
h = df[df.fault_type == "healthy"]
print("healthy rows", len(h))
for c in ("oil_pressure_bar", "EGT_mean_C"):
    r = h[c].to_numpy(float) - models[c].predict(h[ENV].to_numpy(float))
    q = np.percentile(r, [0, 0.1, 1, 50, 99, 100])
    print("%-18s min %+8.4f  p0.1 %+8.4f  p1 %+8.4f  med %+8.4f  p99 %+8.4f  max %+8.4f"
          % (c, *q))
lub = df[df.fault_type == "lubrication_degradation"]
rl = lub["oil_pressure_bar"].to_numpy(float) - models["oil_pressure_bar"].predict(lub[ENV].to_numpy(float))
print("lubrication rows %d  oilP residual med %+.4f  p1 %+.4f  p99 %+.4f"
      % (len(lub), np.median(rl), np.percentile(rl,1), np.percentile(rl,99)))
for col in ("oil_pump_health","bearing_wear","coolant_pump_health","engine_hours"):
    if col in h.columns:
        print("healthy %-20s min %.4f  max %.4f" % (col, h[col].min(), h[col].max()))
