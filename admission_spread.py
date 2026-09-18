"""How much does p_anom move inside the admission band?

throttle_dynamics admits a frame as settled when rpm is within 1.5 and
EGT within 0.25 C of equilibrium. The gate resolves 0.604 rpm and 0.00403 C.
If the score barely moves across the admitted band, the mismatch is
theoretical against a 0.5 threshold. If it swings, admission is unsound.
"""
import numpy as np
from node2_twin_core.predictor import FaultPredictor
from node2_twin_core.residual_calc import _healthy_payload

pred = FaultPredictor()
base = _healthy_payload(pred.calc)
ref = pred.predict(dict(base))
print("equilibrium p_anom %.6f  healthy=%s  threshold %s"
      % (ref.anomaly_probability, ref.is_healthy, ref.gate_threshold))

for ch, tol in (("rpm", 1.5), ("EGT_mean_C", 0.25)):
    if ch not in base:
        print("%s not in payload -- skipped" % ch)
        continue
    scores, flipped = [], []
    for d in np.linspace(-tol, tol, 21):
        q = dict(base); q[ch] = base[ch] + d
        r = pred.predict(q)
        scores.append(r.anomaly_probability)
        if not r.is_healthy:
            flipped.append((d, r.anomaly_probability, r.fault_label))
    lo, hi = min(scores), max(scores)
    print("\n%s  swept +/-%.3f (gate resolves %s)" % (ch, tol, ch))
    print("  p_anom %.6f .. %.6f   spread %.6f" % (lo, hi, hi - lo))
    print("  distance to the 0.5 gate from equilibrium: %.6f"
          % (0.5 - ref.anomaly_probability))
    if flipped:
        print("  CROSSES THE GATE inside the admitted band:")
        for d, s, lbl in flipped[:5]:
            print("    offset %+.3f -> p_anom %.4f  label=%s" % (d, s, lbl))
    else:
        print("  never crosses the gate inside the admitted band")
