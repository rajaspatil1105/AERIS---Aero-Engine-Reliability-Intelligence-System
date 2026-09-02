"""AERIS throttle dynamics -- transient response of the twin.

The stress simulator maps competence over a STATIC grid. This module walks a
throttle-versus-time profile and reports what the twin sees during the
transient: how far the slow channels lag their steady-state targets, whether
p_anom spikes mid-manoeuvre, and at which timestep the trajectory leaves
model competence.

WHAT IS AND IS NOT VALIDATED HERE
The baselines are STEADY-STATE regressors. BaselineDeck.predict() returns
equilibrium values for an operating point; there is no time constant anywhere
in node2 (rul_engine's EWMA smooths RUL, it is not a thermal model). So the
first-order lag applied below is a physical model layered ON TOP of your data,
with time constants chosen from general piston-engine knowledge. Transient
SHAPE is meaningful; the specific seconds are UNVERIFIED and need real Rotax
915 iS transient data or a Cantera transient run to pin down.

THE CENTRAL CONSEQUENCE
Residuals are zero when a frame matches the steady-state prediction. Lag is
therefore precisely what CREATES residuals during a transient: a real engine
mid-slam genuinely runs coolant below equilibrium. The twin will see that and
may score it anomalous. Whether that is a true positive or a false alarm is a
question the current gate cannot answer -- a known-healthy point already sits
at 0.5444 against a 0.65 threshold. Treat transient p_anom excursions as
observations, not verdicts.

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

THROTTLE_DYNAMICS_VERSION = "0.1.0"
FRAME_HZ = 10.0                  # matches RulEngine(frame_hz=10.0)

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


# --------------------------------------------------------------------------
# profiles: throttle command as a function of time
# --------------------------------------------------------------------------

def profile_steady(seconds: float = 20.0, throttle_pct: float = 80.0
                   ) -> List[Tuple[float, float]]:
    n = int(seconds * FRAME_HZ)
    return [(i / FRAME_HZ, throttle_pct) for i in range(n)]


def profile_step(seconds: float = 60.0, low: float = 40.0, high: float = 95.0,
                 step_at_s: float = 15.0) -> List[Tuple[float, float]]:
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

    def settling_time_s(self, key: str, tol_frac: float = 0.02
                        ) -> Optional[float]:
        """Time from the last command change until |lag| stays within tol."""
        changes = [s.t_s for a, s in zip(self.steps, self.steps[1:])
                   if a.throttle_cmd_pct != s.throttle_cmd_pct]
        if not changes:
            return None
        t0 = changes[-1]
        after = [s for s in self.steps if s.t_s >= t0
                 and s.lag_error(key) is not None]
        if not after:
            return None
        for i, s in enumerate(after):
            scale = abs(s.target.get(key, 0.0)) or 1.0
            if all(abs(x.lag_error(key)) / scale <= tol_frac for x in after[i:]):
                return s.t_s - t0
        return None

    def summary(self) -> Dict[str, Any]:
        pk, th = self.peak_residual(), self.thinnest()
        return {
            "profile": self.name,
            "frames": len(self.steps),
            "scored": len(self.of(SCORED)),
            "declined": len(self.of(DECLINED)),
            "refused": len(self.of(REFUSED)),
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
                warm_start: bool = True) -> Trace:
    """Walk a throttle profile frame by frame through the twin."""
    from node1_ingestion.adapter import to_twin_payload, twin_frame_to_dict

    base = reference_op()
    alt = base["altitude_ft"] if altitude_ft is None else float(altitude_ft)
    oat = base["ambient_temperature_C"] if oat_c is None else float(oat_c)

    core.reset()                       # the only mutable state in the core
    tr = Trace(name=name, altitude_ft=alt, oat_c=oat)
    t0 = time.perf_counter()

    state: Optional[Dict[str, float]] = None
    prev_t: Optional[float] = None

    for t_s, throttle in profile:
        cmd_rpm = rpm_for_throttle(throttle)
        op = {"altitude_ft": alt, "ambient_temperature_C": oat,
              "throttle_pct": float(throttle), "rpm": cmd_rpm}

        st = Step(t_s=t_s, throttle_cmd_pct=float(throttle),
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
        else:
            dt = (t_s - prev_t) if prev_t is not None else 1.0 / FRAME_HZ
            for k in LAGGED:
                state[k] = _lag(state[k], target[k], TAU_S[k], dt)
        prev_t = t_s
        st.actual = dict(state)
        st.rpm = state["rpm"]

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
        "id": "rpm_throttle_surrogate_unverified", "verified": False,
        "value": f"linear {RPM_IDLE:.0f}..{RPM_MAX:.0f} rpm",
        "detail": "no throttle-to-rpm map exists in the repo. Linear is used, "
                  "anchored by 80% throttle -> 5000 rpm which matches the deck "
                  "reference point exactly. Part-throttle rpm is a guess.",
    }, {
        "id": "transient_residuals_are_expected", "verified": True,
        "value": "lag creates residuals",
        "detail": "residuals are ~0 only at steady state. A lagging channel "
                  "produces a residual by construction, exactly as a real "
                  "engine does mid-manoeuvre. p_anom excursions during a "
                  "transient are observations, not fault verdicts -- the gate "
                  "is untrusted pre-retrain (healthy point sits at 0.5444).",
    }, {
        "id": "trend_state_reset_per_profile", "verified": True,
        "value": "TwinCore.reset()",
        "detail": "reset() clears rul_engine's EWMA and 50-frame deque, the "
                  "core's only mutable state, so profiles cannot inherit each "
                  "other's RUL trend history.",
    }]


# --------------------------------------------------------------------------
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

    print("\nCASE 0  rpm surrogate is anchored at the deck reference point")
    got = rpm_for_throttle(ref["throttle_pct"])
    print(f"  {ref['throttle_pct']:.0f}% -> {got:.1f} rpm "
          f"(deck reference {ref['rpm']:.1f})")
    check(abs(got - ref["rpm"]) < 1e-9,
          f"surrogate gives {got}, deck reference is {ref['rpm']}")

    print("\nCASE 1  steady profile: residuals stay ~0 and p_anom is the known value")
    tr = run_profile(core, profile_steady(10.0, ref["throttle_pct"]),
                     name="steady")
    s = tr.summary()
    print(f"  frames={s['frames']} scored={s['scored']} "
          f"peak_residual={s['peak_residual']} in {s['elapsed_s']}s")
    first, last = tr.steps[0], tr.steps[-1]
    print(f"  p_anom first={first.p()} last={last.p()}")
    check(s["scored"] == s["frames"], f"only {s['scored']}/{s['frames']} scored")
    check((s["peak_residual"] or 0.0) < 1e-6,
          f"steady profile produced residual {s['peak_residual']}")
    check(abs((last.anomaly_probability or 0) - 0.5443998040908319) < 1e-9,
          f"steady p_anom drifted: {last.anomaly_probability}")

    print("\nCASE 2  reset isolation: the same profile twice must match exactly")
    a = run_profile(core, profile_step(30.0), name="step")
    b = run_profile(core, profile_step(30.0), name="step")
    pa = [x.anomaly_probability for x in a.steps]
    pb = [x.anomaly_probability for x in b.steps]
    print(f"  run A peak residual {a.summary()['peak_residual']}, "
          f"run B {b.summary()['peak_residual']}")
    check(pa == pb, "identical profiles diverged -- state leaked between runs")

    print("\nCASE 3  step 40->95%: lag, peak residual, settling")
    s = a.summary()
    print(f"  peak residual {s['peak_residual']} on {s['peak_residual_channel']} "
          f"at t={s['peak_residual_at_s']}s")
    print(f"  thinnest margin {s['thinnest_margin']} at t={s['thinnest_margin_at_s']}s")
    print(f"  settling: coolant {s['settling_coolant_s']}s  "
          f"oil {s['settling_oil_s']}s")
    for k in ("EGT_mean_C", "coolant_temp_C", "oil_temperature_C"):
        w = a.worst_lag(k)
        if w:
            print(f"  worst {k:<18} lag {w.lag_error(k):+8.2f} at t={w.t_s:.1f}s "
                  f"(tau={TAU_S[k]}s)")
    check((s["peak_residual"] or 0.0) > 0.0,
          "a step change produced no residual -- lag not applied")
    check(s["declined"] == 0 and s["refused"] == 0,
          f"step profile lost frames: {s['declined']} declined, {s['refused']} refused")

    print("\nCASE 4  slow channels lag more than fast ones")
    egt = a.worst_lag("EGT_mean_C")
    oil = a.worst_lag("oil_temperature_C")
    if egt and oil:
        re = abs(egt.lag_error("EGT_mean_C")) / max(abs(egt.target["EGT_mean_C"]), 1.0)
        ro = abs(oil.lag_error("oil_temperature_C")) / max(abs(oil.target["oil_temperature_C"]), 1.0)
        print(f"  EGT relative lag {re:.4f} (tau {TAU_S['EGT_mean_C']}s)")
        print(f"  oil relative lag {ro:.4f} (tau {TAU_S['oil_temperature_C']}s)")
        check(ro > re, "oil did not lag more than EGT despite a longer tau")

    print("\nCASE 5  chop and slam: the thermal-shock manoeuvre")
    cs = run_profile(core, profile_chop_and_slam(), name="chop_and_slam")
    s = cs.summary()
    print(f"  frames={s['frames']} scored={s['scored']} declined={s['declined']} "
          f"breaches={s['breaches']} in {s['elapsed_s']}s")
    print(f"  peak residual {s['peak_residual']} on {s['peak_residual_channel']} "
          f"at t={s['peak_residual_at_s']}s")
    print(f"  thinnest margin {s['thinnest_margin']} at t={s['thinnest_margin_at_s']}s")
    print("  p_anom every 10 s:")
    for st in cs.steps[::int(10 * FRAME_HZ)]:
        print(f"    t={st.t_s:>5.1f}s thr={st.throttle_cmd_pct:>5.1f}% "
              f"p_anom={st.p()} resid={st.largest_residual} "
              f"{st.worst_channel or ''}")
    check(s["scored"] > 0, "chop and slam scored nothing")

    print("\nCASE 6  ramp, and a profile at a declined ambient (40 C)")
    rp = run_profile(core, profile_ramp(), name="ramp")
    print(f"  ramp: peak residual {rp.summary()['peak_residual']}, "
          f"thinnest margin {rp.summary()['thinnest_margin']}")
    hot = run_profile(core, profile_steady(5.0), name="steady", oat_c=40.0)
    hs = hot.summary()
    print(f"  40 C steady: scored={hs['scored']} declined={hs['declined']}")
    if hot.of(DECLINED):
        print(f"  reason: {hot.of(DECLINED)[0].note[:80]}")
    check(hs["declined"] == hs["frames"],
          "40 C was scored; the deck ceiling is 30 C")

    print("\nCASE 7  declared caveats")
    for cv in dynamics_caveats():
        print(f"  {cv['id']:<38} verified={cv['verified']}")

    if fails:
        print(f"\nTHROTTLE DYNAMICS SELF-CHECK FAILED -- {len(fails)}")
        for m in fails:
            print(f"  - {m}")
        raise SystemExit(1)
    print("\nTHROTTLE DYNAMICS SELF-CHECK OK")
    print("  transient shape is real; the time constants are not yet validated")


if __name__ == "__main__":
    _self_test()
