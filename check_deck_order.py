"""Golden-value check: the baseline deck must take ops columns in the order
rpm, throttle_pct, altitude_ft, ambient_temperature_C. Nothing else in the
codebase validates this -- the artifacts carry no feature_names_in_ -- so a
silent re-ordering produces plausible-looking but meaningless residuals."""
import joblib, numpy as np

DECK_ORDER = ["rpm", "throttle_pct", "altitude_ft", "ambient_temperature_C"]
CRUISE = {"rpm": 5000.0, "throttle_pct": 80.0,
          "altitude_ft": 6000.0, "ambient_temperature_C": 10.0}
GOLDEN = {"oil_pressure_bar": 3.1517436686614286,
          "EGT_mean_C": 740.5491707976279}

def check(tol=1e-6):
    x = np.array([[CRUISE[k] for k in DECK_ORDER]])
    bad = []
    for ch, want in GOLDEN.items():
        got = joblib.load("models/baseline/%s_baseline.pkl" % ch).predict(x)[0]
        if abs(got - want) > tol:
            bad.append("%s: expected %.10f got %.10f" % (ch, want, got))
    if bad:
        raise SystemExit("DECK ORDER CHECK FAILED\n  " + "\n  ".join(bad))
    print("deck order OK (%s)" % ", ".join(DECK_ORDER))

if __name__ == "__main__":
    check()
