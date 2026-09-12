import joblib, pathlib
for n in ("fault_classifier.pkl","fault_classifier_multiclass.pkl"):
    p = pathlib.Path("models/classifier")/n
    b = joblib.load(p)
    if isinstance(b, dict):
        joblib.dump(b["model"], p)
        print("repacked bare:", n, type(b["model"]).__name__)
    else:
        print("already bare:", n)
