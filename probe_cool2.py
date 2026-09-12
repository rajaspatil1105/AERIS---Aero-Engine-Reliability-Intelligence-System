import numpy as np, sys
sys.path.insert(0, ".")
from shared import engine_mvem as mvem
import generate_mvem_dataset as g
rng = np.random.default_rng(11)
base = mvem.FaultState()
print("sev     mean_cool  frac>92  frac>98.7  frac>125  max")
for sev, sf in g.SEV.items():
    c = []
    for _ in range(600):
        frac = sf * float(rng.uniform(0.7, 1.0))
        alt = float(rng.uniform(g.ALT_MIN, 9000.0))
        oat = float(min(g.OAT_MAX, max(g.OAT_MIN,
                    g.isa_oat(alt) + float(rng.uniform(12.0, g.ISA_DEV_MAX)))))
        thr = float(rng.uniform(72.0, g.THR_MAX))
        fs = g.build_fault("cooling_degradation", frac, rng, base)
        try:
            c.append(mvem.solve(throttle_pct=thr, altitude_ft=alt,
                                oat_c=oat, fault=fs).coolant_temp_out_c)
        except Exception:
            pass
    c = np.array(c)
    print(" %-8s %7.2f   %5.1f%%    %5.1f%%    %5.1f%%  %6.1f"
          % (sev, c.mean(), 100*(c > 92).mean(), 100*(c > 98.7).mean(),
             100*(c > 125).mean(), c.max()))
