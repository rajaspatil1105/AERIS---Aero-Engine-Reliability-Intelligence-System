import numpy as np, sys
sys.path.insert(0, ".")
from shared import engine_mvem as mvem
rng = np.random.default_rng(7)
N = 1500
thr = rng.uniform(20., 100., N)
alt = rng.uniform(0., 22800., N)
oat = np.clip(15. - 1.98*alt/1000. + rng.uniform(-20., 25., N), -39.5, 49.5)
print("pump   mean_cool  frac>98.7  frac>92  max_cool")
for ph in (0.95, 0.85, 0.75, 0.65, 0.55, 0.45, 0.35):
    c = []
    for i in range(N):
        fs = mvem.FaultState(); fs.coolant_pump_health = float(ph)
        try:
            c.append(mvem.solve(throttle_pct=float(thr[i]), altitude_ft=float(alt[i]),
                                oat_c=float(oat[i]), fault=fs).coolant_temp_out_c)
        except Exception:
            pass
    c = np.array(c)
    print(" %.2f    %7.2f    %6.1f%%   %6.1f%%  %7.1f"
          % (ph, c.mean(), 100*(c > 98.7).mean(), 100*(c > 92).mean(), c.max()))
