import sys, pathlib, csv
sys.path.insert(0, str(pathlib.Path(".").resolve()))
import numpy as np
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import r2_score, mean_absolute_error
from node2_twin_core.rul_engine import FEATURE_ORDER

rows = list(csv.DictReader(open("data/rul_residuals.csv", encoding="utf-8")))
print("rows %d  cols %d" % (len(rows), len(rows[0])))

serials = sorted({r["serial"] for r in rows})
test_ser = set(serials[::4])
print("test engines: %s" % ", ".join(sorted(test_ser)))

def XY(sel):
    X = np.array([[float(r[f]) for f in FEATURE_ORDER] for r in sel])
    y = np.array([float(r["rul_h"]) for r in sel])
    return X, y

def fit_report(tag, tr, te):
    if len(te) < 20:
        print("%-26s too few rows (%d)" % (tag, len(te)))
        return
    Xtr, ytr = XY(tr); Xte, yte = XY(te)
    m = GradientBoostingRegressor(random_state=0).fit(Xtr, ytr)
    p = m.predict(Xte)
    print("%-26s train %5d  test %5d   R2 %+0.3f   MAE %6.1f h   spread %5.0f h"
          % (tag, len(tr), len(te), r2_score(yte, p),
             mean_absolute_error(yte, p), yte.max() - yte.min()))
    return m

tr = [r for r in rows if r["serial"] not in test_ser]
te = [r for r in rows if r["serial"] in test_ser]
m = fit_report("all operating points", tr, te)

for op in sorted({r["thr"] for r in rows}, key=float):
    fit_report("thr %s only" % op,
               [r for r in tr if r["thr"] == op],
               [r for r in te if r["thr"] == op])

if m is not None:
    imp = sorted(zip(FEATURE_ORDER, m.feature_importances_),
                 key=lambda t: -t[1])[:6]
    print("\ntop features: %s" % ", ".join("%s %.3f" % t for t in imp))
