"""
AERIS Phase 1 -- Node 2 residual engine.

measured - expected, where 'expected' comes from the five healthy-only
RandomForest baselines in physics_deck.py.

    signed   = measured - expected
    residual = |measured - expected|      <-- ABSOLUTE, what the models saw

MODE: ABSOLUTE. The deployed classifier was trained on absolute deltas;
the observed split thresholds in the artifact are strictly positive
(delta_coolant_temp_C 0.015-0.144, delta_oil_temperature_C 0.0005-0.38),
which is only possible for an absolute-valued column. Do not change this
without retraining.

SCOPE / HONESTY
---------------
* A residual is a deviation from what a healthy-only forest predicts for
  this operating point. It is not a deviation from physical truth.
* The baselines were fitted inside a bounded envelope. Outside it the
  forests extrapolate flat, so a residual there is fabricated. compute()
  refuses by default rather than returning a confident number.
* ABSOLUTE loses direction: low and high oil pressure produce the same
  residual. Direction is recovered from ResidualSet.signed, which is kept
  for the UI even though the model never sees it.
* This module owns the full 14-column model input vector. FEATURE_ORDER is
  cross-checked against reconstruction_config.json at construction.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from .physics_deck import (
    BASELINE_INPUT_ORDER,
    BASELINE_TARGETS,
    BaselineDeck,
    PhysicsDeckError,
)

ROOT = Path(__file__).resolve().parent.parent
CONFIG_NAME = "reconstruction_config.json"


def _find_config(name: str = CONFIG_NAME) -> Path | None:
    """Project root first, then anywhere below it. The config moved once."""
    direct = ROOT / name
    if direct.is_file():
        return direct
    for cand in sorted(ROOT.rglob(name)):
        if "__pycache__" not in cand.parts:
            return cand
    return None


CONFIG_PATH = _find_config()

RESIDUAL_MODE = "ABSOLUTE"

# The classifier input matrix, in exact column order.
FEATURE_ORDER: list[str] = [
    "altitude_ft",
    "ambient_temperature_C",
    "throttle_pct",
    "rpm",
    "fuelflow_kgh",
    "coolant_temp_C",
    "EGT_mean_C",
    "oil_pressure_bar",
    "oil_temperature_C",
    "delta_EGT_mean_C",
    "delta_coolant_temp_C",
    "delta_oil_pressure_bar",
    "delta_oil_temperature_C",
    "delta_fuelflow_kgh",
]

RAW_FEATURES: list[str] = FEATURE_ORDER[:9]
DELTA_FEATURES: list[str] = FEATURE_ORDER[9:]
DELTA_TARGETS: list[str] = [n[len("delta_"):] for n in DELTA_FEATURES]

# Channels a residual is computed for (the five baseline targets).
MEASURED_CHANNELS: list[str] = list(DELTA_TARGETS)

# Mid-envelope reference point used by _healthy_payload and the self-test.
NOMINAL_OP: dict[str, float] = {
    "rpm": 5000.0,
    "throttle_pct": 80.0,
    "altitude_ft": 6000.0,
    "ambient_temperature_C": 10.0,
}


class ResidualError(RuntimeError):
    """Payload malformed, or residuals would be meaningless."""


@dataclass(frozen=True)
class ResidualSet:
    """One frame of residuals plus the assembled model input vector."""

    payload: dict[str, float]
    expected: dict[str, float]
    signed: dict[str, float]
    residuals: dict[str, float]
    vector: np.ndarray
    features: dict[str, float]
    meaningful: bool
    violations: tuple[str, ...]
    mode: str = RESIDUAL_MODE

    def worst_channel(self) -> str | None:
        """Channel with the largest absolute residual, or None if empty."""
        if not self.residuals:
            return None
        return max(self.residuals, key=lambda k: self.residuals[k])

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "meaningful": self.meaningful,
            "violations": list(self.violations),
            "expected": dict(self.expected),
            "signed": dict(self.signed),
            "residuals": dict(self.residuals),
            "features": dict(self.features),
            "vector": [float(v) for v in self.vector],
        }


def _load_config_schema(path: Path) -> list[str] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ResidualError(f"{path.name} is not valid JSON: {exc}") from exc
    schema = data.get("feature_schema")
    return list(schema) if schema else None


def _validate_payload(payload: Mapping[str, Any]) -> dict[str, float]:
    """Reject missing, None, non-numeric and non-finite fields loudly."""
    if not isinstance(payload, Mapping):
        raise ResidualError(f"payload must be a mapping, got {type(payload).__name__}")

    missing = [k for k in RAW_FEATURES if payload.get(k) is None]
    if missing:
        raise ResidualError(f"missing/None fields {missing}")

    clean: dict[str, float] = {}
    bad: list[str] = []
    for k in RAW_FEATURES:
        try:
            v = float(payload[k])
        except (TypeError, ValueError):
            bad.append(k)
            continue
        if not math.isfinite(v):
            bad.append(k)
            continue
        clean[k] = v
    if bad:
        raise ResidualError(f"non-finite values {bad}")
    return clean

class ResidualCalculator:
    """Turns a telemetry frame into residuals and the 14-column vector."""

    def __init__(
        self,
        deck: BaselineDeck | None = None,
        config_path: Path | None = None,
        strict_config: bool = True,
    ) -> None:
        try:
            self.deck = deck if deck is not None else BaselineDeck()
        except PhysicsDeckError as exc:
            raise ResidualError(f"baseline deck unavailable: {exc}") from exc

        self.mode = RESIDUAL_MODE
        self.feature_order = list(FEATURE_ORDER)

        cfg = config_path or CONFIG_PATH
        self.config_path = cfg
        schema = _load_config_schema(cfg) if cfg is not None else None
        if schema is None:
            if strict_config:
                where = (f"no feature_schema key in {cfg}" if cfg is not None
                         else f"{CONFIG_NAME} not found under {ROOT}")
                raise ResidualError(
                    f"cannot verify column order: {where}")
            self.config_verified = False
        elif schema != self.feature_order:
            raise ResidualError(
                "feature order mismatch -- refusing to build a vector.\n"
                f"  code   : {self.feature_order}\n"
                f"  config : {schema}")
        else:
            self.config_verified = True

        deck_targets = list(getattr(self.deck, "targets", None) or BASELINE_TARGETS)
        self.deck_targets = deck_targets
        missing = [c for c in MEASURED_CHANNELS if c not in deck_targets]
        if missing:
            raise ResidualError(
                f"baseline deck has no model for {missing}; "
                f"deck provides {deck_targets}")

    def compute(
        self,
        payload: Mapping[str, Any],
        require_envelope: bool = True,
    ) -> ResidualSet:
        """Compute residuals for one frame.

        require_envelope=True (default) raises rather than returning a
        fabricated residual outside the baseline training envelope.
        """
        clean = _validate_payload(payload)
        op = {k: clean[k] for k in BASELINE_INPUT_ORDER}

        try:
            pred = self.deck.predict(op)
        except PhysicsDeckError as exc:
            raise ResidualError(f"baseline prediction failed: {exc}") from exc

        violations = tuple(pred.violations)
        if not pred.in_envelope and require_envelope:
            raise ResidualError(
                "operating point outside baseline training envelope; "
                "residuals would be fabricated. "
                + "; ".join(violations))

        expected = {c: float(pred.expected[c]) for c in MEASURED_CHANNELS}
        signed = {c: clean[c] - expected[c] for c in MEASURED_CHANNELS}
        residuals = {c: abs(signed[c]) for c in MEASURED_CHANNELS}

        features: dict[str, float] = {k: clean[k] for k in RAW_FEATURES}
        for fname, target in zip(DELTA_FEATURES, DELTA_TARGETS):
            features[fname] = residuals[target]

        vector = np.asarray([features[n] for n in self.feature_order], dtype=float)
        if vector.shape != (len(self.feature_order),):
            raise ResidualError(f"assembled vector has shape {vector.shape}")
        if not np.all(np.isfinite(vector)):
            raise ResidualError("assembled vector contains NaN or inf")

        return ResidualSet(
            payload=clean,
            expected=expected,
            signed=signed,
            residuals=residuals,
            vector=vector,
            features=features,
            meaningful=bool(pred.in_envelope),
            violations=violations,
        )


def _healthy_payload(
    op: Any = None,
    offsets: Mapping[str, float] | None = None,
    deck: BaselineDeck | None = None,
    **op_kwargs: float,
) -> dict[str, float]:
    """Build a synthetic frame that sits exactly on the healthy baseline.

    Measured channels are set to the baseline prediction, so residuals are
    ~0. `offsets` injects a simulated fault, e.g. {"EGT_mean_C": 45.0}.

    NOTE: these are SIMULATED offsets on a healthy baseline, not measured
    fault data. Anything validated with them is validated as plumbing only.
    """
    if op is not None and not isinstance(op, Mapping):
        # a ResidualCalculator or a BaselineDeck was passed positionally
        cand = getattr(op, "deck", op)
        if not hasattr(cand, "predict"):
            raise ResidualError(
                f"first argument must be a mapping, a ResidualCalculator or a "
                f"BaselineDeck, got {type(op).__name__}")
        deck, op = cand, None

    point = dict(NOMINAL_OP)
    if op:
        point.update({k: float(v) for k, v in dict(op).items()})
    if op_kwargs:
        point.update({k: float(v) for k, v in op_kwargs.items()})

    unknown = [k for k in point if k not in BASELINE_INPUT_ORDER]
    if unknown:
        raise ResidualError(f"not operating coordinates: {unknown}")

    d = deck if deck is not None else BaselineDeck()
    pred = d.predict({k: point[k] for k in BASELINE_INPUT_ORDER})

    frame = dict(point)
    for c in MEASURED_CHANNELS:
        frame[c] = float(pred.expected[c])
    for name, delta in (offsets or {}).items():
        if name not in frame:
            raise ResidualError(f"cannot offset unknown channel {name!r}")
        frame[name] = frame[name] + float(delta)
    return frame


def _self_test() -> None:
    calc = ResidualCalculator()
    print(f"feature count : {len(calc.feature_order)}")
    print(f"mode          : {calc.mode}   config verified: {calc.config_verified}")
    print(f"config        : {calc.config_path}")
    print(f"feature order : {calc.feature_order}")
    failures: list[str] = []

    print("\nCASE 1  healthy point -> residuals ~ 0")
    res = calc.compute(_healthy_payload(deck=calc.deck))
    for c, v in sorted(res.residuals.items(), key=lambda kv: -kv[1]):
        print(f"  {c:<22} {v:.4e}")
    if max(res.residuals.values()) > 1e-9:
        failures.append("healthy residuals not ~0")

    print("\nCASE 2  EGT +45 C (ABSOLUTE)")
    r2 = calc.compute(_healthy_payload(deck=calc.deck, offsets={"EGT_mean_C": 45.0}))
    print(f"  delta_EGT_mean_C     {r2.features['delta_EGT_mean_C']:.2f}")
    print(f"  delta_coolant_temp_C {r2.features['delta_coolant_temp_C']:.4e}")
    print(f"  signed EGT           {r2.signed['EGT_mean_C']:+.2f}")

    print("\nCASE 3  EGT -45 C -> same residual, opposite sign")
    r3 = calc.compute(_healthy_payload(deck=calc.deck, offsets={"EGT_mean_C": -45.0}))
    print(f"  delta_EGT_mean_C     {r3.features['delta_EGT_mean_C']:.2f}")
    print(f"  signed EGT           {r3.signed['EGT_mean_C']:+.2f}")
    if abs(r2.features["delta_EGT_mean_C"] - r3.features["delta_EGT_mean_C"]) > 1e-6:
        failures.append("ABSOLUTE mode is not symmetric")
    if r2.signed["EGT_mean_C"] * r3.signed["EGT_mean_C"] > 0:
        failures.append("signed residual lost direction")

    print("\nCASE 4  vector slot check (column order)")
    r4 = calc.compute(_healthy_payload(deck=calc.deck, offsets={"EGT_mean_C": 45.0}))
    checks = [(0, "altitude_ft", 6000.0), (3, "rpm", 5000.0), (9, "delta_EGT_mean_C", 45.0)]
    for idx, name, want in checks:
        got = float(r4.vector[idx])
        ok = abs(got - want) < 0.01 and calc.feature_order[idx] == name
        print(f"  slot {idx:>2} {name:<22} {got:>10.3f}  {'OK' if ok else 'WRONG'}")
        if not ok:
            failures.append(f"slot {idx} != {name}")

    print("\nCASE 5  outside envelope is refused, not guessed")
    idle = _healthy_payload(deck=calc.deck)
    idle.update({"rpm": 1200.0, "throttle_pct": 20.0})
    try:
        calc.compute(idle)
        print("  FAIL: fabricated residuals were returned")
        failures.append("envelope gate did not fire")
    except ResidualError as exc:
        print(f"  refused: {str(exc)[:96]}")
    r5 = calc.compute(idle, require_envelope=False)
    print(f"  with override: meaningful={r5.meaningful} violations={len(r5.violations)}")
    if r5.meaningful:
        failures.append("override should still report meaningful=False")

    print("\nCASE 6  malformed payloads")
    bad = _healthy_payload(deck=calc.deck)
    bad["EGT_mean_C"] = None
    try:
        calc.compute(bad)
        failures.append("None field accepted")
    except ResidualError as exc:
        print(f"  None field  rejected: {exc}")
    bad2 = _healthy_payload(deck=calc.deck)
    bad2["rpm"] = float("inf")
    try:
        calc.compute(bad2)
        failures.append("inf accepted")
    except ResidualError as exc:
        print(f"  inf value   rejected: {exc}")

    print("\nCASE 7  worst channel + JSON round-trip for the UI")
    print(f"  worst channel: {r2.worst_channel()}")
    blob = json.dumps(r2.to_dict())
    print(f"  serialised {len(blob)} bytes, keys={len(json.loads(blob))}")

    if failures:
        print("\nRESIDUAL SELF-CHECK FAILED")
        for f in failures:
            print(f"  - {f}")
        raise SystemExit(1)
    print("\nRESIDUAL SELF-CHECK OK")
    print("NOTE: faults above are simulated offsets on a healthy baseline,")
    print("      not measured fault data.")


if __name__ == "__main__":
    _self_test()