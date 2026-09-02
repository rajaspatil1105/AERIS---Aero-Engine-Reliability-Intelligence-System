"""
AERIS Phase 1 -- Node 1 vibration signal processing.

Converts a raw vibration time-domain window into rotational-order features
(1x / 2x / 3x), a crest factor, and a high-frequency band energy indicator.

This module is deliberately free of networking, schema, and I/O concerns so
that it can be tested in isolation against signals of known content.

METHOD / HONESTY NOTES
----------------------
* Real DSP. Mean removal -> Hann window -> scipy.fft.rfft -> one-sided
  amplitude spectrum with coherent-gain correction -> per-order band search.
* Amplitude per order is estimated by root-sum-square over the 3 bins centred
  on the local spectral maximum inside a tolerance band around k*f0, divided
  by sqrt(1.5). Rationale: a Hann-windowed sinusoid spreads its main lobe over
  ~3 bins. Bin-centred, the 3-bin RSS is 1.2247x (= sqrt(1.5)) the true
  amplitude; at half-bin offset it is ~1.20x. The correction therefore holds
  to ~2% regardless of where the harmonic falls between bins, whereas naive
  peak-bin picking loses up to 15% (Hann scallop loss, 1.42 dB).
* `bearing_band_g` is an APPROXIMATE relative energy indicator suitable for
  trending only. Spectral leakage and window equivalent-noise-bandwidth are
  not rigorously compensated. Do not treat it as a calibrated RMS in g.
* Any feature that cannot be computed validly is returned as None together
  with a reason string. None means "not computable", never zero.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
from scipy.fft import rfft, rfftfreq

logger = logging.getLogger(__name__)

# Hann 3-bin RSS coherent correction. See METHOD note above.
_HANN_RSS_CORRECTION = math.sqrt(1.5)

# Orders extracted. Schema exposes 1x, 2x, 3x.
_ORDERS = (1, 2, 3)


@dataclass(frozen=True)
class DSPConfig:
    """Vibration processing configuration.

    sample_rate_hz:
        Vibration ADC sample rate. REQUIRED. Must come from the telemetry
        source. There is no safe default -- an incorrect fs scales every
        frequency axis and invalidates all order extraction.
    min_samples:
        Below this window length the spectrum is too coarse to be useful.
    min_rpm:
        Below this speed f0 is not meaningful (engine stopped/cranking).
    order_tolerance_frac:
        Half-width of the search band around k*f0, as a fraction of k*f0.
        0.05 => +/-5%, which absorbs RPM jitter within the window.
    nyquist_margin_frac:
        An order is only accepted if k*f0 <= this fraction of Nyquist.
    bearing_band_start_order:
        Lower edge of the high-frequency band, in orders of f0.
    """

    sample_rate_hz: float
    min_samples: int = 64
    min_rpm: float = 500.0
    order_tolerance_frac: float = 0.05
    nyquist_margin_frac: float = 0.9
    bearing_band_start_order: float = 3.5

    def __post_init__(self) -> None:
        if self.sample_rate_hz <= 0:
            raise ValueError(
                f"sample_rate_hz must be > 0, got {self.sample_rate_hz}. "
                "This value must be supplied by the telemetry source."
            )
        if self.min_samples < 8:
            raise ValueError(f"min_samples too small: {self.min_samples}")
        if not 0 < self.order_tolerance_frac < 0.5:
            raise ValueError(
                f"order_tolerance_frac out of range: {self.order_tolerance_frac}"
            )

    @property
    def nyquist_hz(self) -> float:
        return self.sample_rate_hz / 2.0


@dataclass
class VibrationFeatures:
    """Structured DSP result for one vibration window.

    Fields mapping onto the shared schema are named to match it. Fields that
    could not be computed are None and the reason is recorded in `notes`.
    """

    # Schema-mapped outputs
    vib_f0_hz: Optional[float] = None
    vib_1x_g: Optional[float] = None
    vib_2x_g: Optional[float] = None
    vib_3x_g: Optional[float] = None
    vib_crest_factor: Optional[float] = None
    vib_bearing_band_g: Optional[float] = None

    # Computed from the raw window. The bridge decides whether these override
    # source-reported vib_rms_g / vib_peak_g, or are only used for crest factor.
    computed_rms_g: Optional[float] = None
    computed_peak_g: Optional[float] = None

    # Diagnostics -- not persisted, for logging and test assertions.
    n_samples: int = 0
    freq_resolution_hz: Optional[float] = None
    valid: bool = False
    notes: Dict[str, str] = field(default_factory=dict)

    def to_schema_updates(self) -> Dict[str, Any]:
        """Return only the schema-mapped vib_* fields, for payload update."""
        return {
            "vib_f0_hz": self.vib_f0_hz,
            "vib_1x_g": self.vib_1x_g,
            "vib_2x_g": self.vib_2x_g,
            "vib_3x_g": self.vib_3x_g,
            "vib_crest_factor": self.vib_crest_factor,
            "vib_bearing_band_g": self.vib_bearing_band_g,
        }


class VibrationProcessor:
    """Stateless order-analysis processor. Reusable across frames."""

    def __init__(self, config: DSPConfig) -> None:
        self.config = config

    # ---------------------------------------------------------------- #
    # public API
    # ---------------------------------------------------------------- #
    def process(
        self,
        samples: Optional[Sequence[float]],
        rpm: float,
    ) -> VibrationFeatures:
        """Extract order features from one vibration window.

        Args:
            samples: raw time-domain vibration window in g. None/empty is
                handled and reported, not raised.
            rpm: crankshaft speed for this window, used for f0 = rpm / 60.

        Returns:
            VibrationFeatures. `valid` is True only if the spectrum was
            actually computed; individual orders may still be None.
        """
        out = VibrationFeatures()

        # --- window validity ---
        if samples is None:
            out.notes["window"] = "no vibration samples supplied"
            return out

        x = np.asarray(samples, dtype=np.float64)
        out.n_samples = int(x.size)

        if x.ndim != 1:
            out.notes["window"] = f"expected 1-D window, got shape {x.shape}"
            return out
        if x.size < self.config.min_samples:
            out.notes["window"] = (
                f"window too short: {x.size} < {self.config.min_samples}"
            )
            return out
        if not np.all(np.isfinite(x)):
            out.notes["window"] = "window contains NaN or inf"
            return out

        # --- time-domain stats (computed before windowing) ---
        x = x - float(np.mean(x))  # remove DC / static offset
        rms = float(np.sqrt(np.mean(x**2)))
        peak = float(np.max(np.abs(x)))
        out.computed_rms_g = rms
        out.computed_peak_g = peak

        if rms > 0.0:
            out.vib_crest_factor = peak / rms
        else:
            out.notes["crest_factor"] = "zero RMS after DC removal (flat signal)"

        # --- spectrum ---
        n = x.size
        window = np.hanning(n)
        coherent_gain = float(np.sum(window))
        if coherent_gain <= 0.0:
            out.notes["spectrum"] = "degenerate window"
            return out

        spectrum = np.abs(rfft(x * window)) * (2.0 / coherent_gain)
        freqs = rfftfreq(n, d=1.0 / self.config.sample_rate_hz)

        df = float(self.config.sample_rate_hz) / n
        out.freq_resolution_hz = df
        out.valid = True

        # --- fundamental ---
        if rpm < self.config.min_rpm:
            out.notes["f0"] = (
                f"rpm {rpm:.1f} below min_rpm {self.config.min_rpm} -- "
                "order analysis not meaningful"
            )
            return out

        f0 = rpm / 60.0
        if f0 < 3.0 * df:
            out.notes["f0"] = (
                f"f0 {f0:.2f} Hz unresolvable at df {df:.3f} Hz "
                "(window too short for this speed)"
            )
            return out
        out.vib_f0_hz = f0

        # --- per-order amplitudes ---
        for k in _ORDERS:
            target = k * f0
            amp, reason = self._order_amplitude(spectrum, freqs, target, df)
            setattr(out, f"vib_{k}x_g", amp)
            if reason:
                out.notes[f"{k}x"] = reason

        # --- high-frequency band energy ---
        band_lo = self.config.bearing_band_start_order * f0
        out.vib_bearing_band_g, reason = self._band_energy(spectrum, freqs, band_lo)
        if reason:
            out.notes["bearing_band"] = reason

        return out

    # ---------------------------------------------------------------- #
    # internals
    # ---------------------------------------------------------------- #
    def _order_amplitude(
        self,
        spectrum: np.ndarray,
        freqs: np.ndarray,
        target_hz: float,
        df: float,
    ) -> tuple[Optional[float], Optional[str]]:
        """3-bin RSS amplitude at the local maximum near target_hz."""
        limit = self.config.nyquist_margin_frac * self.config.nyquist_hz
        if target_hz > limit:
            return None, (
                f"{target_hz:.1f} Hz exceeds usable band "
                f"{limit:.1f} Hz (Nyquist {self.config.nyquist_hz:.1f} Hz)"
            )

        # Search band, widened to at least +/-1 bin so it is never empty.
        half = max(self.config.order_tolerance_frac * target_hz, df)
        lo, hi = target_hz - half, target_hz + half

        idx = np.where((freqs >= lo) & (freqs <= hi))[0]
        if idx.size == 0:
            return None, f"empty search band around {target_hz:.1f} Hz"

        peak_idx = int(idx[np.argmax(spectrum[idx])])

        # RSS over the main lobe (peak +/- 1 bin), clipped to array bounds.
        a = max(peak_idx - 1, 0)
        b = min(peak_idx + 2, spectrum.size)
        rss = float(np.sqrt(np.sum(spectrum[a:b] ** 2)))

        return rss / _HANN_RSS_CORRECTION, None

    def _band_energy(
        self,
        spectrum: np.ndarray,
        freqs: np.ndarray,
        band_lo_hz: float,
    ) -> tuple[Optional[float], Optional[str]]:
        """Approximate RMS of everything above band_lo_hz. Trending only."""
        limit = self.config.nyquist_margin_frac * self.config.nyquist_hz
        if band_lo_hz >= limit:
            return None, (
                f"band start {band_lo_hz:.1f} Hz above usable band {limit:.1f} Hz"
            )

        idx = np.where((freqs >= band_lo_hz) & (freqs <= limit))[0]
        if idx.size == 0:
            return None, "no bins in high-frequency band"

        # Amplitude -> RMS for sinusoidal components: A/sqrt(2).
        return float(np.sqrt(np.sum(spectrum[idx] ** 2) / 2.0)), None


# -------------------------------------------------------------------- #
# Self-test: synthetic signal with KNOWN harmonic content.
# -------------------------------------------------------------------- #
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    FS = 2048.0
    N = 2048
    RPM = 6000.0          # -> f0 = 100 Hz
    TRUE = {1: 1.00, 2: 0.50, 3: 0.25}
    TOL = 0.05            # 5% relative

    rng = np.random.default_rng(42)
    cfg = DSPConfig(sample_rate_hz=FS)
    proc = VibrationProcessor(cfg)

    def build(f0: float) -> np.ndarray:
        t = np.arange(N) / FS
        sig = np.zeros(N)
        for k, amp in TRUE.items():
            sig += amp * np.sin(2 * np.pi * k * f0 * t + 0.3 * k)
        return sig + rng.normal(0.0, 0.02, N) + 0.7  # noise + DC offset

    failures = []

    for label, f0 in (("bin-centred f0=100.0", 100.0),
                      ("off-bin   f0=100.4", 100.4)):
        r = proc.process(build(f0), rpm=f0 * 60.0)
        print(f"\n--- {label} ---")
        print(f"valid={r.valid}  N={r.n_samples}  df={r.freq_resolution_hz:.3f} Hz")
        print(f"f0={r.vib_f0_hz:.2f} Hz  crest={r.vib_crest_factor:.3f}")
        print(f"rms={r.computed_rms_g:.4f}  peak={r.computed_peak_g:.4f}")
        for k in _ORDERS:
            got = getattr(r, f"vib_{k}x_g")
            err = abs(got - TRUE[k]) / TRUE[k]
            ok = err <= TOL
            print(f"  {k}x: true={TRUE[k]:.3f} got={got:.4f} err={err*100:5.2f}% "
                  f"{'OK' if ok else 'FAIL'}")
            if not ok:
                failures.append(f"{label} {k}x err {err*100:.2f}%")
        print(f"bearing_band={r.vib_bearing_band_g:.4f} (noise floor, relative)")
        if r.notes:
            print(f"  notes: {r.notes}")

    print("\n--- guard conditions ---")
    print(f"None samples : {proc.process(None, 6000).notes}")
    print(f"short window : {proc.process([0.1] * 10, 6000).notes}")
    print(f"engine stopped: {proc.process(build(100.0), rpm=0.0).notes}")
    with_nan = build(100.0); with_nan[5] = np.nan
    print(f"NaN in window: {proc.process(with_nan, 6000).notes}")

    # 3x beyond Nyquist: f0 = 400 Hz -> 3x = 1200 Hz > 0.9*1024
    r_ny = proc.process(build(400.0), rpm=24000.0)
    print(f"3x past Nyquist -> vib_3x_g={r_ny.vib_3x_g}, "
          f"vib_1x_g is not None: {r_ny.vib_1x_g is not None}")
    if r_ny.vib_3x_g is not None:
        failures.append("3x should be None beyond Nyquist")

    print()
    if failures:
        print("DSP SELF-CHECK FAILED:")
        for f in failures:
            print(f"  - {f}")
        raise SystemExit(1)
    print("DSP SELF-CHECK OK")
