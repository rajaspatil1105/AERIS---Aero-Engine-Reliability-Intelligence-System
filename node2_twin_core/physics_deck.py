#!/usr/bin/env python
"""
node2_twin_core/physics_deck.py -- health baseline deck.

Replaces the former CSV/Delaunay interpolator. Wraps the five
RandomForestRegressor artifacts in models/baseline/.

Baselines were fitted on HEALTHY-ONLY rows (fault_type == 'healthy',
50,000 samples). Their output is therefore the EXPECTED HEALTHY value at
a given operating point. Residual = measured - expected.

INPUT ORDER IS NOT STORED IN THE ARTIFACTS (feature_names_in_ is absent).
The order below was recovered from tree split thresholds, agreed across
all five forests, and confirmed by a monotonic throttle sweep. It differs
from the 14-feature classifier order. DO NOT REORDER.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import joblib
import numpy as np

BASELINE_INPUT_ORDER: tuple[str, ...] = (
    "rpm",
    "throttle_pct",
    "altitude_ft",
    "ambient_temperature_C",
)

BASELINE_TARGETS: tuple[str, ...] = (
    "EGT_mean_C",
    "coolant_temp_C",
    "oil_pressure_bar",
    "oil_temperature_C",
    "fuelflow_kgh",
)


class PhysicsDeckError(RuntimeError):
    """Raised for any unusable baseline artifact or malformed query."""


@dataclass(frozen=True)
class BaselinePrediction:
    expected: dict            # target name -> expected healthy value
    in_envelope: bool         # False if any input outside training range
    violations: tuple         # human-readable reasons


class BaselineDeck:
    def __init__(self, models_dir=None, config_path=None) -> None:
        root = Path(__file__).resolve().parents[1]
        self.models_dir = Path(models_dir) if models_dir else root / "models" / "baseline"
        self.config_path = (Path(config_path) if config_path
                            else root / "models" / "configs" / "reconstruction_config.json")
        if not self.models_dir.is_dir():
            raise PhysicsDeckError(f"baseline directory not found: {self.models_dir}")
        if not self.config_path.is_file():
            raise PhysicsDeckError(f"config not found: {self.config_path}")

        self.stats = json.loads(self.config_path.read_text(encoding="utf-8"))["baseline_stats"]

        self.models = {}
        for name in BASELINE_TARGETS:
            f = self.models_dir / f"{name}_baseline.pkl"
            if not f.is_file():
                raise PhysicsDeckError(f"missing baseline artifact: {f}")
            m = joblib.load(f)
            n = int(getattr(m, "n_features_in_", -1))
            if n != len(BASELINE_INPUT_ORDER):
                raise PhysicsDeckError(
                    f"{f.name} expects {n} inputs, contract declares "
                    f"{len(BASELINE_INPUT_ORDER)}: {list(BASELINE_INPUT_ORDER)}")
            self.models[name] = m

        self.envelope = self._build_envelope()

        self._force_single_thread()

    def _force_single_thread(self) -> None:
        """Forests were pickled with n_jobs=-1.

        For a single-row predict, joblib's thread-pool dispatch costs ~15 ms
        per forest -- far more than evaluating 50 depth-10 trees. Measured:
        90.7 ms -> 27.8 ms per frame across the five baselines, with
        bit-identical outputs (n_jobs changes scheduling, not arithmetic).
        Revert to -1 only for batch scoring, never for 10 Hz inference.
        """
        for name, m in self.models.items():
            if getattr(m, "n_jobs", None) not in (None, 1):
                m.n_jobs = 1

    def _build_envelope(self) -> dict:
        """Intersection of the documented operating ranges. Random forests
        extrapolate as flat constants, so anything outside is meaningless."""
        env: dict = {}
        for st in self.stats.values():
            for k, (lo, hi) in st["operating_range"].items():
                if k in env:
                    env[k] = (max(env[k][0], float(lo)), min(env[k][1], float(hi)))
                else:
                    env[k] = (float(lo), float(hi))
        return env
    def check_envelope(self, op: Mapping) -> tuple:
        out = []
        for k in BASELINE_INPUT_ORDER:
            lo, hi = self.envelope[k]
            v = float(op[k])
            if v < lo or v > hi:
                out.append(f"{k}={v:g} outside trained range [{lo:g}, {hi:g}]")
        return tuple(out)

    def predict(self, op: Mapping) -> BaselinePrediction:
        missing = [k for k in BASELINE_INPUT_ORDER if k not in op]
        if missing:
            raise PhysicsDeckError(f"operating point missing key(s): {missing}")
        vals = [float(op[k]) for k in BASELINE_INPUT_ORDER]
        if any(not math.isfinite(v) for v in vals):
            raise PhysicsDeckError(f"operating point contains NaN/inf: {dict(zip(BASELINE_INPUT_ORDER, vals))}")

        x = np.asarray([vals], dtype=float)
        expected = {n: float(m.predict(x)[0]) for n, m in self.models.items()}
        viol = self.check_envelope(op)
        return BaselinePrediction(expected=expected, in_envelope=not viol, violations=viol)


def _self_test() -> None:
    deck = BaselineDeck()
    print(f"input order  : {list(BASELINE_INPUT_ORDER)}")
    print(f"targets      : {list(BASELINE_TARGETS)}")
    print("envelope     :")
    for k in BASELINE_INPUT_ORDER:
        lo, hi = deck.envelope[k]
        print(f"  {k:<24} [{lo:g}, {hi:g}]")

    fails = []

    print("\nCASE 1  nominal point")
    op = {"rpm": 5000.0, "throttle_pct": 80.0,
          "altitude_ft": 6000.0, "ambient_temperature_C": 10.0}
    r = deck.predict(op)
    for n, v in r.expected.items():
        st = deck.stats[n]
        ok = st["min"] <= v <= st["max"]
        print(f"  {n:<20} {v:>10.3f}  documented [{st['min']:.3f}, {st['max']:.3f}]"
              f"  {'INSIDE' if ok else 'OUT'}")
        if not ok:
            fails.append(f"{n} outside documented range")
    print(f"  in_envelope={r.in_envelope}")

    print("\nCASE 2  throttle sweep 60 -> 100 % (order sanity)")
    for n in ("EGT_mean_C", "fuelflow_kgh"):
        ys = [deck.predict(dict(op, throttle_pct=t)).expected[n]
              for t in (60.0, 70.0, 80.0, 90.0, 100.0)]
        rising = ys[-1] > ys[0] + 1e-6
        print(f"  {n:<16} {[round(v, 2) for v in ys]}  rising={rising}")
        if not rising:
            fails.append(f"{n} not rising with throttle -- input order suspect")

    print("\nCASE 3  below trained envelope (idle)")
    r = deck.predict(dict(op, rpm=1200.0, throttle_pct=20.0))
    print(f"  in_envelope={r.in_envelope}")
    for v in r.violations:
        print(f"    {v}")
    if r.in_envelope or len(r.violations) != 2:
        fails.append("idle point should report 2 envelope violations")

    print("\nCASE 4/5  malformed queries")
    for bad, lbl in (({"rpm": 5000.0}, "missing keys"),
                     (dict(op, rpm=float("nan")), "NaN input")):
        try:
            deck.predict(bad)
            fails.append(f"{lbl} not rejected")
            print(f"  {lbl:<14} NOT REJECTED")
        except PhysicsDeckError as e:
            print(f"  {lbl:<14} rejected: {str(e).splitlines()[0]}")

    if fails:
        print("\nPHYSICS DECK SELF-CHECK FAILED")
        for f in fails:
            print(f"  - {f}")
        raise SystemExit(1)
    print("\nPHYSICS DECK SELF-CHECK OK")
    print("NOTE: baselines are healthy-only fits; residuals outside the")
    print("      envelope above are not meaningful.")


if __name__ == "__main__":
    _self_test()