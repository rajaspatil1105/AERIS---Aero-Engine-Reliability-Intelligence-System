"""Does MVEM output score on the trained twin, or get declined?

The bridge self-check proved MVEM frames reach the adapter's feature stage.
It never asked the twin. The twin was trained on master_dataset.csv, NOT on
MVEM output, so its envelope may not contain our operating points. If it
declines everything, the demo shows "UNAVAILABLE" for the whole video.

Uses the same call path as throttle_dynamics._score_state.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import engine_mvem as mvem
from shared.schema import EngineState, TelemetryPayload
from shared.stress_sim import build_core
from node1_ingestion.adapter import to_twin_payload, twin_frame_to_dict

# (label, throttle_pct, altitude_ft, oat_c) -- a plausible sortie
POINTS = [
    ("ground idle",     20.0,     0.0, 25.0),
    ("takeoff",        100.0,   500.0, 25.0),
    ("climb 3000",      90.0,  3000.0, 19.0),
    ("climb 6000",      90.0,  6000.0, 13.0),
    ("cruise ref",      80.0,  6000.0, 10.0),   # the deck's reference op
    ("cruise 8000",     75.0,  8000.0,  9.0),
    ("cruise 10000",    75.0, 10000.0,  5.0),
    ("loiter",          65.0,  8000.0,  9.0),
    ("descent",         40.0,  4000.0, 16.0),
    ("approach",        30.0,  1000.0, 22.0),
]


def score(core, thr, alt, oat):
    o = mvem.solve(throttle_pct=thr, altitude_ft=alt, oat_c=oat)
    d = dict(o.to_schema_dict())
    d["engine_state"] = EngineState.RUNNING
    keep = {k: v for k, v in d.items() if k in TelemetryPayload.model_fields}
    p = TelemetryPayload(**keep)
    res = to_twin_payload(p, provided=set(keep) | {"engine_state"},
                          strict=False)
    if not res.ok:
        return None, "ADAPTER: " + "; ".join(res.refusals)[:90], o
    out = twin_frame_to_dict(core.process(res.features))
    pa = out.get("anomaly_probability")
    if pa is None:
        why = out.get("reason") or out.get("status") or "declined"
        return None, f"TWIN: {str(why)[:90]}", o
    return float(pa), out.get("status", "?"), o


def main():
    core = build_core()
    print(f"\n{'point':<15}{'thr':>5}{'alt':>7}{'kW':>7}{'rpm':>7}"
          f"{'p_anom':>9}  status / reason")
    ok = bad = 0
    for label, thr, alt, oat in POINTS:
        pa, note, o = score(core, thr, alt, oat)
        pas = f"{pa:.4f}" if pa is not None else "  --  "
        print(f"{label:<15}{thr:5.0f}{alt:7.0f}{o.brake_power_kw:7.1f}"
              f"{o.rpm:7.0f}{pas:>9}  {note}")
        if pa is None:
            bad += 1
        else:
            ok += 1
    print(f"\n{ok} scored, {bad} not scored, of {len(POINTS)} points")
    if bad:
        print("Points that do not score cannot appear in the demo as a "
              "健康/fault verdict -- they show UNAVAILABLE with a reason.")
    if ok:
        print("Scored points are your usable demo envelope. Build the mission "
              "profile from THOSE throttle/altitude combinations.")
    print("\nper-channel disagreement, MVEM vs deck at the reference op")
    from shared.stress_sim import deck, reference_op, _extract
    op = dict(reference_op())
    pred = _extract(deck().predict(dict(op)))
    o = mvem.solve(throttle_pct=op["throttle_pct"],
                   altitude_ft=op["altitude_ft"],
                   oat_c=op["ambient_temperature_C"])
    d = dict(o.to_schema_dict())
    from node1_ingestion.adapter import (FUEL_DENSITY_KG_PER_L, KPA_PER_BAR,
                                         COOLANT_SOURCE_FIELD)
    got = {
        "rpm": d["rpm"],
        "EGT_mean_C": sum(d[f"egt_{i}_c"] for i in range(1, 5)) / 4.0,
        "coolant_temp_C": d[COOLANT_SOURCE_FIELD],
        "oil_temperature_C": d["oil_temp_c"],
        "fuelflow_kgh": d["fuel_flow_lph"] * FUEL_DENSITY_KG_PER_L,
        "oil_pressure_bar": d["oil_pressure_kpa"] / KPA_PER_BAR,
    }
    print(f"  {'channel':<20}{'deck':>12}{'MVEM':>12}{'diff':>12}{'x res':>9}")
    for k in got:
        if k not in pred:
            continue
        p, g = float(pred[k]), float(got[k])
        res = {"rpm": 6.82251, "EGT_mean_C": 1.37316,
               "coolant_temp_C": 0.01455, "oil_temperature_C": 0.00174,
               "fuelflow_kgh": 0.00358, "oil_pressure_bar": 0.00019}[k]
        print(f"  {k:<20}{p:12.4f}{g:12.4f}{g-p:12.4f}{abs(g-p)/res:9.0f}")


if __name__ == "__main__":
    main()

