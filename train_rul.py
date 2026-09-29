import csv, pathlib, statistics as st
import numpy as np
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import r2_score, mean_absolute_error

rows = list(csv.DictReader(pathlib.Path("data/rul_histories.csv").open(encoding="utf-8")))
FEATS = ["coolant", "oil", "bearing"]

def xy(rs):
    X = np.array([[float(r[f]) for f in FEATS] for r in rs])
    y = np.array([float(r["rul_h"]) for r in rs])
    return X, y

serials = sorted({r["serial"] for r in rows})
test_s = set(serials[::4])                 # hold out 4 whole engines
tr = [r for r in rows if r["serial"] not in test_s]
te = [r for r in rows if r["serial"] in test_s]
print("train %d rows / %d engines   test %d rows / %d engines"
      % (len(tr), len(serials) - len(test_s), len(te), len(test_s)))

for tag, keep in (("all rows", lambda r: True),
                  ("degradation started", lambda r: float(r["oil"]) < 0.9999)):
    a, b = [r for r in tr if keep(r)], [r for r in te if keep(r)]
    Xtr, ytr = xy(a); Xte, yte = xy(b)
    m = GradientBoostingRegressor(random_state=0).fit(Xtr, ytr)
    p = m.predict(Xte)
    print("\n%-22s test n=%d" % (tag, len(b)))
    print("   R2  %+.3f     MAE %6.1f h     label spread %.0f h"
          % (r2_score(yte, p), mean_absolute_error(yte, p), st.pstdev(yte)))
