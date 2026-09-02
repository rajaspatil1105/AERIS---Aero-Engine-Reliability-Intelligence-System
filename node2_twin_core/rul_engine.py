#!/usr/bin/env python
"""
node2_twin_core/rul_engine.py -- stateful wrapper around rul_regressor.pkl.

The regressor is stateless and per-frame. At 10 Hz its output jitters with
sensor noise, so this adds:
  * EWMA smoothing
  * least-squares trend over a rolling window
  * a monotonic envelope (running minimum) reported ALONGSIDE the raw
    value, never replacing it
  * projection to zero, only when the trend is genuinely negative

TRUST: rul_trusted is hard-wired False. The artifact scores R2 = -0.103,
i.e. worse than predicting the training mean, so MAE 107 is not a
meaningful accuracy figure. Smoothing makes the number STABLE, not
CORRECT. A steady line here is not evidence of a healthy engine.

UNITS ARE UNKNOWN. No artifact records them. Rendering this as "hours"
would be an invention. Displayed unitless until the training script
confirms.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import joblib
import numpy as np

from node2_twin_core.residual_calc import (
    FEATURE_ORDER,
    ResidualCalculator,
)

RUL_UNITS = "unknown"
DEFAULT_WINDOW = 50          # frames retained for trend fitting
DEFAULT_ALPHA = 0.10         # EWMA weight on the newest sample
MIN_SAMPLES_FOR_TREND = 10
DEFAULT_FRAME_HZ = 10.0
TREND_T_CRIT = 2.0           # slope must exceed 2x its own standard error


class RulEngineError(RuntimeError):
    """Unusable artifact or malformed input."""


@dataclass(frozen=True)
class RulEstimate:
    raw: float                       # this frame, straight from the model
    smoothed: float                  # EWMA
    envelope: float                  # running minimum, non-increasing
    trend_per_frame: float | None    # LS slope, None until warmed up
    trend_per_minute: float | None
    trend_stderr: float | None       # standard error of the slope
    trend_significant: bool          # |slope| > TREND_T_CRIT * stderr
    frames_to_zero: float | None     # only when trend is negative
    minutes_to_zero: float | None
    samples: int
    warmed_up: bool
    trusted: bool                    # always False for this artifact
    units: str

    def describe(self) -> str:
        t = ("n/a" if self.trend_per_minute is None
             else (f"{self.trend_per_minute:+.3f}/min"
                   if self.trend_significant else "not significant"))
        return (f"RUL {self.smoothed:.1f} ({self.units}), raw {self.raw:.1f}, "
                f"trend {t}, UNTRUSTED")

class RulEngine:
    def __init__(self, calc=None, models_dir=None, window=DEFAULT_WINDOW,
                 alpha=DEFAULT_ALPHA, frame_hz=DEFAULT_FRAME_HZ) -> None:
        root = Path(__file__).resolve().parents[1]
        md = Path(models_dir) if models_dir else root / "models"
        f = md / "rul" / "rul_regressor.pkl"
        if not f.is_file():
            raise RulEngineError(f"missing artifact: {f}")
        self.model = joblib.load(f)
        n = int(getattr(self.model, "n_features_in_", -1))
        if n != len(FEATURE_ORDER):
            raise RulEngineError(
                f"{f.name} expects {n} features, contract has "
                f"{len(FEATURE_ORDER)}")

        self.calc = calc or ResidualCalculator()
        if not (0.0 < float(alpha) <= 1.0):
            raise RulEngineError(f"alpha must be in (0, 1], got {alpha}")
        if int(window) < MIN_SAMPLES_FOR_TREND:
            raise RulEngineError(
                f"window {window} below MIN_SAMPLES_FOR_TREND "
                f"{MIN_SAMPLES_FOR_TREND}")
        self.window = int(window)
        self.alpha = float(alpha)
        self.frame_hz = float(frame_hz)
        self.reset()

    def reset(self) -> None:
        self._hist: deque = deque(maxlen=self.window)
        self._ewma: float | None = None
        self._envelope: float | None = None

    def update_vector(self, vector) -> RulEstimate:
        v = np.asarray(vector, dtype=float).reshape(1, -1)
        if v.shape[1] != len(FEATURE_ORDER):
            raise RulEngineError(
                f"vector length {v.shape[1]} != {len(FEATURE_ORDER)}")
        if not np.all(np.isfinite(v)):
            raise RulEngineError("vector contains NaN or inf")

        raw = float(self.model.predict(v)[0])
        if not math.isfinite(raw):
            raise RulEngineError(f"model returned non-finite RUL: {raw}")

        self._ewma = raw if self._ewma is None else (
            self.alpha * raw + (1.0 - self.alpha) * self._ewma)
        self._envelope = (raw if self._envelope is None
                          else min(self._envelope, raw))
        self._hist.append(raw)

        slope = stderr = None
        significant = False
        n = len(self._hist)
        if n >= MIN_SAMPLES_FOR_TREND:
            y = np.asarray(self._hist, dtype=float)
            x = np.arange(n, dtype=float)
            slope, intercept = (float(c) for c in np.polyfit(x, y, 1))
            resid = y - (slope * x + intercept)
            dof = n - 2
            sxx = float(((x - x.mean()) ** 2).sum())
            if dof > 0 and sxx > 0:
                s_res = math.sqrt(float((resid ** 2).sum()) / dof)
                stderr = s_res / math.sqrt(sxx)
                significant = abs(slope) > TREND_T_CRIT * stderr

        per_min = None if slope is None else slope * self.frame_hz * 60.0

        # Projection ONLY on a statistically significant negative slope.
        # Without this gate, sensor noise on a steady engine produces a
        # confident "minutes to failure" figure. Observed in testing: a
        # healthy engine trended DOWN twice as steeply as a real
        # degradation ramp, purely from noise.
        f2z = m2z = None
        if significant and slope is not None and slope < 0 and self._ewma > 0:
            f2z = float(self._ewma / -slope)
            m2z = f2z / (self.frame_hz * 60.0)

        return RulEstimate(
            raw=raw,
            smoothed=float(self._ewma),
            envelope=float(self._envelope),
            trend_per_frame=slope,
            trend_per_minute=per_min,
            trend_stderr=stderr,
            trend_significant=significant,
            frames_to_zero=f2z,
            minutes_to_zero=m2z,
            samples=n,
            warmed_up=n >= MIN_SAMPLES_FOR_TREND,
            trusted=False,
            units=RUL_UNITS)

    def update(self, payload: Mapping, require_envelope: bool = True) -> RulEstimate:
        res = self.calc.compute(payload, require_envelope=require_envelope)
        return self.update_vector(res.vector)


def _self_test() -> None:
    from node2_twin_core.residual_calc import _healthy_payload

    eng = RulEngine()
    fails = []
    p = _healthy_payload(eng.calc)
    rng = np.random.default_rng(0)

    def noisy(base, k=1.0):
        return dict(base,
                    EGT_mean_C=base["EGT_mean_C"] + rng.normal(0, 2.0 * k),
                    coolant_temp_C=base["coolant_temp_C"] + rng.normal(0, .3 * k),
                    oil_pressure_bar=base["oil_pressure_bar"] + rng.normal(0, .02 * k),
                    oil_temperature_C=base["oil_temperature_C"] + rng.normal(0, .2 * k),
                    fuelflow_kgh=base["fuelflow_kgh"] + rng.normal(0, .1 * k))

    print(f"units={RUL_UNITS}  window={eng.window}  alpha={eng.alpha}"
          f"  frame_hz={eng.frame_hz}")

    print("\nCASE 1  warm-up gating")
    for i in range(1, MIN_SAMPLES_FOR_TREND + 2):
        e = eng.update(noisy(p))
        if i in (1, MIN_SAMPLES_FOR_TREND - 1, MIN_SAMPLES_FOR_TREND,
                 MIN_SAMPLES_FOR_TREND + 1):
            print(f"  frame {i:>3}  samples={e.samples:>3} "
                  f"warmed={e.warmed_up}  trend="
                  f"{'None' if e.trend_per_frame is None else f'{e.trend_per_frame:+.4f}'}")
            if e.samples < MIN_SAMPLES_FOR_TREND and e.trend_per_frame is not None:
                fails.append("trend reported before warm-up")

    print("\nCASE 2  smoothing reduces variance on a steady engine")
    eng.reset()
    raws, smoo = [], []
    for _ in range(120):
        e = eng.update(noisy(p))
        raws.append(e.raw)
        smoo.append(e.smoothed)
    sr, ss = float(np.std(raws)), float(np.std(smoo[40:]))
    print(f"  std(raw)={sr:.4f}   std(smoothed, post-warmup)={ss:.4f}")
    if sr > 1e-9 and ss > sr:
        fails.append("smoothing increased variance")

    print("\nCASE 3  degradation ramp (oil pressure 3.16 -> 2.30)")
    eng.reset()
    first = last = None
    for i in range(60):
        q = dict(p, oil_pressure_bar=p["oil_pressure_bar"] - 0.86 * i / 59.0)
        e = eng.update(noisy(q, 0.3))
        if i == 0:
            first = e.raw
        last = e
    print(f"  raw {first:.1f} -> {last.raw:.1f}   smoothed {last.smoothed:.1f}")
    print(f"  trend {last.trend_per_minute:+.2f}/min  "
          f"minutes_to_zero="
          f"{'n/a' if last.minutes_to_zero is None else f'{last.minutes_to_zero:.1f}'}")
    if last.raw >= first:
        print("  OBSERVATION: RUL did NOT fall under worsening oil pressure.")
        print("  Consistent with R2 = -0.103. Plumbing is fine; model is not.")

    print("\nCASE 4  envelope is non-increasing")
    eng.reset()
    envs = [eng.update(noisy(p)).envelope for _ in range(80)]
    ok = all(b <= a + 1e-12 for a, b in zip(envs, envs[1:]))
    print(f"  first={envs[0]:.2f} last={envs[-1]:.2f} non_increasing={ok}")
    if not ok:
        fails.append("envelope increased")

    print("\nCASE 5  reset clears state")
    eng.reset()
    e = eng.update(p)
    print(f"  samples={e.samples} warmed={e.warmed_up} trend={e.trend_per_frame}")
    if e.samples != 1 or e.warmed_up:
        fails.append("reset did not clear history")

    print("\nCASE 6  trust flag and bad input")
    if e.trusted:
        fails.append("trusted must be False")
    print(f"  trusted={e.trusted}")
    for bad, lbl in (([0.0] * 13, "wrong length"),
                     ([float('nan')] * 14, "NaN vector")):
        try:
            eng.update_vector(bad)
            fails.append(f"{lbl} not rejected")
            print(f"  {lbl:<14} NOT REJECTED")
        except RulEngineError as ex:
            print(f"  {lbl:<14} rejected: {str(ex).splitlines()[0][:60]}")

    print("\nCASE 8  steady engine must NOT project a failure")
    eng.reset()
    sig = 0
    for _ in range(200):
        s = eng.update(noisy(p))
        if s.minutes_to_zero is not None:
            sig += 1
    print(f"  frames projecting failure on a HEALTHY engine: {sig}/200")
    print(f"  last slope={s.trend_per_frame:+.4f} stderr="
          f"{s.trend_stderr:.4f} significant={s.trend_significant}")
    if sig > 20:
        fails.append(f"{sig}/200 false failure projections on steady data")

    print("\nCASE 7  describe() for the UI")
    print(f"  {last.describe()}")

    if fails:
        print("\nRUL ENGINE SELF-CHECK FAILED")
        for f in fails:
            print(f"  - {f}")
        raise SystemExit(1)
    print("\nRUL ENGINE SELF-CHECK OK")
    print("NOTE: smoothing makes the number stable, not correct. Units are")
    print("      unknown and must not be labelled 'hours' on the dashboard.")


if __name__ == "__main__":
    _self_test()