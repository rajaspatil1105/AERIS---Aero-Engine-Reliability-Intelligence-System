"""AERIS environmental stress simulator.

At what ambient conditions does the engine lose margin to the anomaly gate,
asked before the mission flies.

THREE OUTCOMES PER CELL, and the distinction is the whole point:

  scored    the twin returned a probability; margin is meaningful
  declined  the twin refused to diagnose because the point is outside its
            training envelope (status UNAVAILABLE, ml_evaluated False). This
            is CORRECT behaviour and a first-class pre-flight result: "the
            twin cannot judge a 40 C takeoff" is exactly what a pilot needs
            to know. Not an error.
  refused   something actually went wrong: synthesis, adapter bounds, or the
            -6000 ft density-altitude floor.

DESIGN NOTE (v0.1.0 mistake, kept as a warning):
A cell may NOT hold one frame fixed and move only altitude/OAT. The baselines
predict expected coolant, fuel, EGT and oil *from* the operating point, so a
frame healthy at 6000 ft is grossly unhealthy at 10000 ft / -20 C. v0.1.0 did
that and all 18 cells read FAULT at p_anom ~0.98 -- the twin was right, the
sweep was meaningless. Each cell now asks BaselineDeck.predict() what healthy
looks like AT THAT POINT, so residuals are ~0 by construction and p_anom
varies only with operating-point position. This does NOT model a degrading
engine; it maps where the models still have competence.

Weather reaches the twin only through DENSITY ALTITUDE -- humidity, ambient
pressure and dewpoint are not twin features. Declared UNVERIFIED: the training
data was generated at geometric altitude with a floor of exactly sea level,
so any cold day (negative DA) is extrapolation by construction.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from shared import atmosphere as atm

STRESS_SIM_VERSION = "0.3.0"
GATE_THRESHOLD = 0.65
DECK_REFERENCE_P_ANOM = 0.5443998040908319

OP_KEYS = ("altitude_ft", "ambient_temperature_C", "throttle_pct", "rpm")
DEPENDENT = ("fuelflow_kgh", "coolant_temp_C", "EGT_mean_C",
             "oil_pressure_bar", "oil_temperature_C")

SCORED, DECLINED, REFUSED = "scored", "declined", "refused"


class StressSimError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# envelope verdicts.  TWO sources disagree; report both, reconcile neither.
# --------------------------------------------------------------------------

def envelope_verdict(alt_ft: float, oat_c: float) -> Tuple[str, List[str]]:
    """Percentile verdict from the 3.12M-row dataset scan."""
    notes: List[str] = []
    a_lo, a_hi = atm.TRAINING_ALT_FT
    t_lo, t_hi = atm.TRAINING_OAT_C
    if not (a_lo <= alt_ft <= a_hi):
        notes.append(f"altitude {alt_ft:.0f} ft outside trained {a_lo:.0f}..{a_hi:.0f}")
    if not (t_lo <= oat_c <= t_hi):
        notes.append(f"OAT {oat_c:.1f} C outside trained {t_lo:.1f}..{t_hi:.1f}")
    if notes:
        return "outside", notes

    c_a_lo, c_a_hi = atm.CORE_ALT_FT
    c_t_lo, c_t_hi = atm.CORE_OAT_C
    if not (c_a_lo <= alt_ft <= c_a_hi):
        notes.append(f"altitude {alt_ft:.0f} ft in the sparse tail")
    if not (c_t_lo <= oat_c <= c_t_hi):
        notes.append(f"OAT {oat_c:.1f} C in the sparse tail")
    if notes:
        return "range", notes

    if not atm.JOINT_ENVELOPE_CHECKED:
        notes.append("marginal bounds only; the alt/OAT pairing is unchecked")
    return "core", notes


def deck_violations(op: Mapping) -> Tuple[bool, List[str]]:
    """BaselineDeck.check_envelope returns a bare tuple of violation strings.

    Empty tuple means inside.  v0.2.0 unpacked it as `ok, *rest` which raised
    ValueError on the empty case and silently reported None for every row.
    """
    try:
        v = deck().check_envelope(dict(op))
    except Exception as exc:
        return True, [f"check_envelope raised {type(exc).__name__}: {exc}"]
    if isinstance(v, (list, tuple)):
        viol = [str(x) for x in v]
        return (len(viol) == 0), viol
    if isinstance(v, bool):
        return v, []
    return True, [f"unrecognised check_envelope return {type(v).__name__}"]


# --------------------------------------------------------------------------
# deck access
# --------------------------------------------------------------------------

_DECK = None
_REFERENCE_OP: Optional[Dict[str, float]] = None


def deck() -> Any:
    global _DECK
    if _DECK is None:
        from node2_twin_core.physics_deck import BaselineDeck
        _DECK = BaselineDeck()
    return _DECK


def reference_op() -> Dict[str, float]:
    global _REFERENCE_OP
    if _REFERENCE_OP is None:
        from node1_ingestion.adapter import _deck_nominal_frame
        _, _, healthy = _deck_nominal_frame()
        _REFERENCE_OP = {k: float(healthy[k]) for k in OP_KEYS}
    return dict(_REFERENCE_OP)


def discover_deck_bounds(key: str, lo: float, hi: float,
                         tol: float = 0.05) -> Tuple[float, float]:
    """Bisect check_envelope to recover the deck's own bound on one axis."""
    base = reference_op()

    def inside(v: float) -> bool:
        ok, _ = deck_violations(dict(base, **{key: v}))
        return ok

    mid = float(base[key])
    if not inside(mid):
        return (float("nan"), float("nan"))

    a, b = lo, mid
    while b - a > tol:
        m = (a + b) / 2.0
        a, b = (a, m) if inside(m) else (m, b)
    low = b
    a, b = mid, hi
    while b - a > tol:
        m = (a + b) / 2.0
        a, b = (m, b) if inside(m) else (a, m)
    return low, a


# --------------------------------------------------------------------------
# frame synthesis
# --------------------------------------------------------------------------

def _extract(pred: Any) -> Dict[str, float]:
    for probe in (pred,
                  getattr(pred, "values", None),
                  getattr(pred, "baseline", None),
                  getattr(pred, "expected", None),
                  getattr(pred, "predictions", None),
                  getattr(pred, "__dict__", None)):
        if isinstance(probe, Mapping) and any(k in probe for k in DEPENDENT):
            return {k: float(probe[k]) for k in DEPENDENT if k in probe}
    got = {k: float(getattr(pred, k)) for k in DEPENDENT
           if isinstance(getattr(pred, k, None), (int, float))}
    if got:
        return got
    raise StressSimError(
        f"cannot read baseline values from {type(pred).__name__}; "
        f"attrs={[a for a in dir(pred) if not a.startswith('_')][:12]}")


def synthesise_frame(op: Mapping, humidity_pct: float = 0.0) -> Tuple[Any, set]:
    """Deck's healthy prediction at op, inverted into a 68-column frame."""
    from node1_ingestion.adapter import (
        FT_PER_M, FUEL_DENSITY_KG_PER_L, KPA_PER_BAR, COOLANT_SOURCE_FIELD,
    )
    from shared.schema import EngineState, TelemetryPayload

    exp = _extract(deck().predict(dict(op)))
    missing = [k for k in DEPENDENT if k not in exp]
    if missing:
        raise StressSimError(f"deck prediction lacks {missing}")

    egt = exp["EGT_mean_C"]
    raw = dict(
        engine_state=EngineState.RUNNING,
        rpm=float(op["rpm"]),
        throttle_pct=float(op["throttle_pct"]),
        altitude_m=float(op["altitude_ft"]) / FT_PER_M,
        oat_c=float(op["ambient_temperature_C"]),
        humidity_pct=float(humidity_pct),
        fuel_flow_lph=exp["fuelflow_kgh"] / FUEL_DENSITY_KG_PER_L,
        oil_pressure_kpa=exp["oil_pressure_bar"] * KPA_PER_BAR,
        oil_temp_c=exp["oil_temperature_C"],
        egt_1_c=egt - 7.5, egt_2_c=egt - 2.5,
        egt_3_c=egt + 2.5, egt_4_c=egt + 7.5,
        egt_spread_c=15.0,
    )
    raw[COOLANT_SOURCE_FIELD] = exp["coolant_temp_C"]
    other = ("coolant_temp_in_c" if COOLANT_SOURCE_FIELD == "coolant_temp_out_c"
             else "coolant_temp_out_c")
    raw[other] = exp["coolant_temp_C"] - 7.0
    return TelemetryPayload(**raw), set(raw) | {"engine_state"}


# --------------------------------------------------------------------------
# a cell
# --------------------------------------------------------------------------

@dataclass
class StressCell:
    altitude_ft: float = 0.0
    oat_c: float = 0.0
    throttle_pct: float = 0.0
    rpm: float = 0.0

    geometric_altitude_m: Optional[float] = None
    humidity_pct: float = 0.0
    ambient_pressure_kpa: Optional[float] = None
    da_shift_ft: float = 0.0
    humidity_penalty_ft: float = 0.0
    isa_deviation_c: float = 0.0
    density_kgm3: float = 0.0

    outcome: str = REFUSED
    stage: str = "unrun"
    refusals: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    anomaly_probability: Optional[float] = None
    margin_to_gate: Optional[float] = None
    status: Optional[str] = None
    headline: Optional[str] = None
    largest_residual: Optional[float] = None
    ml_evaluated: Optional[bool] = None
    twin_in_envelope: Optional[bool] = None
    twin_violations: List[str] = field(default_factory=list)

    envelope: str = "unknown"
    envelope_notes: List[str] = field(default_factory=list)
    deck_envelope_ok: Optional[bool] = None
    deck_violations: List[str] = field(default_factory=list)

    @property
    def scored(self) -> bool:
        return self.outcome == SCORED

    def m(self) -> str:
        return "   --   " if self.margin_to_gate is None else f"{self.margin_to_gate:+.4f} "

    def p(self) -> str:
        return "  --  " if self.anomaly_probability is None else f"{self.anomaly_probability:.4f}"

    def label(self) -> str:
        p = ("" if self.ambient_pressure_kpa is None
             else f" P={self.ambient_pressure_kpa:.0f}kPa")
        g = ("" if self.geometric_altitude_m is None
             else f"{self.geometric_altitude_m:>5.0f}m ")
        return f"{g}{self.oat_c:>6.1f}C RH={self.humidity_pct:>5.1f}%{p}"

    def why(self) -> str:
        if self.outcome == SCORED:
            return "scored"
        if self.outcome == DECLINED:
            v = self.twin_violations or self.deck_violations or self.envelope_notes
            return f"declined ({v[0][:64]})" if v else "declined (no reason given)"
        return f"refused at {self.stage} ({self.refusals[0][:64] if self.refusals else '?'})"


def _largest_residual(out: Mapping) -> Optional[float]:
    r = out.get("residuals")
    if isinstance(r, Mapping) and r:
        try:
            return max(abs(float(x)) for x in r.values())
        except (TypeError, ValueError):
            return None
    return None


def run_op(core: Any, op: Mapping, humidity_pct: float = 0.0,
           altitude_is_density: bool = False,
           cell: Optional[StressCell] = None) -> StressCell:
    from node1_ingestion.adapter import to_twin_payload, twin_frame_to_dict

    c = cell or StressCell()
    c.altitude_ft = float(op["altitude_ft"])
    c.oat_c = float(op["ambient_temperature_C"])
    c.throttle_pct = float(op["throttle_pct"])
    c.rpm = float(op["rpm"])
    c.humidity_pct = humidity_pct
    c.envelope, c.envelope_notes = envelope_verdict(c.altitude_ft, c.oat_c)
    c.deck_envelope_ok, c.deck_violations = deck_violations(op)

    try:
        payload, provided = synthesise_frame(op, humidity_pct)
    except Exception as exc:
        c.outcome, c.stage = REFUSED, "synthesis"
        c.refusals.append(f"{type(exc).__name__}: {str(exc)[:200]}")
        return c

    res = to_twin_payload(payload, altitude_is_density=altitude_is_density,
                          provided=provided, strict=False)
    c.warnings.extend(res.warnings)
    if not res.ok:
        c.outcome, c.stage = REFUSED, "adapter"
        c.refusals.extend(res.refusals)
        return c

    try:
        out = twin_frame_to_dict(core.process(res.features))
    except Exception as exc:
        c.outcome, c.stage = REFUSED, "twin"
        c.refusals.append(f"{type(exc).__name__}: {str(exc)[:200]}")
        return c

    c.status = out.get("status")
    c.headline = out.get("headline")
    c.ml_evaluated = out.get("ml_evaluated")
    c.twin_in_envelope = out.get("in_envelope")
    c.twin_violations = [str(v) for v in (out.get("envelope_violations") or [])]
    c.largest_residual = _largest_residual(out)

    pa = out.get("anomaly_probability")
    if pa is None:
        # The twin withheld diagnosis on purpose.  Not a failure.
        c.outcome, c.stage = DECLINED, "declined_by_twin"
        return c

    c.anomaly_probability = float(pa)
    c.margin_to_gate = GATE_THRESHOLD - float(pa)
    c.outcome, c.stage = SCORED, "complete"
    return c


def run_cell(core: Any, geometric_altitude_m: float, oat_c: float,
             humidity_pct: float = 0.0,
             ambient_pressure_kpa: Optional[float] = None,
             throttle_pct: Optional[float] = None,
             rpm: Optional[float] = None) -> StressCell:
    """Weather -> density altitude -> healthy-at-that-DA -> twin."""
    from node1_ingestion.adapter import DENSITY_ALT_FLOOR_FT

    base = reference_op()
    c = StressCell(geometric_altitude_m=geometric_altitude_m,
                   humidity_pct=humidity_pct,
                   ambient_pressure_kpa=ambient_pressure_kpa)
    try:
        air = atm.solve(altitude_m=geometric_altitude_m, oat_c=oat_c,
                        ambient_pressure_kpa=ambient_pressure_kpa,
                        humidity_pct=humidity_pct)
    except Exception as exc:
        c.outcome, c.stage, c.oat_c = REFUSED, "atmosphere", oat_c
        c.refusals.append(f"{type(exc).__name__}: {exc}")
        return c

    da_ft = air.density_altitude_m * atm.FT_PER_M
    c.da_shift_ft = da_ft - geometric_altitude_m * atm.FT_PER_M
    c.humidity_penalty_ft = air.humidity_penalty_ft
    c.isa_deviation_c = air.isa_deviation_c
    c.density_kgm3 = air.density_kgm3
    c.warnings.extend(air.warnings)

    if da_ft < DENSITY_ALT_FLOOR_FT:
        c.outcome, c.stage = REFUSED, "da_floor"
        c.altitude_ft, c.oat_c = da_ft, oat_c
        c.envelope, c.envelope_notes = envelope_verdict(da_ft, oat_c)
        c.refusals.append(f"density altitude {da_ft:.0f} ft below floor "
                          f"{DENSITY_ALT_FLOOR_FT:.0f} ft")
        return c

    op = {"altitude_ft": da_ft, "ambient_temperature_C": oat_c,
          "throttle_pct": base["throttle_pct"] if throttle_pct is None else throttle_pct,
          "rpm": base["rpm"] if rpm is None else rpm}
    return run_op(core, op, humidity_pct, altitude_is_density=True, cell=c)


# --------------------------------------------------------------------------
# sweep
# --------------------------------------------------------------------------

@dataclass
class StressReport:
    cells: List[StressCell] = field(default_factory=list)
    elapsed_s: float = 0.0

    def of(self, outcome: str) -> List[StressCell]:
        return [c for c in self.cells if c.outcome == outcome]

    def thinnest(self, n: int = 5) -> List[StressCell]:
        d = [c for c in self.of(SCORED) if c.margin_to_gate is not None]
        return sorted(d, key=lambda c: c.margin_to_gate)[:n]

    def breaches(self) -> List[StressCell]:
        return [c for c in self.of(SCORED) if c.margin_to_gate < 0.0]

    def summary(self) -> Dict[str, Any]:
        by_env: Dict[str, int] = {}
        for c in self.cells:
            by_env[c.envelope] = by_env.get(c.envelope, 0) + 1
        mg = [c.margin_to_gate for c in self.of(SCORED)]
        rs = [c.largest_residual for c in self.of(SCORED)
              if c.largest_residual is not None]
        return {
            "stress_sim_version": STRESS_SIM_VERSION,
            "cells": len(self.cells),
            "scored": len(self.of(SCORED)),
            "declined": len(self.of(DECLINED)),
            "refused": len(self.of(REFUSED)),
            "breaches": len(self.breaches()),
            "min_margin": min(mg) if mg else None,
            "max_margin": max(mg) if mg else None,
            "worst_residual": max(rs) if rs else None,
            "by_envelope": by_env,
            "gate_threshold": GATE_THRESHOLD, "gate_trusted": False,
            "elapsed_s": round(self.elapsed_s, 2),
        }


def build_core() -> Any:
    from node2_twin_core.twin_core import TwinCore
    return TwinCore(warm_up=False, explain=False)


def sweep(core: Any = None,
          altitudes_m: Tuple[float, ...] = (0.0, 1500.0, 3000.0),
          oats_c: Tuple[float, ...] = (-20.0, 15.0, 40.0),
          humidities_pct: Tuple[float, ...] = (0.0, 100.0),
          pressures_kpa: Tuple[Optional[float], ...] = (None,)) -> StressReport:
    """Cartesian sweep.  Pressure axis is kPa; the dataset column is hPa."""
    if core is None:
        core = build_core()
    rep = StressReport()
    t0 = time.perf_counter()
    for alt in altitudes_m:
        for oat in oats_c:
            for rh in humidities_pct:
                for p in pressures_kpa:
                    rep.cells.append(run_cell(core, alt, oat, rh, p))
    rep.elapsed_s = time.perf_counter() - t0
    return rep


def scenario_sweep(core: Any = None) -> List[Tuple[str, StressCell]]:
    if core is None:
        core = build_core()
    return [(n, run_cell(core, a.geometric_altitude_m, a.oat_c, a.humidity_pct))
            for n, a in ((n, atm.scenario(n)) for n in sorted(atm.SCENARIOS))]


def gate_resolution_ft(core: Any, steps: Tuple[float, ...] =
                       (1.0, 10.0, 50.0, 100.0, 250.0, 500.0, 1000.0, 2000.0)
                       ) -> Tuple[Optional[float], List[Tuple[float, Optional[float]]]]:
    """Smallest altitude step that moves p_anom.  The forest is piecewise
    constant, so a humidity shift smaller than this is below the gate's
    resolution and must not be reported as a margin change."""
    ref = reference_op()
    base = run_op(core, dict(ref))
    if not base.scored:
        return None, []
    trace: List[Tuple[float, Optional[float]]] = []
    first = None
    for s in steps:
        c = run_op(core, dict(ref, altitude_ft=ref["altitude_ft"] + s))
        p = c.anomaly_probability
        trace.append((s, p))
        if first is None and p is not None and p != base.anomaly_probability:
            first = s
    return first, trace


def stress_caveats() -> List[Dict[str, Any]]:
    out = [{
        "id": "gate_untrusted_pre_retrain", "verified": False,
        "value": GATE_THRESHOLD,
        "detail": "a known-healthy deck point scores 0.5444 against the 0.65 "
                  "gate: +0.1056 headroom. Margins are relative indicators, "
                  "not airworthiness statements.",
    }, {
        "id": "cells_are_synthesised_healthy", "verified": True,
        "value": "BaselineDeck.predict",
        "detail": "each cell is the deck's own healthy prediction at that "
                  "operating point, so residuals are ~0 by construction. This "
                  "maps model competence, it does NOT simulate degradation.",
    }, {
        "id": "two_disagreeing_envelopes", "verified": False,
        "value": "deck vs dataset percentiles",
        "detail": "BaselineDeck.check_envelope reports an OAT ceiling near "
                  "30 C; the 3.12M-row scan of master_dataset.csv gives "
                  "34.351 C. Both are reported per cell; neither is silently "
                  "preferred. The deck config is the stricter authority.",
    }, {
        "id": "cold_days_are_extrapolation_by_construction", "verified": True,
        "value": 0.0,
        "detail": "the training altitude floor is exactly sea level, so any "
                  "below-ISA day yields a negative density altitude the "
                  "models have never seen. The twin declines these; that is a "
                  "training-data gap, not a defect.",
    }]
    out.extend(atm.atmosphere_caveats())
    return out


# --------------------------------------------------------------------------
def _self_test() -> None:
    fails: List[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            fails.append(msg)
            print(f"  FAIL: {msg}")

    print(f"stress_sim v{STRESS_SIM_VERSION}")
    core = build_core()
    ref = reference_op()

    print("\nCASE 0  the two envelopes, side by side")
    print(f"  dataset scan : alt {atm.TRAINING_ALT_FT} ft   OAT {atm.TRAINING_OAT_C} C")
    d_oat = discover_deck_bounds("ambient_temperature_C", -80.0, 80.0)
    d_alt = discover_deck_bounds("altitude_ft", -8000.0, 40000.0)
    print(f"  deck bisect  : alt ({d_alt[0]:.0f}, {d_alt[1]:.0f}) ft   "
          f"OAT ({d_oat[0]:.2f}, {d_oat[1]:.2f}) C")
    print("  -> the deck is the stricter authority; both are reported per cell")
    check(d_oat[1] == d_oat[1], "deck OAT bound not discoverable")

    print("\nCASE 1  check_envelope binding (v0.2.0 returned None for every row)")
    for tag, op in (("ref", dict(ref)),
                    ("hot 40C", dict(ref, ambient_temperature_C=40.0))):
        ok, v = deck_violations(op)
        print(f"  {tag:<8} inside={ok} violations={v}")
    ok_ref, _ = deck_violations(dict(ref))
    ok_hot, v_hot = deck_violations(dict(ref, ambient_temperature_C=40.0))
    check(ok_ref is True, "reference op reported outside the deck envelope")
    check(ok_hot is False and len(v_hot) > 0, "40 C not flagged by the deck")

    print("\nCASE 2  synthesis at the deck's own point reproduces 0.5444")
    c = run_op(core, dict(ref))
    print(f"  outcome={c.outcome} p_anom={c.p()} margin={c.m()} "
          f"status={c.status} resid={c.largest_residual}")
    check(c.scored, f"reference not scored: {c.why()}")
    if c.anomaly_probability is not None:
        d = abs(c.anomaly_probability - DECK_REFERENCE_P_ANOM)
        print(f"  delta from {DECK_REFERENCE_P_ANOM}: {d:.3e}")
        check(d < 1e-9, f"synthesis drifted by {d:.3e}")
    check(c.largest_residual == 0.0,
          f"residuals not zero by construction: {c.largest_residual}")

    print("\nCASE 3  the decline path is deliberate, not a crash")
    cold = run_cell(core, 0.0, -20.0, 0.0)
    print(f"  0 m / -20 C -> DA shift {cold.da_shift_ft:+.0f} ft")
    print(f"  outcome={cold.outcome} status={cold.status} "
          f"ml_evaluated={cold.ml_evaluated} in_envelope={cold.twin_in_envelope}")
    print(f"  headline: {cold.headline}")
    print(f"  twin violations: {cold.twin_violations[:2]}")
    check(cold.outcome == DECLINED, f"expected declined, got {cold.outcome}")
    check(cold.ml_evaluated is False, "twin scored an out-of-envelope point")
    check(len(cold.twin_violations) > 0, "decline carried no stated reason")

    print("\nCASE 4  every core-envelope cell must score")
    for alt in (0.0, 6000.0, 15000.0, 20000.0):
        op = dict(ref, altitude_ft=alt,
                  ambient_temperature_C=atm.isa_temperature_c(alt / atm.FT_PER_M))
        cc = run_op(core, op)
        print(f"  {alt:>7.0f} ft ISA {op['ambient_temperature_C']:>6.1f} C -> "
              f"{cc.outcome:<8} p_anom={cc.p()} margin={cc.m()} "
              f"[{cc.envelope}] deck_ok={cc.deck_envelope_ok}")
        if cc.envelope == "core":
            check(cc.scored, f"core cell at {alt:.0f} ft not scored: {cc.why()}")

    print("\nCASE 5  gate resolution: how far must altitude move to move p_anom")
    first, trace = gate_resolution_ft(core)
    for s, p in trace:
        print(f"  +{s:>7.0f} ft -> {p}")
    print(f"  smallest step that moves the score: {first} ft")
    check(first is not None, "score never moved; classifier may be degenerate")

    print("\nCASE 6  humidity, judged against that resolution")
    dry = run_cell(core, 0.0, 30.0, 0.0)
    wet = run_cell(core, 0.0, 30.0, 100.0)
    print(f"  dry  DA shift {dry.da_shift_ft:+7.0f} ft  {dry.outcome} p_anom={dry.p()}")
    print(f"  100% DA shift {wet.da_shift_ft:+7.0f} ft  {wet.outcome} p_anom={wet.p()}")
    pen = wet.humidity_penalty_ft
    print(f"  humidity penalty {pen:+.0f} ft vs gate resolution {first} ft")
    if dry.scored and wet.scored:
        moved = wet.margin_to_gate - dry.margin_to_gate
        print(f"  margin moved {moved:+.6f}")
        if moved == 0.0 and first is not None and pen < first:
            print("  -> BELOW GATE RESOLUTION: honest null result, not a bug")
    check(wet.da_shift_ft > dry.da_shift_ft, "humidity did not raise DA")

    print("\nCASE 7  cold air, negative DA, and the -6000 ft floor")
    for oat in (-20.0, -30.0, -45.0):
        cc = run_cell(core, 0.0, oat, 0.0)
        print(f"  {oat:>6.1f} C -> DA shift {cc.da_shift_ft:+7.0f} ft "
              f"{cc.outcome:<8} {cc.why()[:56]}")
    floor = run_cell(core, 0.0, -45.0, 0.0)
    check(floor.stage == "da_floor", "-45 C did not hit the DA floor")

    print("\nCASE 8  grid sweep")
    rep = sweep(core=core)
    s = rep.summary()
    print(f"  cells={s['cells']} scored={s['scored']} declined={s['declined']} "
          f"refused={s['refused']} breaches={s['breaches']} in {s['elapsed_s']}s")
    print(f"  margin range   : {s['min_margin']} .. {s['max_margin']}")
    print(f"  worst residual : {s['worst_residual']}")
    print(f"  by envelope    : {s['by_envelope']}")
    check(s["scored"] > 0, "no cell scored")
    check(s["refused"] == 0 or all(c.stage == "da_floor" for c in rep.of(REFUSED)),
          f"unexpected refusals: {[c.why() for c in rep.of(REFUSED)][:3]}")
    print("  thinnest margins:")
    for cc in rep.thinnest(5):
        print(f"    {cc.label()} DAshift={cc.da_shift_ft:+7.0f} ft "
              f"margin={cc.m()} [{cc.envelope}]")
    if rep.of(DECLINED):
        print(f"  declined ({len(rep.of(DECLINED))}) -- the twin's own limits:")
        for cc in rep.of(DECLINED)[:6]:
            print(f"    {cc.label()} {cc.why()[:70]}")

    print("\nCASE 9  named scenarios")
    for name, cc in scenario_sweep(core=core):
        print(f"  {name:<16} DAshift={cc.da_shift_ft:+7.0f} ft p_anom={cc.p()} "
              f"margin={cc.m()} [{cc.envelope}] {cc.why()[:44]}")

    print("\nCASE 10  pressure axis is kPa, not hPa")
    lo = run_cell(core, 0.0, 15.0, 0.0, ambient_pressure_kpa=43.0)
    hi = run_cell(core, 0.0, 15.0, 0.0, ambient_pressure_kpa=101.325)
    print(f"   43.0 kPa -> DA shift {lo.da_shift_ft:+7.0f} ft {lo.outcome}")
    print(f"  101.3 kPa -> DA shift {hi.da_shift_ft:+7.0f} ft {hi.outcome} "
          f"p_anom={hi.p()}")
    check(lo.da_shift_ft > hi.da_shift_ft, "low pressure did not raise DA")

    print("\nCASE 11  declared caveats")
    for cv in stress_caveats():
        print(f"  {cv['id']:<44} verified={cv['verified']}")

    if fails:
        print(f"\nSTRESS SIM SELF-CHECK FAILED -- {len(fails)}")
        for m in fails:
            print(f"  - {m}")
        raise SystemExit(1)
    print("\nSTRESS SIM SELF-CHECK OK")
    print("  scored / declined / refused are three different answers")


if __name__ == "__main__":
    _self_test()
