import sys, joblib, numpy as np
sys.path.insert(0, ".")
from node2_twin_core.residual_calc import FEATURE_ORDER, DELTA_FEATURES, DELTA_TARGETS
import shared.engine_mvem as mvem
from shared import mission_engine as me
import generate_mvem_dataset as gen          # ctrl-C if this starts generating

rng = np.random.default_rng(0)
base   = mvem.FaultState()
forced = me.FORCED_FAULTS["lubrication_degradation"]("severe")
trained = gen.build_fault("lubrication_degradation", 1.0, rng, base)

for tag, fs in (("FORCED ", forced), ("TRAINED", trained)):
    print(tag, {k: v for k, v in vars(fs).items()})

OP = dict(throttle_pct=80.0, altitude_ft=6000.0, oat_c=10.0)
ENVK = ["rpm", "throttle_pct", "altitude_ft", "ambient_temperature_C"]
mdl = {c: joblib.load("models/baseline/%s_baseline.pkl" % c) for c in DELTA_TARGETS}
g = joblib.load("models/classifier/fault_classifier.pkl")
idx = [FEATURE_ORDER.index(n) for n in DELTA_FEATURES]

def score(tag, fs):
    feat = dict(gen.measure(mvem.solve(fault=fs, **OP), rng))
    feat.update(altitude_ft=6000.0, ambient_temperature_C=10.0, throttle_pct=80.0)
    env = np.array([[float(feat[k]) for k in ENVK]])
    for c in DELTA_TARGETS:
        feat["delta_" + c] = float(feat[c]) - float(mdl[c].predict(env)[0])
    v = np.array([[float(feat[n]) for n in FEATURE_ORDER]])
    w = v.copy(); w[0, idx] *= -1.0
    print("\n%s  p_anom %.4f   (deltas negated %.4f)"
          % (tag, g.predict_proba(v)[0, 1], g.predict_proba(w)[0, 1]))
    for n in DELTA_FEATURES:
        print("    %-26s %+.4f" % (n, feat[n]))

score("healthy", base); score("FORCED ", forced); score("TRAINED", trained)
