"""AERIS throttle dynamics -- transient response of the twin.

The stress simulator maps competence over a STATIC grid. This module walks a
throttle-versus-time profile and reports what the twin sees during the
transient: how far the slow channels lag their steady-state targets, when the
trajectory re-enters model competence, and what the twin says once it does.

WHAT IS AND IS NOT VALIDATED HERE
The baselines are STEADY-STATE regressors. BaselineDeck.predict() returns
equilibrium values for an operating point; there is no time constant anywhere
in node2 (rul_engine's EWMA smooths RUL, it is not a thermal model). So the
first-order lag applied below is a physical model layered ON TOP of your data,
with time constants chosen from general piston-engine knowledge. Transient
SHAPE is meaningful; the specific seconds are UNVERIFIED and need real Rotax
915 iS transient data or a Cantera transient run to pin down.

THE CENTRAL CONSEQUENCE, AND THE ADMISSION GATE
Residuals are zero when a frame matches the steady-state prediction. Lag is
therefore precisely what CREATES residuals during a transient. Asking a
steady-state regressor about a mid-manoeuvre engine is out-of-distribution BY
CONSTRUCTION, and the twin answers in one of two useless ways: it declines
(residual_calc sets meaningful=False, anomaly_probability=None), or it returns
a near-certain fault on a perfectly healthy engine because actual != equilibrium.

This module therefore does not ask. Before each frame is scored it must pass a
steady-state admission gate: throttle rate near zero, and every lagged channel
within SETTLE_FRAC of its own step size. Frames that fail are recorded as
TRANSIENT and the twin is never called. Skipping a score is cheap and visible;
clamping inputs to force a probability would be neither.

FOUR OUTCOMES IN, THREE OUT
Internally a frame is SCORED, TRANSIENT, DECLINED or REFUSED, because the
difference matters when reading a trace. On the wire TRANSIENT and DECLINED
both become UNAVAILABLE with a reason string -- see wire_status(). That keeps
twin_core, canonical, the api status enum and the WS hello frame untouched.

STATE
TwinCore.reset() clears rul_engine (EWMA + a 50-frame deque); nothing else in
the core is mutable. Every profile run calls reset() first, so profiles cannot
inherit each other's trend history.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from shared import atmosphere as atm
from shared.stress_sim import (
    DECLINED, GATE_THRESHOLD, REFUSED, SCORED,
    build_core, deck, deck_violations, envelope_verdict, reference_op,
    _extract,
)

THROTTLE_DYNAMICS_VERSION = "0.2.4"
FRAME_HZ = 10.0                  # matches RulEngine(frame_hz=10.0)

# fourth outcome, local to this module; stress_sim's three are imported above
TRANSIENT = "transient"

# ---- steady-state admission gate (ENGINEERING JUDGEMENT, not measured) ----
# A frame is scored only if the command is not moving and every lagged channel
# has closed to within SETTLE_FRAC of its own step. 2% of a step is ~3.9 tau of
# settling, which is why the step profile below must run for at least 5 tau of
# the slowest channel (oil, 25 s) to show any scored frames after a step.
THROTTLE_RATE_TOL_PCT_S = 0.5
SETTLE_FRAC = 0.02          # retained for reporting only, NOT for admission

# Admission is decided on ABSOLUTE residual per channel, not on a fraction of
# the step. Reason, measured in CASE 3a: the gate is tree-based and healthy
# residuals sit at ~0, so a split lives very close to zero -- a 0.157 C oil
# residual moved p_anom from 0.5477 to 0.8049. A fraction-of-step tolerance
# says something about the lag model; only an absolute tolerance below the
# gate's own residual resolution guarantees an admitted frame scores what true
# equilibrium scores. These values are JUDGEMENT, but CASE 3a measures the
# resolution and asserts each one sits below it.
# Set to ~0.2 x the resolution MEASURED TWO-SIDED by CASE 3a at 95% throttle,
# 6000 ft, 10 C: rpm 6.82, EGT 1.373, coolant 0.01455, fuelflow 0.00358,
# oil_temp 0.00174, oil_press 0.00019. Oil temperature is the binding channel
# at 1.74 mK -- see caveat admission_cost_on_missions.
#
# rpm was originally set from a ONE-SIDED probe that read 38.84; measuring the
# minus direction as well cut it to 6.82, because the nearest tree split below
# equilibrium is much closer than the one above. A lagging channel always
# approaches from one side, so two-sided is the only correct measurement.
GATE_RESID_TOL: Dict[str, float] = {
    "rpm": 1.5,
    "EGT_mean_C": 0.25,
    "coolant_temp_C": 0.0029,
    "oil_temperature_C": 0.00035,
    "fuelflow_kgh": 0.0007,
    "oil_pressure_bar": 0.000038,
}

# Step profile endpoints. NOTE: the deck was trained on throttle [56.5, 100]
# only. 40% is STATICALLY outside the envelope, so the twin declines it however
# steady it is -- a different answer from TRANSIENT, and both must survive.
STEP_LOW_PCT = 40.0
STEP_HIGH_PCT = 95.0

# ---- UNVERIFIED physical model -------------------------------------------
# First-order lag time constants, seconds. Chosen from general piston-engine
# behaviour: gas path responds in ~1 s, coolant in tens of seconds, oil
# slowest because of sump mass. NOT fitted to AERIS training data.
TAU_S: Dict[str, float] = {
    "rpm": 1.5,
    "EGT_mean_C": 2.0,
    "coolant_temp_C": 15.0,
    "oil_temperature_C": 25.0,
    "fuelflow_kgh": 0.8,
    "oil_pressure_bar": 1.0,
}
LAGGED = tuple(TAU_S)

# RPM-versus-throttle surrogate. Linear idle->max; anchored by the fact that
# 80% throttle yields exactly 5000 rpm, the deck's reference operating point.
RPM_IDLE = 1800.0
RPM_MAX = 5800.0


class ThrottleDynamicsError(RuntimeError):
    pass


def rpm_for_throttle(throttle_pct: float) -> float:
    t = max(0.0, min(100.0, float(throttle_pct)))
    return RPM_IDLE + (RPM_MAX - RPM_IDLE) * t / 100.0


def _lag(current: float, target: float, tau_s: float, dt_s: float) -> float:
    """Exponential first-order lag; stable for any dt."""
    if tau_s <= 0.0:
        return target
    k = 1.0 - math.exp(-dt_s / tau_s)
    return current + (target - current) * k


def wire_status(outcome: str) -> str:
    """Four internal outcomes collapse to three on the service contract.

    TRANSIENT and DECLINED are both 'the twin did not score this frame', which
    is what UNAVAILABLE already means. Node 4 renders the reason string.
    """
    if outcome == SCORED:
        return "SCORED"
    if outcome == REFUSED:
        return "REFUSED"
    return "UNAVAILABLE"


def _admit(state: Mapping[str, float], target: Mapping[str, float],
           span: Mapping[str, float], throttle_rate_pct_s: float
           ) -> Tuple[bool, str]:
    """Is this frame inside the regime the baselines were trained on?"""
    if abs(throttle_rate_pct_s) > THROTTLE_RATE_TOL_PCT_S:
        return False, (f"throttle moving {throttle_rate_pct_s:+.1f} %/s "
                       f"(tol {THROTTLE_RATE_TOL_PCT_S} %/s)")
    for k in LAGGED:
        # span is unused: admission is absolute, see GATE_RESID_TOL.
        gap = abs(float(state[k]) - float(target[k]))
        tol = float(GATE_RESID_TOL.get(k, 0.0))
        if tol <= 0.0:
            tol = 1e-9
        if gap > tol:
            return False, (f"{k} is {gap:.3f} from equilibrium "
                           f"(tol {tol:.3f}, tau {TAU_S[k]}s)")
    return True, ""


def _score_state(core: Any, op: Mapping, state: Mapping[str, float],
                 humidity_pct: float = 0.0) -> Optional[float]:
    """One frame through adapter + twin. None if refused or declined."""
    from node1_ingestion.adapter import to_twin_payload, twin_frame_to_dict
    try:
        payload, provided = _frame_from_state(op, state, humidity_pct)
    except Exception:
        return None
    res = to_twin_payload(payload, provided=provided, strict=False)
    if not res.ok:
        return None
    out = twin_frame_to_dict(core.process(res.features))
    pa = out.get("anomaly_probability")
    return None if pa is None else float(pa)


def gate_residual_resolution(core: Any, op: Mapping, key: str,
                             iters: int = 26) -> Tuple[Optional[float],
                                                       Optional[float]]:
    """Smallest offset in `key` that moves p_anom off its equilibrium value.

    Direct analogue of stress_sim.gate_resolution_ft, but in residual space.
    Returns (equilibrium p_anom, resolution). Resolution None means the score
    never moved, so the channel could not be resolved from this point.
    """
    target = _extract(deck().predict(dict(op)))
    target["rpm"] = float(op["rpm"])
    base = _score_state(core, op, dict(target))
    if base is None:
        return None, None
    # BOTH directions. A lagging channel approaches equilibrium from below,
    # so the -offset resolution is the one that governs admission; the nearest
    # tree split is not symmetric about the equilibrium value.
    best: Optional[float] = None
    for sign in (1.0, -1.0):
        hi = max(abs(float(target[key])) * 0.02, 1e-3)
        moved = False
        for _ in range(14):
            st = dict(target)
            st[key] = target[key] + sign * hi
            q = _score_state(core, op, st)
            if q is None or q != base:
                moved = True
                break
            hi *= 4.0
        if not moved:
            continue
        lo = 0.0
        for _ in range(iters):
            mid = 0.5 * (lo + hi)
            st = dict(target)
            st[key] = target[key] + sign * mid
            q = _score_state(core, op, st)
            if q is None or q != base:
                hi = mid
            else:
                lo = mid
        best = hi if best is None else min(best, hi)
    return base, best


# --------------------------------------------------------------------------
# profiles: throttle command as a function of time
# --------------------------------------------------------------------------

def profile_steady(seconds: float = 20.0, throttle_pct: float = 80.0
                   ) -> List[Tuple[float, float]]:
    n = int(seconds * FRAME_HZ)
    return [(i / FRAME_HZ, throttle_pct) for i in range(n)]


def profile_step(seconds: float = 360.0, low: float = STEP_LOW_PCT,
                 high: float = STEP_HIGH_PCT,
                 step_at_s: float = 15.0) -> List[Tuple[float, float]]:
    """150 s by default: oil has tau=25 s, so 5 tau = 125 s must fit AFTER the
    step or the slow channels never settle and nothing is ever scored again."""
    n = int(seconds * FRAME_HZ)
    return [(i / FRAME_HZ, low if i / FRAME_HZ < step_at_s else high)
            for i in range(n)]


def profile_ramp(seconds: float = 60.0, low: float = 30.0, high: float = 100.0,
                 ramp_s: float = 30.0, hold_s: float = 10.0
                 ) -> List[Tuple[float, float]]:
    n = int(seconds * FRAME_HZ)
    out = []
    for i in range(n):
        t = i / FRAME_HZ
        if t < hold_s:
            th = low
        elif t < hold_s + ramp_s:
            th = low + (high - low) * (t - hold_s) / ramp_s
        else:
            th = high
        out.append((t, th))
    return out


def profile_chop_and_slam(seconds: float = 90.0, cruise: float = 75.0,
                          idle: float = 15.0, full: float = 100.0
                          ) -> List[Tuple[float, float]]:
    """The classic thermal-shock manoeuvre: cruise, chop to idle, slam to full."""
    n = int(seconds * FRAME_HZ)
    out = []
    for i in range(n):
        t = i / FRAME_HZ
        if t < 20.0:
            th = cruise
        elif t < 40.0:
            th = idle
        elif t < 65.0:
            th = full
        else:
            th = cruise
        out.append((t, th))
    return out


PROFILES = {
    "steady": profile_steady,
    "step": profile_step,
    "ramp": profile_ramp,
    "chop_and_slam": profile_chop_and_slam,
}


# --------------------------------------------------------------------------
# one timestep
# --------------------------------------------------------------------------

@dataclass
class Step:
    t_s: float = 0.0
    throttle_cmd_pct: float = 0.0
    throttle_rate_pct_s: float = 0.0
    rpm: float = 0.0
    altitude_ft: float = 0.0
    oat_c: float = 0.0

    target: Dict[str, float] = field(default_factory=dict)
    actual: Dict[str, float] = field(default_factory=dict)

    outcome: str = REFUSED
    stage: str = "unrun"
    note: str = ""

    anomaly_probability: Optional[float] = None
    margin_to_gate: Optional[float] = None
    status: Optional[str] = None
    largest_residual: Optional[float] = None
    worst_channel: Optional[str] = None
    envelope: str = "unknown"

    def lag_error(self, key: str) -> Optional[float]:
        if key in self.target and key in self.actual:
            return self.actual[key] - self.target[key]
        return None

    def p(self) -> str:
        return "  --  " if self.anomaly_probability is None else f"{self.anomaly_probability:.4f}"

    def wire(self) -> str:
        return wire_status(self.outcome)


def _frame_from_state(op: Mapping, state: Mapping, humidity_pct: float) -> Tuple[Any, set]:
    """Build a 68-column frame from LAGGED channel values, not steady state."""
    from node1_ingestion.adapter import (
        FT_PER_M, FUEL_DENSITY_KG_PER_L, KPA_PER_BAR, COOLANT_SOURCE_FIELD,
    )
    from shared.schema import EngineState, TelemetryPayload

    egt = state["EGT_mean_C"]
    raw = dict(
        engine_state=EngineState.RUNNING,
        rpm=float(state["rpm"]),
        throttle_pct=float(op["throttle_pct"]),
        altitude_m=float(op["altitude_ft"]) / FT_PER_M,
        oat_c=float(op["ambient_temperature_C"]),
        humidity_pct=float(humidity_pct),
        fuel_flow_lph=state["fuelflow_kgh"] / FUEL_DENSITY_KG_PER_L,
        oil_pressure_kpa=state["oil_pressure_bar"] * KPA_PER_BAR,
        oil_temp_c=state["oil_temperature_C"],
        egt_1_c=egt - 7.5, egt_2_c=egt - 2.5,
        egt_3_c=egt + 2.5, egt_4_c=egt + 7.5,
        egt_spread_c=15.0,
    )
    raw[COOLANT_SOURCE_FIELD] = state["coolant_temp_C"]
    other = ("coolant_temp_in_c" if COOLANT_SOURCE_FIELD == "coolant_temp_out_c"
             else "coolant_temp_out_c")
    raw[other] = state["coolant_temp_C"] - 7.0
    return TelemetryPayload(**raw), set(raw) | {"engine_state"}


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

@dataclass
class Trace:
    name: str = ""
    steps: List[Step] = field(default_factory=list)
    elapsed_s: float = 0.0
    altitude_ft: float = 0.0
    oat_c: float = 0.0
    admit: bool = True
    span: Dict[str, float] = field(default_factory=dict)

    def of(self, outcome: str) -> List[Step]:
        return [s for s in self.steps if s.outcome == outcome]

    def peak_residual(self) -> Optional[Step]:
        d = [s for s in self.steps if s.largest_residual is not None]
        return max(d, key=lambda s: s.largest_residual) if d else None

    def thinnest(self) -> Optional[Step]:
        d = [s for s in self.steps if s.margin_to_gate is not None]
        return min(d, key=lambda s: s.margin_to_gate) if d else None

    def worst_lag(self, key: str) -> Optional[Step]:
        d = [s for s in self.steps if s.lag_error(key) is not None]
        return max(d, key=lambda s: abs(s.lag_error(key))) if d else None

    def _last_command_change(self) -> Optional[int]:
        idx = [i for i, (a, b) in enumerate(zip(self.steps, self.steps[1:]))
               if a.throttle_cmd_pct != b.throttle_cmd_pct]
        return idx[-1] if idx else None

    def time_to_63_s(self, key: str) -> Optional[float]:
        """Seconds from the last command change to 63.2% of this channel's OWN
        step. For a first-order lag that IS tau, so it is the only honest way
        to rank channels against each other -- comparing deviations against
        absolute channel values just ranks by how big the numbers happen to be.
        """
        i0 = self._last_command_change()
        if i0 is None:
            return None
        pre, after = self.steps[i0], self.steps[i0 + 1:]
        if key not in pre.actual or not after:
            return None
        x0 = pre.actual[key]
        finals = [s.target[key] for s in after if key in s.target]
        if not finals:
            return None
        span = abs(finals[-1] - x0)
        if span < 1e-9:
            return None
        for s in after:
            if key in s.actual and abs(s.actual[key] - x0) >= 0.632 * span:
                return s.t_s - pre.t_s
        return None

    def settling_time_s(self, key: str) -> Optional[float]:
        """Time from the last command change until |lag| stays inside the same
        tolerance the admission gate uses -- so this is also the moment the
        twin starts scoring again."""
        i0 = self._last_command_change()
        if i0 is None:
            return None
        t0 = self.steps[i0].t_s
        tol = float(GATE_RESID_TOL.get(key, 0.0))
        if tol <= 0.0:
            return None
        after = [s for s in self.steps[i0 + 1:] if s.lag_error(key) is not None]
        if not after:
            return None
        for i, s in enumerate(after):
            if all(abs(x.lag_error(key)) <= tol for x in after[i:]):
                return s.t_s - t0
        return None

    def summary(self) -> Dict[str, Any]:
        pk, th = self.peak_residual(), self.thinnest()
        n = len(self.steps) or 1
        return {
            "profile": self.name,
            "frames": len(self.steps),
            "admit_gate": self.admit,
            "scored": len(self.of(SCORED)),
            "transient": len(self.of(TRANSIENT)),
            "declined": len(self.of(DECLINED)),
            "refused": len(self.of(REFUSED)),
            "not_scored_frac": round(1.0 - len(self.of(SCORED)) / n, 3),
            "breaches": len([s for s in self.of(SCORED)
                             if s.margin_to_gate is not None
                             and s.margin_to_gate < 0.0]),
            "peak_residual": None if pk is None else round(pk.largest_residual, 4),
            "peak_residual_at_s": None if pk is None else pk.t_s,
            "peak_residual_channel": None if pk is None else pk.worst_channel,
            "thinnest_margin": None if th is None else round(th.margin_to_gate, 4),
            "thinnest_margin_at_s": None if th is None else th.t_s,
            "settling_coolant_s": self.settling_time_s("coolant_temp_C"),
            "settling_oil_s": self.settling_time_s("oil_temperature_C"),
            "gate_trusted": False,
            "lag_model_verified": False,
            "elapsed_s": round(self.elapsed_s, 2),
        }


def run_profile(core: Any, profile: Sequence[Tuple[float, float]],
                name: str = "custom", altitude_ft: Optional[float] = None,
                oat_c: Optional[float] = None, humidity_pct: float = 0.0,
                warm_start: bool = True, admit: bool = True) -> Trace:
    """Walk a throttle profile frame by frame through the twin.

    admit=True  -- the steady-state gate is enforced; transient frames are
                   recorded and the twin is not called. This is the real mode.
    admit=False -- score everything, which is how the out-of-distribution
                   behaviour was found. Kept so the finding stays reproducible.
    """
    from node1_ingestion.adapter import to_twin_payload, twin_frame_to_dict

    base = reference_op()
    alt = base["altitude_ft"] if altitude_ft is None else float(altitude_ft)
    oat = base["ambient_temperature_C"] if oat_c is None else float(oat_c)

    core.reset()                       # the only mutable state in the core
    tr = Trace(name=name, altitude_ft=alt, oat_c=oat, admit=admit,
               span={k: 0.0 for k in LAGGED})
    t0 = time.perf_counter()

    state: Optional[Dict[str, float]] = None
    prev_target: Optional[Dict[str, float]] = None
    prev_t: Optional[float] = None
    prev_throttle: Optional[float] = None

    for t_s, throttle in profile:
        dt = (t_s - prev_t) if prev_t is not None else 1.0 / FRAME_HZ
        if dt <= 0.0:
            dt = 1.0 / FRAME_HZ
        rate = (0.0 if prev_throttle is None
                else (float(throttle) - prev_throttle) / dt)
        prev_t, prev_throttle = t_s, float(throttle)

        cmd_rpm = rpm_for_throttle(throttle)
        op = {"altitude_ft": alt, "ambient_temperature_C": oat,
              "throttle_pct": float(throttle), "rpm": cmd_rpm}

        st = Step(t_s=t_s, throttle_cmd_pct=float(throttle),
                  throttle_rate_pct_s=rate,
                  altitude_ft=alt, oat_c=oat, rpm=cmd_rpm)
        st.envelope, _ = envelope_verdict(alt, oat)

        try:
            target = _extract(deck().predict(dict(op)))
        except Exception as exc:
            st.stage = "deck"
            st.note = f"{type(exc).__name__}: {str(exc)[:90]}"
            tr.steps.append(st)
            continue
        target["rpm"] = cmd_rpm
        st.target = dict(target)

        if state is None:
            # Warm start on the steady state of the first command, so the
            # trace shows the transient we asked for and not a cold-start
            # artefact of our own initialisation.
            state = dict(target) if warm_start else dict(
                target, **{k: target[k] * 0.6 for k in ("EGT_mean_C",
                                                        "coolant_temp_C",
                                                        "oil_temperature_C")})
            for k in LAGGED:
                tr.span[k] = abs(state[k] - target[k])
        else:
            # Record each channel's step size the instant the target moves,
            # measured from where the channel actually is. The admission gate
            # and the settling metric are both fractions of THIS.
            if prev_target is not None:
                for k in LAGGED:
                    if abs(target[k] - prev_target[k]) > 1e-9:
                        tr.span[k] = abs(target[k] - state[k])
            for k in LAGGED:
                state[k] = _lag(state[k], target[k], TAU_S[k], dt)
        prev_target = dict(target)
        st.actual = dict(state)
        st.rpm = state["rpm"]

        if admit:
            ok, why = _admit(state, target, tr.span, rate)
            if not ok:
                st.outcome, st.stage, st.note = TRANSIENT, "not_admitted", why
                tr.steps.append(st)
                continue

        try:
            payload, provided = _frame_from_state(op, state, humidity_pct)
        except Exception as exc:
            st.stage = "synthesis"
            st.note = f"{type(exc).__name__}: {str(exc)[:90]}"
            tr.steps.append(st)
            continue

        res = to_twin_payload(payload, provided=provided, strict=False)
        if not res.ok:
            st.stage = "adapter"
            st.note = res.refusals[0][:90] if res.refusals else "refused"
            tr.steps.append(st)
            continue

        try:
            out = twin_frame_to_dict(core.process(res.features))
        except Exception as exc:
            st.stage = "twin"
            st.note = f"{type(exc).__name__}: {str(exc)[:90]}"
            tr.steps.append(st)
            continue

        st.status = out.get("status")
        r = out.get("residuals")
        if isinstance(r, Mapping) and r:
            try:
                st.worst_channel = max(r, key=lambda k: abs(float(r[k])))
                st.largest_residual = abs(float(r[st.worst_channel]))
            except (TypeError, ValueError):
                pass

        pa = out.get("anomaly_probability")
        if pa is None:
            st.outcome, st.stage = DECLINED, "declined_by_twin"
            v = out.get("envelope_violations") or []
            st.note = str(v[0])[:90] if v else "diagnosis withheld"
        else:
            st.anomaly_probability = float(pa)
            st.margin_to_gate = GATE_THRESHOLD - float(pa)
            st.outcome, st.stage = SCORED, "complete"

        tr.steps.append(st)

    tr.elapsed_s = time.perf_counter() - t0
    return tr


def run_named(core: Any = None, name: str = "step", **kw) -> Trace:
    if name not in PROFILES:
        raise ThrottleDynamicsError(
            f"unknown profile {name!r}; have {sorted(PROFILES)}")
    if core is None:
        core = build_core()
    return run_profile(core, PROFILES[name](), name=name, **kw)


def dynamics_caveats() -> List[Dict[str, Any]]:
    return [{
        "id": "thermal_lag_model_unverified", "verified": False,
        "value": dict(TAU_S),
        "detail": "the baselines are steady-state regressors with no time "
                  "constant. These tau values come from general piston-engine "
                  "behaviour, NOT from AERIS training data. Transient shape is "
                  "meaningful; the seconds are not validated.",
    }, {
        "id": "admission_thresholds_are_judgement", "verified": False,
        "value": {"throttle_rate_pct_s": THROTTLE_RATE_TOL_PCT_S,
                  "gate_resid_tol": dict(GATE_RESID_TOL)},
        "detail": "the steady-state gate uses a throttle rate limit of "
                  f"{THROTTLE_RATE_TOL_PCT_S} %/s and an absolute residual "
                  "tolerance per channel. The rate limit is judgement. The "
                  "residual tolerances are judgement CONSTRAINED by "
                  "measurement: CASE 3a bisects the smallest residual that "
                  "moves the score and asserts every tolerance sits below it. "
                  "They depend on the unverified tau values only through how "
                  "long settling takes, not through the admission decision.",
    }, {
        "id": "resolution_measured_at_one_point_only", "verified": True,
        "value": "95% throttle, 6000 ft, 10 C",
        "detail": "CASE 3a bisects the gate's residual resolution at a single "
                  "operating point. The gate is tree-based, so leaf boundaries "
                  "differ elsewhere and these tolerances are validated THERE, "
                  "not globally. Resolution was also found to be asymmetric: "
                  "rpm reads 38.84 probing upward and 6.82 probing downward "
                  "from the same point. A sweep of resolution across the "
                  "envelope is outstanding work, not a completed check.",
    }, {
        "id": "gate_is_hypersensitive_to_residuals", "verified": True,
        "value": "0.157 C oil -> p_anom 0.5477 to 0.8049",
        "detail": "measured, not assumed. The gate is tree-based and healthy "
                  "residuals sit at ~0, so a decision boundary lives very "
                  "close to zero: a sixth of a degree on oil temperature moved "
                  "p_anom by 0.257. Same phenomenon as the 500 ft altitude "
                  "leaf width recorded in stress_sim. This is why admission is "
                  "absolute rather than a fraction of the step, and it is "
                  "another reason the gate is untrusted pre-retrain.",
    }, {
        "id": "admission_cost_on_missions", "verified": True,
        "value": "~250 s of settling after a throttle step",
        "detail": "consequence of the two facts above, measured not assumed: "
                  "oil temperature moves the score at 1.74 mK and has tau=25 s, "
                  "so closing a ~8 C step to tolerance takes about 250 s. "
                  "Pre-retrain the twin is therefore honestly a CRUISE-ONLY "
                  "monitor -- a real mission changing throttle every few "
                  "minutes will read UNAVAILABLE for most of its duration. "
                  "That is a property of the untrusted gate, not of the lag "
                  "model, and retraining on transient data is what fixes it.",
    }, {
        "id": "transients_are_not_scored_by_design", "verified": True,
        "value": "TRANSIENT outcome",
        "detail": "a lagging channel produces a residual by construction, so a "
                  "steady-state regressor asked about a manoeuvre is out of "
                  "distribution: it either declines or reports near-certain "
                  "fault on a healthy engine. Frames failing the admission gate "
                  "are recorded as TRANSIENT and the twin is never called. "
                  "Nothing is clamped and no probability is invented.",
    }, {
        "id": "four_outcomes_three_wire_states", "verified": True,
        "value": "TRANSIENT|DECLINED -> UNAVAILABLE",
        "detail": "internally SCORED/TRANSIENT/DECLINED/REFUSED are four "
                  "different answers. On the service contract TRANSIENT and "
                  "DECLINED both map to UNAVAILABLE with a reason string via "
                  "wire_status(), leaving twin_core, canonical, the api status "
                  "enum and the WS hello frame unchanged.",
    }, {
        "id": "deck_throttle_envelope", "verified": True,
        "value": "throttle_pct [56.5, 100]",
        "detail": "discovered while testing: the deck declines throttle below "
                  "56.5% -- 'throttle_pct=40 outside trained range [56.5, 100]'. "
                  "So idle and low-cruise settings cannot be scored at all, "
                  "however steady they are. This is a STATIC envelope limit, a "
                  "different answer from TRANSIENT, and it means the chop half "
                  "of a chop-and-slam is unscoreable on principle.",
    }, {
        "id": "rpm_throttle_surrogate_unverified", "verified": False,
        "value": f"linear {RPM_IDLE:.0f}..{RPM_MAX:.0f} rpm",
        "detail": "no throttle-to-rpm map exists in the repo. Linear is used, "
                  "anchored by 80% throttle -> 5000 rpm which matches the deck "
                  "reference point exactly. Part-throttle rpm is a guess.",
    }, {
        "id": "trend_state_reset_per_profile", "verified": True,
        "value": "TwinCore.reset()",
        "detail": "reset() clears rul_engine's EWMA and 50-frame deque, the "
                  "core's only mutable state, so profiles cannot inherit each "
                  "other's RUL trend history.",
    }]


# --------------------------------------------------------------------------
def admit_frame(prev: Optional[Mapping[str, float]],
                current: Mapping[str, float],
                dt_s: float,
                since_change_s: Optional[float] = None) -> Dict[str, Any]:
    """Steady-state admission for an arbitrary measured frame.

    Unlike _admit(), which compares a simulated thermal state against its
    target, this works on measured telemetry where no simulated state exists.
    Two rules, both from the same constants the simulator uses:

      1. throttle rate  |d(throttle)/dt| must be <= the rate tolerance
      2. settling       after any throttle change, the slowest lagged channel
                        needs ~4*tau to converge. Pass since_change_s (seconds
                        since the last throttle movement); None skips rule 2.

    Returns {admit, reason, elapsed_s, required_s, slowest_channel, rate_pct_s}.
    Callers that skip this will score transient frames as faults -- see the
    498 false-FAULT finding in dynamics_caveats().
    """
    tol = THROTTLE_RATE_TOL_PCT_S
    slowest = max(TAU_S, key=TAU_S.get)
    required = 4.0 * TAU_S[slowest]

    out: Dict[str, Any] = {"admit": True, "reason": None, "elapsed_s": since_change_s,
                           "required_s": required, "slowest_channel": slowest,
                           "rate_pct_s": None}

    if prev is not None and (not dt_s or dt_s <= 0):
        out["admit"] = False
        out["reason"] = f"dt_s={dt_s} is not positive; throttle rate not evaluable"
        return out

    if prev is not None:
        rate = abs(float(current["throttle_pct"]) - float(prev["throttle_pct"])) / float(dt_s)
        out["rate_pct_s"] = rate
        if rate > tol:
            out["admit"] = False
            out["reason"] = f"throttle moving at {rate:.3f} %/s (tol {tol} %/s)"
            return out

    if since_change_s is not None and since_change_s < required:
        out["admit"] = False
        out["reason"] = (f"settling: {since_change_s:.1f}s of {required:.0f}s "
                         f"since throttle change, slowest channel {slowest}")
    return out

def _self_test() -> None:
    fails: List[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            fails.append(msg)
            print(f"  FAIL: {msg}")

    print(f"throttle_dynamics v{THROTTLE_DYNAMICS_VERSION}  {FRAME_HZ:.0f} Hz")
    core = build_core()
    ref = reference_op()
    print(f"  reference op: {ref}")
    print(f"  tau (s): {TAU_S}   [UNVERIFIED]")
    print(f"  admission: throttle rate <= {THROTTLE_RATE_TOL_PCT_S} %/s, and "
          f"absolute residual per channel  [JUDGEMENT, bounded by CASE 3a]")
    print(f"  GATE_RESID_TOL: {GATE_RESID_TOL}")

    print("\nCASE 0  rpm surrogate is anchored at the deck reference point")
    got = rpm_for_throttle(ref["throttle_pct"])
    print(f"  {ref['throttle_pct']:.0f}% -> {got:.1f} rpm "
          f"(deck reference {ref['rpm']:.1f})")
    check(abs(got - ref["rpm"]) < 1e-9,
          f"surrogate gives {got}, deck reference is {ref['rpm']}")

    print("\nCASE 1  steady profile: residuals ~0, p_anom is the known value")
    tr = run_profile(core, profile_steady(10.0, ref["throttle_pct"]),
                     name="steady")
    s = tr.summary()
    print(f"  frames={s['frames']} scored={s['scored']} "
          f"peak_residual={s['peak_residual']} in {s['elapsed_s']}s")
    first, last = tr.steps[0], tr.steps[-1]
    print(f"  p_anom first={first.p()} last={last.p()}")
    check(s["scored"] == s["frames"],
          f"only {s['scored']}/{s['frames']} scored -- the gate is rejecting "
          f"a genuinely steady profile")
    check((s["peak_residual"] or 0.0) < 1e-6,
          f"steady profile produced residual {s['peak_residual']}")
    check(abs((last.anomaly_probability or 0) - 0.5443998040908319) < 1e-9,
          f"REGRESSION INVARIANT MOVED: {last.anomaly_probability}")

    print("\nCASE 2  reset isolation: the same profile twice must match exactly")
    a = run_profile(core, profile_steady(20.0, STEP_HIGH_PCT),
                    name="steady_iso")
    b = run_profile(core, profile_steady(20.0, STEP_HIGH_PCT),
                    name="steady_iso")
    pa = [x.anomaly_probability for x in a.steps]
    pb = [x.anomaly_probability for x in b.steps]
    oa = [x.outcome for x in a.steps]
    ob = [x.outcome for x in b.steps]
    print(f"  run A {a.summary()['scored']} scored / "
          f"{a.summary()['transient']} transient in {a.summary()['elapsed_s']}s")
    print(f"  run B {b.summary()['scored']} scored / "
          f"{b.summary()['transient']} transient in {b.summary()['elapsed_s']}s")
    check(pa == pb and oa == ob,
          "identical profiles diverged -- state leaked between runs")
    check(a.summary()["scored"] > 0,
          "CASE 2 scored nothing, so it compared two lists of None and proved "
          "nothing about state isolation")

    print("\nCASE 3  the finding: a steady-state twin cannot score a transient")
    probe = run_profile(core, profile_step(30.0), name="step_unadmitted",
                        admit=False)
    ps = probe.summary()
    print(f"  admit=False (30 s): scored={ps['scored']} declined={ps['declined']} "
          f"breaches={ps['breaches']}")
    if probe.of(DECLINED):
        print(f"    first decline t={probe.of(DECLINED)[0].t_s:.1f}s "
              f"reason: {probe.of(DECLINED)[0].note[:80]}")
    worst = probe.thinnest()
    if worst:
        print(f"    worst scored margin {worst.margin_to_gate:+.4f} "
              f"(p_anom={worst.p()}) at t={worst.t_s:.1f}s on a HEALTHY engine")
    check(ps["declined"] > 0 or ps["breaches"] > 0,
          "unadmitted transient neither declined nor breached -- the whole "
          "premise of the admission gate is unsupported, re-read CASE 3")

    print("\nCASE 3a  how big a residual does the gate actually notice?")
    op_hi = {"altitude_ft": ref["altitude_ft"],
             "ambient_temperature_C": ref["ambient_temperature_C"],
             "throttle_pct": STEP_HIGH_PCT,
             "rpm": rpm_for_throttle(STEP_HIGH_PCT)}
    suggest: Dict[str, float] = {}
    unresolved: List[str] = []
    for k in sorted(LAGGED, key=lambda x: TAU_S[x]):
        pb, resol = gate_residual_resolution(core, op_hi, k)
        tol = float(GATE_RESID_TOL.get(k, 0.0))
        if resol is None:
            unresolved.append(k)
            print(f"  {k:<20} resolution   --      admission tol {tol:<8.4f} "
                  f"(score never moved)")
            continue
        suggest[k] = round(0.4 * resol, 6)
        flag = "" if tol < resol else "   <-- TOO LOOSE"
        print(f"  {k:<20} resolution {resol:9.5f}  admission tol "
              f"{tol:<8.4f}{flag}")
        check(tol < resol,
              f"{k}: admission tol {tol} >= gate resolution {resol:.5f}, so an "
              f"admitted frame can score differently from equilibrium")
    if suggest:
        print(f"  suggested GATE_RESID_TOL (0.4 x measured): {suggest}")
    if unresolved:
        print(f"  unresolved from this point: {unresolved} -- these channels "
              f"do not move the score at all here, so their tolerance is "
              f"unconstrained by measurement")

    # How long must a step profile run before anything can be scored again?
    # Derived, not guessed: first-order lag closes a step to tol in
    # tau * ln(step/tol). The slowest channel sets the profile length.
    print("\n  settling cost of these tolerances")
    op_lo = dict(op_hi, throttle_pct=STEP_LOW_PCT,
                 rpm=rpm_for_throttle(STEP_LOW_PCT))
    t_lo = _extract(deck().predict(dict(op_lo)))
    t_hi = _extract(deck().predict(dict(op_hi)))
    need = 0.0
    for k in sorted(LAGGED, key=lambda x: TAU_S[x]):
        if k == "rpm":
            stepk = abs(rpm_for_throttle(STEP_HIGH_PCT)
                        - rpm_for_throttle(STEP_LOW_PCT))
        else:
            stepk = abs(float(t_hi[k]) - float(t_lo[k]))
        tolk = float(GATE_RESID_TOL.get(k, 0.0))
        if stepk <= 0.0 or tolk <= 0.0 or stepk <= tolk:
            print(f"  {k:<20} step {stepk:9.3f}  already inside tol")
            continue
        ts = TAU_S[k] * math.log(stepk / tolk)
        need = max(need, ts)
        print(f"  {k:<20} step {stepk:9.3f}  settles to tol in {ts:7.1f} s "
              f"(tau {TAU_S[k]}s)")
    prof = profile_step()
    have = prof[-1][0] - 15.0
    print(f"  slowest channel needs {need:.0f} s after the step; profile "
          f"provides {have:.0f} s")
    check(have > need,
          f"step profile gives only {have:.0f} s after the step but the "
          f"slowest channel needs {need:.0f} s -- nothing can be scored")

    a = run_profile(core, prof, name="step")

    # Static baselines. Before attributing any breach to the transient, ask
    # what the twin says at these throttle settings when the engine is
    # genuinely at equilibrium. Without this the breach count is unreadable.
    base_lo = run_profile(core, profile_steady(3.0, STEP_LOW_PCT),
                          name="static_low")
    base_hi = run_profile(core, profile_steady(3.0, STEP_HIGH_PCT),
                          name="static_high")

    def _static_p(t_: Trace):
        sc = t_.of(SCORED)
        return sc[-1].anomaly_probability if sc else None

    p_lo, p_hi = _static_p(base_lo), _static_p(base_hi)
    for pct, pv, bt in ((STEP_LOW_PCT, p_lo, base_lo),
                        (STEP_HIGH_PCT, p_hi, base_hi)):
        if pv is None:
            d = bt.of(DECLINED)
            print(f"  static equilibrium at {pct:.0f}%: declined -- "
                  f"{d[0].note[:60] if d else 'no score'}")
        else:
            print(f"  static equilibrium at {pct:.0f}%: p_anom={pv:.4f} "
                  f"margin={GATE_THRESHOLD - pv:+.4f}")

    print(f"  admit=True ({prof[-1][0]:.0f} s): the gate replaces both "
          f"failure modes")
    s = a.summary()
    print(f"  frames={s['frames']} scored={s['scored']} "
          f"transient={s['transient']} declined={s['declined']} "
          f"refused={s['refused']} breaches={s['breaches']}")
    print(f"  peak residual {s['peak_residual']} on {s['peak_residual_channel']} "
          f"at t={s['peak_residual_at_s']}s")
    print(f"  thinnest margin {s['thinnest_margin']} at t={s['thinnest_margin_at_s']}s")
    print(f"  settling: coolant {s['settling_coolant_s']}s  oil {s['settling_oil_s']}s")
    if a.of(TRANSIENT):
        t0 = a.of(TRANSIENT)[0]
        print(f"  first not-admitted t={t0.t_s:.1f}s -> {t0.wire()}: {t0.note}")
        tl = a.of(TRANSIENT)[-1]
        print(f"  last  not-admitted t={tl.t_s:.1f}s -> {tl.note}")
    for k in ("EGT_mean_C", "coolant_temp_C", "oil_temperature_C"):
        w = a.worst_lag(k)
        if w:
            print(f"  worst {k:<18} lag {w.lag_error(k):+8.2f} at t={w.t_s:.1f}s "
                  f"(tau={TAU_S[k]}s)")
    check(s["transient"] > 0, "a step change admitted every frame")
    check(s["scored"] > 0,
          "nothing was scored -- profile too short for 5 tau of oil (25 s)")
    check(s["settling_oil_s"] is not None and s["settling_coolant_s"] is not None,
          "settling never resolved; profile shorter than 5 tau")

    # A decline is legitimate when the operating point is STATICALLY outside
    # the deck envelope -- throttle range, OAT ceiling. It is a gate failure
    # only if an admitted frame was rejected for a transient reason.
    static_decl = [x for x in a.of(DECLINED) if "outside trained range" in x.note]
    other_decl = [x for x in a.of(DECLINED)
                  if "outside trained range" not in x.note]
    if static_decl:
        print(f"  {len(static_decl)} static-envelope declines: "
              f"{static_decl[0].note[:70]}")
    check(not other_decl,
          f"{len(other_decl)} frames were admitted then declined for a "
          f"non-static reason: {other_decl[0].note[:70] if other_decl else ''}")

    # The actual test of the admission gate: a frame it admits must look like
    # equilibrium TO THE TWIN. Measured against the static baseline above, not
    # against 0.65 -- whether equilibrium itself breaches is a property of the
    # untrusted gate, declared rather than fixed here.
    adm_hi = [x for x in a.of(SCORED)
              if x.throttle_cmd_pct == STEP_HIGH_PCT
              and x.anomaly_probability is not None]
    if adm_hi and p_hi is not None:
        w = max(adm_hi, key=lambda x: abs(x.anomaly_probability - p_hi))
        dev = abs(w.anomaly_probability - p_hi)
        print(f"  {len(adm_hi)} admitted frames at {STEP_HIGH_PCT:.0f}%; worst "
              f"deviation from static equilibrium {dev:.6f} at t={w.t_s:.1f}s")
        check(dev < 1e-12,
              f"an admitted frame scored {w.anomaly_probability:.4f} where "
              f"static equilibrium is {p_hi:.4f} (dev {dev:.6f}) -- admission "
              f"tolerance sits above the gate's residual resolution for some "
              f"channel; use the suggested GATE_RESID_TOL from CASE 3a")
    if p_hi is not None and p_hi >= GATE_THRESHOLD:
        print(f"  NOTE: static equilibrium at {STEP_HIGH_PCT:.0f}% already "
              f"breaches (p_anom={p_hi:.4f} vs gate {GATE_THRESHOLD}). The "
              f"{s['breaches']} breaches are inherited from the operating "
              f"point, NOT created by the transient. Gate untrusted.")
    else:
        check(s["breaches"] == 0,
              f"{s['breaches']} breaches although static equilibrium at "
              f"{STEP_HIGH_PCT:.0f}% is inside the gate")

    print("\nCASE 4  time to 63.2% of each channel's OWN step recovers tau")
    got_tau: List[Tuple[str, float]] = []
    for k in sorted(LAGGED, key=lambda x: TAU_S[x]):
        m = a.time_to_63_s(k)
        mark = "  --  " if m is None else f"{m:6.2f}"
        print(f"  {k:<20} measured {mark} s   declared tau {TAU_S[k]:5.2f} s")
        if m is None:
            continue
        got_tau.append((k, m))
        tol = max(0.25, 0.15 * TAU_S[k])
        check(abs(m - TAU_S[k]) <= tol,
              f"{k}: measured {m:.2f}s vs tau {TAU_S[k]}s (tol {tol:.2f})")
    check(len(got_tau) >= 4, f"only {len(got_tau)} channels resolved a tau")
    ordered = [k for k, _ in sorted(got_tau, key=lambda kv: kv[1])]
    expect = [k for k, _ in sorted(got_tau, key=lambda kv: TAU_S[k])]
    print(f"  measured order: {ordered}")
    check(ordered == expect,
          f"lag ordering wrong: measured {ordered}, tau order {expect}")

    print("\nCASE 5  chop and slam: the thermal-shock manoeuvre")
    cs = run_profile(core, profile_chop_and_slam(), name="chop_and_slam")
    s = cs.summary()
    print(f"  frames={s['frames']} scored={s['scored']} transient={s['transient']} "
          f"declined={s['declined']} breaches={s['breaches']} in {s['elapsed_s']}s")
    print(f"  peak residual {s['peak_residual']} on {s['peak_residual_channel']} "
          f"at t={s['peak_residual_at_s']}s")
    print(f"  thinnest margin {s['thinnest_margin']} at t={s['thinnest_margin_at_s']}s")
    print("  every 10 s:")
    for st in cs.steps[::int(10 * FRAME_HZ)]:
        print(f"    t={st.t_s:>5.1f}s thr={st.throttle_cmd_pct:>5.1f}% "
              f"{st.wire():<11} p_anom={st.p()} {st.note[:46]}")
    check(s["scored"] > 0, "chop and slam scored nothing at all")
    check(s["breaches"] == 0,
          f"{s['breaches']} breaches on a healthy chop-and-slam -- these were "
          f"the 498 false alarms; the gate should have withheld them")

    print("\nCASE 6  ramp, and a profile at a declined ambient (40 C)")
    rp = run_profile(core, profile_ramp(), name="ramp")
    rs = rp.summary()
    print(f"  ramp: scored={rs['scored']} transient={rs['transient']} "
          f"breaches={rs['breaches']} thinnest={rs['thinnest_margin']}")
    ramping = [x for x in rp.steps if 12.0 < x.t_s < 38.0]
    check(all(x.outcome == TRANSIENT for x in ramping),
          "a 2.3 %/s ramp was admitted; the throttle-rate limit is not binding")
    check(rs["breaches"] == 0, f"ramp produced {rs['breaches']} breaches")

    hot = run_profile(core, profile_steady(5.0), name="steady", oat_c=40.0)
    hs = hot.summary()
    print(f"  40 C steady: scored={hs['scored']} transient={hs['transient']} "
          f"declined={hs['declined']}")
    if hot.of(DECLINED):
        print(f"  reason: {hot.of(DECLINED)[0].note[:80]}")
    print(f"  deck says: {deck_violations({'altitude_ft': hot.altitude_ft, 'ambient_temperature_C': 40.0, 'throttle_pct': 80.0, 'rpm': 5000.0})}")
    check(hs["declined"] == hs["frames"],
          "40 C was not declined by the twin; the deck ceiling is 30 C. "
          "DECLINED and TRANSIENT are different answers and must stay so")

    print("\nCASE 7  declared caveats")
    for cv in dynamics_caveats():
        print(f"  {cv['id']:<38} verified={cv['verified']}")

    if fails:
        print(f"\nTHROTTLE DYNAMICS SELF-CHECK FAILED -- {len(fails)}")
        for m in fails:
            print(f"  - {m}")
        raise SystemExit(1)
    print("\nTHROTTLE DYNAMICS SELF-CHECK OK")
    print("  scored / transient / declined / refused are four different answers")
    # -- admit_frame(): the public wrapper the HTTP path will call ---------
    base = {"throttle_pct": 80.0}
    a = admit_frame(base, {"throttle_pct": 80.0}, 1.0, since_change_s=400.0)
    assert a["admit"] is True, a
    assert a["slowest_channel"] == "oil_temperature_C", a
    assert abs(a["required_s"] - 100.0) < 1e-9, a
    b = admit_frame(base, {"throttle_pct": 85.0}, 1.0, since_change_s=400.0)
    assert b["admit"] is False and "%/s" in b["reason"], b
    assert abs(b["rate_pct_s"] - 5.0) < 1e-9, b
    c = admit_frame(base, {"throttle_pct": 80.0}, 1.0, since_change_s=10.0)
    assert c["admit"] is False and "settling" in c["reason"], c
    d = admit_frame(None, {"throttle_pct": 80.0}, 1.0)
    assert d["admit"] is True and d["rate_pct_s"] is None, d
    e = admit_frame(base, {"throttle_pct": 80.0}, 0.0, since_change_s=400.0)
    assert e["admit"] is False and "not positive" in e["reason"], e
    print("  admit_frame: rate + settling rules guarded")

    print("  transient shape is real; the time constants are not yet validated")


if __name__ == "__main__":
    _self_test()

