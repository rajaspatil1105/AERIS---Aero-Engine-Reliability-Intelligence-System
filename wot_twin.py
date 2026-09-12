import numpy as np
from node2_twin_core.physics_deck import BaselineDeck
RHO = 0.7503
MFR = {5800: 33.8, 5500: 32.1, 5000: 27.8, 4500: 23.1, 3000: 10.0}
deck = BaselineDeck()
print("Rotex915 twin at WOT (throttle=100) vs manufacturer")
print(f"{'rpm':>5} | {'L/h mfr':>8} {'L/h twin':>9} {'diff':>8}")
for rpm in sorted(MFR, reverse=True):
    kgh = deck.models["fuelflow_kgh"].predict(np.array([[rpm, 100.0, 0.0, 30.0]]))[0]
    lph = kgh / RHO
    print(f"{rpm:5d} | {MFR[rpm]:8.1f} {lph:9.1f} {100*(lph/MFR[rpm]-1):7.1f}%")
