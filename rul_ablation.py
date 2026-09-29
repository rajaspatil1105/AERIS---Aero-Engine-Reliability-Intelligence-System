import csv, pathlib
import numpy as np
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import r2_score, mean_absolute_error

rows = list(csv.DictReader(pathlib.Path("data/rul_histories.csv").open(encoding="utf-8")))
serials = sorted({r["serial"] for r in rows})
test_s = set(serials[::4])
tr = [r for r in rows if r["serial"] not in test_s]
te = [r for r in rows if r["serial"] in test_s]

def run(feats, tag):
    X = lambda rs: np.array([[float(r[f]) for f in feats] for r in rs])
    y = lambda rs: np.array([float(r["rul_h"]) for r in rs])
    m = GradientBoostingRegressor(random_state=0).fit(X(tr), y(tr))
    p = m.predict(X(te))
    print("%-28s R2 %+.3f  MAE %6.1f h" % (tag, r2_score(y(te), p),
                                           mean_absolute_error(y(te), p)))
    return m

run(["oil"], "oil only")
run(["coolant", "bearing"], "coolant+bearing (no oil)")
m = run(["coolant", "oil", "bearing"], "all three")
print("\nfeature importances:", dict(zip(["coolant","oil","bearing"],
                                         [round(v,3) for v in m.feature_importances_])))
