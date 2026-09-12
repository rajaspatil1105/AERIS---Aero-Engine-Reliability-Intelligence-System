"""
AERIS -- MVEM training set generator.

WHY
---
The Rotex915 CSV and shared/engine_mvem.py are two different engines. At the
one point manufacturer data exists for (5800 rpm WOT, AVL Boost paper Table 4)
MVEM is exact on fuel, power and BSFC; the CSV baseline is +33% on fuel. So the
twin is refitted on MVEM and every model downstream shares one physics.

WHAT THIS FIXES BEYOND THE MISMATCH
-----------------------------------
* ENVELOPE. Swept to 35,000 ft and 20% throttle, so the Heron Mk II ceiling and
  descent are inside the trained region. The old envelope stopped at 21,709 ft.
* TRANSIENT FALSE ALARMS. A fraction of rows are thermally lagged mid-manoeuvre
  and labelled HEALTHY, so the gate learns that a climbing coolant temperature
  is not a fault. The old training set was steady-state only, which is why
  RAPID_THROTTLE carried ~1.5 C excess EGT residual.
* PER-ENGINE HEALTHY ROWS. Every virtual engine emits both healthy and faulted
  rows, so an engine-grouped split cannot produce a one-class test set.

HONESTY
-------
MVEM is CALIBRATED to Table 4, not validated against it -- it reproduces that
table because it was fitted to it. Table 4 is WOT at sea level only, so
part-throttle and altitude behaviour is physically modelled but has no
measured reference anywhere. rpm follows throttle directly, so variable
propeller load is not represented. State this in CAVEATS.
"""
from __future__ import annotations

import math
from dataclasses import replace
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

import shared.engine_mvem as mvem
from shared.throttle_dynamics import TAU_S, _lag

OUT = "C:/aeris_data/datasets/mvem_v3.parquet"
N_ENGINES = 60
ROWS_HEALTHY = 6000          # per engine
ROWS_PER_FAULT = 900         # per engine per fault class
FRAC_TRANSIENT = 0.18        # of healthy rows, lagged mid-manoeuvre
SEED = 42

THR_MIN, THR_MAX = 20.0, 100.0
ALT_MIN, ALT_MAX = 0.0, 22800.0   # Rotax 915iS published ceiling is
# 23000 ft [A] and mvem.solve() refuses above it. The Heron Mk II airframe
# ceiling is 35000 ft, so high-altitude loiter is OUT OF SCOPE for scoring.
ISA_DEV_MIN, ISA_DEV_MAX = -20.0, 25.0

# Sensor noise floor, carried over from the old dataset's declared sigmas.
SIGMA: Dict[str, float] = {"rpm": 2.0, "EGT_mean_C": 0.75, "coolant_temp_C": 0.3,
                           "fuelflow_kgh": 0.158, "oil_pressure_bar": 0.02,
                           "oil_temperature_C": 0.25}

SEV = {"mild": 0.30, "moderate": 0.60, "severe": 1.00}

# Fault class -> how it is realised in physics.
# sensor_drift is NOT a physics fault: FaultState's own docstring insists sensor
# faults must not move the rest of the engine, so it is applied to the measured
# channel only, after solve(). That separation is what lets the twin tell a
# broken probe from a broken engine.
PHYSICS_FAULTS = ("cooling_degradation", "lubrication_degradation",
                  "misfire", "fuel_pressure_dev")
ALL_FAULTS = PHYSICS_FAULTS + ("sensor_drift",)
DRIFT_CHANNELS = ("coolant_temp_C", "oil_temperature_C", "EGT_mean_C")


# Certified ambient range is (-40, 50) C [A] and mvem.solve() refuses outside
# it. ISA at 22,800 ft is about -30 C, so a large negative deviation falls off
# the bottom; the sampled value is clamped rather than the deviation narrowed,
# which keeps full weather variety at the low altitudes that matter.
OAT_MIN, OAT_MAX = -39.5, 49.5


def isa_oat(alt_ft: float) -> float:
    return 15.0 - 1.98 * alt_ft / 1000.0


def sample_oat(alt_ft: float, rng) -> float:
    dev = float(rng.uniform(ISA_DEV_MIN, ISA_DEV_MAX))
    return float(min(OAT_MAX, max(OAT_MIN, isa_oat(alt_ft) + dev)))


def build_fault(kind: str, frac: float, rng: np.random.Generator,
                base: mvem.FaultState) -> mvem.FaultState:
    fs = replace(base, cylinder_fuel_trim=list(base.cylinder_fuel_trim))
    if kind == "cooling_degradation":
        # 0.45 puts severe near 0.54 pump health. Deeper was tested and
        # rejected: at 0.35 only 31% of envelope-sampled rows clear 98.7 C
        # and peak coolant reaches 210 C, which is fiction - pressurised
        # 50/50 glycol boils near 120-125 C and MVEM models no boiling or
        # coolant loss. Detectability comes from injecting under thermal
        # load instead, see the fault loop in main(). [UNVERIFIED]
        fs.coolant_pump_health = max(0.05, base.coolant_pump_health - 0.45 * frac)
    elif kind == "lubrication_degradation":
        fs.oil_pump_health = max(0.05, base.oil_pump_health - 0.30 * frac)
        fs.bearing_wear = min(1.0, base.bearing_wear + 0.25 * frac)
    elif kind == "misfire":
        cyl = int(rng.integers(0, 4))
        fs.cylinder_fuel_trim[cyl] = max(0.05, 1.0 - 0.38 * frac)
    elif kind == "fuel_pressure_dev":
        # No fuel-rail knob exists, so a global fuel trim stands in: a rail
        # pressure deviation leans or enriches all four cylinders together,
        # which is the signature that distinguishes it from single-cylinder
        # misfire. Sign is sampled so the class is not direction-locked.
        t = 1.0 + (0.16 * frac) * (1.0 if rng.random() < 0.5 else -1.0)
        fs.cylinder_fuel_trim = [t, t, t, t]
    fs.label = kind
    fs.validate()
    return fs


def measure(o, rng: np.random.Generator) -> Dict[str, float]:
    """EngineOutputs -> the nine AERIS fields, with sensor noise."""
    raw = {"rpm": o.rpm, "EGT_mean_C": o.egt_mean_c,
           "coolant_temp_C": o.coolant_temp_out_c,
           "oil_temperature_C": o.oil_temp_c,
           "oil_pressure_bar": o.oil_pressure_bar,
           "fuelflow_kgh": o.fuel_flow_kgh}
    return {k: v + rng.normal(0.0, SIGMA[k]) for k, v in raw.items()}


def lagged_run(thr0: float, thr1: float, alt: float, oat: float,
               fs: mvem.FaultState, rng: np.random.Generator) -> Dict[str, float]:
    """Solve a throttle step and stop partway through the thermal transient,
    so the row carries a genuine mid-manoeuvre lag rather than equilibrium."""
    o0 = mvem.solve(throttle_pct=thr0, altitude_ft=alt, oat_c=oat, fault=fs)
    o1 = mvem.solve(throttle_pct=thr1, altitude_ft=alt, oat_c=oat, fault=fs)
    a = {"rpm": o0.rpm, "EGT_mean_C": o0.egt_mean_c,
         "coolant_temp_C": o0.coolant_temp_out_c, "oil_temperature_C": o0.oil_temp_c,
         "oil_pressure_bar": o0.oil_pressure_bar, "fuelflow_kgh": o0.fuel_flow_kgh}
    b = {"rpm": o1.rpm, "EGT_mean_C": o1.egt_mean_c,
         "coolant_temp_C": o1.coolant_temp_out_c, "oil_temperature_C": o1.oil_temp_c,
         "oil_pressure_bar": o1.oil_pressure_bar, "fuelflow_kgh": o1.fuel_flow_kgh}
    t = float(rng.uniform(1.0, 90.0))          # seconds into the step
    out = {k: _lag(a[k], b[k], TAU_S[k], t) for k in TAU_S}
    return {k: v + rng.normal(0.0, SIGMA[k]) for k, v in out.items()},  o1


def main() -> None:
    rng = np.random.default_rng(SEED)
    rows: List[Dict] = []

    for e in range(1, N_ENGINES + 1):
        eid = f"ENG_{e:04d}"
        # Unit-to-unit scatter: no two engines leave the factory identical.
        base = mvem.FaultState(
            coolant_pump_health=float(rng.uniform(0.97, 1.00)),
            oil_pump_health=float(rng.uniform(0.97, 1.00)),
            bearing_wear=float(rng.uniform(0.0, 0.04)))
        base.validate()

        n_tr = int(ROWS_HEALTHY * FRAC_TRANSIENT)
        for i in range(ROWS_HEALTHY):
            alt = float(rng.uniform(ALT_MIN, ALT_MAX))
            oat = sample_oat(alt, rng)
            thr = float(rng.uniform(THR_MIN, THR_MAX))
            if i < n_tr:
                thr2 = float(np.clip(thr + rng.normal(0.0, 22.0), THR_MIN, THR_MAX))
                m, o = lagged_run(thr, thr2, alt, oat, base, rng)
                thr_rec, trans = thr2, True
            else:
                o = mvem.solve(throttle_pct=thr, altitude_ft=alt, oat_c=oat, fault=base)
                m, thr_rec, trans = measure(o, rng), thr, False
            rows.append({"engine_id": eid, "fault_type": "healthy",
                         "fault_severity": "none", "is_transient": trans,
                         "altitude_ft": alt, "ambient_temperature_C": oat,
                         "throttle_pct": thr_rec, "power_kW": o.brake_power_kw, **m})

        for kind in ALL_FAULTS:
            for _ in range(ROWS_PER_FAULT):
                sev = str(rng.choice(list(SEV)))
                frac = SEV[sev] * float(rng.uniform(0.7, 1.0))
                if kind == "cooling_degradation":
                    # A thermostatted engine hides a weak coolant pump at
                    # low load: the outlet sensor holds setpoint until heat
                    # rejection exceeds what the degraded pump can carry.
                    # Sampling this class across the full envelope produced
                    # 2% gate detection. It is therefore defined only where
                    # the fault is thermodynamically observable - high
                    # throttle, warm air, lower altitude. Cooling detection
                    # is CONDITIONAL ON THERMAL LOAD, not envelope-wide. The window
                    # must stay WIDE: a tight corner (thr>=72, alt<=9000,
                    # ISA+12) made the operating point itself the label -
                    # the gate hit 0.98 on cooling rows with no coolant
                    # signal and 21% false alarm on healthy cruise.
                    # Proper fix is coolant dT (coolant_temp_in_c vs
                    # _out_c), which needs a 6th sensor channel. [UNVERIFIED]
                    alt = float(rng.uniform(ALT_MIN, 16000.0))
                    oat = float(min(OAT_MAX, max(OAT_MIN, isa_oat(alt)
                                    + float(rng.uniform(0.0, ISA_DEV_MAX)))))
                    thr = float(rng.uniform(55.0, THR_MAX))
                else:
                    alt = float(rng.uniform(ALT_MIN, ALT_MAX))
                    oat = sample_oat(alt, rng)
                    thr = float(rng.uniform(THR_MIN, THR_MAX))
                if kind == "sensor_drift":
                    o = mvem.solve(throttle_pct=thr, altitude_ft=alt,
                                   oat_c=oat, fault=base)
                    m = measure(o, rng)
                    ch = str(rng.choice(DRIFT_CHANNELS))
                    span = {"coolant_temp_C": 9.0, "oil_temperature_C": 11.0,
                            "EGT_mean_C": 45.0}[ch]
                    m[ch] += span * frac * (1.0 if rng.random() < 0.5 else -1.0)
                else:
                    fs = build_fault(kind, frac, rng, base)
                    o = mvem.solve(throttle_pct=thr, altitude_ft=alt,
                                   oat_c=oat, fault=fs)
                    m = measure(o, rng)
                rows.append({"engine_id": eid, "fault_type": kind,
                             "fault_severity": sev, "is_transient": False,
                             "altitude_ft": alt, "ambient_temperature_C": oat,
                             "throttle_pct": thr, "power_kW": o.brake_power_kw, **m})

        if e % 10 == 0:
            print(f"  engine {e}/{N_ENGINES}  rows {len(rows):,}")

    df = pd.DataFrame(rows)
    BOIL_C = 125.0
    n_boil = int((df.coolant_temp_C > BOIL_C).sum())
    if n_boil:
        df = df[df.coolant_temp_C <= BOIL_C].reset_index(drop=True)
    print(f"dropped {n_boil:,} rows with coolant > {BOIL_C:.0f} C: "
          "pressurised 50/50 glycol boils near 120-125 C and MVEM models "
          "no boiling or coolant loss, so those rows are out of scope")
    df.to_parquet(OUT, index=False)
    print(f"\nwritten {OUT}   rows {len(df):,}")
    print(df.fault_type.value_counts().to_string())
    print(f"\ntransient healthy rows: {int(df.is_transient.sum()):,}")
    print("\nhealthy channel ranges (what the new envelope covers):")
    h = df[df.fault_type == "healthy"]
    for c in ("altitude_ft", "throttle_pct", "rpm", "ambient_temperature_C",
              "coolant_temp_C", "EGT_mean_C", "oil_temperature_C",
              "oil_pressure_bar", "fuelflow_kgh", "power_kW"):
        print(f"  {c:<24} {h[c].min():9.2f} .. {h[c].max():9.2f}")


if __name__ == "__main__":
    main()
