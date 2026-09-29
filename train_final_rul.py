import sys, pathlib, csv, shutil, time
sys.path.insert(0, str(pathlib.Path(".").resolve()))
import numpy as np, joblib
from sklearn.ensemble import GradientBoostingRegressor
from node2_twin_core.rul_engine import FEATURE_ORDER

rows = list(csv.DictReader(open("data/rul_residuals.csv", encoding="utf-8")))
X = np.array([[float(r[f]) for f in FEATURE_ORDER] for r in rows])
y = np.array([float(r["rul_h"]) for r in rows])
print("training on %d rows x %d features" % X.shape)

m = GradientBoostingRegressor(random_state=0).fit(X, y)
print("n_features_in_ = %d (contract %d)" % (m.n_features_in_, len(FEATURE_ORDER)))

art = pathlib.Path("models/rul/rul_regressor.pkl")
if art.is_file():
    bak = art.with_suffix(".pkl.bak_%s" % time.strftime("%Y%m%d"))
    shutil.copy2(art, bak)
    print("backed up old artifact -> %s" % bak)
else:
    art.parent.mkdir(parents=True, exist_ok=True)
    print("no existing artifact at %s -- check the path" % art)

joblib.dump(m, art)
print("wrote %s (%.1f KB)" % (art, art.stat().st_size / 1024.0))

from node2_twin_core.rul_engine import RulEngine
e = RulEngine()
for tag, i in (("newest row", int(np.argmax(y))), ("oldest row", int(np.argmin(y)))):
    est = e.update_vector(X[i])
    print("%-11s true %7.1f h   model %7.1f h" % (tag, y[i], est.raw))
