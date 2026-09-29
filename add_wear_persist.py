import pathlib, shutil
P = pathlib.Path("shared/mission_engine.py")
src = P.read_text(encoding="utf-8")
shutil.copy(P, pathlib.Path("shared/mission_engine.py.bak_persist"))

OLD = "            for e in FLEET]\n"
NEW = '''            for e in _fleet_now()]


# ==================================================================== #
# Cumulative wear across missions
# ==================================================================== #
#
# FLEET above is the FACTORY baseline and never changes. Accumulated damage
# lives in a JSON overlay so an engine that flew a punishing mission is still
# worn after a restart. Engines age across missions, not within one flight.

_WEAR_PATH = pathlib.Path(
    os.environ.get("AERIS_WEAR",
                   pathlib.Path.home() / ".aeris" / "fleet_wear.json"))


def _wear_load() -> Dict[str, Dict[str, float]]:
    try:
        return json.loads(_WEAR_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _wear_save(d: Dict[str, Dict[str, float]]) -> None:
    _WEAR_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _WEAR_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(_WEAR_PATH)


def _fleet_now() -> List[FleetEngine]:
    """Factory fleet with accumulated wear applied."""
    acc = _wear_load()
    out = []
    for e in FLEET:
        a = acc.get(e.serial)
        if not a:
            out.append(e)
            continue
        out.append(dataclasses.replace(
            e,
            hours=e.hours + a.get("hours", 0.0),
            coolant_pump_health=max(0.05, e.coolant_pump_health - a.get("coolant", 0.0)),
            oil_pump_health=max(0.05, e.oil_pump_health - a.get("oil", 0.0)),
            bearing_wear=min(1.0, e.bearing_wear + a.get("bearing", 0.0)),
            note=e.note + " +%.0f h flown" % a.get("hours", 0.0)))
    return out


def engine_now(serial: str) -> FleetEngine:
    for e in _fleet_now():
        if e.serial == serial:
            return e
    raise MissionEngineError("unknown engine %s" % serial)


def record_wear(serial: str, st: "StressState", flown_h: float) -> Dict[str, float]:
    """Fold one mission's damage into the engine's permanent record."""
    acc = _wear_load()
    a = acc.setdefault(serial, {"hours": 0.0, "coolant": 0.0,
                                "oil": 0.0, "bearing": 0.0})
    a["hours"] += flown_h
    a["coolant"] += 0.30 * min(1.0, st.thermal)
    a["oil"] += 0.25 * min(1.0, st.oil)
    a["bearing"] += 0.35 * min(1.0, st.power)
    _wear_save(acc)
    return a


def reset_wear(serial: Optional[str] = None) -> None:
    """Back to factory. Whole fleet if serial is None."""
    if serial is None:
        _wear_save({})
        return
    acc = _wear_load()
    acc.pop(serial, None)
    _wear_save(acc)
'''
if OLD not in src:
    raise SystemExit("fleet_listing anchor not found")
src = src.replace(OLD, NEW, 1)

for mod in ("import json", "import os", "import pathlib", "import dataclasses"):
    if ("\n" + mod + "\n") not in src:
        src = src.replace("from dataclasses import", mod + "\nfrom dataclasses import", 1)
P.write_text(src, encoding="utf-8")
print("patched -- cumulative wear overlay")
