import pathlib
p = pathlib.Path("shared/mission_engine.py")
s = p.read_text(encoding="utf-8")

anchor = "_CANDIDATES: Dict[str, Tuple[str, ...]] = {"
if anchor not in s: raise SystemExit("NOT FOUND: _CANDIDATES anchor")

block = '''# --- named fault builders (tab 3 dropdown) ------------------------------
# Depths mirror generate_mvem_dataset.build_fault() so a forced fault looks
# like the rows the classifier was trained on. Restated rather than imported
# because the generator is a top-level script; if you change one, change both.
SEV_FRAC = {"mild": 0.35, "moderate": 0.65, "severe": 1.0}


def _forced(kind: str, severity: str = "moderate") -> mvem.FaultState:
    if severity not in SEV_FRAC:
        raise MissionEngineError("severity must be one of %s"
                                 % sorted(SEV_FRAC))
    frac = SEV_FRAC[severity]
    fs = mvem.FaultState()
    if kind == "cooling_degradation":
        # Detected only ~0.19 of the time and that is real: the thermostat
        # holds 88 C until heat rejection exceeds what the weak pump carries.
        # Force this at high throttle and warm ambient or it will not show.
        fs.coolant_pump_health = max(0.05, 1.0 - 0.45 * frac)
    elif kind == "lubrication_degradation":
        fs.oil_pump_health = max(0.05, 1.0 - 0.30 * frac)
        fs.bearing_wear = min(1.0, 0.25 * frac)
    elif kind == "misfire":
        fs.cylinder_fuel_trim = [max(0.05, 1.0 - 0.38 * frac), 1.0, 1.0, 1.0]
    elif kind == "fuel_pressure_dev":
        t = 1.0 - 0.16 * frac
        fs.cylinder_fuel_trim = [t, t, t, t]
    else:
        raise MissionEngineError("no builder for %r" % kind)
    fs.label = kind
    fs.validate()
    return fs


# sensor_drift is absent on purpose: it is a measurement-layer offset added
# after mvem.solve(), not a FaultState the engine can be solved with. Forcing
# it needs a post-measurement hook in run_mission, which does not exist yet.
FORCED_FAULTS = {k: (lambda sev, _k=k: _forced(_k, sev))
                 for k in ("cooling_degradation", "lubrication_degradation",
                           "misfire", "fuel_pressure_dev")}


'''
s = s.replace(anchor, block + anchor, 1)
compile(s.lstrip("\ufeff"), str(p), "exec")
p.write_text(s, encoding="utf-8")
print("FORCED_FAULTS added, syntax OK")
