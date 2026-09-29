import pathlib, shutil

OLD = '''def _forced(kind: str, severity: str = "moderate") -> mvem.FaultState:
    if severity not in SEV_FRAC:
        raise MissionEngineError("severity must be one of %s"
                                 % sorted(SEV_FRAC))
    frac = SEV_FRAC[severity]
    fs = mvem.FaultState()
    if kind == "cooling_degradation":'''

NEW = '''def _forced(kind: str, severity: str = "moderate",
            base: Optional[FleetEngine] = None) -> mvem.FaultState:
    """Build a forced fault, COMPOSED with the engine's existing wear.

    Previously this started from a bare FaultState(), so severe lubrication
    always produced oil_pump_health 0.70 whatever engine was selected -- a
    factory-fresh engine and one at 2010 h both reported 2.2400 bar and the
    dropdown had no effect once a fault was active. apply_degradation() has
    always composed with `base`; this path now matches it.

    Deficits are applied MULTIPLICATIVELY, so a fault on a worn pump lands
    worse than the same fault on a fresh one, and the ordering across the
    fleet is preserved. NOTE this means a forced fault on a worn engine is
    deeper than the depths in generate_mvem_dataset.build_fault() that the
    classifier was trained on; the type label may be less reliable there
    even though detection is not.
    """
    if severity not in SEV_FRAC:
        raise MissionEngineError("severity must be one of %s"
                                 % sorted(SEV_FRAC))
    frac = SEV_FRAC[severity]
    fs = base.fault_state() if base is not None else mvem.FaultState()
    if kind == "cooling_degradation":'''

EDITS = [
 (OLD, NEW),
 ('''        fs.coolant_pump_health = max(0.05, 1.0 - 0.45 * frac)''',
  '''        fs.coolant_pump_health = max(
            0.05, fs.coolant_pump_health * (1.0 - 0.45 * frac))'''),
 ('''        fs.oil_pump_health = max(0.05, 1.0 - 0.30 * frac)
        fs.bearing_wear = min(1.0, 0.25 * frac)''',
  '''        fs.oil_pump_health = max(
            0.05, fs.oil_pump_health * (1.0 - 0.30 * frac))
        fs.bearing_wear = min(1.0, fs.bearing_wear + 0.25 * frac)'''),
 ('''FORCED_FAULTS = {k: (lambda sev, _k=k: _forced(_k, sev))''',
  '''FORCED_FAULTS = {k: (lambda sev, base=None, _k=k: _forced(_k, sev, base))'''),
]

p = pathlib.Path("shared/mission_engine.py")
s = p.read_text(encoding="utf-8-sig")
if "COMPOSED with the engine" in s:
    print("already patched -- no change")
else:
    missing = [i for i, (o, _) in enumerate(EDITS) if o not in s]
    if missing:
        print("ANCHOR(S) NOT FOUND at %s -- nothing written" % missing)
    else:
        shutil.copy(p, p.with_suffix(".py.bak"))
        for o, n in EDITS:
            s = s.replace(o, n, 1)
        p.write_text(s, encoding="utf-8")
        print("patched -- forced faults now compose with fleet wear")
