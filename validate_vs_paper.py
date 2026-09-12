import numpy as np
import shared.engine_mvem as mvem
from node2_twin_core.physics_deck import BaselineDeck

RHO = 0.7503
# rpm: (L/h, Nm, kW, g/kWh) manufacturer column, AVL Boost paper Table 4
MFR = {5800: (33.8, 171.9, 104.4, 243.0), 5500: (32.1, 172.1, 99.1, 242.8),
       5000: (27.8, 167.8, 87.8, 237.3), 4500: (23.1, 158.6, 74.7, 231.7),
       3000: (10.0, 104.5, 32.8, 229.5)}

def thr_for(rpm): return (rpm - 1800.0) / 4000.0 * 100.0

deck = BaselineDeck()
print("MVEM vs manufacturer  (WOT, sea level, 30 C)")
print(f"{'rpm':>5} {'thr%':>5} | {'L/h mfr':>8} {'L/h sim':>8} {'d%':>7} | "
      f"{'kW mfr':>7} {'kW sim':>7} {'d%':>7} | {'bsfc m':>7} {'bsfc s':>7} {'d%':>7}")
for rpm in sorted(MFR, reverse=True):
    lph, nm, kw, bsfc = MFR[rpm]
    thr = thr_for(rpm)
    o = mvem.solve(throttle_pct=thr, altitude_ft=0.0, oat_c=30.0)
    s_lph = o.fuel_flow_kgh / RHO
    s_bsfc = o.bsfc_g_per_kwh
    print(f"{rpm:5d} {thr:5.1f} | {lph:8.1f} {s_lph:8.1f} {100*(s_lph/lph-1):6.1f}% | "
          f"{kw:7.1f} {o.brake_power_kw:7.1f} {100*(o.brake_power_kw/kw-1):6.1f}% | "
          f"{bsfc:7.1f} {s_bsfc:7.1f} {100*(s_bsfc/bsfc-1):6.1f}%")

print()
print("Rotex915 twin baseline vs manufacturer  (same points, fuel only)")
print(f"{'rpm':>5} {'thr%':>5} | {'L/h mfr':>8} {'L/h twin':>9} {'d%':>7}  envelope")
for rpm in sorted(MFR, reverse=True):
    lph = MFR[rpm][0]
    thr = thr_for(rpm)
    t_kgh = deck.models["fuelflow_kgh"].predict(np.array([[rpm, thr, 0.0, 30.0]]))[0]
    t_lph = t_kgh / RHO
    ok = "in" if (thr >= 56.5 and 3000 <= rpm <= 5800) else "OUT (extrapolating)"
    print(f"{rpm:5d} {thr:5.1f} | {lph:8.1f} {t_lph:9.1f} {100*(t_lph/lph-1):6.1f}%  {ok}")
