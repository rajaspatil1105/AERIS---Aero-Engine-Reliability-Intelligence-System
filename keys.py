import joblib, pathlib
for p in sorted(pathlib.Path("models/baseline").glob("*_baseline.pkl")):
    b = joblib.load(p)
    print(p.name, "->", type(b).__name__,
          sorted(b.keys()) if isinstance(b, dict) else "(bare estimator)")
