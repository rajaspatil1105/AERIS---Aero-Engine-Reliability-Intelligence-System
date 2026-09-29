import sys, pathlib, dataclasses
sys.path.insert(0, str(pathlib.Path(".").resolve()))
from shared import engine_mvem as mvem
from shared import mission_engine as me

CASES = [("normal cruise",    75.0, 6000.0, 15.0),
         ("warm cruise",      80.0, 4000.0, 25.0),
         ("hot low WOT",     100.0, 1000.0, 38.0),
         ("hottest legal",   100.0,  500.0, 39.8)]

print("knees: coolant %.1f  oil %.1f  power %.1f"
      % (me.COOLANT_KNEE_C, me.OIL_KNEE_C, me.POWER_KNEE_KW))

o0 = mvem.solve(throttle_pct=75.0, altitude_ft=6000.0, oat_c=15.0,
                fault=mvem.FaultState())
try:
    print("fields:", [f.name for f in dataclasses.fields(o0)])
except TypeError:
    print("fields:", [a for a in dir(o0) if not a.startswith("_")])

for name, thr, alt, oat in CASES:
    o = mvem.solve(throttle_pct=thr, altitude_ft=alt, oat_c=oat,
                   fault=mvem.FaultState())
    print("%-14s coolant %6.2f  oil %6.2f  power %6.2f"
          % (name, me._pluck(o, "coolant_temp_C"),
             me._pluck(o, "oil_temperature_C"),
             me._pluck(o, "power_kW")))
