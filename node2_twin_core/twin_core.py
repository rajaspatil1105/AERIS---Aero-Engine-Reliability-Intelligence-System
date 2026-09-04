#!/usr/bin/env python
"""
node2_twin_core/twin_core.py -- Node 2 orchestrator.

One call per telemetry frame, returning a JSON-serializable result for
Node 3 (persistence + API) and Node 4 (dashboard).

PIPELINE
  1 safety_limits   catastrophic hard limits    -- operating-point independent
  2 residual_calc   baseline expectation + 14-feature vector
  3 plausibility    outside the healthy range   -- one-sided advisory
  4 gate            healthy / anomalous          (near chance -- see caveats)
  5 multiclass      which fault                  (unvalidated)
  6 rul_engine      smoothed RUL + gated trend
  7 shap            attribution, warmed at startup

STATUS PRECEDENCE
  CRITICAL    a hard limit is breached
  FAULT       the ML gate calls it anomalous
  ADVISORY    a measurement is outside the observed healthy range while
              the gate still says healthy -- this tier exists because the
              gate missed a 1.0 bar oil pressure loss in testing
  HEALTHY     nothing fired
  UNAVAILABLE outside the baseline training envelope, so residuals would
              be fabricated; ML is skipped but hard limits still apply

OUT OF ENVELOPE
Frames below 3000 rpm or 56.5 % throttle are outside the baseline
training data. The forests extrapolate flat there, so residuals are
meaningless. Such frames are NOT fed to the models and do NOT update
RUL history, but hard limits are absolute and still evaluated.
"""
from __future__ import annotations

import time
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

import numpy as np

from node2_twin_core.plausibility import check_healthy_range
from node2_twin_core.manifest import ManifestError, ModelManifest
from node2_twin_core.predictor import MODEL_CAVEATS, FaultPredictor
from node2_twin_core.residual_calc import ResidualError
from node2_twin_core.rul_engine import RUL_UNITS, RulEngine
from node2_twin_core.safety_limits import check_limits, missing_fields

STATUS_CRITICAL = "CRITICAL"
STATUS_FAULT = "FAULT"
STATUS_ADVISORY = "ADVISORY"
STATUS_HEALTHY = "HEALTHY"
STATUS_UNAVAILABLE = "UNAVAILABLE"

# Why a frame was refused. Prose lives in admit_reason; this is the field a
# UI switches on. Exactly one is set whenever ml_evaluated is False.
REFUSAL_TRANSIENT = "transient"                      # settles in seconds
REFUSAL_ENV_RECOVERABLE = "envelope_recoverable"     # pilot can clear it
REFUSAL_ENV_PERSISTENT = "envelope_persistent"       # dispatch decision
REFUSAL_TELEMETRY = "telemetry_unusable"             # residuals not computable

# Channels an operator changes within seconds. Anything else (ambient
# temperature above all) is weather and will not clear during the flight.
RECOVERABLE_CHANNELS = ("throttle_pct", "rpm", "altitude_ft")
_VIOL_CHANNEL = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)")


def classify_envelope(violations) -> str:
    """Recoverable only if EVERY violated channel is operator-controllable.
    A frame that is both too hot and at low throttle is persistent: the
    worse condition governs, and levelling off will not fix the weather."""
    chans = []
    for v in violations or ():
        m = _VIOL_CHANNEL.match(str(v))
        if m:
            chans.append(m.group(1))
    if chans and all(c in RECOVERABLE_CHANNELS for c in chans):
        return REFUSAL_ENV_RECOVERABLE
    return REFUSAL_ENV_PERSISTENT


STATUS_RANK = {
    STATUS_CRITICAL: 0, STATUS_FAULT: 1, STATUS_ADVISORY: 2,
    STATUS_HEALTHY: 3, STATUS_UNAVAILABLE: 4,
}


@dataclass
class TwinFrame:
    status: str
    headline: str
    timestamp: float
    latency_ms: float

    safety_alert: bool = False
    safety_breaches: list = field(default_factory=list)
    unmonitored_fields: list = field(default_factory=list)
    advisories: list = field(default_factory=list)

    ml_evaluated: bool = False
    in_envelope: bool = True
    envelope_violations: list = field(default_factory=list)
    admit_reason: str | None = None   # set only when a frame was refused
    refusal_class: str | None = None   # machine-readable: see REFUSAL_* below

    is_healthy: bool | None = None
    anomaly_probability: float | None = None
    gate_threshold: float | None = None
    fault_label: str | None = None
    fault_confidence: float | None = None
    fault_probabilities: dict = field(default_factory=dict)

    rul: float | None = None
    rul_raw: float | None = None
    rul_units: str = RUL_UNITS
    rul_trend_per_minute: float | None = None
    rul_trend_significant: bool = False
    rul_minutes_to_zero: float | None = None
    rul_trusted: bool = False

    expected: dict = field(default_factory=dict)
    residuals: dict = field(default_factory=dict)
    features: dict = field(default_factory=dict)

    explanation: dict | None = None
    error: str | None = None
    caveats: dict = field(default_factory=lambda: MODEL_CAVEATS)

    def to_dict(self) -> dict:
        """Plain JSON-serializable dict -- no numpy, no tuples."""
        def clean(o: Any) -> Any:
            if isinstance(o, dict):
                return {str(k): clean(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [clean(v) for v in o]
            if isinstance(o, (np.integer,)):
                return int(o)
            if isinstance(o, (np.floating,)):
                return float(o)
            if isinstance(o, np.bool_):
                return bool(o)
            return o
        return clean(asdict(self))

class TwinCore:
    def __init__(self, predictor=None, explain: bool = True,
                 warm_up: bool = True, verify_models: bool = True,
    ) -> None:
        self.predictor = predictor or FaultPredictor()
        self.calc = self.predictor.calc
        self.stats = self.calc.deck.stats
        self.rul_engine = RulEngine(calc=self.calc)

        self.shap = None
        if explain:
            from node2_twin_core.shap_explainer import ShapExplainer
            self.shap = ShapExplainer(predictor=self.predictor)
            if warm_up:
                # The FIRST explain_fault call costs ~7 s (shap internal
                # setup + numba compile); every later call is ~2 ms. Paying
                # it here keeps the first operator request from hanging.
                bg = self.shap.background[0]
                t0 = time.perf_counter()
                self.shap.explain_fault(bg)
                print(f"[twin] shap warm-up "
                      f"{(time.perf_counter() - t0) * 1000:.0f} ms "
                      f"(subsequent calls ~2 ms)")
        print(f"[twin] ready | explain={self.shap is not None}")
        if verify_models:
            try:
                ModelManifest.load().enforce()
                print("[twin_core] model manifest enforced")
            except ManifestError as exc:
                raise TwinCoreError(
                    "refusing to start: " + str(exc)) from exc


    def reset(self) -> None:
        self.rul_engine.reset()

    def process(self, payload: Mapping, explain: bool = False,
                admit_ok: bool = True,
                admit_reason: str | None = None) -> TwinFrame:
        t0 = time.perf_counter()
        ts = time.time()

        breaches = check_limits(payload)
        unmon = list(missing_fields(payload))
        advisories = [a.describe() for a in
                      check_healthy_range(payload, self.stats)]

        def finish(fr: TwinFrame) -> TwinFrame:
            fr.latency_ms = (time.perf_counter() - t0) * 1000.0
            return fr

        try:
            res = self.calc.compute(payload, require_envelope=False)
        except ResidualError as exc:
            return finish(TwinFrame(
                status=STATUS_CRITICAL if breaches else STATUS_UNAVAILABLE,
                headline=("CRITICAL SAFETY LIMIT BREACHED" if breaches
                          else "telemetry unusable"),
                refusal_class=REFUSAL_TELEMETRY,
                timestamp=ts, latency_ms=0.0,
                safety_alert=bool(breaches),
                safety_breaches=[b.describe() for b in breaches],
                unmonitored_fields=unmon, advisories=advisories,
                error=str(exc).splitlines()[0]))

        common = dict(
            timestamp=ts, latency_ms=0.0,
            safety_alert=bool(breaches),
            safety_breaches=[b.describe() for b in breaches],
            unmonitored_fields=unmon, advisories=advisories,
            in_envelope=res.meaningful,
            envelope_violations=list(res.violations),
            expected=dict(res.expected), residuals=dict(res.residuals),
            features=dict(res.features))

        # Outside the training envelope the models would be extrapolating.
        # Hard limits are absolute and were already evaluated above.
        if not res.meaningful:
            return finish(TwinFrame(
                status=STATUS_CRITICAL if breaches else STATUS_UNAVAILABLE,
                headline=("CRITICAL SAFETY LIMIT BREACHED" if breaches
                          else "outside trained envelope -- diagnosis withheld"),
                ml_evaluated=False,
                refusal_class=classify_envelope(res.violations),
                **common))

        # Transient: the frame is INSIDE the envelope but the lagged
        # channels have not settled, so residuals reflect thermal lag and
        # not engine condition. Same treatment as out-of-envelope -- models
        # skipped, RUL trend untouched, hard limits above still authoritative.
        # The caller decides admission (see shared/throttle_dynamics.py
        # admit_frame); this only decides what the refused frame looks like.
        if not admit_ok:
            return finish(TwinFrame(
                status=STATUS_CRITICAL if breaches else STATUS_UNAVAILABLE,
                headline=("CRITICAL SAFETY LIMIT BREACHED" if breaches
                          else "transient -- diagnosis withheld until settled"),
                ml_evaluated=False,
                admit_reason=admit_reason,
                refusal_class=REFUSAL_TRANSIENT,
                **common))

        x = np.asarray([res.vector], dtype=float)
        p_anom = float(self.predictor.gate.predict_proba(x)[0]
                       [self.predictor.gate_col])
        gate_fault = p_anom >= self.predictor.threshold

        label = conf = None
        probs: dict = {}
        if gate_fault or breaches:
            pr = np.asarray(self.predictor.multiclass.predict_proba(x)[0],
                            dtype=float)
            probs = {n: float(v)
                     for n, v in zip(self.predictor.fault_names, pr)}
            k = int(np.argmax(pr))
            label, conf = self.predictor.fault_names[k], float(pr[k])

        rul = self.rul_engine.update_vector(res.vector)

        if breaches:
            status = STATUS_CRITICAL
            head = "CRITICAL SAFETY LIMIT BREACHED"
        elif gate_fault:
            status = STATUS_FAULT
            head = f"{label} (unvalidated)"
        elif advisories:
            status = STATUS_ADVISORY
            head = "outside healthy range -- gate says healthy"
        else:
            status = STATUS_HEALTHY
            head = "healthy (low-confidence gate)"

        expl = None
        if explain and self.shap is not None:
            e = self.shap.explain_fault(res.vector)
            expl = {"target": e.target, "predicted_class": e.predicted_class,
                    "probability": e.probability, "method": e.method,
                    "elapsed_ms": e.elapsed_ms, "caveat": e.caveat,
                    "top": [{"feature": a.feature, "value": a.value,
                             "shap": a.shap} for a in e.top]}

        return finish(TwinFrame(
            status=status, headline=head,
            ml_evaluated=True,
            is_healthy=(status == STATUS_HEALTHY),
            anomaly_probability=p_anom,
            gate_threshold=self.predictor.threshold,
            fault_label=label, fault_confidence=conf,
            fault_probabilities=probs,
            rul=rul.smoothed, rul_raw=rul.raw,
            rul_trend_per_minute=(rul.trend_per_minute
                                  if rul.trend_significant else None),
            rul_trend_significant=rul.trend_significant,
            rul_minutes_to_zero=rul.minutes_to_zero,
            rul_trusted=rul.trusted,
            explanation=expl, **common))


def _self_test() -> None:
    import json

    from node2_twin_core.residual_calc import _healthy_payload

    core = TwinCore()
    fails = []
    p = _healthy_payload(core.calc)

    print("\nCASE 1  healthy frame")
    f = core.process(p)
    print(f"  {f.status:<12} {f.headline}")
    print(f"  p_anom={f.anomaly_probability:.3f} rul={f.rul:.1f} "
          f"latency={f.latency_ms:.1f} ms")
    if f.status != STATUS_HEALTHY:
        fails.append(f"healthy frame gave {f.status}")

    print("\nCASE 2  hard limit breach -> CRITICAL")
    f = core.process(dict(p, oil_pressure_bar=0.6))
    print(f"  {f.status:<12} {f.headline}")
    for b in f.safety_breaches:
        print(f"    {b}")
    if f.status != STATUS_CRITICAL:
        fails.append("0.6 bar did not give CRITICAL")

    print("\nCASE 3  the gap the gate missed -> ADVISORY")
    f = core.process(dict(p, oil_pressure_bar=p["oil_pressure_bar"] - 1.0))
    print(f"  {f.status:<12} {f.headline}")
    print(f"  p_anom={f.anomaly_probability:.3f} (below "
          f"{f.gate_threshold} -> gate says healthy)")
    for a in f.advisories:
        print(f"    {a}")
    if f.status != STATUS_ADVISORY:
        fails.append(f"2.16 bar gave {f.status}, expected ADVISORY")

    print("\nCASE 4  outside envelope -> UNAVAILABLE, ML skipped")
    f = core.process(dict(p, rpm=1200.0))
    print(f"  {f.status:<12} {f.headline}")
    print(f"  ml_evaluated={f.ml_evaluated} fault_label={f.fault_label}")
    for v in f.envelope_violations:
        print(f"    {v}")
    if f.ml_evaluated or f.status != STATUS_UNAVAILABLE:
        fails.append("out-of-envelope frame was still scored")

    print("\nCASE 5  hard limit still applies outside the envelope")
    f = core.process(dict(p, rpm=1200.0, oil_pressure_bar=0.5))
    print(f"  {f.status:<12} ml_evaluated={f.ml_evaluated} "
          f"breaches={len(f.safety_breaches)}")
    if f.status != STATUS_CRITICAL:
        fails.append("hard limit ignored outside envelope")

    print("\nCASE 10 transient refused -> UNAVAILABLE, still in envelope")
    f = core.process(p, admit_ok=False, admit_reason="throttle moving at 5.0 %/s")
    print(f"  {f.status:<12} {f.headline}")
    print(f"  ml_evaluated={f.ml_evaluated} in_envelope={f.in_envelope}")
    if f.status != STATUS_UNAVAILABLE or f.ml_evaluated:
        fails.append("transient frame was still scored")
    if not f.in_envelope:
        fails.append("transient frame reported out of envelope")
    if f.admit_reason is None:
        fails.append("transient refusal lost its admit_reason")
    if f.anomaly_probability is not None or f.rul is not None:
        fails.append("transient refusal carried ML output")

    print("\nCASE 11 hard limit still applies to a refused transient")
    f = core.process(dict(p, oil_pressure_bar=0.5), admit_ok=False,
                     admit_reason="throttle moving at 5.0 %/s")
    print(f"  {f.status:<12} breaches={len(f.safety_breaches)}")
    if f.status != STATUS_CRITICAL:
        fails.append("hard limit ignored on a refused transient")

    print("\nCASE 6  attribution on demand")
    f = core.process(dict(p, EGT_mean_C=p["EGT_mean_C"] + 80.0), explain=True)
    print(f"  {f.status:<12} {f.headline}")
    for a in f.explanation["top"][:3]:
        print(f"    {a['feature']:<26} shap={a['shap']:+.4f}")
    print(f"  shap {f.explanation['elapsed_ms']:.1f} ms | "
          f"frame {f.latency_ms:.1f} ms")
    if f.explanation is None:
        fails.append("explanation missing")

    print("\nCASE 7  JSON round-trip for Node 3")
    d = core.process(p, explain=True).to_dict()
    s = json.dumps(d)
    print(f"  serialized {len(s)} bytes, {len(d)} top-level keys")
    print(f"  round-trip ok: {json.loads(s)['status'] == d['status']}")

    print("\nCASE 8  streaming latency, 60 frames at 10 Hz budget")
    core.reset()
    rng = np.random.default_rng(1)
    lat = []
    for _ in range(60):
        q = dict(p, EGT_mean_C=p["EGT_mean_C"] + rng.normal(0, 2.0),
                 oil_pressure_bar=p["oil_pressure_bar"] + rng.normal(0, .02))
        lat.append(core.process(q).latency_ms)
    lat_s = sorted(lat)
    print(f"  mean={sum(lat)/len(lat):.1f} ms  p95={lat_s[56]:.1f} ms  "
          f"max={lat_s[-1]:.1f} ms   budget=100 ms")
    if lat_s[56] > 100.0:
        fails.append(f"p95 latency {lat_s[56]:.0f} ms exceeds frame budget")

    print("\nCASE 9  status precedence")
    for lbl, q in (("critical+advisory",
                    dict(p, oil_pressure_bar=0.5)),
                   ("advisory only",
                    dict(p, oil_pressure_bar=p["oil_pressure_bar"] - 1.0))):
        r = core.process(q)
        print(f"  {lbl:<20} -> {r.status}")

    if fails:
        print("\nTWIN CORE SELF-CHECK FAILED")
        for f2 in fails:
            print(f"  - {f2}")
        raise SystemExit(1)
    print("\nTWIN CORE SELF-CHECK OK")
    print("NOTE: Node 2 plumbing is verified. The models it wraps are not:")
    print("      gate near chance, RUL R2 negative, fuel_pressure_dev dead.")


if __name__ == "__main__":
    _self_test()
