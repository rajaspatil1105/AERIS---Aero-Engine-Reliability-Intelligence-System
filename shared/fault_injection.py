"""AERIS fault injection -- synthetic degradations that cross the gate.

WHY THIS EXISTS
Every frame the rest of the system can generate is a SYNTHESISED HEALTHY
point: adapter, stress_sim and throttle_dynamics all build frames from
BaselineDeck.predict(), which by definition returns what a healthy engine
does. So nothing in the repo could demonstrate the detection path actually
firing. This module perturbs one or more channels away from the healthy
prediction and reports what the twin says.

WHAT AN INJECTION IS, AND IS NOT
It is an OFFSET APPLIED TO A HEALTHY PREDICTION. It is not a simulated
failure mechanism. A real cooling failure changes coolant temperature AND
EGT AND oil temperature in a coupled way over time; here coolant is simply
displaced by +10 C and everything else left at equilibrium. The residual the
twin sees is therefore exactly the offset, which is why these cases are
useful as a test of the DETECTION PATH and useless as evidence about
detection ACCURACY on real hardware.

WHY THE MAGNITUDES ARE WHAT THEY ARE
throttle_dynamics measured the gate's residual resolution two-sided at this
operating point: coolant 0.01455 C, oil_temp 0.00174 C, oil_press 0.00019
bar, EGT 1.373 C, fuelflow 0.00358 kg/h, rpm 6.82. Injections here sit
hundreds to thousands of times above resolution, so crossing the gate is not
a marginal result. CASE 5 additionally bisects the SMALLEST offset per
channel that crosses 0.65, which is the number worth quoting.

THE LABEL MAPPING IS DISCOVERED, NOT ASSUMED
Which fault_label a given channel offset produces is a property of the
Cantera-generated training set (declared ~70% faithful), not of a real
Rotax. CASE 6 scans every channel in both directions and prints the mapping
it finds. fuel_pressure_dev is a known dead class and is asserted never to
win, rather than being quietly excluded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from node1_ingestion.adapter import to_twin_payload, twin_frame_to_dict
from shared.stress_sim import (
    GATE_THRESHOLD, build_core, deck, envelope_verdict, reference_op, _extract,
)
from shared.throttle_dynamics import GATE_RESID_TOL, _frame_from_state

FAULT_INJECTION_VERSION = "0.1.2"
HEALTHY_P_ANOM = 0.5443998040908319      # the regression invariant
DEAD_CLASSES = ("fuel_pressure_dev",)

# The twin's full status vocabulary, discovered by injection. ADVISORY was not
# known until oil_pressure_low produced it: gate not crossed, but a channel is
# outside its healthy stats range. It is driven by the advisory channel, NOT by
# the 0.65 gate, and fault_label is None there.
KNOWN_STATUSES = ("HEALTHY", "ADVISORY", "FAULT", "UNAVAILABLE")

# Scenarios that were MEASURED to cross the 0.65 gate.
MUST_CROSS = ("coolant_hot", "coolant_very_hot", "egt_high", "egt_low",
              "oil_hot", "lubrication", "fuel_lean", "fuel_rich",
              "overheat_coupled")

# Scenarios measured NOT to cross, pinned with the value observed. A -1.0 bar
# loss on a 3.162 bar nominal -- 32% of oil pressure -- reads 0.5679, only
# +0.0235 above the healthy 0.5444. Asserted to stay sub-gate so that a
# retrain which fixes this sensitivity gap fails the test loudly instead of
# passing silently.
# The pinned value is the FULL-PRECISION measured one. v0.1.1 pinned
# 0.5679216802197834, which was "0.5679" read off a rounded console print with
# the remaining digits invented; the test correctly rejected it. Pin only what
# was actually measured.
KNOWN_SUBGATE: Dict[str, float] = {"oil_pressure_low": 0.567868692948779}

# Pairs measured to score IDENTICALLY, pinned for the same reason.
#   saturation: 2.5x the coolant excursion, same score -- p_anom carries no
#               severity information.
#   direction:  opposite fuel-flow faults, same score -- residuals are
#               reported unsigned, so the gate cannot tell lean from rich.
IDENTICAL_PAIRS = (("coolant_hot", "coolant_very_hot", "severity saturation"),
                   ("fuel_lean", "fuel_rich", "direction blindness"))

# Named scenarios: channel -> additive offset in the channel's own unit.
# Signs are physical: a cooling fault runs HOT, a lubrication fault runs hot
# AND loses pressure, a lean fuel condition flows LESS.
SCENARIOS: Dict[str, Dict[str, float]] = {
    "coolant_hot":        {"coolant_temp_C": +10.0},
    "coolant_very_hot":   {"coolant_temp_C": +25.0},
    "egt_high":           {"EGT_mean_C": +60.0},
    "egt_low":            {"EGT_mean_C": -60.0},
    "oil_hot":            {"oil_temperature_C": +20.0},
    "oil_pressure_low":   {"oil_pressure_bar": -1.0},
    "lubrication":        {"oil_temperature_C": +20.0,
                           "oil_pressure_bar": -0.8},
    "fuel_lean":          {"fuelflow_kgh": -1.5},
    "fuel_rich":          {"fuelflow_kgh": +1.5},
    "overheat_coupled":   {"coolant_temp_C": +18.0, "oil_temperature_C": +14.0,
                           "EGT_mean_C": +35.0},
}

# Channels scanned by CASE 6, with an offset comfortably above resolution.
SCAN_OFFSETS: Dict[str, float] = {
    "coolant_temp_C": 10.0,
    "EGT_mean_C": 50.0,
    "oil_temperature_C": 15.0,
    "oil_pressure_bar": 0.8,
    "fuelflow_kgh": 1.5,
    "rpm": 250.0,
}


class FaultInjectionError(RuntimeError):
    pass


@dataclass
class Injected:
    name: str = ""
    offsets: Dict[str, float] = field(default_factory=dict)
    expected: Dict[str, float] = field(default_factory=dict)
    actual: Dict[str, float] = field(default_factory=dict)

    ok: bool = False
    note: str = ""

    anomaly_probability: Optional[float] = None
    status: Optional[str] = None
    is_healthy: Optional[bool] = None
    ml_evaluated: Optional[bool] = None
    fault_label: Optional[str] = None
    fault_confidence: Optional[float] = None
    fault_probabilities: Dict[str, float] = field(default_factory=dict)
    headline: Optional[str] = None
    residuals: Dict[str, float] = field(default_factory=dict)
    advisories: List[str] = field(default_factory=list)
    safety_alert: Optional[bool] = None
    rul_raw: Optional[float] = None
    rul_trusted: Optional[bool] = None
    latency_ms: Optional[float] = None

    def crossed(self) -> bool:
        return (self.anomaly_probability is not None
                and self.anomaly_probability >= GATE_THRESHOLD)

    def margin(self) -> Optional[float]:
        if self.anomaly_probability is None:
            return None
        return GATE_THRESHOLD - self.anomaly_probability

    def p(self) -> str:
        return ("  --  " if self.anomaly_probability is None
                else f"{self.anomaly_probability:.4f}")

    def line(self) -> str:
        return (f"p_anom={self.p()} status={str(self.status):<11} "
                f"label={str(self.fault_label):<24} "
                f"conf={'  --  ' if self.fault_confidence is None else format(self.fault_confidence, '.3f')}")


def healthy_state(op: Optional[Mapping] = None
                  ) -> Tuple[Dict[str, float], Dict[str, float]]:
    """The deck's healthy prediction at an operating point, plus the op."""
    o = dict(reference_op() if op is None else op)
    target = _extract(deck().predict(dict(o)))
    target["rpm"] = float(o["rpm"])
    return target, o


def inject(core: Any, offsets: Mapping[str, float],
           op: Optional[Mapping] = None, name: str = "custom",
           humidity_pct: float = 0.0) -> Injected:
    """Apply offsets to the healthy prediction and score the result."""
    target, o = healthy_state(op)
    unknown = [k for k in offsets if k not in target]
    if unknown:
        raise FaultInjectionError(
            f"channel(s) not in the deck prediction: {unknown}; "
            f"have {sorted(target)}")

    state = dict(target)
    for k, dv in offsets.items():
        state[k] = float(target[k]) + float(dv)

    inj = Injected(name=name, offsets=dict(offsets),
                   expected=dict(target), actual=dict(state))

    try:
        payload, provided = _frame_from_state(o, state, humidity_pct)
    except Exception as exc:
        inj.note = f"synthesis {type(exc).__name__}: {str(exc)[:80]}"
        return inj

    res = to_twin_payload(payload, provided=provided, strict=False)
    if not res.ok:
        inj.note = res.refusals[0][:100] if res.refusals else "adapter refused"
        return inj

    try:
        out = twin_frame_to_dict(core.process(res.features))
    except Exception as exc:
        inj.note = f"twin {type(exc).__name__}: {str(exc)[:80]}"
        return inj

    pa = out.get("anomaly_probability")
    inj.anomaly_probability = None if pa is None else float(pa)
    inj.status = out.get("status")
    inj.is_healthy = out.get("is_healthy")
    inj.ml_evaluated = out.get("ml_evaluated")
    inj.fault_label = out.get("fault_label")
    fc = out.get("fault_confidence")
    inj.fault_confidence = None if fc is None else float(fc)
    fp = out.get("fault_probabilities")
    if isinstance(fp, Mapping):
        inj.fault_probabilities = {k: float(v) for k, v in fp.items()}
    inj.headline = out.get("headline")
    r = out.get("residuals")
    if isinstance(r, Mapping):
        inj.residuals = {k: float(v) for k, v in r.items()}
    adv = out.get("advisories") or []
    inj.advisories = [str(a) for a in adv]
    inj.safety_alert = out.get("safety_alert")
    rr = out.get("rul_raw")
    inj.rul_raw = None if rr is None else float(rr)
    inj.rul_trusted = out.get("rul_trusted")
    lm = out.get("latency_ms")
    inj.latency_ms = None if lm is None else float(lm)
    inj.ok = inj.anomaly_probability is not None
    if not inj.ok:
        v = out.get("envelope_violations") or []
        inj.note = str(v[0])[:100] if v else "twin declined to score"
    return inj


def run_scenario(core: Any, name: str, op: Optional[Mapping] = None
                 ) -> Injected:
    if name not in SCENARIOS:
        raise FaultInjectionError(
            f"unknown scenario {name!r}; have {sorted(SCENARIOS)}")
    return inject(core, SCENARIOS[name], op=op, name=name)


def run_all(core: Any, op: Optional[Mapping] = None) -> List[Injected]:
    return [run_scenario(core, n, op=op) for n in SCENARIOS]


def detection_threshold(core: Any, channel: str, sign: float = 1.0,
                        op: Optional[Mapping] = None, iters: int = 22
                        ) -> Optional[float]:
    """Smallest |offset| in one channel whose score reaches the 0.65 gate.

    Bisection assumes the crossing is monotonic in offset. The gate is
    tree-based and piecewise constant, so this finds A boundary, not
    necessarily THE global minimum. Declared in injection_caveats().
    """
    target, o = healthy_state(op)
    if channel not in target:
        return None
    hi = max(abs(float(target[channel])) * 0.02, 1e-3)
    found = False
    for _ in range(16):
        got = inject(core, {channel: sign * hi}, op=o, name="probe")
        if got.crossed():
            found = True
            break
        hi *= 2.0
    if not found:
        return None
    lo = 0.0
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        got = inject(core, {channel: sign * mid}, op=o, name="probe")
        if got.crossed():
            hi = mid
        else:
            lo = mid
    return hi


def scan_labels(core: Any, op: Optional[Mapping] = None
                ) -> List[Tuple[str, float, Injected]]:
    """Every channel, both directions, at an offset far above resolution."""
    out: List[Tuple[str, float, Injected]] = []
    for ch, mag in SCAN_OFFSETS.items():
        for sign in (1.0, -1.0):
            inj = inject(core, {ch: sign * mag}, op=op,
                         name=f"{ch}{'+' if sign > 0 else '-'}{mag}")
            out.append((ch, sign * mag, inj))
    return out


def injection_caveats() -> List[Dict[str, Any]]:
    return [{
        "id": "injections_are_offsets_not_failures", "verified": True,
        "value": f"{len(SCENARIOS)} scenarios",
        "detail": "an injection displaces one or more channels from the deck's "
                  "healthy prediction by a fixed amount. It does NOT simulate a "
                  "failure mechanism: a real cooling failure couples coolant, "
                  "EGT and oil over time, whereas here only the named channels "
                  "move and the rest stay at equilibrium. These cases test the "
                  "DETECTION PATH; they are not evidence about accuracy on real "
                  "hardware.",
    }, {
        "id": "injection_magnitudes_are_chosen", "verified": False,
        "value": {k: v for k, v in SCAN_OFFSETS.items()},
        "detail": "magnitudes were picked to be plainly visible -- hundreds to "
                  "thousands of times the gate's measured residual resolution "
                  f"({dict(GATE_RESID_TOL)} is the admission tolerance derived "
                  "from it). They are not calibrated to any observed Rotax "
                  "failure severity. CASE 5 reports the smallest offset that "
                  "actually crosses the gate, which is the honest number.",
    }, {
        "id": "label_mapping_is_dataset_property", "verified": False,
        "value": "channel offset -> fault_label",
        "detail": "which label an offset produces is a property of the "
                  "Cantera-generated training set, declared ~70% faithful to a "
                  "real engine, and of a gate that is untrusted pre-retrain. "
                  "CASE 6 scans and prints the mapping rather than asserting "
                  "one. A label being plausible is not the same as it being "
                  "correct.",
    }, {
        "id": "dead_class_cannot_be_diagnosed", "verified": True,
        "value": list(DEAD_CLASSES),
        "detail": "fuel_pressure_dev appears in fault_probabilities with small "
                  "nonzero mass but never wins, including for direct fuel-flow "
                  "injections. One of five advertised labels is therefore "
                  "undiagnosable. CASE 7 asserts it never becomes fault_label "
                  "instead of hiding it, and it must not be used in a demo.",
    }, {
        "id": "rul_collapses_under_injection", "verified": True,
        "value": "182.48 healthy -> -0.06 raw under lubrication",
        "detail": "measured: rul_raw falls from 182.4773909895507 at the "
                  "healthy point to about -0.056 for the lubrication case, i.e. "
                  "past zero. rul_trusted stays False and rul_units is "
                  "'unknown', so RUL must never be rendered as minutes "
                  "remaining. It is a direction, not a duration.",
    }, {
        "id": "residuals_reported_unsigned", "verified": True,
        "value": "-0.8 bar injected reads 0.8",
        "detail": "measured: the twin's residuals dict carries magnitudes, not "
                  "signed deviations, so a pressure DROP and a pressure RISE of "
                  "equal size are indistinguishable downstream. Anything "
                  "comparing residuals to injected offsets must compare "
                  "absolute values, and a UI cannot infer direction from "
                  "residuals alone -- use expected vs features.",
    }, {
        "id": "four_twin_statuses_not_three", "verified": True,
        "value": list(KNOWN_STATUSES),
        "detail": "discovered by injection: oil_pressure_low returns "
                  "status='ADVISORY' with is_healthy=False and "
                  "fault_label=None. ADVISORY is produced by the stats-range "
                  "advisory channel, independently of the 0.65 gate, so the "
                  "twin has FOUR statuses and any UI must render all four. "
                  "Not the same vocabulary as throttle_dynamics.wire_status().",
    }, {
        "id": "oil_pressure_sensitivity_gap", "verified": True,
        "value": "-1.0 bar of 3.162 -> p_anom 0.5679, no crossing",
        "detail": "measured: losing 32% of oil pressure does NOT cross the "
                  "gate; it scores 0.5679, only +0.0235 above the healthy "
                  "0.5444, and reports ADVISORY. Meanwhile the measured "
                  "residual resolution for that channel is 0.00019 bar, so "
                  "the gate is strongly NON-MONOTONIC in offset: tiny changes "
                  "move the score, a large one barely does. A real oil "
                  "pressure failure could be missed. Pinned in KNOWN_SUBGATE.",
    }, {
        "id": "p_anom_is_not_severity_or_direction", "verified": True,
        "value": "coolant +10 == +25; fuel -1.5 == +1.5",
        "detail": "measured: a +10 C and a +25 C coolant excursion both score "
                  "exactly 0.7229, so p_anom carries no severity information. "
                  "A 1.5 kg/h fuel DEFICIT and a 1.5 kg/h EXCESS both score "
                  "exactly 0.6651 with the same label, because residuals are "
                  "reported unsigned -- the gate cannot distinguish opposite "
                  "physical faults. p_anom answers 'is something wrong', not "
                  "'how badly' or 'which way'. Pinned in IDENTICAL_PAIRS.",
    }, {
        "id": "labels_reflect_channel_count_not_mechanism", "verified": False,
        "value": "oil_hot -> sensor_drift; oil_hot+press_low -> lubrication",
        "detail": "measured: a lone oil-temperature excursion is labelled "
                  "sensor_drift, while the same excursion combined with a "
                  "pressure loss is labelled lubrication_degradation. Reading "
                  "a single implausible channel as an instrumentation problem "
                  "is plausible behaviour, but it is a property of the "
                  "Cantera-generated training set (~70% faithful), not "
                  "validated physics.",
    }, {
        "id": "gate_is_non_monotonic", "verified": True,
        "value": "coolant crosses at 0.038 C but not at -10 C",
        "detail": "MEASURED, and it invalidates any reading of p_anom as "
                  "severity. CASE 5 bisects the smallest crossing offset, "
                  "CASE 6 applies a large one, and they disagree: coolant "
                  "crosses at 0.0383 C yet -10 C scores 0.5965 and does not "
                  "cross; rpm crosses at +88.96 yet +250 scores 0.5457 and "
                  "does not; oil pressure never crosses by bisection although "
                  "its residual resolution is 0.00019 bar. The gate is "
                  "tree-based, so a threshold is a LEAF BOUNDARY, not a floor "
                  "above which detection is guaranteed. Same phenomenon as the "
                  "500 ft altitude leaf width in stress_sim.",
    }, {
        "id": "oil_temperature_hypersensitive", "verified": True,
        "value": "0.0017 C crosses the gate",
        "detail": "MEASURED: an oil-temperature offset of 1.7 mK reaches the "
                  "0.65 gate and reports FAULT, against an admission tolerance "
                  "of 0.35 mK -- a band of about 5x in millikelvin between "
                  "'admitted as healthy' and 'reported as faulty'. This is the "
                  "quantitative justification for the steady-state admission "
                  "gate in throttle_dynamics: the transient lag it was "
                  "previously admitting at 2% of step was 0.157 C, which is 92x "
                  "this detection threshold, so every transient frame was "
                  "guaranteed to read FAULT. Note the safety asymmetry: oil "
                  "TEMPERATURE is hypersensitive (false positives) while oil "
                  "PRESSURE misses a 32% loss (false negatives).",
    }, {
        "id": "safety_alert_never_observed", "verified": False,
        "value": "0 of 10 injections",
        "detail": "safety_alert was False and safety_breaches empty for every "
                  "injection, including cases with rul_raw at -0.06 and -25.26 "
                  "and with advisories present. So the safety channel is either "
                  "unreachable through the six channels injected here or it is "
                  "a second dead path alongside fuel_pressure_dev. NOT yet "
                  "established which; do not present safety_alert as a working "
                  "feature until a case is found that fires it.",
    }, {
        "id": "rul_grades_severity_where_p_anom_does_not", "verified": True,
        "value": "182.5 healthy -> -25.3 overheat_coupled",
        "detail": "measured across injections: rul_raw orders as 182.5 healthy, "
                  "165.9 fuel, 163.7 oil pressure, 120.3 coolant (identical for "
                  "+10 and +25 C, so it saturates too), 53.0 EGT, 18.4 oil hot, "
                  "-0.06 lubrication, -25.3 coupled overheat. It carries more "
                  "severity information than p_anom, which is flat. But it goes "
                  "NEGATIVE, rul_units is 'unknown' and rul_trusted is False "
                  "throughout, so it is a direction of travel and must never be "
                  "rendered as minutes remaining.",
    }]


# --------------------------------------------------------------------------
def _self_test() -> None:
    fails: List[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            fails.append(msg)
            print(f"  FAIL: {msg}")

    print(f"fault_injection v{FAULT_INJECTION_VERSION}  gate {GATE_THRESHOLD}")
    core = build_core()
    target, op = healthy_state()
    ev, _ = envelope_verdict(op["altitude_ft"], op["ambient_temperature_C"])
    print(f"  operating point: {op}  envelope={ev}")
    print(f"  healthy prediction: "
          f"{ {k: round(v, 3) for k, v in target.items()} }")

    print("\nCASE 0  the zero injection must reproduce the invariant exactly")
    base = inject(core, {}, name="none")
    print(f"  {base.line()}")
    print(f"  rul_raw={base.rul_raw} trusted={base.rul_trusted} "
          f"latency={None if base.latency_ms is None else round(base.latency_ms, 1)} ms")
    check(base.ok, f"zero injection did not score: {base.note}")
    check(base.anomaly_probability is not None
          and abs(base.anomaly_probability - HEALTHY_P_ANOM) < 1e-12,
          f"REGRESSION INVARIANT MOVED: {base.anomaly_probability}")
    check(base.status == "HEALTHY" and base.is_healthy is True,
          f"zero injection is not healthy: {base.status}")
    check(not base.residuals or max(abs(v) for v in base.residuals.values()) < 1e-9,
          f"zero injection has residuals: {base.residuals}")

    print("\nCASE 1  every scenario scores, and none is refused or declined")
    results = run_all(core)
    for inj in results:
        print(f"  {inj.name:<18} {inj.line()}")
        if not inj.ok:
            print(f"      note: {inj.note}")
    check(all(r.ok for r in results),
          f"{sum(1 for r in results if not r.ok)} scenario(s) never scored")

    print("\nCASE 2  what crosses the gate, and what measurably does not")
    by_name = {r.name: r for r in results}
    for inj in results:
        if not inj.ok:
            continue
        tag = "crosses" if inj.crossed() else "SUB-GATE"
        print(f"  {inj.name:<18} p_anom={inj.p()} margin={inj.margin():+.4f} "
              f"status={str(inj.status):<9} {tag}")
        check(str(inj.status) in KNOWN_STATUSES,
              f"{inj.name}: status {inj.status!r} is outside the known "
              f"vocabulary {KNOWN_STATUSES}")
        check(inj.ml_evaluated is True,
              f"{inj.name}: ml_evaluated={inj.ml_evaluated}")

    for name in MUST_CROSS:
        inj = by_name.get(name)
        if inj is None or not inj.ok:
            check(False, f"{name}: expected to cross but never scored")
            continue
        check(inj.crossed(),
              f"{name}: p_anom {inj.anomaly_probability:.4f} below the "
              f"{GATE_THRESHOLD} gate")
        check(inj.status == "FAULT" and inj.is_healthy is False,
              f"{name}: crossed the gate but status={inj.status} "
              f"is_healthy={inj.is_healthy}")

    print("  measured sensitivity gaps (pinned, not hidden):")
    for name, seen in KNOWN_SUBGATE.items():
        inj = by_name.get(name)
        if inj is None or not inj.ok:
            check(False, f"{name}: sub-gate case never scored")
            continue
        print(f"    {name:<18} p_anom={inj.p()} vs gate {GATE_THRESHOLD} "
              f"-- offsets {inj.offsets}")
        check(not inj.crossed(),
              f"{name} now CROSSES the gate at {inj.anomaly_probability:.4f}. "
              f"That is an improvement, but this test pinned the measured "
              f"value {seen:.4f}; update KNOWN_SUBGATE and MUST_CROSS")
        check(abs(inj.anomaly_probability - seen) < 1e-9,
              f"{name}: p_anom {inj.anomaly_probability!r} moved from the "
              f"pinned {seen!r}")
        check(inj.fault_label is None,
              f"{name}: sub-gate but carries fault_label {inj.fault_label!r}")

    print("\nCASE 2b  p_anom is neither severity-graded nor direction-aware")
    for a_name, b_name, why in IDENTICAL_PAIRS:
        x, y = by_name.get(a_name), by_name.get(b_name)
        if not (x and y and x.ok and y.ok):
            check(False, f"{a_name}/{b_name}: one of the pair never scored")
            continue
        print(f"  {a_name} {x.p()} vs {b_name} {y.p()}  ({why})")
        print(f"    offsets {x.offsets} vs {y.offsets}")
        check(x.anomaly_probability == y.anomaly_probability,
              f"{a_name} and {b_name} no longer score identically "
              f"({x.anomaly_probability} vs {y.anomaly_probability}); the "
              f"pinned {why} finding has changed -- update IDENTICAL_PAIRS")
    print("  identical scores confirmed: p_anom answers whether, not how much "
          "or which way")

    print("\nCASE 3  residual magnitude equals the injected magnitude")
    print("  (the twin reports residuals UNSIGNED -- compare absolute values)")
    for inj in results:
        if not inj.ok:
            continue
        for ch, dv in inj.offsets.items():
            got = inj.residuals.get(ch)
            if got is None:
                check(False, f"{inj.name}: no residual reported for {ch}")
                continue
            check(abs(abs(got) - abs(dv)) < 1e-6,
                  f"{inj.name}/{ch}: injected {dv:+.4f}, residual {got:+.4f}")
        extra = {k: v for k, v in inj.residuals.items()
                 if k not in inj.offsets and abs(v) > 1e-6}
        if extra:
            print(f"  {inj.name:<18} unexpected residual on {extra}")
        check(not extra,
              f"{inj.name}: residual on channels that were not injected: {extra}")
    print("  all injected residuals match their offsets")

    print("\nCASE 4  a live label with confidence, and the headline text")
    for inj in results:
        if not inj.ok:
            continue
        conf = ("  --  " if inj.fault_confidence is None
                else f"{inj.fault_confidence:.3f}")
        print(f"  {inj.name:<18} {str(inj.fault_label):<24} conf={conf}  "
              f"{inj.headline!r}")
        if not inj.crossed():
            # Sub-gate frames legitimately carry no label; asserting one here
            # is what crashed v0.1.0 on NoneType.__format__.
            check(inj.fault_label is None,
                  f"{inj.name}: below the gate yet labelled "
                  f"{inj.fault_label!r}")
            continue
        check(inj.fault_label is not None,
              f"{inj.name}: crossed the gate with no fault_label")
        check(inj.fault_confidence is not None and inj.fault_confidence > 0.3,
              f"{inj.name}: label {inj.fault_label} at confidence "
              f"{inj.fault_confidence}")
        check(inj.headline is not None and "unvalidated" in str(inj.headline),
              f"{inj.name}: headline {inj.headline!r} does not declare itself "
              f"unvalidated -- the gate is untrusted pre-retrain")
    print("  single-channel oil temperature reads as sensor_drift; the same "
          "excursion WITH a pressure loss reads as lubrication_degradation")

    print("\nCASE 5  smallest offset per channel that reaches the gate")
    print("  channel              direction   threshold   admission tol")
    thresholds: Dict[Tuple[str, float], Optional[float]] = {}
    for ch in SCAN_OFFSETS:
        for sign, arrow in ((1.0, "up  "), (-1.0, "down")):
            thr = detection_threshold(core, ch, sign=sign)
            thresholds[(ch, sign)] = thr
            tol = GATE_RESID_TOL.get(ch)
            shown = "  never  " if thr is None else f"{thr:9.4f}"
            print(f"  {ch:<20} {arrow}       {shown}   "
                  f"{'--' if tol is None else format(tol, '.5f')}")
            if thr is not None and tol is not None:
                check(thr > tol,
                      f"{ch} {arrow}: detection threshold {thr:.5f} is at or "
                      f"below the admission tolerance {tol} -- an admitted "
                      f"healthy frame could be scored as a fault")
            if thr is None:
                print(f"      no crossing found by bisection; oil pressure is "
                      f"known non-monotonic (-1.0 bar reads 0.5679)")

    print("\nCASE 6  discovered mapping: channel offset -> fault_label")
    scan = scan_labels(core)
    for ch, dv, inj in scan:
        lbl = str(inj.fault_label) if inj.crossed() else "(did not cross)"
        print(f"  {ch:<20} {dv:+9.2f}  p_anom={inj.p()}  {lbl}")
    # CASE 5 found the smallest crossing offset; CASE 6 applies a much
    # larger one. Where a small offset crosses and a large one does not, the
    # score is NON-MONOTONIC in that channel and no "threshold" can be read as
    # a severity floor.
    print("  non-monotonic channels (small offset crosses, large one does not):")
    nonmono = 0
    for ch, dv, inj in scan:
        thr = thresholds.get((ch, 1.0 if dv > 0 else -1.0))
        if thr is None or inj.crossed() or abs(dv) <= thr:
            continue
        nonmono += 1
        print(f"    {ch:<20} crosses at {thr:8.4f} but {dv:+9.2f} scores "
              f"{inj.p()} -- no crossing")
    if not nonmono:
        print("    none found")
    check(nonmono > 0,
          "no non-monotonicity found; the gate may have become monotone, in "
          "which case revisit caveat gate_is_non_monotonic")

    crossed = [x for _, _, x in scan if x.crossed()]
    labels = sorted({str(x.fault_label) for x in crossed})
    print(f"  distinct labels produced: {labels}")
    check(len(crossed) > 0, "no scanned channel crossed the gate")
    check(len(labels) >= 2,
          f"every injection produced the same label {labels} -- the multiclass "
          f"stage is not discriminating between channels")

    print("\nCASE 7  the dead class never wins")
    everything = results + crossed
    dead_wins = [x.name for x in everything
                 if str(x.fault_label) in DEAD_CLASSES]
    mass = max((x.fault_probabilities.get(DEAD_CLASSES[0], 0.0)
                for x in everything if x.fault_probabilities), default=0.0)
    print(f"  {DEAD_CLASSES[0]}: max probability mass seen {mass:.4f}, "
          f"times it won: {len(dead_wins)}")
    check(not dead_wins,
          f"{DEAD_CLASSES[0]} was reported as the label for {dead_wins} -- it "
          f"is a known dead class and must not be diagnosable")

    print("\nCASE 8  RUL under injection, and the safety channel")
    for inj in results:
        if not inj.ok:
            continue
        print(f"  {inj.name:<18} rul_raw="
              f"{'  --  ' if inj.rul_raw is None else format(inj.rul_raw, '9.3f')} "
              f"trusted={inj.rul_trusted} safety_alert={inj.safety_alert} "
              f"advisories={len(inj.advisories)}")
        check(inj.rul_trusted is False,
              f"{inj.name}: rul_trusted={inj.rul_trusted}; RUL is not "
              f"validated and must not be shown as minutes remaining")
    adv = [x for x in results if x.advisories]
    if adv:
        print(f"  example advisory: {adv[0].advisories[0][:110]}")

    print("\nCASE 9  declared caveats")
    for cv in injection_caveats():
        print(f"  {cv['id']:<40} verified={cv['verified']}")

    if fails:
        print(f"\nFAULT INJECTION SELF-CHECK FAILED -- {len(fails)}")
        for m in fails:
            print(f"  - {m}")
        raise SystemExit(1)
    print("\nFAULT INJECTION SELF-CHECK OK")
    print("  the detection path fires with live labels; offsets are synthetic, "
          "the gate is untrusted,")
    print("  p_anom grades neither severity nor direction, and a 32% oil "
          "pressure loss does not cross it")


if __name__ == "__main__":
    _self_test()
