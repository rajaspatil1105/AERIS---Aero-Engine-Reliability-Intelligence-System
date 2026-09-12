"""
AERIS -- mission engine. Shared core for all three simulator modes.

WHY THIS FILE EXISTS
--------------------
simulator_bridge.walk() emits STEADY-STATE frames along a profile and cannot
carry state between steps. A mission simulator needs the opposite: an engine
that remembers. Heat soaks in, oil takes 25 s to respond, and abuse accumulates
until something breaks.

So this module owns three things bridge.walk() deliberately does not:

  1. THERMAL LAG. Channels are first-order lagged using throttle_dynamics.TAU_S,
     which remains the only place those constants live.
  2. STRESS ACCUMULATION. Four counters that grow only while the engine is
     actually being abused, not with wall-clock time.
  3. PHYSICS-CAUSED DEGRADATION. When a counter crosses its threshold this
     module walks FaultState knobs (coolant_pump_health, oil_pump_health,
     bearing_wear) DOWNWARD. The engine then runs hot because the pump is
     genuinely weaker, not because an offset was added to a temperature.

That last point is the whole design. Stage 7 injected faults additively; a
fouled pump here changes combustion and every downstream channel coherently,
which is what makes the twin's residual mean something.

WHAT THIS MODULE DOES NOT CLAIM
-------------------------------
The stress THRESHOLDS are engineering judgement, not measured failure data.
There is no Rotax 915iS field reliability dataset behind them. Direction is
defensible physics -- sustained high coolant temperature degrades a pump, time
at high power wears bearings -- but the RATES are invented. This is a
stress-triggered scenario generator, not a failure-rate predictor. Say so in
CAVEATS and it is honest; claim otherwise and it is not.
"""
from __future__ import annotations

import pathlib

import math
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

import shared.engine_mvem as mvem
from shared.throttle_dynamics import TAU_S, _lag
from node1_ingestion.simulator_bridge import Setpoint

# Trained-envelope edges, read off models/training_envelope.json. Frames
# outside these are emitted with scoreable=False; the twin extrapolates there
# and its own manifest says the residuals are meaningless.
# The trained envelope is owned by models/configs/reconstruction_config.json
# (written by the baseline refit from the data actually fit on). Do NOT
# restate it here: a local copy of the 14-column feature contract disagreed
# with residual_calc once and the gate scored permuted columns for a whole
# retrain cycle. Read it, fall back only if the config is unreadable.
def _load_envelope() -> dict:
    import json
    try:
        cfg = json.loads((pathlib.Path(__file__).resolve().parents[1]
               / 'models' / 'configs' / 'reconstruction_config.json')
               .read_text(encoding='utf-8'))
        rng = next(iter(cfg['baseline_stats'].values()))['operating_range']
        return {k: (float(v[0]), float(v[1])) for k, v in rng.items()}
    except Exception as exc:
        print('[mission_engine] envelope config unreadable (%s), '
              'using fallback' % exc)
        return {'altitude_ft': (0.0, 22799.88),
                'throttle_pct': (20.0, 100.0),
                'rpm': (2592.26, 5807.61),
                'ambient_temperature_C': (-39.5, 39.86)}

ENVELOPE = _load_envelope()
ENV_ALT_MIN_FT, ENV_ALT_MAX_FT = ENVELOPE['altitude_ft']
ENV_THR_MIN_PCT, ENV_THR_MAX_PCT = ENVELOPE['throttle_pct']
ENV_RPM_MIN, ENV_RPM_MAX = ENVELOPE['rpm']
ENV_OAT_MIN_C, ENV_OAT_MAX_C = ENVELOPE['ambient_temperature_C']

AERIS_FIELDS = ("altitude_ft", "ambient_temperature_C", "throttle_pct", "rpm",
                "fuelflow_kgh", "coolant_temp_C", "EGT_mean_C",
                "oil_pressure_bar", "oil_temperature_C")


class MissionEngineError(RuntimeError):
    """Unusable profile, fleet entry, or physics output."""


# ==================================================================== #
# Fleet -- hardcoded wear states
# ==================================================================== #
#
# Wear is a STARTING CONDITION, not an accumulated history. A worn engine
# begins with a weaker pump and therefore genuinely runs hotter; AERIS infers
# that from telemetry rather than reading a number we typed in. Do NOT add an
# rul_hours field here -- predicting a value you hardcoded is circular, and
# that is exactly the criticism a reviewer will make.

@dataclass(frozen=True)
class FleetEngine:
    serial: str
    uav_tail: str
    hours: float
    coolant_pump_health: float
    oil_pump_health: float
    bearing_wear: float
    note: str

    def fault_state(self) -> mvem.FaultState:
        fs = mvem.FaultState(
            coolant_pump_health=self.coolant_pump_health,
            oil_pump_health=self.oil_pump_health,
            bearing_wear=self.bearing_wear,
            label="HEALTHY" if self.hours < 600 else "WORN")
        fs.validate()
        return fs


FLEET: Tuple[FleetEngine, ...] = (
    FleetEngine("RTX915-0001", "HRN-01",   40.0, 1.000, 1.000, 0.000, "factory fresh"),
    FleetEngine("RTX915-0002", "HRN-01",  180.0, 0.995, 0.995, 0.005, "run-in complete"),
    FleetEngine("RTX915-0003", "HRN-02",  340.0, 0.985, 0.990, 0.012, "nominal"),
    FleetEngine("RTX915-0004", "HRN-02",  520.0, 0.975, 0.980, 0.022, "nominal"),
    FleetEngine("RTX915-0005", "HRN-03",  690.0, 0.960, 0.972, 0.035, "mid-life"),
    FleetEngine("RTX915-0006", "HRN-03",  810.0, 0.948, 0.960, 0.048, "mid-life"),
    FleetEngine("RTX915-0007", "HRN-04",  950.0, 0.930, 0.948, 0.062, "hot-climate ops"),
    FleetEngine("RTX915-0008", "HRN-04", 1080.0, 0.915, 0.935, 0.078, "hot-climate ops"),
    FleetEngine("RTX915-0009", "HRN-05", 1210.0, 0.898, 0.920, 0.095, "cooling pack due"),
    FleetEngine("RTX915-0010", "HRN-05", 1340.0, 0.880, 0.905, 0.112, "cooling pack due"),
    FleetEngine("RTX915-0011", "HRN-06", 1480.0, 0.862, 0.888, 0.130, "oil analysis flagged"),
    FleetEngine("RTX915-0012", "HRN-06", 1620.0, 0.845, 0.870, 0.150, "oil analysis flagged"),
    FleetEngine("RTX915-0013", "HRN-07", 1760.0, 0.826, 0.852, 0.172, "overhaul window"),
    FleetEngine("RTX915-0014", "HRN-07", 1890.0, 0.808, 0.834, 0.195, "overhaul window"),
    FleetEngine("RTX915-0015", "HRN-08", 2010.0, 0.790, 0.815, 0.220, "past soft limit"),
)

FLEET_BY_SERIAL: Dict[str, FleetEngine] = {e.serial: e for e in FLEET}


def fleet_listing() -> List[Dict[str, Any]]:
    """Fleet as JSON, ordered best condition first. Feeds the UI dropdown."""
    return [{"serial": e.serial, "uav_tail": e.uav_tail, "hours": e.hours,
             "coolant_pump_health": e.coolant_pump_health,
             "oil_pump_health": e.oil_pump_health,
             "bearing_wear": e.bearing_wear, "note": e.note}
            for e in FLEET]


# ==================================================================== #
# Stress accumulation
# ==================================================================== #
#
# Each counter integrates EXCEEDANCE, not time. Cruising at 88 C adds nothing;
# sitting at 102 C adds fast. Units are dimensionless "damage", 1.0 = trigger.

COOLANT_KNEE_C = 95.0        # above this, thermal damage accrues
OIL_KNEE_C = 105.0
POWER_KNEE_KW = 95.0
THERMAL_FULL_S = 2400.0      # seconds at knee+10 C to reach 1.0
OIL_FULL_S = 3000.0
POWER_FULL_S = 5400.0
CYCLE_FULL_N = 400.0         # throttle excursions >30 %/s to reach 1.0
CYCLE_RATE_PCT_S = 30.0


@dataclass
class StressState:
    thermal: float = 0.0
    oil: float = 0.0
    power: float = 0.0
    cycles: float = 0.0
    triggered: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {"thermal": round(self.thermal, 4), "oil": round(self.oil, 4),
                "power": round(self.power, 4), "cycles": round(self.cycles, 4),
                "triggered": list(self.triggered)}


def accumulate(st: StressState, coolant_c: float, oil_c: float, power_kw: float,
               dthrottle_pct_s: float, dt_s: float, wear: float) -> None:
    """Integrate one step of abuse. `wear` (0..1) accelerates accumulation:
    a worn engine reaches the same damage sooner under identical abuse."""
    accel = 1.0 + 2.0 * max(0.0, min(1.0, wear))
    if coolant_c > COOLANT_KNEE_C:
        st.thermal += accel * dt_s * (coolant_c - COOLANT_KNEE_C) / (10.0 * THERMAL_FULL_S)
    if oil_c > OIL_KNEE_C:
        st.oil += accel * dt_s * (oil_c - OIL_KNEE_C) / (10.0 * OIL_FULL_S)
    if power_kw > POWER_KNEE_KW:
        st.power += accel * dt_s * (power_kw - POWER_KNEE_KW) / (10.0 * POWER_FULL_S)
    if abs(dthrottle_pct_s) > CYCLE_RATE_PCT_S:
        st.cycles += accel / CYCLE_FULL_N


# Which knob each counter walks, and how far per unit of overshoot past 1.0.
# Degradation is CONTINUOUS past the trigger: the fault deepens if abuse
# continues, and holds (does not heal) if it stops. Real damage is not a window.
DEGRADE = {
    "thermal": ("coolant_pump_health", -0.30, "cooling_degradation"),
    "oil":     ("oil_pump_health",     -0.25, "lubrication_degradation"),
    "power":   ("bearing_wear",        +0.35, "bearing_wear"),
    "cycles":  ("cylinder_fuel_trim",  -0.18, "misfire"),
}


def apply_degradation(fs: mvem.FaultState, st: StressState,
                      base: FleetEngine) -> List[str]:
    """Walk FaultState knobs from accumulated stress. Returns newly fired names."""
    fired: List[str] = []
    for counter, (knob, gain, label) in DEGRADE.items():
        over = getattr(st, counter) - 1.0
        if over <= 0.0:
            continue
        if label not in st.triggered:
            st.triggered.append(label)
            fired.append(label)
        frac = min(1.0, over)
        if knob == "cylinder_fuel_trim":
            t = max(0.05, 1.0 + gain * frac)
            fs.cylinder_fuel_trim = [t, 1.0, 1.0, 1.0]   # one weak cylinder
        elif knob == "bearing_wear":
            fs.bearing_wear = max(0.0, min(1.0, base.bearing_wear + gain * frac))
        else:
            start = getattr(base, knob)
            setattr(fs, knob, max(0.05, start + gain * frac))
    fs.label = "+".join(st.triggered) if st.triggered else "HEALTHY"
    fs.validate()
    return fired


# ==================================================================== #
# EngineOutputs -> AERIS nine fields
# ==================================================================== #
#
# Attribute names on EngineOutputs are not guessed silently. Each field lists
# candidates; if none resolve the run stops and names the field, because a
# missing channel filled with a default is how residuals acquire silent bias.

_CANDIDATES: Dict[str, Tuple[str, ...]] = {
    "rpm": ("rpm",),
    "EGT_mean_C": ("egt_mean_c", "EGT_mean_C", "egt_mean", "egt_c"),
    "coolant_temp_C": ("coolant_temp_out_c", "coolant_temp_c"),
    "oil_temperature_C": ("oil_temp_c",),
    "oil_pressure_bar": ("oil_pressure_bar", "oil_press_bar"),
    "fuelflow_kgh": ("fuel_flow_kgh", "fuelflow_kgh", "fuel_kgh"),
    "power_kW": ("brake_power_kw", "power_kw"),
}


def _pluck(o: Any, key: str) -> float:
    for name in _CANDIDATES[key]:
        if hasattr(o, name):
            v = getattr(o, name)
            if isinstance(v, (list, tuple)):
                v = sum(v) / len(v)
            return float(v)
    raise MissionEngineError(
        f"EngineOutputs has no attribute for '{key}'; tried "
        f"{_CANDIDATES[key]}. Available: "
        f"{sorted(a for a in dir(o) if not a.startswith('_'))[:40]}")


def probe_outputs() -> None:
    """Print EngineOutputs fields so _CANDIDATES can be corrected if needed."""
    o = mvem.solve(throttle_pct=80.0, altitude_ft=6000.0, oat_c=10.0)
    print("EngineOutputs attributes:")
    for a in sorted(a for a in dir(o) if not a.startswith("_")):
        v = getattr(o, a)
        if isinstance(v, (int, float, list, tuple)):
            print(f"  {a:<32} {v}")
    print("\nresolved:")
    for k in _CANDIDATES:
        try:
            print(f"  {k:<20} {_pluck(o, k):.3f}")
        except MissionEngineError as exc:
            print(f"  {k:<20} UNRESOLVED -- {str(exc)[:80]}")


# ==================================================================== #
# The mission loop
# ==================================================================== #

def _interp(pts: Sequence[Setpoint], t: float) -> Tuple[float, float, Optional[float]]:
    lo, hi = pts[0], pts[-1]
    for a, b in zip(pts, pts[1:]):
        if a.t_s <= t <= b.t_s:
            lo, hi = a, b
            break
    span = hi.t_s - lo.t_s
    f = 0.0 if span <= 0.0 else (t - lo.t_s) / span
    thr = lo.throttle_pct + f * (hi.throttle_pct - lo.throttle_pct)
    alt = lo.altitude_ft + f * (hi.altitude_ft - lo.altitude_ft)
    oat = None
    if lo.oat_c is not None and hi.oat_c is not None:
        oat = lo.oat_c + f * (hi.oat_c - lo.oat_c)
    elif lo.oat_c is not None:
        oat = lo.oat_c
    return thr, alt, oat


def run_mission(profile: Sequence[Setpoint],
                engine: FleetEngine,
                dt_s: float = 1.0,
                stress_enabled: bool = True,
                forced_fault: Optional[mvem.FaultState] = None,
                forced_at_s: Optional[float] = None,
                forced_clear_s: Optional[float] = None,
                emit_cruise_s: float = 60.0,
                emit_event_s: float = 1.0) -> Iterator[Dict[str, Any]]:
    """Run a profile with memory. Yields AERIS frames plus diagnostics.

    stress_enabled  -- physics-caused faults from accumulated abuse (modes 1, 2)
    forced_fault    -- a FaultState imposed at forced_at_s (mode 3)

    Emission is adaptive: every emit_event_s during transients, climbs and for
    120 s after any fault fires; every emit_cruise_s otherwise. A 45 h mission
    is ~160k physics steps but only ~5k emitted frames.
    """
    if len(profile) < 2:
        raise MissionEngineError("a profile needs at least two setpoints")
    if dt_s <= 0.0:
        raise MissionEngineError(f"dt_s {dt_s} must be positive")

    pts = sorted(profile, key=lambda s: s.t_s)
    fs = engine.fault_state()
    _forced_on = False
    st = StressState()
    lagged: Dict[str, float] = {}
    prev_thr = pts[0].throttle_pct
    last_emit = -1e9
    event_until = -1e9
    t_end = pts[-1].t_s
    n = int(math.floor(t_end / dt_s)) + 1

    for i in range(n):
        t = i * dt_s
        thr, alt, oat = _interp(pts, t)
        dthr = (thr - prev_thr) / dt_s
        prev_thr = thr

        if forced_fault is not None and forced_at_s is not None:
            active = t >= forced_at_s and (forced_clear_s is None
                                            or t <= forced_clear_s)
            # Do NOT infer forced state from fs.label: apply_degradation()
            # rewrites it when stress fires, the label comparison then misses
            # and the forced fault never clears (measured: injected at 300 s,
            # still applied at 1770 s with forced_clear_s=900).
            if active and not _forced_on:
                fs = replace(forced_fault)
                _forced_on = True
            elif not active and _forced_on:
                fs = engine.fault_state()          # recovery: back to baseline
                _forced_on = False
                event_until = t + 120.0

        o = mvem.solve(throttle_pct=thr, altitude_ft=alt, oat_c=oat, fault=fs)

        target = {k: _pluck(o, k) for k in TAU_S}
        if not lagged:
            lagged = dict(target)
        else:
            for k, tau in TAU_S.items():
                lagged[k] = _lag(lagged[k], target[k], tau, dt_s)

        power = _pluck(o, "power_kW")
        if stress_enabled:
            accumulate(st, lagged["coolant_temp_C"], lagged["oil_temperature_C"],
                       power, dthr, dt_s, engine.bearing_wear)
            for name in apply_degradation(fs, st, engine):
                event_until = t + 120.0

        transient = abs(dthr) > 0.5 or t <= event_until
        due = emit_event_s if transient else emit_cruise_s
        if t - last_emit < due and 0 < i < n - 1:
            continue
        last_emit = t

        ambient = o.oat_c if hasattr(o, "oat_c") else (oat if oat is not None else 15.0)
        frame = {"altitude_ft": round(alt, 1),
                 "ambient_temperature_C": round(float(ambient), 2),
                 "throttle_pct": round(thr, 2),
                 "rpm": round(lagged["rpm"], 1),
                 "fuelflow_kgh": round(lagged["fuelflow_kgh"], 4),
                 "coolant_temp_C": round(lagged["coolant_temp_C"], 2),
                 "EGT_mean_C": round(lagged["EGT_mean_C"], 2),
                 "oil_pressure_bar": round(lagged["oil_pressure_bar"], 4),
                 "oil_temperature_C": round(lagged["oil_temperature_C"], 2)}

        # Ambient is checked too: a hot sea-level day can exceed the
        # +39.9 C the baselines ever saw, and forests extrapolate flat.
        scoreable = (ENV_ALT_MIN_FT <= alt <= ENV_ALT_MAX_FT
                     and ENV_THR_MIN_PCT <= thr <= ENV_THR_MAX_PCT
                     and ENV_RPM_MIN <= lagged["rpm"] <= ENV_RPM_MAX
                     and ENV_OAT_MIN_C <= oat <= ENV_OAT_MAX_C)
        yield {"t_s": round(t, 2), "frame": frame, "power_kW": round(power, 2),
               "stress": st.as_dict(), "fault_label": fs.label,
               "scoreable": scoreable,
               "envelope_note": None if scoreable else
               "outside trained envelope -- twin extrapolates, residuals not meaningful",
               "engine": engine.serial, "uav_tail": engine.uav_tail}


if __name__ == "__main__":
    probe_outputs()
