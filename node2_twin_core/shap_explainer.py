#!/usr/bin/env python
"""
node2_twin_core/shap_explainer.py -- per-frame attribution (criterion H).

WHY TWO EXPLAINERS
shap.TreeExplainer refuses multiclass GradientBoostingClassifier
("only supported for binary classification right now"). That is a gap in
shap's tree path, not a defect in the artifact. So:
  gate (binary GBC)    -> TreeExplainer, exact, sub-millisecond
  multiclass (5-class) -> Permutation over predict_proba, model-agnostic,
                          seconds per call, ON DEMAND ONLY
If the models are retrained as HistGradientBoosting / RandomForest /
XGBoost, TreeExplainer supports multiclass and this file simplifies.

BACKGROUND SET
Healthy operating points from the baseline deck with all residuals at
zero, so attributions read as "relative to a healthy engine at
comparable operating points".

WHAT SHAP DOES NOT TELL YOU
SHAP explains the MODEL, not the ENGINE. This classifier is unvalidated:
fuel_pressure_dev is never predicted, output saturates after one step,
and the gate runs at chance. A confident attribution means "the model
keyed on this feature", never "this component is failing".
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from node2_twin_core.residual_calc import FEATURE_ORDER, ResidualCalculator

TOP_N_DEFAULT = 5
BACKGROUND_SIZE = 16          # 48 rows cost ~7 s per explanation
DEFAULT_MAX_EVALS = 2 * len(FEATURE_ORDER) + 1
SEED = 0

EXPLAINER_CAVEAT = (
    "SHAP explains the model, not the engine. This classifier is "
    "unvalidated; attributions are diagnostic of model behaviour only."
)


class ShapExplainerError(RuntimeError):
    """Unusable explainer or malformed input."""


@dataclass(frozen=True)
class Attribution:
    feature: str
    value: float
    shap: float
    abs_shap: float

    def describe(self) -> str:
        d = "toward" if self.shap >= 0 else "away from"
        return (f"{self.feature}={self.value:.4g} pushes {d} the prediction "
                f"(shap {self.shap:+.4f})")


@dataclass(frozen=True)
class Explanation:
    target: str
    predicted_class: str
    predicted_index: int
    probability: float
    base_value: float
    top: tuple
    all_attributions: dict
    total_abs: float
    method: str
    elapsed_ms: float
    caveat: str = EXPLAINER_CAVEAT

    def summary(self, n: int = 3) -> str:
        parts = [f"{a.feature}({a.shap:+.3f})" for a in self.top[:n]]
        return (f"[{self.target}] {self.predicted_class} "
                f"p={self.probability:.3f} <- " + ", ".join(parts))


def build_healthy_background(calc, size: int = BACKGROUND_SIZE) -> np.ndarray:
    """Healthy reference rows spanning the baseline training envelope."""
    env = calc.deck.envelope
    rng = np.random.default_rng(SEED)
    rows = []
    for _ in range(size):
        op = {k: float(rng.uniform(*env[k]))
              for k in ("rpm", "throttle_pct", "altitude_ft",
                        "ambient_temperature_C")}
        exp = calc.deck.predict(op).expected
        payload = dict(op, **exp)          # measured == expected => residual 0
        rows.append(calc.compute(payload).vector)
    return np.asarray(rows, dtype=float)

class ShapExplainer:
    def __init__(self, predictor=None, top_n: int = TOP_N_DEFAULT,
                 max_evals: int = DEFAULT_MAX_EVALS,
                 background_size: int = BACKGROUND_SIZE) -> None:
        try:
            import shap
        except ImportError as exc:
            raise ShapExplainerError(
                "shap is not installed. Run: python -m pip install shap"
            ) from exc
        self._shap = shap

        if predictor is None:
            from node2_twin_core.predictor import FaultPredictor
            predictor = FaultPredictor()
        self.predictor = predictor
        self.calc = predictor.calc
        self.multiclass = predictor.multiclass
        self.gate = predictor.gate
        self.labels = list(predictor.fault_names)
        self.top_n = int(top_n)
        self.max_evals = max(int(max_evals), 2 * len(FEATURE_ORDER) + 1)

        self.background = build_healthy_background(self.calc, background_size)
        print(f"[shap] healthy background: {self.background.shape}")

        try:
            self.gate_explainer = shap.TreeExplainer(self.gate)
            self.gate_method = "TreeExplainer (exact)"
        except Exception as exc:
            self.gate_explainer = None
            self.gate_method = f"unavailable: {exc}"
        print(f"[shap] gate       -> {self.gate_method}")

        masker = shap.maskers.Independent(self.background,
                                          max_samples=len(self.background))
        self.fault_explainer = shap.explainers.Permutation(
            self.multiclass.predict_proba, masker, seed=SEED)
        self.fault_method = f"Permutation (max_evals={self.max_evals})"
        print(f"[shap] multiclass -> {self.fault_method}")

    @staticmethod
    def _pick(values, base, k: int, n_feat: int) -> tuple:
        v = np.asarray(values)
        if v.ndim == 3:
            arr = v[0, :, k]
        elif v.ndim == 2:
            arr = v[0] if v.shape[1] == n_feat else v[:, k]
        else:
            arr = v.ravel()
        b = np.asarray(base).ravel()
        bv = float(b[k]) if b.size > k else float(b[0])
        arr = np.asarray(arr, dtype=float).ravel()
        if arr.size != n_feat:
            raise ShapExplainerError(
                f"shap returned {arr.size} values, expected {n_feat}")
        return arr, bv

    @staticmethod
    def _check(vector) -> np.ndarray:
        x = np.asarray(vector, dtype=float).reshape(1, -1)
        if x.shape[1] != len(FEATURE_ORDER):
            raise ShapExplainerError(
                f"vector length {x.shape[1]} != {len(FEATURE_ORDER)}")
        if not np.all(np.isfinite(x)):
            raise ShapExplainerError("vector contains NaN or inf")
        return x

    def _assemble(self, x, vals, base, target, cls, k, prob, method, ms):
        atts = [Attribution(n, float(x[0, i]), float(vals[i]),
                            abs(float(vals[i])))
                for i, n in enumerate(FEATURE_ORDER)]
        return Explanation(
            target=target, predicted_class=cls, predicted_index=k,
            probability=prob, base_value=base,
            top=tuple(sorted(atts, key=lambda a: -a.abs_shap)[:self.top_n]),
            all_attributions={a.feature: a.shap for a in atts},
            total_abs=float(np.abs(vals).sum()),
            method=method, elapsed_ms=ms)

    def explain_gate(self, vector: Sequence) -> Explanation:
        if self.gate_explainer is None:
            raise ShapExplainerError(f"gate explainer {self.gate_method}")
        x = self._check(vector)
        t0 = time.perf_counter()
        vals, base = self._pick(self.gate_explainer.shap_values(x),
                                self.gate_explainer.expected_value,
                                self.predictor.gate_col, len(FEATURE_ORDER))
        ms = (time.perf_counter() - t0) * 1000.0
        p = float(self.gate.predict_proba(x)[0][self.predictor.gate_col])
        return self._assemble(x, vals, base, "gate", "anomalous",
                              self.predictor.gate_col, p, self.gate_method, ms)

    def explain_fault(self, vector: Sequence) -> Explanation:
        x = self._check(vector)
        proba = np.asarray(self.multiclass.predict_proba(x)[0], dtype=float)
        k = int(np.argmax(proba))
        # shap's Permutation explainer shuffles feature order using the
        # GLOBAL numpy RNG, and the explainer object persists between
        # calls, so its state advances. The seed= constructor argument
        # does not cover this. Without reseeding, two identical frames
        # differ by ~1e-3 -- enough to reorder the dashboard top-3.
        np.random.seed(SEED)
        t0 = time.perf_counter()
        sv = self.fault_explainer(x, max_evals=self.max_evals)
        ms = (time.perf_counter() - t0) * 1000.0
        vals, base = self._pick(sv.values, sv.base_values, k,
                                len(FEATURE_ORDER))
        return self._assemble(x, vals, base, "fault", self.labels[k], k,
                              float(proba[k]), self.fault_method, ms)

    def explain(self, payload: Mapping, require_envelope: bool = True) -> Explanation:
        res = self.calc.compute(payload, require_envelope=require_envelope)
        return self.explain_fault(res.vector)


def _self_test() -> None:
    from node2_twin_core.residual_calc import _healthy_payload

    ex = ShapExplainer()
    fails = []
    p = _healthy_payload(ex.calc)
    vec = ex.calc.compute(p).vector
    print(f"\ncaveat: {EXPLAINER_CAVEAT}")

    print("\nCASE 1  gate attribution (TreeExplainer, exact)")
    try:
        g = ex.explain_gate(vec)
        print(f"  {g.summary()}   [{g.elapsed_ms:.1f} ms]")
        print(f"  base_value={g.base_value:+.4f}")
        for a in g.top[:3]:
            print(f"    {a.feature:<26} shap={a.shap:+.4f}")
    except ShapExplainerError as exc:
        print(f"  unavailable: {exc}")
        fails.append("gate explainer failed (binary GBC should work)")

    print("\nCASE 2  fault attribution (Permutation)")
    e = ex.explain_fault(vec)
    print(f"  {e.summary()}   [{e.elapsed_ms:.0f} ms]")
    print(f"  base={e.base_value:+.4f} total|shap|={e.total_abs:.4f}")
    for a in e.top:
        print(f"    {a.feature:<26} value={a.value:>10.3f} shap={a.shap:+.4f}")
    if len(e.all_attributions) != 14:
        fails.append(f"{len(e.all_attributions)} attributions, expected 14")

    print("\nCASE 3  does SHAP name the feature we perturbed?")
    hits = 0
    for fld, off, expect in (("coolant_temp_C", 15.0, "delta_coolant_temp_C"),
                             ("EGT_mean_C", 80.0, "delta_EGT_mean_C"),
                             ("oil_pressure_bar", -1.0, "delta_oil_pressure_bar"),
                             ("oil_temperature_C", 10.0, "delta_oil_temperature_C"),
                             ("fuelflow_kgh", 4.0, "delta_fuelflow_kgh")):
        q = ex.calc.compute(dict(p, **{fld: p[fld] + off})).vector
        e3 = ex.explain_fault(q)
        names = [a.feature for a in e3.top[:3]]
        hit = expect in names
        hits += hit
        print(f"  {fld:<20}{off:>+7.2f} -> {e3.predicted_class:<24}"
              f" top={names[0]:<26} {'HIT' if hit else 'miss'}")
    print(f"  perturbed feature in top-3: {hits}/5")
    if hits == 0:
        fails.append("SHAP never identified the perturbed feature")

    print("\nCASE 4  additivity: base + sum(shap) ~= predict_proba")
    q = ex.calc.compute(dict(p, EGT_mean_C=p["EGT_mean_C"] + 80.0)).vector
    e4 = ex.explain_fault(q)
    s = sum(e4.all_attributions.values())
    print(f"  base={e4.base_value:.4f} + sum={s:+.4f} = {e4.base_value + s:.4f}"
          f"   actual p={e4.probability:.4f}")
    print(f"  error={abs(e4.base_value + s - e4.probability):.4f}")

    print("\nCASE 5  determinism (reseeded permutation)")
    runs = [ex.explain_fault(vec).all_attributions for _ in range(3)]
    md = max(abs(runs[0][k] - r[k]) for r in runs[1:] for k in runs[0])
    print(f"  max difference across three runs: {md:.2e}")
    if md > 1e-9:
        fails.append("permutation output not deterministic")

    print("\nCASE 6  bad input rejected")
    for bad, lbl in (([0.0] * 13, "wrong length"),
                     ([float('nan')] * 14, "NaN vector")):
        try:
            ex.explain_fault(bad)
            fails.append(f"{lbl} not rejected")
            print(f"  {lbl:<14} NOT REJECTED")
        except ShapExplainerError as exc:
            print(f"  {lbl:<14} rejected: {str(exc).splitlines()[0][:58]}")

    print("\nCASE 7  per-explanation cost (frame budget at 10 Hz = 100 ms)")
    t = [ex.explain_fault(vec).elapsed_ms for _ in range(3)]
    mean_ms = sum(t) / len(t)
    print(f"  fault: {mean_ms:.0f} ms mean over 3 runs")
    print(f"  per-frame feasible: {mean_ms < 100.0}  -> "
          f"{'inline' if mean_ms < 100.0 else 'ON DEMAND ONLY'}")
    if ex.gate_explainer is not None:
        tg = [ex.explain_gate(vec).elapsed_ms for _ in range(3)]
        print(f"  gate : {sum(tg)/len(tg):.1f} ms mean -- inline is fine")

    if fails:
        print("\nSHAP SELF-CHECK FAILED")
        for f in fails:
            print(f"  - {f}")
        raise SystemExit(1)
    print("\nSHAP SELF-CHECK OK")
    print("NOTE: attributions describe model behaviour on SIMULATED offsets,")
    print("      not evidence about a physical engine.")


if __name__ == "__main__":
    _self_test()