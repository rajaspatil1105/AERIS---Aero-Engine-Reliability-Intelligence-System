"""
AERIS -- MVEM to telemetry bridge.

WHY THIS FILE EXISTS
--------------------
shared/engine_mvem.py produces engine state in model units (bar, kg/h, feet,
four separate EGTs). shared/schema.py declares the canonical 68-column contract
in schema units (kPa, L/h, metres). node1_ingestion/adapter.py already owns the
schema-to-twin translation and refuses frames it cannot honestly convert.

This module is the one missing link: EngineOutputs -> TelemetryPayload. It
performs NO unit arithmetic of its own beyond what EngineOutputs.to_schema_dict
already did, adds the fields the physics does not know about (engine state,
timestamp, sequence, provenance), and reports which fields it populated so the
adapter can distinguish a measurement from a schema default.

WHAT THIS MODULE MUST NOT DO
----------------------------
* No unit conversions the adapter already performs. Two sources of truth for
  kPa-to-bar is how residuals acquire silent biases.
* No thermal lag. shared/throttle_dynamics.py owns that with its own declared
  time constants. This bridge emits STEADY-STATE frames; the caller lags them.
* No model imports. The physics side of the arrow stays free of node2.

THE FUEL DENSITY CONFLICT, AND THE CHOICE MADE HERE
---------------------------------------------------
engine_mvem is calibrated to MOGAS at 0.7503 kg/L, derived from [B] Table 4
which over-determines itself. adapter.py converts L/h to kg/h at 0.72 kg/L,
which is AVGAS 100LL and is declared UNVERIFIED there.

Chained naively the error is silent and one-directional: the MVEM computes mass
flow, divides by 0.7503 to report volume, and the adapter multiplies by 0.72,
delivering 4.04% LESS mass flow to the twin than the physics produced. Every
fuel residual is then biased by that ratio and nothing anywhere says so.

Mass flow is the physical quantity; volumetric flow is derived from it by a
density that depends on which fuel is in the tank. So the default mode here is
MASS_PRESERVING: convert kg/h to L/h using THE ADAPTER'S density, so the mass
flow arriving at the twin equals the mass flow the physics computed, exactly.
The cost is that the reported litres-per-hour corresponds to AVGAS while the
calibration is MOGAS -- a declared 4% discrepancy in a displayed number rather
than a hidden 4% bias in every residual.

Mode VOLUME_TRUE is available for when the adapter's density is corrected or
made configurable. It reports true MOGAS volume and accepts the mass error
until then. It is not the default because a wrong number you can see is safer
than a wrong number you cannot.
"""

from __future__ import annotations

import json
import math
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import engine_mvem as mvem                      # noqa: E402
from shared.schema import EngineState, TelemetryPayload      # noqa: E402
from node1_ingestion import adapter as adp                   # noqa: E402

BRIDGE_VERSION = "0.1.0"

# ------------------------------------------------------------------ #
# Fuel density reconciliation -- see the module docstring
# ------------------------------------------------------------------ #
MODE_MASS_PRESERVING = "MASS_PRESERVING"
MODE_VOLUME_TRUE = "VOLUME_TRUE"
FUEL_MODE_DEFAULT = MODE_MASS_PRESERVING

FUEL_DENSITY_MISMATCH_PCT = 100.0 * abs(
    adp.FUEL_DENSITY_KG_PER_L - mvem.FUEL_DENSITY_KG_PER_L
) / mvem.FUEL_DENSITY_KG_PER_L

# ------------------------------------------------------------------ #
# Engine state inference. The MVEM has no notion of a stopped engine;
# it always solves a running one. The bridge decides, because the
# adapter's zero-channel refusal depends on getting this right.
# ------------------------------------------------------------------ #
IDLE_RPM_CEILING = 2400.0

# Fields the adapter treats as required sources. Asserted present by CASE 2 so
# a schema rename cannot silently break the chain.
REQUIRED_BY_ADAPTER: Tuple[str, ...] = (
    "altitude_m", "oat_c", "throttle_pct", "rpm", "fuel_flow_lph",
    adp.COOLANT_SOURCE_FIELD, "egt_1_c", "egt_2_c", "egt_3_c", "egt_4_c",
    "oil_pressure_kpa", "oil_temp_c",
)


class BridgeError(Exception):
    """Raised when engine state cannot be honestly represented as telemetry."""


# ==================================================================== #
# Frame production
# ==================================================================== #

@dataclass
class BridgeFrame:
    """One telemetry frame plus everything the adapter and store need."""

    payload: TelemetryPayload
    provided: set
    seq: int = 0
    t_s: float = 0.0
    timestamp: str = ""
    fault_label: str = "HEALTHY"
    limit_breaches: List[str] = field(default_factory=list)
    extras: Dict[str, Any] = field(default_factory=dict)
    provenance: str = "MVEM_SIMULATED"
    bridge_version: str = BRIDGE_VERSION
    mvem_version: str = mvem.ENGINE_MVEM_VERSION
    fuel_mode: str = FUEL_MODE_DEFAULT

    def to_json_dict(self) -> Dict[str, Any]:
        """Body for POST /frames. Only populated fields, so the receiver can
        tell a measurement from a schema default."""
        d = {k: getattr(self.payload, k) for k in self.provided
             if k != "engine_state"}
        d["engine_state"] = self.payload.engine_state.value
        d["timestamp"] = self.timestamp
        d["seq"] = self.seq
        d["provenance"] = self.provenance
        return d


def infer_engine_state(o: mvem.EngineOutputs) -> EngineState:
    """Map engine outputs onto the schema's state enum.

    Matters because adapter.NONZERO_WHEN_RUNNING refuses a frame whose channels
    read exactly 0.0 while the state claims the engine is running. The MVEM
    never produces a stopped engine, so mislabelling here would either mask a
    real refusal or trigger a false one.
    """
    if o.rpm <= 1.0:
        return EngineState.STOPPED
    if o.rpm < IDLE_RPM_CEILING:
        return EngineState.IDLE
    return EngineState.RUNNING


def fuel_flow_lph_for_schema(fuel_kgh: float,
                             mode: str = FUEL_MODE_DEFAULT) -> float:
    """Volumetric flow to report, per the declared reconciliation mode."""
    if mode == MODE_MASS_PRESERVING:
        return fuel_kgh / adp.FUEL_DENSITY_KG_PER_L
    if mode == MODE_VOLUME_TRUE:
        return fuel_kgh / mvem.FUEL_DENSITY_KG_PER_L
    raise BridgeError(f"unknown fuel reconciliation mode {mode!r}")


def to_payload(o: mvem.EngineOutputs,
               seq: int = 0,
               t_s: float = 0.0,
               timestamp: Optional[str] = None,
               fuel_mode: str = FUEL_MODE_DEFAULT) -> BridgeFrame:
    """Convert one solved operating point into a canonical telemetry frame."""
    d = dict(o.to_schema_dict())

    # The only value overridden after to_schema_dict: fuel volume, per the
    # reconciliation mode. Everything else passes through untouched.
    d["fuel_flow_lph"] = fuel_flow_lph_for_schema(o.fuel_flow_kgh, fuel_mode)

    state = infer_engine_state(o)
    d["engine_state"] = state

    unknown = [k for k in d if not hasattr(TelemetryPayload(), k)]
    if unknown:
        raise BridgeError(
            f"engine_mvem emits field names the schema does not declare: "
            f"{unknown}. Reconcile shared/schema.py and "
            f"EngineOutputs.to_schema_dict before generating data.")

    payload = TelemetryPayload(**d)
    ts = timestamp or datetime.now(timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")

    return BridgeFrame(
        payload=payload, provided=set(d), seq=seq, t_s=t_s, timestamp=ts,
        fault_label=o.fault_label, limit_breaches=list(o.limit_breaches),
        extras=dict(o.extras()), fuel_mode=fuel_mode)


def to_twin_features(frame: BridgeFrame,
                     strict: bool = True) -> adp.AdapterResult:
    """Push a bridge frame through the existing adapter.

    Deliberately delegates rather than reimplementing: the adapter owns the
    conversions, the bounds and the refusal logic, and it is already tested.
    """
    return adp.to_twin_payload(frame.payload, altitude_is_density=False,
                               provided=frame.provided, strict=strict)


# ==================================================================== #
# Profile walking
# ==================================================================== #

@dataclass
class Setpoint:
    """One commanded condition. Throttle in percent, altitude in feet."""
    t_s: float
    throttle_pct: float
    altitude_ft: float
    oat_c: Optional[float] = None        # None -> ISA at that altitude
    humidity_pct: float = 0.0
    airspeed_ms: float = mvem.AIRSPEED_REF_MS
    electrical_load_a: float = mvem.ELECTRICAL_BASE_LOAD_A


def walk(setpoints: Sequence[Setpoint],
         hz: float = 2.0,
         fault: Optional[mvem.FaultState] = None,
         fuel_mode: str = FUEL_MODE_DEFAULT,
         t0_unix: Optional[float] = None) -> Iterator[BridgeFrame]:
    """Emit frames along a piecewise-linear command profile.

    STEADY STATE ONLY. Each frame is the equilibrium solution at that instant's
    commanded condition, with no thermal lag whatsoever. Real EGT and coolant
    lag their targets by seconds to minutes, so a climb generated here shows
    temperatures snapping to their new values instantly. That is wrong, it is
    declared in bridge_caveats(), and the fix is to run the output through
    shared/throttle_dynamics.py -- which owns the time constants and must
    remain the only place they live.
    """
    if len(setpoints) < 2:
        raise BridgeError("a profile needs at least two setpoints")
    if hz <= 0.0:
        raise BridgeError(f"sample rate {hz} Hz must be positive")

    pts = sorted(setpoints, key=lambda s: s.t_s)
    for a, b in zip(pts, pts[1:]):
        if b.t_s <= a.t_s:
            raise BridgeError(f"setpoint times must strictly increase "
                              f"({a.t_s} then {b.t_s})")

    base = time.time() if t0_unix is None else float(t0_unix)
    dt = 1.0 / hz
    t_end = pts[-1].t_s
    n_frames = int(math.floor(t_end / dt)) + 1

    for i in range(n_frames):
        t = i * dt
        lo = pts[0]
        hi = pts[-1]
        for a, b in zip(pts, pts[1:]):
            if a.t_s <= t <= b.t_s:
                lo, hi = a, b
                break
        span = hi.t_s - lo.t_s
        f = 0.0 if span <= 0.0 else (t - lo.t_s) / span

        def lerp(x: float, y: float) -> float:
            return x + f * (y - x)

        oat = None
        if lo.oat_c is not None and hi.oat_c is not None:
            oat = lerp(lo.oat_c, hi.oat_c)
        elif lo.oat_c is not None:
            oat = lo.oat_c

        o = mvem.solve(
            throttle_pct=lerp(lo.throttle_pct, hi.throttle_pct),
            altitude_ft=lerp(lo.altitude_ft, hi.altitude_ft),
            oat_c=oat,
            humidity_pct=lerp(lo.humidity_pct, hi.humidity_pct),
            airspeed_ms=lerp(lo.airspeed_ms, hi.airspeed_ms),
            fault=fault,
            electrical_load_a=lerp(lo.electrical_load_a, hi.electrical_load_a))

        ts = datetime.fromtimestamp(base + t, tz=timezone.utc).isoformat(
            timespec="milliseconds").replace("+00:00", "Z")
        yield to_payload(o, seq=i + 1, t_s=t, timestamp=ts, fuel_mode=fuel_mode)


def survey_profile(cruise_ft: float = 12000.0,
                   cruise_throttle: float = 78.0,
                   climb_s: float = 600.0,
                   cruise_s: float = 1800.0,
                   descent_s: float = 500.0) -> List[Setpoint]:
    """A minimal taxi-climb-cruise-descend sortie.

    Placeholder shape only. The real mission planner -- map polygon, survey
    legs, Open-Meteo winds aloft -- replaces this and will emit the same
    Setpoint sequence, so nothing downstream changes when it arrives.
    """
    t = 0.0
    pts = [Setpoint(t, 12.0, 0.0, airspeed_ms=mvem.AIRSPEED_MIN_MS)]
    t += 120.0
    pts.append(Setpoint(t, 95.0, 0.0, airspeed_ms=30.0))
    t += climb_s
    pts.append(Setpoint(t, 92.0, cruise_ft, airspeed_ms=48.0))
    t += cruise_s
    pts.append(Setpoint(t, cruise_throttle, cruise_ft, airspeed_ms=52.0))
    t += descent_s
    pts.append(Setpoint(t, 30.0, 500.0, airspeed_ms=42.0))
    return pts


# ==================================================================== #
# Optional HTTP sink
# ==================================================================== #

def post_frame(frame: BridgeFrame, url: str = "http://127.0.0.1:8000/frames",
               timeout: float = 5.0) -> Dict[str, Any]:
    """POST one frame to the running service. Optional -- dataset generation
    never touches this, which is why the core stays pure."""
    body = json.dumps(frame.to_json_dict()).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise BridgeError(f"POST {url} returned {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise BridgeError(f"POST {url} failed: {exc.reason}; is the service "
                          f"running?") from exc


def stream(setpoints: Sequence[Setpoint], hz: float = 2.0,
           url: str = "http://127.0.0.1:8000/frames",
           fault: Optional[mvem.FaultState] = None,
           realtime: bool = True,
           fuel_mode: str = FUEL_MODE_DEFAULT) -> int:
    """Walk a profile and post each frame, optionally in real time."""
    dt = 1.0 / hz
    n = 0
    for fr in walk(setpoints, hz=hz, fault=fault, fuel_mode=fuel_mode):
        post_frame(fr, url=url)
        n += 1
        if realtime:
            time.sleep(dt)
    return n


# ==================================================================== #
# Declared caveats
# ==================================================================== #

def bridge_caveats() -> List[Dict[str, Any]]:
    return [
        {"id": "fuel_density_reconciliation", "verified": True,
         "value": {"mode": FUEL_MODE_DEFAULT,
                   "mvem_kg_per_l": mvem.FUEL_DENSITY_KG_PER_L,
                   "adapter_kg_per_l": adp.FUEL_DENSITY_KG_PER_L,
                   "mismatch_pct": round(FUEL_DENSITY_MISMATCH_PCT, 3)},
         "note": ("engine_mvem is calibrated to MOGAS (0.7503 kg/L, derived "
                  "from published data) and adapter.py converts at 0.72 kg/L, "
                  "which is AVGAS and is UNVERIFIED there. Chained naively the "
                  "twin would receive 4.04% less mass flow than the physics "
                  "produced, biasing every fuel residual silently. "
                  "MASS_PRESERVING converts using the ADAPTER'S density so "
                  "mass round-trips exactly; the cost is that reported L/h "
                  "corresponds to AVGAS while the calibration is MOGAS. A "
                  "visible discrepancy in a displayed number beats a hidden "
                  "one in a residual. Fix properly by making the adapter's "
                  "density configurable per session.")},
        {"id": "steady_state_frames_no_lag", "verified": True,
         "value": "no thermal time constants applied",
         "note": ("every frame is the equilibrium solution at that instant's "
                  "command, so a simulated climb shows EGT and coolant "
                  "snapping instantly to new values. Real lag is seconds to "
                  "minutes. shared/throttle_dynamics.py owns the time "
                  "constants and must remain the only place they live; run "
                  "bridge output through it before treating a transient as "
                  "realistic.")},
        {"id": "engine_state_inferred_not_measured", "verified": True,
         "value": {"stopped_below_rpm": 1.0,
                   "idle_below_rpm": IDLE_RPM_CEILING},
         "note": ("the MVEM always solves a running engine and has no concept "
                  "of a start sequence, so engine_state is inferred from rpm "
                  "alone. adapter.NONZERO_WHEN_RUNNING refuses frames whose "
                  "channels read 0.0 while claiming to run, so this inference "
                  "decides whether a refusal is real. No cranking, priming or "
                  "shutdown transient exists.")},
        {"id": "provenance_tag_must_survive", "verified": True,
         "value": "MVEM_SIMULATED",
         "note": ("every frame carries this tag. If it is ever dropped between "
                  "here and the report, simulated data becomes "
                  "indistinguishable from a real CAN capture in the audit "
                  "trail. Node 3 must persist it and the report must print "
                  "it.")},
        {"id": "no_sensor_model_yet", "verified": True,
         "value": "noise-free, lag-free, quantisation-free",
         "note": ("this bridge emits PHYSICS, not sensor readings. There is no "
                  "measurement noise, no ADC quantisation, no multi-rate "
                  "sampling and no dropout or freeze failure mode. Residuals "
                  "computed against this data are therefore optimistically "
                  "clean. A sensor model belongs between the MVEM and this "
                  "bridge, and is where sensor-versus-engine decoupling will "
                  "live.")},
    ]


# ==================================================================== #
# Self-check
# ==================================================================== #

def _self_test() -> None:
    fails: List[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            fails.append(msg)
            print(f"  FAIL: {msg}")

    print(f"simulator_bridge v{BRIDGE_VERSION}  "
          f"mvem v{mvem.ENGINE_MVEM_VERSION}  adapter v{adp.ADAPTER_VERSION}")

    print("\nCASE 1  one cruise frame survives the whole chain")
    o = mvem.solve(throttle_pct=80.0, altitude_ft=6000.0, oat_c=10.0)
    fr = to_payload(o, seq=1)
    print(f"  populated {len(fr.provided)} schema fields, state "
          f"{fr.payload.engine_state.value}, extras {len(fr.extras)}")
    r = to_twin_features(fr, strict=False)
    print(f"  adapter ok={r.ok} meaningful={r.meaningful} "
          f"refusals={len(r.refusals)} warnings={len(r.warnings)}")
    for m in r.refusals:
        print(f"    refusal: {m[:110]}")
    for m in r.warnings:
        print(f"    warning: {m[:110]}")
    check(r.ok, "a healthy cruise frame was refused by the adapter")
    check(r.features is not None, "adapter produced no features")

    print("\nCASE 2  every field the adapter requires is populated")
    missing = [k for k in REQUIRED_BY_ADAPTER if k not in fr.provided]
    print(f"  required {len(REQUIRED_BY_ADAPTER)}, missing {missing or 'none'}")
    check(not missing,
          f"bridge does not populate adapter-required fields {missing}")
    print(f"  coolant channel the adapter reads: {adp.COOLANT_SOURCE_FIELD} "
          f"= {getattr(fr.payload, adp.COOLANT_SOURCE_FIELD):.2f} C")

    print("\nCASE 3  the fuel density conflict, resolved and measured")
    print(f"  mvem {mvem.FUEL_DENSITY_KG_PER_L} kg/L ({mvem.FUEL_TYPE})  "
          f"adapter {adp.FUEL_DENSITY_KG_PER_L} kg/L  "
          f"apart {FUEL_DENSITY_MISMATCH_PCT:.2f}%")
    mass_in = o.fuel_flow_kgh
    mass_out = r.features["fuelflow_kgh"]
    err = 100.0 * (mass_out - mass_in) / mass_in
    print(f"  MASS_PRESERVING: physics {mass_in:.4f} kg/h -> twin "
          f"{mass_out:.4f} kg/h  ({err:+.4f}%)")
    check(abs(err) < 1e-9,
          f"mass flow changed by {err:+.4f}% crossing the bridge, so fuel "
          f"residuals carry a silent bias")

    fr_v = to_payload(o, seq=1, fuel_mode=MODE_VOLUME_TRUE)
    r_v = to_twin_features(fr_v, strict=False)
    err_v = 100.0 * (r_v.features["fuelflow_kgh"] - mass_in) / mass_in
    print(f"  VOLUME_TRUE:     physics {mass_in:.4f} kg/h -> twin "
          f"{r_v.features['fuelflow_kgh']:.4f} kg/h  ({err_v:+.4f}%)")
    check(abs(err_v) > 3.0,
          "VOLUME_TRUE should show the 4% mass error; if it does not, the two "
          "densities have converged and this caveat can be retired")
    print(f"  reported volume differs between modes: "
          f"{fr.payload.fuel_flow_lph:.3f} vs {fr_v.payload.fuel_flow_lph:.3f} "
          f"L/h -- the visible cost of preserving mass")

    print("\nCASE 4  engine state inference")
    for label, thr, expect in (("cruise", 80.0, EngineState.RUNNING),
                               ("low throttle", 5.0, EngineState.IDLE)):
        oo = mvem.solve(throttle_pct=thr, altitude_ft=0.0, oat_c=15.0)
        st = infer_engine_state(oo)
        print(f"  {label:14s} {oo.rpm:6.0f} rpm -> {st.value}")
        check(st == expect, f"{label} inferred {st.value}, expected "
                            f"{expect.value}")
    frl = to_payload(mvem.solve(throttle_pct=5.0, altitude_ft=0.0, oat_c=15.0))
    rl = to_twin_features(frl, strict=False)
    print(f"  idle frame through the adapter: ok={rl.ok} "
          f"meaningful={rl.meaningful}")
    check(rl.ok, "an idle frame was refused; check NONZERO_WHEN_RUNNING")

    print("\nCASE 5  a full sortie walks without a single refusal")
    pts = survey_profile()
    frames = list(walk(pts, hz=0.5, t0_unix=1.7e9))
    print(f"  {len(frames)} frames over {pts[-1].t_s:.0f} s at 0.5 Hz")
    refused: List[str] = []
    seqs: List[int] = []
    for f_ in frames:
        seqs.append(f_.seq)
        rr = to_twin_features(f_, strict=False)
        if not rr.ok:
            refused.append(f"seq {f_.seq} t={f_.t_s:.0f}s: {rr.refusals[:1]}")
    print(f"  refused {len(refused)} of {len(frames)}")
    for m in refused[:4]:
        print(f"    {m}")
    check(not refused, f"{len(refused)} frames refused during a healthy sortie")
    check(seqs == list(range(1, len(frames) + 1)),
          "sequence numbers are not contiguous from 1")
    check(len({f_.timestamp for f_ in frames}) == len(frames),
          "timestamps are not unique per frame")
    first, last = frames[0], frames[-1]
    print(f"  first: {first.timestamp} alt {first.payload.altitude_m:.0f} m "
          f"thr {first.payload.throttle_pct:.0f}%")
    print(f"  last:  {last.timestamp} alt {last.payload.altitude_m:.0f} m "
          f"thr {last.payload.throttle_pct:.0f}%")

    print("\nCASE 6  breaches and fault labels survive the crossing")
    hot = mvem.solve(throttle_pct=100.0, altitude_ft=0.0, oat_c=38.0,
                     humidity_pct=95.0, airspeed_ms=mvem.AIRSPEED_MIN_MS)
    fh = to_payload(hot)
    print(f"  tropical static breaches carried: {len(fh.limit_breaches)}")
    for b in fh.limit_breaches:
        print(f"    {b}")
    check(bool(fh.limit_breaches),
          "limit breaches were lost crossing the bridge")

    trim = [1.0, 1.0, 0.85, 1.0]
    ff = mvem.FaultState(cylinder_fuel_trim=trim, label="LEAN_MIXTURE_CYL3_15")
    of = mvem.solve(throttle_pct=95.0, altitude_ft=6000.0, oat_c=10.0, fault=ff)
    frf = to_payload(of)
    rf = to_twin_features(frf, strict=False)
    print(f"  fault frame label={frf.fault_label} adapter ok={rf.ok}")
    print(f"  EGT spread {frf.payload.egt_spread_c:.1f} C, per-cylinder "
          f"{[round(getattr(frf.payload, f'egt_{i}_c'), 1) for i in range(1, 5)]}")
    check(frf.fault_label == "LEAN_MIXTURE_CYL3_15", "fault label lost")
    check(rf.ok, "a fault frame was refused; faults must reach the detector")
    spread_warn = [w for w in rf.warnings if "egt_spread_c" in w]
    check(not spread_warn,
          f"adapter disputes the reported EGT spread: {spread_warn}")

    print("\nCASE 7  the mistuned holdout also converts cleanly")
    om = mvem.solve(throttle_pct=80.0, altitude_ft=6000.0, oat_c=10.0,
                    fault=mvem.mistuned())
    frm = to_payload(om)
    rm = to_twin_features(frm, strict=False)
    dm = 100.0 * (rm.features["fuelflow_kgh"] - r.features["fuelflow_kgh"]) / \
        r.features["fuelflow_kgh"]
    print(f"  ok={rm.ok} label={frm.fault_label} fuel flow vs nominal "
          f"{dm:+.1f}%")
    check(rm.ok, "the mistuned holdout was refused, so no holdout can be built")
    check(abs(dm) > 1.0,
          "the holdout is indistinguishable from nominal at the twin's input")

    print("\nCASE 8  JSON body is serialisable and complete")
    body = fr.to_json_dict()
    txt = json.dumps(body)
    print(f"  {len(body)} keys, {len(txt)} bytes, provenance "
          f"{body.get('provenance')}")
    check(body.get("provenance") == "MVEM_SIMULATED",
          "provenance tag missing from the POST body")
    check("engine_state" in body and isinstance(body["engine_state"], str),
          "engine_state is not JSON-serialisable")
    check(all(k in body for k in REQUIRED_BY_ADAPTER),
          "POST body omits a field the adapter requires")
    round_trip = json.loads(txt)
    check(round_trip == body, "body does not survive a JSON round trip")

    print("\nCASE 9  refusals")
    for label, fn in (
        ("one setpoint", lambda: list(walk([Setpoint(0.0, 50.0, 0.0)]))),
        ("negative hz", lambda: list(walk(survey_profile(), hz=-1.0))),
        ("duplicate times", lambda: list(walk(
            [Setpoint(0.0, 50.0, 0.0), Setpoint(0.0, 60.0, 100.0)]))),
        ("bad fuel mode", lambda: fuel_flow_lph_for_schema(10.0, "GUESS")),
    ):
        try:
            fn()
            check(False, f"{label} was accepted")
            print(f"  {label:18s} -> ACCEPTED (wrong)")
        except BridgeError:
            print(f"  {label:18s} -> refused")

    print("\nCASE 10  no ML on the physics side of the arrow")
    for mod in ("node2_twin_core", "sklearn"):
        loaded = any(m == mod or m.startswith(mod + ".") for m in sys.modules)
        print(f"  {mod:16s} imported: {loaded}")
        check(not loaded,
              f"{mod} was imported by the bridge chain; the generator must not "
              f"depend on the consumer")

    print("\nCASE 11  declared caveats, bridge and inherited")
    cav = bridge_caveats()
    for c in cav:
        print(f"  {c['id']:38s} verified={c['verified']}")
    for must in ("fuel_density_reconciliation", "steady_state_frames_no_lag",
                 "no_sensor_model_yet", "provenance_tag_must_survive"):
        check(any(c["id"] == must for c in cav),
              f"caveat {must} is missing and must not be dropped")
    print(f"  inherited: {len(mvem.mvem_caveats())} from engine_mvem, "
          f"{len(adp.adapter_caveats())} assumptions from the adapter")
    print("  NOTE the report must print all three sets. A frame that crosses "
          "this bridge carries every one of them.")

    if fails:
        print(f"\nBRIDGE SELF-CHECK FAILED ({len(fails)} problem(s)):")
        for m in fails:
            print(f"  - {m}")
        raise SystemExit(1)
    print("\nBRIDGE SELF-CHECK OK")
    print("  physics -> schema -> adapter -> twin features, no refusals")
    print("  mass flow preserved exactly; the density conflict is declared")


if __name__ == "__main__":
    _self_test()
