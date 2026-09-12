import inspect, sys
sys.path.insert(0, ".")
from shared import mission_engine as me
for n in ("run_mission", "fleet_listing", "accumulate", "apply_degradation"):
    f = getattr(me, n, None)
    print(n, inspect.signature(f) if f else "MISSING")
print("\nSetpoint:", inspect.signature(me.Setpoint) if hasattr(me,"Setpoint") else "not here")
print("StressState:", inspect.signature(me.StressState))
print("DEGRADE:", me.DEGRADE)
print("\nfleet (first 3 of %d):" % len(me.fleet_listing()))
for e in me.fleet_listing()[:3]: print("  ", e)
src = inspect.getsource(me.run_mission)
print("\nrun_mission yields/returns:")
for l in src.splitlines():
    if "yield" in l or "return" in l or "def " in l: print("  ", l.strip())
