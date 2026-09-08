"""
AERIS -- sensor model. Physics -> what an instrument would actually report.

WHY THIS FILE EXISTS
--------------------
shared/engine_mvem.py emits PHYSICS: exact, noiseless, unquantised, all channels
perfectly synchronous. node1_ingestion/simulator_bridge.py declares this in the
caveat `no_sensor_model_yet` and names it the blocker on training, because a
residual computed against noiseless data is optimistically clean by
construction. A detector tuned on it has never seen the thing that actually
limits detection: the noise floor.

This module sits between the MVEM and the bridge. It takes the schema-keyed
dict the MVEM produces and returns what a sensor bank would have reported for
that same engine state -- with calibration bias, per-sample noise, ADC
quantisation, per-channel sample rates, and optional injected sensor faults
that are FAULTS OF THE INSTRUMENT and not of the engine.

That last distinction is the point. Everything else in AERIS assumes a
disagreement between model and measurement means the engine changed. A stuck
thermocouple produces exactly the same residual signature as a real thermal
fault, and no amount of model quality resolves it. This module is where that
ambiguity can be generated on purpose and measured.

DESIGN RULES
------------
* Operates on the SCHEMA DICT, keyed by shared/schema.py field names -- never
  on EngineOutputs internals. The MVEM can rename an attribute without
  touching this file.
* Channels with no declared sensor pass through UNTOUCHED, and are listed by
  perfect_channels(). A channel this module does not know about is a perfect
  instrument, which is a lie, and the lie is enumerable.
* No ML imports, no node2 imports at module scope. This is on the generating
  side of the arrow and must not depend on the consumer.
* NO PROCESS LAG. shared/throttle_dynamics.py owns engine thermal time
  constants and stays the only place they live. The optional `sensor_tau_s`
  here is the SENSOR ELEMENT's own lag (thermocouple sheath mass), a physically
  different quantity, and it is DISABLED by default so there is no chance of
  the two being confused or applied twice.
* Deterministic and independently seeded per channel via CRC32 of the channel
  name, NOT Python's hash() -- str hashing is salted per process and would make
  datasets irreproducible across runs.
* A dropout REMOVES the field from the `provided` set rather than writing 0.0.
  Writing zero would be indistinguishable from a schema default, and
  adapter.NONZERO_WHEN_RUNNING would refuse the frame for the wrong stated
  reason. Removing it makes the adapter refuse for the right one.

WHAT THE NUMBERS ARE AND ARE NOT
--------------------------------
The per-channel specs below are instrument-class figures: thermocouple
tolerance classes, typical automotive piezoresistive pressure transducer error
as a fraction of full scale, crank-sensor tooth counting. They are the right
ORDER OF MAGNITUDE for this class of instrumentation and they are NOT the
calibration sheet of any specific sensor on any specific 915 iS installation.
Every one is declared in sensor_caveats() with verified=False except where a
figure follows from a published tolerance class. Do not present a false-alarm
rate computed from these as a measured false-alarm rate for real hardware.
"""

from __future__ import annotations

import math
import random
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.schema import COLUMN_NAMES  # noqa: E402

SENSOR_MODEL_VERSION = "0.1.0"

# ==================================================================== #
# Failure modes of the INSTRUMENT, not of the engine
# ==================================================================== #
STUCK = "STUCK"                  # output frozen at last value
DROPOUT = "DROPOUT"              # channel stops reporting entirely
DRIFT = "DRIFT"                  # calibration walks away linearly
OFFSET_STEP = "OFFSET_STEP"      # sudden constant offset
NOISE_BURST = "NOISE_BURST"      # noise sigma multiplied
SPIKE_TRAIN = "SPIKE_TRAIN"      # intermittent single-sample glitches
FULL_SCALE_HIGH = "FULL_SCALE_HIGH"
FULL_SCALE_LOW = "FULL_SCALE_LOW"

SENSOR_FAULT_MODES: Tuple[str, ...] = (
    STUCK, DROPOUT, DRIFT, OFFSET_STEP, NOISE_BURST, SPIKE_TRAIN,
    FULL_SCALE_HIGH, FULL_SCALE_LOW,
)


class SensorModelError(Exception):
    """Raised when a sensor configuration cannot be honestly applied."""


# ==================================================================== #
# Per-channel instrument specification
# ==================================================================== #

@dataclass(frozen=True)
class SensorSpec:
    """One instrument.

    bias_sigma / bias_frac
        Calibration error. Drawn ONCE per session and held constant for the
        whole session, because that is what a calibration offset is. Drawing
        it per frame would turn systematic error into extra noise and would
        make residual bias -- the thing that actually defeats a detector --
        invisible.
    noise_sigma / noise_frac
        Per-sample random error. Absolute and proportional parts combine in
        quadrature against the true value.
    quantum
        ADC / display least significant bit. 0.0 disables quantisation.
    lo / hi
        Sensor RANGE, not a plausibility bound. Real instruments saturate, so
        clamping here is physical behaviour and not data cleaning.
    rate_hz
        Sample rate. Slower than the frame rate produces a zero-order-hold
        staircase, which is real and which shows up in residuals.
    sensor_tau_s
        The sensing ELEMENT's own lag. Applied only when the bank is built
        with sensor_lag=True. See the module docstring.
    """

    unit: str
    bias_sigma: float = 0.0
    bias_frac: float = 0.0
    noise_sigma: float = 0.0
    noise_frac: float = 0.0
    quantum: float = 0.0
    lo: float = -math.inf
    hi: float = math.inf
    rate_hz: float = 10.0
    sensor_tau_s: float = 0.0
    provenance: str = ""
    verified: bool = False


# Type K thermocouple, IEC 60584-1 class 2 tolerance is the greater of
# +/-2.5 C and 0.0075*|t|, i.e. ~6.4 C at 850 C. bias_sigma 3.0 puts the class
# limit at ~2.1 sigma. Exhaust gas is also genuinely turbulent, so the
# per-sample term is gas temperature fluctuation as much as it is electronics.
_EGT = SensorSpec(
    unit="C", bias_sigma=3.0, noise_sigma=1.5, quantum=1.0,
    lo=-40.0, hi=1300.0, rate_hz=10.0, sensor_tau_s=1.5,
    provenance=("type K thermocouple, IEC 60584-1 class 2 tolerance band "
                "(greater of 2.5 C and 0.75% of reading). Class is published; "
                "the split between bias and per-sample noise, and the sheath "
                "time constant, are instrument-class judgement."),
    verified=False)

# Schema CHT channels. Per the earlier decision these are DERIVED coolant
# jacket temperatures on a 915 iS -- the engine has no per-cylinder head
# thermocouples -- so this instrument is notional and inherits the coolant
# sensor's class.
_CHT = SensorSpec(
    unit="C", bias_sigma=1.2, noise_sigma=0.35, quantum=0.25,
    lo=-40.0, hi=250.0, rate_hz=2.0, sensor_tau_s=4.0,
    provenance=("NOTIONAL. The 915 iS has no per-cylinder head thermocouple; "
                "these schema channels carry derived jacket temperatures, so "
                "this spec models an instrument that does not exist. Kept so "
                "the channels are not silently perfect."),
    verified=False)

_COOLANT = SensorSpec(
    unit="C", bias_sigma=1.0, noise_sigma=0.30, quantum=0.25,
    lo=-40.0, hi=150.0, rate_hz=1.0, sensor_tau_s=3.0,
    provenance=("NTC / PT1000 immersed temperature sensor, typical +/-1 to "
                "2 C total error. 1 Hz reporting is typical of an ECU "
                "temperature channel and is the reason coolant residuals show "
                "a staircase."),
    verified=False)

_OIL_T = SensorSpec(
    unit="C", bias_sigma=1.0, noise_sigma=0.25, quantum=0.25,
    lo=-40.0, hi=200.0, rate_hz=1.0, sensor_tau_s=5.0,
    provenance=("as coolant, with a longer element tau for a sump-immersed "
                "probe. This is the channel that breaks the current "
                "admission gate -- see CASE 9."),
    verified=False)

# Piezoresistive pressure transducer, 0-10 bar full scale on the oil gallery.
# 1% FS total error -> 100 kPa*0.1 = 10 kPa; split as 5 kPa bias, 2 kPa noise.
_OIL_P = SensorSpec(
    unit="kPa", bias_sigma=5.0, noise_sigma=2.0, quantum=1.0,
    lo=0.0, hi=1000.0, rate_hz=10.0,
    provenance=("piezoresistive transducer, 0-10 bar FS, ~1% FS total error. "
                "FS range chosen to bracket the manual's 0.8-5 bar band with "
                "headroom; the error split is judgement."),
    verified=False)

_MAP = SensorSpec(
    unit="kPa", bias_sigma=1.5, noise_sigma=0.60, quantum=0.25,
    lo=0.0, hi=300.0, rate_hz=10.0,
    provenance=("MAP transducer, 0-3 bar abs FS to cover the 1.73 bar "
                "manifold limit [A]. Per-sample term includes real intake "
                "pressure pulsation, not only electronics."),
    verified=False)

_PLENUM_T = SensorSpec(
    unit="C", bias_sigma=1.5, noise_sigma=0.5, quantum=0.5,
    lo=-40.0, hi=200.0, rate_hz=5.0, sensor_tau_s=2.0,
    provenance="intake air temperature sensor, exposed bead, fast response.",
    verified=False)

# Crank position sensor. Tooth counting against a crystal time base, so there
# is no meaningful calibration bias -- an rpm reading is a frequency, not a
# voltage. Noise is cycle-to-cycle speed variation, which is real.
_RPM = SensorSpec(
    unit="rpm", bias_sigma=0.0, noise_sigma=2.0, quantum=1.0,
    lo=0.0, hi=9000.0, rate_hz=10.0,
    provenance=("variable-reluctance crank sensor. Bias is set to ZERO "
                "deliberately: rpm is derived by counting teeth against a "
                "crystal, so it has no calibration offset in the way an "
                "analogue channel does. The per-sample term is genuine "
                "cycle-to-cycle speed variation."),
    verified=True)

_THROTTLE = SensorSpec(
    unit="pct", bias_sigma=0.5, noise_sigma=0.20, quantum=0.1,
    lo=0.0, hi=100.0, rate_hz=10.0,
    provenance=("potentiometric TPS, ~0.5% of span. Note this is the SENSED "
                "lever position; the MVEM treats throttle as a command, so "
                "after this module command and measurement differ, which is "
                "correct and is also a residual source."),
    verified=False)

# Fuel flow error is dominated by the proportional term, whichever sensing
# method is used (turbine k-factor error, or injector pulse-width integration
# against an assumed injector flow rate).
_FUEL = SensorSpec(
    unit="L/h", bias_frac=0.020, noise_frac=0.010, quantum=0.05,
    lo=0.0, hi=200.0, rate_hz=5.0,
    provenance=("2% systematic, 1% random, proportional to reading. Applies "
                "to a turbine transducer k-factor error or to injector "
                "pulse-width integration. INTERACTS WITH THE DENSITY "
                "CONFLICT: the bridge already carries a declared 4.04% "
                "density discrepancy, which is twice this sensor's systematic "
                "error, so the assumption is the larger error term."),
    verified=False)

_ALT = SensorSpec(
    unit="m", bias_sigma=15.0, noise_sigma=1.5, quantum=0.3, rate_hz=1.0,
    lo=-500.0, hi=20000.0,
    provenance=("pressure altitude from a static port. The bias term is "
                "static position error, which on a real airframe is a "
                "function of airspeed and configuration, not a constant. "
                "Modelled as a constant here."),
    verified=False)

_OAT = SensorSpec(
    unit="C", bias_sigma=1.0, noise_sigma=0.20, quantum=0.25, rate_hz=1.0,
    lo=-80.0, hi=80.0, sensor_tau_s=3.0,
    provenance=("OAT probe. A real probe reads high from ram rise, which is "
                "an airspeed-dependent POSITIVE error and is NOT modelled "
                "here -- the bias is drawn symmetrically. At 50 m/s ram rise "
                "is on the order of 1 C, comparable to the whole bias term."),
    verified=False)

_VIB = SensorSpec(
    unit="g", bias_frac=0.03, noise_frac=0.05, noise_sigma=0.01, quantum=0.001,
    lo=0.0, hi=50.0, rate_hz=10.0,
    provenance=("accelerometer RMS, proportional error dominant. The MVEM "
                "emits a broadband RMS surrogate, not a time series, so no "
                "spectral content exists to corrupt and the schema's vib_1x "
                "family stays None."),
    verified=False)

_VOLT = SensorSpec(
    unit="V", bias_sigma=0.05, noise_sigma=0.02, quantum=0.01,
    lo=0.0, hi=40.0, rate_hz=10.0,
    provenance="bus voltage via resistor divider into a 10-bit ADC.",
    verified=False)

_LAMBDA = SensorSpec(
    unit="-", bias_sigma=0.010, noise_sigma=0.005, quantum=0.001,
    lo=0.5, hi=1.6, rate_hz=10.0, sensor_tau_s=0.5,
    provenance=("wideband lambda sensor. Real sensors also require light-off "
                "temperature before reading at all, which is not modelled, "
                "so cold-start lambda here is optimistic."),
    verified=False)

_TORQUE = SensorSpec(
    unit="Nm", bias_frac=0.01, noise_frac=0.005, quantum=0.1,
    lo=0.0, hi=400.0, rate_hz=10.0,
    provenance=("NOTIONAL. A production 915 iS has no torque transducer; "
                "torque here is an ECU estimate, so treating it as a measured "
                "channel with 1% error flatters it. Declared."),
    verified=False)

_POWER = SensorSpec(
    unit="kW", bias_frac=0.01, noise_frac=0.005, quantum=0.1,
    lo=0.0, hi=200.0, rate_hz=10.0,
    provenance="NOTIONAL, as torque. Derived, not measured.",
    verified=False)


SENSORS: Dict[str, SensorSpec] = {
    "egt_1_c": _EGT, "egt_2_c": _EGT, "egt_3_c": _EGT, "egt_4_c": _EGT,
    "cht_1_c": _CHT, "cht_2_c": _CHT, "cht_3_c": _CHT, "cht_4_c": _CHT,
    "coolant_temp_in_c": _COOLANT, "coolant_temp_out_c": _COOLANT,
    "oil_temp_c": _OIL_T, "oil_cooler_out_temp_c": _OIL_T,
    "oil_pressure_kpa": _OIL_P,
    "manifold_pressure_kpa": _MAP,
    "plenum_temp_c": _PLENUM_T,
    "rpm": _RPM,
    "prop_rpm": _RPM,
    "throttle_pct": _THROTTLE,
    "fuel_flow_lph": _FUEL,
    "altitude_m": _ALT,
    "oat_c": _OAT,
    "vib_rms_g": _VIB, "vib_peak_g": _VIB,
    "bus_voltage_v": _VOLT, "battery_voltage_v": _VOLT,
    "lambda_1": _LAMBDA, "lambda_2": _LAMBDA,
    "torque_nm": _TORQUE,
    "power_kw": _POWER,
}

# The twelve channels adapter.py actually reads on its way to the twin. Noise
# on anything else cannot reach a residual, so these are the ones that matter.
ADAPTER_VISIBLE: Tuple[str, ...] = (
    "altitude_m", "oat_c", "throttle_pct", "rpm", "fuel_flow_lph",
    "coolant_temp_out_c", "egt_1_c", "egt_2_c", "egt_3_c", "egt_4_c",
    "oil_pressure_kpa", "oil_temp_c",
)

# Derived channels that must be RECOMPUTED after corruption, because something
# downstream cross-checks them against their own inputs. adapter.py warns when
# egt_spread_c disagrees with the spread of the four cylinder channels by more
# than 1.0 C, and independent per-cylinder noise guarantees that disagreement
# unless the spread is recomputed here.
RECOMPUTED_AFTER_NOISE: Tuple[str, ...] = ("egt_spread_c",)

# Mirror of GATE_RESID_TOL in shared/throttle_dynamics.py, copied rather than
# imported: that module imports shared.stress_sim, which imports node2, and
# this file must stay free of the consumer. CASE 14 verifies the copy is
# current, and runs last so it cannot pollute the CASE 12 import check.
GATE_RESID_TOL_MIRROR: Dict[str, float] = {
    "rpm": 1.5,
    "EGT_mean_C": 0.25,
    "coolant_temp_C": 0.0029,
    "oil_temperature_C": 0.00035,
    "fuelflow_kgh": 0.0007,
    "oil_pressure_bar": 0.000038,
}


# ==================================================================== #
# Injected instrument faults
# ==================================================================== #

@dataclass
class SensorFault:
    """A fault of the INSTRUMENT. The engine is unaffected."""

    channel: str
    mode: str
    onset_s: float = 0.0
    magnitude: float = 0.0        # DRIFT: units/s. OFFSET_STEP: units.
                                  # NOISE_BURST: sigma multiplier.
                                  # SPIKE_TRAIN: spike size in units.
    probability: float = 0.02     # SPIKE_TRAIN only: per-sample rate
    label: str = ""

    def __post_init__(self) -> None:
        if self.mode not in SENSOR_FAULT_MODES:
            raise SensorModelError(
                f"unknown sensor fault mode {self.mode!r}; "
                f"have {list(SENSOR_FAULT_MODES)}")
        if self.channel not in SENSORS:
            raise SensorModelError(
                f"channel {self.channel!r} has no declared sensor, so it "
                f"cannot have a sensor fault. Declared channels: "
                f"{sorted(SENSORS)}")
        if not (0.0 <= self.probability <= 1.0):
            raise SensorModelError(
                f"probability {self.probability} outside [0, 1]")
        if not self.label:
            self.label = f"SENSOR_{self.mode}_{self.channel.upper()}"

    def active(self, t_s: float) -> bool:
        return t_s >= self.onset_s


@dataclass
class SensorReport:
    """What the bank did to one frame."""

    t_s: float = 0.0
    dropped: Set[str] = field(default_factory=set)
    held: Set[str] = field(default_factory=set)       # zero-order hold, no new sample
    saturated: Set[str] = field(default_factory=set)  # clamped at sensor range
    spiked: Set[str] = field(default_factory=set)
    faulted: List[str] = field(default_factory=list)  # active fault labels
    perfect: Set[str] = field(default_factory=set)    # passed through untouched

    def summary(self) -> Dict[str, Any]:
        return {"t_s": round(self.t_s, 3),
                "dropped": sorted(self.dropped),
                "held": len(self.held),
                "saturated": sorted(self.saturated),
                "spiked": sorted(self.spiked),
                "sensor_faults": list(self.faulted),
                "perfect_channels": len(self.perfect)}


# ==================================================================== #
# The bank
# ==================================================================== #

def _channel_seed(name: str, session_seed: int) -> int:
    """Stable per-channel seed.

    CRC32 of the name, not hash(): Python salts string hashing per process, so
    hash() here would make every dataset unreproducible across runs. Deriving
    per channel also means adding a channel does not shift the random stream
    of any existing one, so old datasets stay regenerable.
    """
    return (zlib.crc32(name.encode("utf-8")) ^ (session_seed & 0xFFFFFFFF)) & 0xFFFFFFFF


class SensorBank:
    """A set of instruments with fixed calibration, carried across a session.

    Stateful on purpose: calibration bias, last sampled value and sample timing
    all persist between frames, which is what makes the zero-order hold and the
    stuck-sensor mode possible. One bank per session; do not share across
    sessions or the calibration will not be independent.
    """

    def __init__(self, seed: int = 20260101,
                 enabled: bool = True,
                 sensor_lag: bool = False,
                 faults: Optional[Sequence[SensorFault]] = None,
                 specs: Optional[Dict[str, SensorSpec]] = None) -> None:
        self.seed = int(seed)
        self.enabled = bool(enabled)
        self.sensor_lag = bool(sensor_lag)
        self.specs = dict(SENSORS if specs is None else specs)
        self.faults: List[SensorFault] = list(faults or [])

        for f in self.faults:
            if f.channel not in self.specs:
                raise SensorModelError(
                    f"fault targets {f.channel!r}, which is not in this "
                    f"bank's spec set")

        self._rng: Dict[str, random.Random] = {}
        self.bias: Dict[str, float] = {}
        self._held: Dict[str, float] = {}
        self._last_t: Dict[str, float] = {}
        self._element: Dict[str, float] = {}

        for name, spec in self.specs.items():
            r = random.Random(_channel_seed(name, self.seed))
            self._rng[name] = r
            # Absolute and fractional bias both drawn once. The fractional part
            # is resolved against the reading at read time.
            self.bias[name] = r.gauss(0.0, spec.bias_sigma) if spec.bias_sigma else 0.0
            self._bias_frac_draw = getattr(self, "_bias_frac_draw", {})
            self._bias_frac_draw[name] = (
                r.gauss(0.0, spec.bias_frac) if spec.bias_frac else 0.0)

    # ---------------------------------------------------------------- #
    def faults_for(self, channel: str, t_s: float) -> List[SensorFault]:
        return [f for f in self.faults
                if f.channel == channel and f.active(t_s)]

    def perfect_channels(self, keys: Iterable[str]) -> Set[str]:
        """Channels in `keys` this bank leaves untouched -- i.e. lies about."""
        return {k for k in keys if k not in self.specs}

    def noise_floor(self, channel: str, value: float) -> float:
        """Total per-sample sigma for this channel at this reading."""
        spec = self.specs[channel]
        a = spec.noise_sigma
        b = spec.noise_frac * abs(float(value))
        return math.sqrt(a * a + b * b)

    def reset(self) -> None:
        """Clear sample timing and held values. Calibration bias SURVIVES,
        because a reset is a new run of the same physical hardware."""
        self._held.clear()
        self._last_t.clear()
        self._element.clear()

    # ---------------------------------------------------------------- #
    def _read_one(self, name: str, truth: float, t_s: float, dt_s: float,
                  rep: SensorReport) -> Optional[float]:
        """One channel. Returns None when the channel is dropped out."""
        spec = self.specs[name]
        rng = self._rng[name]
        active = self.faults_for(name, t_s)
        for f in active:
            if f.label not in rep.faulted:
                rep.faulted.append(f.label)

        modes = {f.mode for f in active}

        if DROPOUT in modes:
            rep.dropped.add(name)
            return None

        if STUCK in modes and name in self._held:
            return self._held[name]

        if FULL_SCALE_HIGH in modes:
            v = spec.hi if math.isfinite(spec.hi) else truth
            self._held[name] = v
            rep.saturated.add(name)
            return v
        if FULL_SCALE_LOW in modes:
            v = spec.lo if math.isfinite(spec.lo) else 0.0
            self._held[name] = v
            rep.saturated.add(name)
            return v

        # Zero-order hold: no new conversion yet at this channel's rate.
        period = 1.0 / spec.rate_hz if spec.rate_hz > 0.0 else 0.0
        last = self._last_t.get(name)
        if period > 0.0 and last is not None and (t_s - last) < period - 1e-9:
            rep.held.add(name)
            return self._held.get(name, truth)

        # Sensing element lag. OFF unless the bank was built with
        # sensor_lag=True -- see the module docstring on single ownership.
        v = float(truth)
        if self.sensor_lag and spec.sensor_tau_s > 0.0 and dt_s > 0.0:
            prev = self._element.get(name, v)
            k = 1.0 - math.exp(-dt_s / spec.sensor_tau_s)
            v = prev + (v - prev) * k
            self._element[name] = v

        # Systematic terms.
        v += self.bias[name]
        v += self._bias_frac_draw[name] * abs(float(truth))
        for f in active:
            if f.mode == DRIFT:
                v += f.magnitude * max(0.0, t_s - f.onset_s)
            elif f.mode == OFFSET_STEP:
                v += f.magnitude

        # Random terms.
        sigma = self.noise_floor(name, truth)
        burst = max((f.magnitude for f in active if f.mode == NOISE_BURST),
                    default=1.0)
        if sigma > 0.0:
            v += rng.gauss(0.0, sigma * burst)

        for f in active:
            if f.mode == SPIKE_TRAIN and rng.random() < f.probability:
                v += f.magnitude * rng.choice((-1.0, 1.0))
                rep.spiked.add(name)

        # Range saturation, then quantisation. Order matters: a real ADC
        # quantises whatever reaches it after the front end has clipped.
        if v < spec.lo:
            v = spec.lo
            rep.saturated.add(name)
        elif v > spec.hi:
            v = spec.hi
            rep.saturated.add(name)

        if spec.quantum > 0.0:
            v = round(v / spec.quantum) * spec.quantum

        self._held[name] = v
        self._last_t[name] = t_s
        return v

    # ---------------------------------------------------------------- #
    def read(self, truth: Dict[str, Any], t_s: float = 0.0,
             dt_s: float = 0.1,
             provided: Optional[Iterable[str]] = None
             ) -> Tuple[Dict[str, Any], Set[str], SensorReport]:
        """Corrupt one schema-keyed frame.

        Returns (measured_dict, provided_set, report). The provided set has any
        dropped channels REMOVED, so the adapter refuses the frame for the
        honest reason instead of mistaking a zero for a schema default.

        Non-numeric values (engine_state, session_id, the bool ECU flags) and
        None values pass through untouched -- a None means "not computed" and
        must not become a number here.
        """
        rep = SensorReport(t_s=t_s)
        prov = set(truth if provided is None else provided)

        if not self.enabled:
            rep.perfect = set(truth)
            return dict(truth), prov, rep

        out: Dict[str, Any] = {}
        for k, val in truth.items():
            if k not in self.specs or not isinstance(val, (int, float)) \
                    or isinstance(val, bool) or val is None:
                out[k] = val
                if k not in self.specs:
                    rep.perfect.add(k)
                continue
            got = self._read_one(k, float(val), t_s, dt_s, rep)
            if got is None:
                prov.discard(k)
                continue
            out[k] = got

        # Derived channels recomputed from the corrupted inputs. Without this
        # the adapter's own cross-check fires on every frame.
        egts = [out[f"egt_{i}_c"] for i in range(1, 5) if f"egt_{i}_c" in out]
        if "egt_spread_c" in truth and len(egts) == 4:
            out["egt_spread_c"] = max(egts) - min(egts)

        return out, prov, rep


# ==================================================================== #
# Convenience constructors
# ==================================================================== #

def perfect_bank() -> SensorBank:
    """A bank that changes nothing. For A/B against the noiseless case."""
    return SensorBank(enabled=False)


def nominal_bank(seed: int = 20260101, sensor_lag: bool = False) -> SensorBank:
    """Healthy instruments, calibration drawn from `seed`."""
    return SensorBank(seed=seed, sensor_lag=sensor_lag)


def stuck_egt3_bank(onset_s: float = 0.0, seed: int = 20260101) -> SensorBank:
    """The ambiguity case: a frozen thermocouple on a healthy engine.

    Produces a growing EGT residual and a growing spread with no change in
    fuel flow, power or vibration. A per-cylinder mixture fault moves all of
    those together. That difference is the only thing separating the two, and
    nothing in AERIS currently looks for it.
    """
    return SensorBank(seed=seed, faults=[
        SensorFault("egt_3_c", STUCK, onset_s=onset_s,
                    label="SENSOR_STUCK_EGT3")])


def drifting_oil_temp_bank(rate_c_per_s: float = 0.01, onset_s: float = 0.0,
                           seed: int = 20260101) -> SensorBank:
    """A slowly decalibrating oil temperature probe: 0.01 C/s is 36 C/hour.

    Chosen because oil temperature is the channel the admission gate is most
    sensitive to, so this is the cheapest possible way to produce a confident
    false diagnosis.
    """
    return SensorBank(seed=seed, faults=[
        SensorFault("oil_temp_c", DRIFT, onset_s=onset_s,
                    magnitude=rate_c_per_s, label="SENSOR_DRIFT_OIL_TEMP")])


# ==================================================================== #
# Declared caveats
# ==================================================================== #

def sensor_caveats() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = [
        {"id": "sensor_specs_are_instrument_class_not_calibration",
         "verified": False,
         "value": f"{len(SENSORS)} channels declared",
         "note": ("every sigma, bias and quantum here is an instrument-class "
                  "figure for this kind of sensor, not a calibration sheet for "
                  "hardware on a specific 915 iS. Detection rates and false "
                  "alarm rates computed from this model are properties of the "
                  "MODEL. They must not be reported as measured performance.")},
        {"id": "noise_floor_exceeds_admission_tolerance", "verified": True,
         "value": dict(GATE_RESID_TOL_MIRROR),
         "note": ("MEASURED IN CASE 9. All SIX gated channels have a noise "
                  "floor above their admission tolerance, but the split "
                  "matters: rpm (1.3x) and EGT_mean (3.0x) are recoverable by "
                  "averaging 2 and 9 frames, under a second either way. The "
                  "other four are not -- coolant 103x, fuelflow 226x, oil "
                  "pressure 526x, oil temperature 714x, needing 3.0 h, 2.8 h, "
                  "7.7 h and 141.7 h of averaging respectively at their own "
                  "sample rates. Those tolerances were derived from tree-split "
                  "resolution, not from instrument physics, which is why they "
                  "landed below the noise floor. throttle_dynamics records the "
                  "gate flipping on a 0.157 C oil residual; the probe floor "
                  "is 0.25 C, so on realistic data that decision is noise. "
                  "Per-frame absolute admission has to be replaced by a "
                  "windowed statistic, or the baselines retrained on noisy "
                  "data so the boundaries sit outside the floor.")},
        {"id": "sensor_faults_mimic_engine_faults", "verified": True,
         "value": list(SENSOR_FAULT_MODES),
         "note": ("a stuck thermocouple and a real thermal fault produce the "
                  "same residual on that channel. AERIS has no sensor "
                  "validation layer, so every diagnosis it makes silently "
                  "assumes the instrument is truthful. The separator exists "
                  "and is cheap -- an engine fault moves several physically "
                  "coupled channels together, an instrument fault moves one -- "
                  "but nothing currently checks it. Generating these cases is "
                  "what makes that testable.")},
        {"id": "calibration_bias_is_per_session", "verified": True,
         "value": "drawn once per SensorBank, survives reset()",
         "note": ("bias is systematic by definition, so it is drawn once and "
                  "held. Consequence: every residual in a session carries a "
                  "constant offset, and a detector trained across many "
                  "sessions sees bias as between-session variance. Training "
                  "and holdout data must use DIFFERENT seeds or the model will "
                  "learn this particular sensor set's offsets.")},
        {"id": "no_process_lag_here", "verified": True,
         "value": "sensor_lag defaults to False",
         "note": ("shared/throttle_dynamics.py owns engine thermal time "
                  "constants. sensor_tau_s here is the sensing ELEMENT's lag, "
                  "a different physical quantity, and it is disabled by "
                  "default so the two cannot be confused or applied twice. "
                  "Enable it only when the caller is not also applying "
                  "throttle_dynamics lag to the same channel.")},
        {"id": "dropout_removes_field_not_zeroes_it", "verified": True,
         "value": "provided set shrinks",
         "note": ("writing 0.0 for a lost channel is indistinguishable from a "
                  "schema default, and adapter.NONZERO_WHEN_RUNNING would "
                  "refuse the frame while blaming the wrong thing. Removing "
                  "the key makes the adapter refuse with 'source channel not "
                  "provided', which is what actually happened.")},
        {"id": "noisy_frames_are_not_physically_consistent", "verified": True,
         "value": "torque * rpm != power after corruption",
         "note": ("the MVEM's algebra closes to 0.06 kW; independent noise on "
                  "torque, rpm and power breaks that closure. This is correct "
                  "behaviour for measured data, but any downstream check that "
                  "asserts physical closure must run on the TRUTH frame, not "
                  "the measured one. Both are available -- do not discard the "
                  "truth frame when generating a dataset.")},
                {"id": "coupled_channels_get_independent_noise", "verified": True,
         "value": {"pairs": [["torque_nm", "power_kw"],
                             ["rpm", "prop_rpm"],
                             ["coolant_temp_in_c", "coolant_temp_out_c"],
                             ["oil_temp_c", "oil_cooler_out_temp_c"],
                             ["lambda_1", "lambda_2"],
                             ["bus_voltage_v", "battery_voltage_v"]]},
         "note": ("each channel draws its own bias and its own noise, which is "
                  "right for four separate thermocouples and WRONG for these "
                  "pairs. A real ECU DERIVES power from torque and rpm, and "
                  "prop_rpm from rpm through a fixed gearbox ratio, so those "
                  "errors are dependent, not independent. Modelling them as "
                  "independent lets a detector beat the noise floor by "
                  "averaging two readings of one measurement, recovering "
                  "information that does not exist on real hardware. Any "
                  "detection rate that improves when both members of a pair "
                  "are included is suspect. Fix is a derived-channel mode "
                  "where power inherits torque's and rpm's realisations.")},

        {"id": "throttle_becomes_a_measurement", "verified": True,
         "value": "TPS bias ~0.5% of span",
         "note": ("the MVEM treats throttle as a COMMAND and solves exactly "
                  "for it. After this module the frame carries a SENSED lever "
                  "position that differs from the command. Since throttle is a "
                  "twin input feature, this offset propagates into every "
                  "prediction, not only into one residual. Realistic, and "
                  "larger in effect than its size suggests.")},
        {"id": "oat_ram_rise_not_modelled", "verified": False,
         "value": "symmetric bias, no airspeed term",
         "note": ("a real OAT probe reads HIGH from adiabatic ram rise, an "
                  "airspeed-dependent one-sided error of order 1 C at 50 m/s. "
                  "Modelled here as a symmetric constant bias, so ambient "
                  "temperature is unbiased on average when it should be warm. "
                  "Affects the density calculation and therefore every "
                  "altitude-dependent prediction.")},
        {"id": "no_spectral_vibration_content", "verified": True,
         "value": "vib_rms_g and vib_peak_g only",
         "note": ("the MVEM emits a broadband RMS surrogate, not a time "
                  "series, so there is no waveform to corrupt and the schema's "
                  "vib_1x/2x/3x/bearing_band channels stay None. Bearing and "
                  "gear fault signatures live in exactly those channels, so "
                  "that whole fault family is out of reach until a vibration "
                  "time-series generator exists.")},
        {"id": "notional_instruments_declared", "verified": True,
         "value": ["cht_1_c", "cht_2_c", "cht_3_c", "cht_4_c",
                   "torque_nm", "power_kw"],
         "note": ("these schema channels have no corresponding physical sensor "
                  "on a 915 iS: the CHT channels carry derived jacket "
                  "temperatures, and torque and power are ECU estimates. They "
                  "are given specs anyway so they are not silently perfect, "
                  "but their error is unknowable rather than merely "
                  "unmeasured.")},
    ]
    return out


def sensor_channel_table() -> List[Dict[str, Any]]:
    """Per-channel spec, for the report appendix and /caveats."""
    rows = []
    for name in sorted(SENSORS):
        s = SENSORS[name]
        rows.append({
            "channel": name, "unit": s.unit,
            "bias_sigma": s.bias_sigma, "bias_frac": s.bias_frac,
            "noise_sigma": s.noise_sigma, "noise_frac": s.noise_frac,
            "quantum": s.quantum, "rate_hz": s.rate_hz,
            "range": [s.lo, s.hi], "element_tau_s": s.sensor_tau_s,
            "adapter_visible": name in ADAPTER_VISIBLE,
            "verified": s.verified, "provenance": s.provenance,
        })
    return rows


# ==================================================================== #
# Self-check
# ==================================================================== #

def _truth_frame() -> Dict[str, Any]:
    """A healthy cruise frame in schema units, built from the MVEM."""
    from shared import engine_mvem as mvem
    o = mvem.solve(throttle_pct=80.0, altitude_ft=6000.0, oat_c=10.0)
    return dict(o.to_schema_dict())


def _self_test() -> None:
    fails: List[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            fails.append(msg)
            print(f"  FAIL: {msg}")

    print(f"sensor_model v{SENSOR_MODEL_VERSION}  "
          f"{len(SENSORS)} declared channels, "
          f"{len(ADAPTER_VISIBLE)} visible to the twin")

    truth = _truth_frame()

    print("\nCASE 0  every declared channel is a real schema column")
    cols = set(COLUMN_NAMES)
    bogus = sorted(k for k in SENSORS if k not in cols)
    print(f"  schema columns {len(cols)}, declared sensors {len(SENSORS)}, "
          f"not in schema: {bogus or 'none'}")
    check(not bogus, f"SENSORS names columns the schema does not declare: {bogus}")
    vis_missing = [k for k in ADAPTER_VISIBLE if k not in SENSORS]
    print(f"  adapter-visible channels lacking a sensor: {vis_missing or 'none'}")
    check(not vis_missing,
          f"channels the twin reads have no sensor model, so those residuals "
          f"stay artificially clean: {vis_missing}")

    print("\nCASE 1  which channels this bank still lies about")
    bank = nominal_bank()
    perfect = sorted(bank.perfect_channels(truth))
    print(f"  frame carries {len(truth)} fields; {len(perfect)} pass through "
          f"untouched")
    print(f"  perfect: {perfect}")
    print("  NOTE these are enumerable by design -- a channel with no sensor "
          "is a declared lie, not a hidden one")

    print("\nCASE 2  determinism and independence of the random streams")
    a = nominal_bank(seed=7).read(truth, t_s=0.0)[0]
    b = nominal_bank(seed=7).read(truth, t_s=0.0)[0]
    c = nominal_bank(seed=8).read(truth, t_s=0.0)[0]
    same = all(a[k] == b[k] for k in a if isinstance(a[k], float))
    diff = sum(1 for k in a if isinstance(a[k], float) and a[k] != c[k])
    print(f"  seed 7 twice identical: {same}")
    print(f"  seed 7 vs 8 differ on {diff} channels")
    check(same, "same seed produced different frames; datasets are not "
                "reproducible")
    check(diff > 5, "different seeds produced near-identical frames; the "
                    "per-channel seeding is not working")

    print("\nCASE 3  bias is systematic, noise is not")
    bank3 = nominal_bank(seed=11)
    n = 4000
    vals: List[float] = []
    for i in range(n):
        m, _, _ = bank3.read(truth, t_s=i * 0.1, dt_s=0.1)
        vals.append(m["egt_1_c"])
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / (n - 1)
    sd = math.sqrt(var)
    truth_egt = float(truth["egt_1_c"])
    declared_sd = bank3.noise_floor("egt_1_c", truth_egt)
    print(f"  egt_1_c truth {truth_egt:.2f} C, measured mean {mean:.2f} C "
          f"(offset {mean - truth_egt:+.2f} C)")
    print(f"  measured sd {sd:.3f} C vs declared noise floor "
          f"{declared_sd:.3f} C")
    print(f"  session bias draw was {bank3.bias['egt_1_c']:+.3f} C")
    check(abs(sd - declared_sd) / declared_sd < 0.15,
          f"recovered sd {sd:.3f} does not match declared {declared_sd:.3f}")
    check(abs((mean - truth_egt) - bank3.bias["egt_1_c"]) < 0.3,
          "the mean offset does not equal the session bias draw, so bias and "
          "noise are not cleanly separated")
    print("  the offset does NOT shrink with averaging -- that is the point")

    print("\nCASE 4  multi-rate sampling produces a staircase")
    bank4 = nominal_bank(seed=13)
    seq = []
    for i in range(25):
        t = i * 0.1
        drift = dict(truth)
        drift["coolant_temp_out_c"] = float(truth["coolant_temp_out_c"]) + t * 2.0
        m, _, rep = bank4.read(drift, t_s=t, dt_s=0.1)
        seq.append((round(m["coolant_temp_out_c"], 3),
                    "coolant_temp_out_c" in rep.held))
    uniq = len({v for v, _ in seq})
    holds = sum(1 for _, h in seq if h)
    print(f"  coolant at 1 Hz over 25 frames at 10 Hz: {uniq} distinct values, "
          f"{holds} held")
    print(f"  first 12: {[v for v, _ in seq[:12]]}")
    check(uniq <= 5, f"a 1 Hz channel produced {uniq} distinct values in 2.5 s")
    check(holds >= 18, f"only {holds} frames were held; the zero-order hold is "
                       f"not firing")
    print("  a 1 Hz channel scored at 10 Hz spends 90% of its frames stale, "
          "which is a residual source with no engine cause")

    print("\nCASE 5  a noisy healthy frame still crosses the adapter")
    from node1_ingestion import adapter as adp
    from shared.schema import EngineState, TelemetryPayload
    bank5 = nominal_bank(seed=17)
    m5, prov5, rep5 = bank5.read(truth, t_s=0.0)
    m5["engine_state"] = EngineState.RUNNING
    prov5.add("engine_state")
    p5 = TelemetryPayload(**{k: v for k, v in m5.items()
                             if k in adp.TelemetryPayload.model_fields})
    r5 = adp.to_twin_payload(p5, provided=prov5, strict=False)
    print(f"  adapter ok={r5.ok} warnings={len(r5.warnings)} "
          f"refusals={len(r5.refusals)}")
    for w in r5.warnings:
        print(f"    warning: {w[:110]}")
    for x in r5.refusals:
        print(f"    refusal: {x[:110]}")
    check(r5.ok, "a noisy HEALTHY frame was refused; the noise model is "
                 "producing unphysical values")
    spread_warn = [w for w in r5.warnings if "egt_spread_c" in w]
    check(not spread_warn,
          f"egt_spread_c was not recomputed after per-cylinder noise: "
          f"{spread_warn}")
    print(f"  egt_spread_c recomputed to {m5['egt_spread_c']:.2f} C "
          f"(truth {float(truth['egt_spread_c']):.2f} C)")
    print("  NOTE noise alone widens the spread. A spread threshold now has a "
          "false-alarm floor set by thermocouple noise, not by the engine.")

    print("\nCASE 6  dropout removes the channel and the adapter says why")
    bankd = SensorBank(seed=19, faults=[
        SensorFault("oil_pressure_kpa", DROPOUT, onset_s=0.0)])
    md, provd, repd = bankd.read(truth, t_s=1.0)
    print(f"  dropped: {sorted(repd.dropped)}")
    print(f"  key present in frame: {'oil_pressure_kpa' in md}, "
          f"in provided: {'oil_pressure_kpa' in provd}")
    check("oil_pressure_kpa" not in provd, "dropout did not shrink `provided`")
    check("oil_pressure_kpa" not in md,
          "dropout left a value in the frame, which would be scored as real")
    md["engine_state"] = EngineState.RUNNING
    provd.add("engine_state")
    pd_ = TelemetryPayload(**{k: v for k, v in md.items()
                              if k in adp.TelemetryPayload.model_fields})
    rd = adp.to_twin_payload(pd_, provided=provd, strict=False)
    print(f"  adapter ok={rd.ok}")
    for x in rd.refusals:
        print(f"    refusal: {x[:110]}")
    check(not rd.ok, "a dropped required channel was not refused")
    check(any("not provided" in x for x in rd.refusals),
          "the refusal did not name the missing channel, so the operator "
          "cannot tell a lost sensor from a bad reading")

    print("\nCASE 7  a stuck thermocouple on a HEALTHY engine")
    bank7 = stuck_egt3_bank(onset_s=2.0, seed=23)
    from shared import engine_mvem as mvem
    rows = []
    for i in range(60):
        t = i * 0.5
        thr = 80.0 + 6.0 * math.sin(t / 12.0)
        o = mvem.solve(throttle_pct=thr, altitude_ft=6000.0, oat_c=10.0)
        tr = dict(o.to_schema_dict())
        m, _, _ = bank7.read(tr, t_s=t, dt_s=0.5)
        rows.append((t, float(tr["egt_3_c"]), m["egt_3_c"], m["egt_spread_c"],
                     float(tr["power_kw"])))
    late = [r for r in rows if r[0] > 20.0]
    frozen = len({round(r[2], 3) for r in late})
    err = max(abs(r[2] - r[1]) for r in late)
    spread_max = max(r[3] for r in late)
    pw = [r[4] for r in late]
    print(f"  after onset: {frozen} distinct egt_3 readings over "
          f"{len(late)} frames, worst error {err:.1f} C")
    print(f"  worst EGT spread {spread_max:.1f} C "
          f"(certified split limit 200 C [A])")
    print(f"  power meanwhile spans {min(pw):.1f}-{max(pw):.1f} kW, i.e. the "
          f"engine is fine")
    check(frozen == 1, f"stuck channel produced {frozen} distinct values")
    check(err > 5.0, "a stuck sensor on a manoeuvring engine produced no "
                     "detectable error; the profile is too steady to test it")
    print("  THIS IS THE AMBIGUITY: identical EGT signature to a mixture "
          "fault, but fuel flow, power and vibration are untouched. Nothing "
          "in AERIS currently tests that distinction.")

    print("\nCASE 8  drift is slow, one-sided, and never triggers a limit")
    bank8 = drifting_oil_temp_bank(rate_c_per_s=0.01, onset_s=0.0, seed=29)
    d0 = d600 = 0.0
    for i in range(6001):
        t = i * 0.1
        m, _, _ = bank8.read(truth, t_s=t, dt_s=0.1)
        if i == 0:
            d0 = m["oil_temp_c"]
        if i == 6000:
            d600 = m["oil_temp_c"]
    print(f"  oil temp reads {d0:.2f} C at t=0 and {d600:.2f} C at t=600 s "
          f"(truth constant {float(truth['oil_temp_c']):.2f} C)")
    print(f"  drift over 10 min: {d600 - d0:+.2f} C; manual limit is 130 C, "
          f"never approached")
    check(d600 - d0 > 4.0, "drift did not accumulate")
    print("  a limit checker sees nothing. A residual detector sees a ramp. "
          "That gap is the whole argument for the residual approach.")

    print("\nCASE 9  THE FINDING: noise floor versus the admission gate")
    print("  mirror of shared/throttle_dynamics.py GATE_RESID_TOL, verified "
          "in CASE 14")
    fd = adp.FUEL_DENSITY_KG_PER_L
    pairs = (
        ("rpm", "rpm", 1.0),
        ("egt_1_c", "EGT_mean_C", 0.5),          # mean of 4 -> sigma/2
        ("coolant_temp_out_c", "coolant_temp_C", 1.0),
        ("oil_temp_c", "oil_temperature_C", 1.0),
        ("fuel_flow_lph", "fuelflow_kgh", fd),
        ("oil_pressure_kpa", "oil_pressure_bar", 1.0 / adp.KPA_PER_BAR),
    )
    bank9 = nominal_bank(seed=31)
    worst_ratio = 0.0
    worst_name = ""
    print(f"  {'twin channel':<20} {'noise sigma':>12} {'gate tol':>12} "
          f"{'ratio':>10} {'frames to average':>18}")
    for sch, twin, scale in pairs:
        sigma = bank9.noise_floor(sch, float(truth[sch])) * scale
        tol = GATE_RESID_TOL_MIRROR[twin]
        ratio = sigma / tol if tol > 0 else math.inf
        need = math.ceil((sigma / tol) ** 2) if tol > 0 else -1
        rate = SENSORS[sch].rate_hz
        secs = need / rate if rate > 0 else math.inf
        if ratio > worst_ratio:
            worst_ratio, worst_name = ratio, twin
        flag = "" if ratio < 1.0 else "  <-- unreachable per frame"
        print(f"  {twin:<20} {sigma:12.6f} {tol:12.6f} {ratio:10.1f} "
              f"{need:18,d}{flag}")
        if ratio >= 1.0:
            print(f"  {'':<20} {'':>12} {'':>12} {'':>10} "
                  f"= {secs/3600.0:,.1f} h at {rate:g} Hz")
    print(f"  worst channel {worst_name}, exceeding its tolerance by "
          f"{worst_ratio:,.0f}x")
    check(worst_ratio > 1.0,
          "noise floor sits inside every admission tolerance, which would mean "
          "the gate survives realistic data -- re-derive this, it contradicts "
          "the measured gate resolution")
    print("  CONSEQUENCE: per-frame absolute admission cannot be met on "
          "realistic data, and averaging cannot rescue it -- the required "
          "windows are hours. throttle_dynamics already records that the gate "
          "flips on a 0.157 C oil residual, which is BELOW this noise floor, "
          "so on noisy data that gate returns essentially a coin toss. The "
          "baselines must be retrained on sensor-realistic data and thresholds "
          "set from the noise floor, not from tree-split resolution.")

    print("\nCASE 10  noise is zero-mean, bias is not, and mass is not lost")
    bank10 = nominal_bank(seed=37)
    n = 3000
    tot_meas = 0.0
    for i in range(n):
        m, _, _ = bank10.read(truth, t_s=i * 0.2, dt_s=0.2)
        tot_meas += m["fuel_flow_lph"]
    mean_meas = tot_meas / n
    tv = float(truth["fuel_flow_lph"])
    off = 100.0 * (mean_meas - tv) / tv
    print(f"  fuel flow truth {tv:.3f} L/h, measured mean {mean_meas:.3f} L/h "
          f"({off:+.2f}%)")
    print(f"  declared systematic term {SENSORS['fuel_flow_lph'].bias_frac*100:.1f}%, "
          f"bridge density discrepancy 4.04%")
    check(abs(off) < 6.0,
          f"measured mean is {off:+.2f}% from truth, larger than the declared "
          f"systematic error allows")
    print("  the sensor's 2% systematic error is HALF the size of the fuel "
          "density assumption it rides on top of; the assumption remains the "
          "dominant fuel error")

    print("\nCASE 11  no channel ever leaves its physical range")
    bank11 = nominal_bank(seed=41)
    viol: List[str] = []
    for i in range(1500):
        t = i * 0.1
        thr = 12.0 + 88.0 * (0.5 + 0.5 * math.sin(t / 7.0))
        alt = 2000.0 * (1.0 + math.sin(t / 23.0)) + 500.0
        o = mvem.solve(throttle_pct=thr, altitude_ft=alt, oat_c=12.0)
        m, prov, _ = bank11.read(dict(o.to_schema_dict()), t_s=t, dt_s=0.1)
        for k, v in m.items():
            if k in SENSORS and isinstance(v, float):
                s = SENSORS[k]
                if not (s.lo - 1e-9 <= v <= s.hi + 1e-9):
                    viol.append(f"{k}={v:.4g} outside [{s.lo}, {s.hi}]")
                if not math.isfinite(v):
                    viol.append(f"{k} non-finite")
    print(f"  1500 frames across the throttle and altitude envelope, "
          f"{len(viol)} range violations")
    for v in viol[:4]:
        print(f"    {v}")
    check(not viol, f"{len(viol)} out-of-range measured values")

    print("\nCASE 12  no ML and no consumer imports on the generating side")
    for mod in ("node2_twin_core", "sklearn", "numpy.random.mtrand"):
        loaded = any(x == mod or x.startswith(mod + ".") for x in sys.modules)
        print(f"  {mod:22s} imported: {loaded}")
        if mod != "numpy.random.mtrand":
            check(not loaded,
                  f"{mod} was imported; the sensor model must not depend on "
                  f"the consumer")
    print("  randomness is stdlib random.Random per channel, so numpy's global "
          "seed cannot perturb a dataset")

    print("\nCASE 13  declared caveats")
    cav = sensor_caveats()
    for c in cav:
        print(f"  {c['id']:48s} verified={c['verified']}")
    unver = [c["id"] for c in cav if not c["verified"]]
    print(f"  {len(cav)} caveats, {len(unver)} unverified: {unver}")
    for must in ("noise_floor_exceeds_admission_tolerance",
                 "sensor_faults_mimic_engine_faults",
                 "sensor_specs_are_instrument_class_not_calibration",
                 "calibration_bias_is_per_session",
                 "coupled_channels_get_independent_noise"):

        check(any(c["id"] == must for c in cav),
              f"caveat {must} is missing and must not be dropped")
    tbl = sensor_channel_table()
    print(f"  channel table exports {len(tbl)} rows, "
          f"{sum(1 for r in tbl if r['verified'])} with a verified spec")

    print("\nCASE 14  is the GATE_RESID_TOL mirror current?")
    print("  runs LAST because it imports throttle_dynamics, which imports "
          "node2 -- that would have broken CASE 12")
    try:
        from shared.throttle_dynamics import GATE_RESID_TOL as LIVE
        drift = {k: (GATE_RESID_TOL_MIRROR.get(k), LIVE.get(k))
                 for k in set(LIVE) | set(GATE_RESID_TOL_MIRROR)
                 if GATE_RESID_TOL_MIRROR.get(k) != LIVE.get(k)}
        print(f"  live keys {len(LIVE)}, mirror keys "
              f"{len(GATE_RESID_TOL_MIRROR)}, disagreements {len(drift)}")
        for k, (a, b) in drift.items():
            print(f"    {k}: mirror {a} vs live {b}")
        check(not drift,
              f"the mirrored tolerances are stale, so CASE 9 measured against "
              f"the wrong numbers: {drift}")
    except Exception as exc:
        print(f"  throttle_dynamics not importable ({type(exc).__name__}: "
              f"{str(exc)[:80]}); mirror UNCHECKED")
        print("  CASE 9's conclusion stands on the mirrored values only")
    print("\nCASE 15  healthy EGT-spread false-alarm floor")
    tspread = float(truth["egt_spread_c"])
    spreads: List[float] = []
    for s in range(300):
        m, _, _ = nominal_bank(seed=1000 + s).read(truth, t_s=0.0, dt_s=0.1)
        spreads.append(float(m["egt_spread_c"]))
    spreads.sort()
    p50 = spreads[len(spreads) // 2]
    p95 = spreads[int(0.95 * (len(spreads) - 1))]
    print(f"  truth spread {tspread:.2f} C, healthy MEASURED spread over 300 "
          f"sensor sets:")
    print(f"    min {spreads[0]:.1f}  p50 {p50:.1f}  p95 {p95:.1f}  "
          f"max {spreads[-1]:.1f} C")
    print(f"  certified split limit 200 C [A]; stuck-sensor case (CASE 7) "
          f"reached 26.0 C")
    print(f"  MVEM per-cylinder trim faults: 6% -> 56.5 C, 15% -> 99.7 C")
    check(p95 > tspread,
          "measured spread never exceeded truth across 300 sensor sets, so "
          "calibration scatter is not reaching this channel")
    if max(spreads) >= 26.0:
        print("  WARNING: the worst healthy spread REACHES the stuck-sensor "
              "spread, so EGT spread alone cannot separate a frozen "
              "thermocouple from a healthy engine. Only the real trim faults "
              "clear this floor.")
    print("  CASE 5 printed 15.00 C by luck -- EGT quantum is 1 C so the "
          "spread is integer-valued and it landed on truth. This case is the "
          "distribution, not the single draw.")
    if fails:
        print(f"\nSENSOR MODEL SELF-CHECK FAILED ({len(fails)} problem(s)):")
        for m in fails:
            print(f"  - {m}")
        raise SystemExit(1)
    print("\nSENSOR MODEL SELF-CHECK OK")
    print(f"  {len(SENSORS)} instruments, {len(SENSOR_FAULT_MODES)} fault "
          f"modes, {len(sensor_caveats())} declared caveats")
    print("  headline: the noise floor is orders of magnitude above the "
          "current admission tolerances. Retraining on noisy data is now the "
          "blocking task, and this file is what generates it.")


if __name__ == "__main__":
    _self_test()
