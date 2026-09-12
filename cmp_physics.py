import numpy as np
import shared.engine_mvem as mvem
from node2_twin_core.physics_deck import BaselineDeck
deck = BaselineDeck()
print("thr  alt     EGTmvem EGTtwin  diff    fuelm fuelt   coolm coolt")
for thr in (60., 70., 80., 90., 100.):
    for alt in (2000., 10000., 18000.):
        o = mvem.solve(throttle_pct=thr, altitude_ft=alt, oat_c=10.)
        X = np.array([[o.rpm, thr, alt, 10.]])
        e = deck.models["EGT_mean_C"].predict(X)[0]
        f = deck.models["fuelflow_kgh"].predict(X)[0]
        c = deck.models["coolant_temp_C"].predict(X)[0]
        print(f"{thr:4.0f} {alt:6.0f} {o.egt_mean_c:8.1f} {e:7.1f} {o.egt_mean_c-e:7.1f}"
              f" {o.fuel_flow_kgh:6.2f} {f:6.2f} {o.coolant_temp_out_c:6.1f} {c:6.1f}")
