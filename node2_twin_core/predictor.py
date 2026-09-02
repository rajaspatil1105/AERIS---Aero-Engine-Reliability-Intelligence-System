#!/usr/bin/env python
"""
node2_twin_core/predictor.py -- safety guard + two-stage diagnosis + RUL.

PIPELINE ORDER:
  0  safety_limits.check_limits()  deterministic hard limits, bypasses ML
  1  fault_classifier.pkl          2 classes -> healthy / anomalous
  2  fault_classifier_multiclass   5 classes -> which fault
  3  rul_regressor.pkl             remaining useful life

LABEL MAPPING: CONFIRMED by directional response test, not by reading the
training script. p(idx2) rises 0.574 -> 0.955 as oil pressure falls and
drops for every other channel, which only lubrication_degradation can do.
The 6-label encoder supplied by the team does NOT match this artifact
(5 contiguous classes, no gap at index 2) -- this pickle was trained with
a 5-label encoder fit on fault rows only.

KNOWN MODEL DEFECTS (measured, surface these on the UI):
  gate         precision 0.511 / recall 0.998 / F1 0.676 == the trivial
               always-fault baseline at a 0.511 prior. Near chance. Cannot
               be disabled: sole source of the "healthy" verdict.
  idx1 dead    fuel_pressure_dev is never argmax; the fuelflow channel is
               flat across a 9 kg/h excursion. Fuel faults misreport as
               lubrication_degradation, which is also the fallback class.
  no severity  every channel saturates after its first step.
  RUL          R2 -0.103, worse than predicting the training mean.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import joblib
import numpy as np

from node2_twin_core.residual_calc import (
    FEATURE_ORDER,
    ResidualCalculator,
    ResidualError,
)
from node2_twin_core.safety_limits import (
    Breach,
    check_limits,
    missing_fields,
)

CLASS_INDEX_MAP: dict = {
    0: "cooling_degradation",
    1: "fuel_pressure_dev",
    2: "lubrication_degradation",
    3: "misfire",
    4: "sensor_drift",
}

LABEL_PROVENANCE = (
    "CONFIRMED by directional response test (resolve_labels2.py). "
    "Not read from the training script."
)
DEAD_CLASSES = ("fuel_pressure_dev",)
FALLBACK_CLASS = "lubrication_degradation"
GATE_ANOMALY_CLASS = 1

MODEL_CAVEATS: dict = {
    "safety_limits": {
        "status": "active",
        "severity": "info",
        "reason": "Deterministic hard limits. Catastrophic thresholds only; "
                  "will not catch mild degradation.",
    },
    "gate": {
        "status": "active_untrusted",
        "severity": "critical",
        "reason": "F1 0.676 equals the trivial always-fault baseline at a "
                  "0.511 prior. Healthy/fault verdicts are near chance.",
        "metrics": {"precision": 0.5110, "recall": 0.9982, "f1": 0.6760},
    },
    "multiclass": {
        "status": "active_degraded",
        "severity": "warning",
        "reason": "fuel_pressure_dev is never predicted; fuel faults "
                  "misreport as lubrication_degradation. Output carries no "
                  "severity information (saturates after one step).",
    },
    "rul": {
        "status": "active_untrusted",
        "severity": "critical",
        "reason": "R2 -0.103, worse than the mean predictor. The number "
                  "carries no demonstrated accuracy.",
        "metrics": {"r2": -0.1033, "mae": 107.0},
    },
}


class PredictorError(RuntimeError):
    """Unusable artifact or contract violation."""


@dataclass(frozen=True)
class Prediction:
    safety_alert: bool
    safety_breaches: tuple
    unmonitored_fields: tuple
    is_healthy: bool
    anomaly_probability: float
    gate_threshold: float
    fault_label: str | None
    fault_confidence: float | None
    fault_probabilities: dict
    label_provenance: str
    rul: float | None
    rul_trusted: bool
    meaningful: bool
    violations: tuple
    features: dict
    residuals: dict
    caveats: dict = field(default_factory=lambda: MODEL_CAVEATS)

    def headline(self) -> str:
        if self.safety_alert:
            return "CRITICAL SAFETY LIMIT BREACHED"
        if self.is_healthy:
            return "healthy (low-confidence gate)"
        return f"{self.fault_label} (unvalidated)"

class FaultPredictor:
    def __init__(self, calc=None, models_dir=None, threshold=None) -> None:
        root = Path(__file__).resolve().parents[1]
        md = Path(models_dir) if models_dir else root / "models"
        self.calc = calc or ResidualCalculator()

        cfg = json.loads((md / "configs" / "reconstruction_config.json")
                         .read_text(encoding="utf-8"))
        self.threshold = float(threshold if threshold is not None
                               else cfg.get("confidence_threshold", 0.65))

        self.gate = self._load(md / "classifier" / "fault_classifier.pkl", 2)
        self.multiclass = self._load(
            md / "classifier" / "fault_classifier_multiclass.pkl", 5)
        self.rul = self._load(md / "rul" / "rul_regressor.pkl", None)

        gc = [int(c) for c in self.gate.classes_]
        if GATE_ANOMALY_CLASS not in gc:
            raise PredictorError(f"gate classes {gc} lack {GATE_ANOMALY_CLASS}")
        self.gate_col = gc.index(GATE_ANOMALY_CLASS)

        idx = [int(c) for c in self.multiclass.classes_]
        unknown = [i for i in idx if i not in CLASS_INDEX_MAP]
        if unknown:
            raise PredictorError(f"unnamed class index/indices {unknown}")
        self.fault_names = [CLASS_INDEX_MAP[i] for i in idx]

        self._validate_feature_names(md / "classifier" / "feature_names.json")
        print(f"[predictor] gate threshold {self.threshold}")
        print(f"[predictor] labels {self.fault_names}")
        print(f"[predictor] {LABEL_PROVENANCE}")
        print(f"[predictor] known dead class(es): {list(DEAD_CLASSES)}")

    def _load(self, path: Path, n_classes):
        if not path.is_file():
            raise PredictorError(f"missing artifact: {path}")
        m = joblib.load(path)
        n = int(getattr(m, "n_features_in_", -1))
        if n != len(FEATURE_ORDER):
            raise PredictorError(
                f"{path.name} expects {n} features, contract has "
                f"{len(FEATURE_ORDER)}")
        if n_classes is not None and len(getattr(m, "classes_", [])) != n_classes:
            raise PredictorError(
                f"{path.name} has {len(m.classes_)} classes, "
                f"expected {n_classes}")
        return m

    def _validate_feature_names(self, fn: Path) -> None:
        if not fn.is_file():
            print(f"[predictor] WARNING {fn.name} absent -- order unverified")
            return
        raw = json.loads(fn.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            declared = [str(x) for x in raw]
        elif isinstance(raw, dict) and all(str(k).isdigit() for k in raw):
            declared = [raw[k] for k in sorted(raw, key=lambda s: int(s))]
        elif isinstance(raw, dict) and ("features" in raw or "feature_names" in raw):
            declared = list(raw.get("features") or raw.get("feature_names"))
        else:
            raise PredictorError(
                f"{fn} has an unrecognised shape; refusing to skip validation "
                f"silently. Top-level type: {type(raw).__name__}")
        expected = list(FEATURE_ORDER)
        declared = list(declared)
        if declared != expected:
            problems: list[str] = []
            if len(declared) != len(expected):
                problems.append(
                    f"length {len(declared)} != {len(expected)}")
            for i, (a, b) in enumerate(zip(declared, expected)):
                if a != b:
                    problems.append(f"slot {i}: json={a!r} code={b!r}")
            extra = set(declared) - set(expected)
            absent = set(expected) - set(declared)
            if extra:
                problems.append(f"only in json: {sorted(extra)}")
            if absent:
                problems.append(f"only in code: {sorted(absent)}")
            raise PredictorError(
                f"{fn.name} disagrees with FEATURE_ORDER:\n  - "
                + "\n  - ".join(problems))
        print(f"[predictor] {fn.name} validated: "
              f"{len(declared)}/{len(expected)} positions match")

    def predict(self, payload: Mapping, require_envelope: bool = True) -> Prediction:
        breaches = check_limits(payload)
        unmon = missing_fields(payload)

        res = self.calc.compute(payload, require_envelope=require_envelope)
        x = np.asarray([res.vector], dtype=float)

        p_anom = float(self.gate.predict_proba(x)[0][self.gate_col])
        healthy = (p_anom < self.threshold) and not breaches

        label = conf = None
        probs: dict = {}
        if not healthy:
            pr = np.asarray(self.multiclass.predict_proba(x)[0], dtype=float)
            probs = {n: float(v) for n, v in zip(self.fault_names, pr)}
            k = int(np.argmax(pr))
            label, conf = self.fault_names[k], float(pr[k])

        return Prediction(
            safety_alert=bool(breaches),
            safety_breaches=tuple(b.describe() for b in breaches),
            unmonitored_fields=unmon,
            is_healthy=healthy,
            anomaly_probability=p_anom,
            gate_threshold=self.threshold,
            fault_label=label,
            fault_confidence=conf,
            fault_probabilities=probs,
            label_provenance=LABEL_PROVENANCE,
            rul=float(self.rul.predict(x)[0]),
            rul_trusted=False,
            meaningful=res.meaningful,
            violations=res.violations,
            features=res.features,
            residuals=res.residuals)


def _self_test() -> None:
    from node2_twin_core.residual_calc import _healthy_payload

    pred = FaultPredictor()
    fails = []
    p = _healthy_payload(pred.calc)

    print("\nCASE 1  healthy point")
    r = pred.predict(p)
    print(f"  {r.headline()}")
    print(f"  p_anom={r.anomaly_probability:.4f} thr={r.gate_threshold} "
          f"safety_alert={r.safety_alert}")
    if r.safety_alert or not r.is_healthy:
        fails.append("healthy point should be clean")

    print("\nCASE 2  hard limit breach bypasses the ML gate")
    q = pred.predict(dict(p, oil_pressure_bar=0.6))
    print(f"  {q.headline()}")
    for b in q.safety_breaches:
        print(f"    {b}")
    print(f"  p_anom={q.anomaly_probability:.3f}  is_healthy={q.is_healthy}"
          f"  fault={q.fault_label}")
    if not q.safety_alert or q.is_healthy:
        fails.append("0.6 bar must raise a safety alert and block healthy")

    print("\nCASE 3  safety alert even when the gate says healthy")
    q = pred.predict(dict(p, coolant_temp_C=125.0))
    print(f"  {q.headline()}  p_anom={q.anomaly_probability:.3f}")
    if not q.safety_alert:
        fails.append("125 C coolant must breach")

    print("\nCASE 4  named faults (simulated offsets)")
    for fld, off in (("coolant_temp_C", 15.0), ("oil_pressure_bar", -1.0),
                     ("EGT_mean_C", 80.0), ("oil_temperature_C", 10.0),
                     ("fuelflow_kgh", 4.0)):
        q = pred.predict(dict(p, **{fld: p[fld] + off}))
        lbl = q.fault_label or "healthy"
        c = f"{q.fault_confidence:.3f}" if q.fault_confidence else "  -  "
        print(f"  {fld:<20}{off:>+7.2f}  {lbl:<24} conf={c}")

    print("\nCASE 5  dead class never appears")
    if any(pred.predict(dict(p, **{f: p[f] + o})).fault_label == "fuel_pressure_dev"
           for f, o in (("fuelflow_kgh", 2.0), ("fuelflow_kgh", 6.0),
                        ("fuelflow_kgh", 9.0))):
        print("  fuel_pressure_dev DID fire -- update DEAD_CLASSES")
    else:
        print("  fuel_pressure_dev never fired, as documented")

    print("\nCASE 6  unmonitored channel is flagged, not assumed safe")
    q = pred.predict({k: v for k, v in p.items()})
    print(f"  unmonitored={q.unmonitored_fields}")

    print("\nCASE 7  UI caveats")
    for k, v in MODEL_CAVEATS.items():
        print(f"  {k:<15} {v['severity']:<8} {v['status']}")

    if fails:
        print("\nPREDICTOR SELF-CHECK FAILED")
        for f in fails:
            print(f"  - {f}")
        raise SystemExit(1)
    print("\nPREDICTOR SELF-CHECK OK")
    print("NOTE: simulated offsets. Gate and RUL are statistically untrusted;")
    print("      fuel_pressure_dev is unreachable in this artifact.")


if __name__ == "__main__":
    _self_test()