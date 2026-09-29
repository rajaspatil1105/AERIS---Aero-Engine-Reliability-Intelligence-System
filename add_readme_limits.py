import io, pathlib

note = """

## Known limitation: degradation shows as pressure, not temperature

The engine's thermostat is an absolute regulator. Oil holds 90.0 C and
coolant 88.0 C at every realistic operating point, and pump health does
not reduce that capacity -- an engine at 0.737 oil pump health runs the
same temperatures as a factory-fresh one. Measured, not assumed:

    engine wear        loiter oil    loiter coolant
    factory  0.991         90.0 C          88.0 C
    +600 h   0.889         90.0 C          88.0 C
    +1500 h  0.737         90.0 C          88.0 C

Three consequences, stated plainly:

1. Wear accumulates only from temperature exceedance, and temperature
   only exceeds the knees (oil 94 C, coolant 95 C) at full throttle,
   low altitude, in hot air. Every realistic surveillance tasking --
   high ISR at 62% and 12000 ft, low patrol at 75% and 3000 ft, even
   contested at 85% and 1500 ft -- comes home with zero wear in any
   season. That is the correct physical answer, not a bug.

2. There is no compounding decline. A worn engine does not run hotter,
   so it does not wear faster. Degradation is linear in exposure to the
   one damaging condition.

3. Degradation is visible as falling oil pressure and nothing else:
   3.20 bar factory to 2.24 bar at end of life, with temperatures,
   fuel flow and EGT essentially unmoved. This is why the retrained RUL
   model puts 0.938 of its feature importance on the oil pressure
   residual -- it is the only channel carrying the signal.

Fixing this properly means making pump health reduce heat rejection in
the MVEM thermal model, so a tired engine cannot hold 90 C when it is
working. That is a change to the most load-bearing part of the system
and everything downstream was calibrated against current behaviour, so
it is deliberately out of scope here and recorded rather than hidden.

## Seasonal weather

Missions fetch real weather from Open-Meteo for any past date and for
roughly the next 16 days. Past dates use the archive's surface
temperature plus the ISA lapse rate, because the archive serves no
pressure-level data; near-future dates use real pressure-level
temperature. Falls back to ISA if the network is down.

Same Rajasthan sortie, December vs June: 25.8 C vs 36.0 C at takeoff,
peak EGT 803 vs 813 C. The seasonal signal is real and visible in
temperatures; it does not produce a wear difference, for the reason
above.
"""

p = pathlib.Path("README.md")
if not p.is_file():
    print("no README.md in this directory -- creating one")
    p.write_text("# AERIS\n", encoding="utf-8")
with io.open(p, "a", encoding="utf-8") as f:
    f.write(note)
print("appended known-limitations section to README.md (%d bytes)"
      % p.stat().st_size)
