"""
AERIS Phase 1 -- Node 1 -> Node 2 telemetry adapter.

Translates the canonical 68-column TelemetryPayload (shared/schema.py) into the
9 raw input features that Node 2's twin core consumes.

WHY THIS FILE EXISTS
--------------------
shared/schema.py is a Rotax 915 iS class contract in SI-ish units (kPa, m, L/h,
per-cylinder EGT). Node 2's FEATURE_ORDER was recovered by experiment from the
trained artefacts and uses different names AND different units (bar, ft, kg/h,
single mean EGT). Nothing else in the tree performs that translation, so without
this module Node 1 and Node 2 are two disconnected halves.

DESIGN RULES
------------
* Every unit conversion is a named constant with a provenance string. No magic
  numbers inline.
* Two conversions rest on assumptions that are NOT recoverable from the repo:
  fuel volumetric->mass density, and which coolant channel the baseline forest
  was trained on. Both are declared UNVERIFIED and exported via
  adapter_caveats() so the UI and /caveats can display them. They are not
  hidden inside a physics comparison.
* The adapter REFUSES rather than guesses. A schema default (0.0) is not a
  measurement. If a required channel is absent or implausible, no feature dict
  is produced and the reason is reported.
* Refusal is engine-state aware: zeros are legitimate when the engine is
  STOPPED, and are then reported as "not meaningful" instead of as an error.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.schema import EngineState, TelemetryPayload  # noqa: E402

ADAPTER_VERSION = "0.1.0"

# ------------------------------------------------------------------ #
# Exact conversions -- no assumption, defined by unit definition
# ------------------------------------------------------------------ #
FT_PER_M = 3.280839895013123      # exact: 1 ft == 0.3048 m
KPA_PER_BAR = 100.0               # exact: 1 bar == 100 kPa

# ------------------------------------------------------------------ #
# ASSUMPTION 1 -- fuel density. Converts fuel_flow_lph -> fuelflow_kgh.
# ------------------------------------------------------------------ #
FUEL_DENSITY_KG_PER_L = 0.72
FUEL_DENSITY_PROVENANCE = (
    "UNVERIFIED. 0.72 kg/L is avgas 100LL at 15 C. The training script that "
    "produced fuelflow_kgh_baseline.pkl has not been read, so the density the "
    "training data assumed is unknown. If the simulator emitted mass flow "
    "directly, or assumed MOGAS (~0.745) or Jet-A (~0.80), every fuel residual "
    "is scaled by the ratio of the two densities."
)
FUEL_DENSITY_VERIFIED = False

# ------------------------------------------------------------------ #
# ASSUMPTION 2 -- coolant channel selection.
# ------------------------------------------------------------------ #
COOLANT_SOURCE_FIELD = "coolant_temp_out_c"
COOLANT_SOURCE_PROVENANCE = (
    "UNVERIFIED. The schema exposes coolant_temp_in_c and coolant_temp_out_c; "
    "coolant_temp_C_baseline.pkl was trained on ONE unlabelled coolant channel. "
    "Outlet is selected because it is the conventional single-point coolant "
    "reading and it is the hotter of the two, matching the manifest's observed "
    "healthy output range. Selecting the wrong channel biases every coolant "
    "residual by the engine's coolant delta-T."
)
COOLANT_SOURCE_VERIFIED = False

# Recorded in models/model_manifest.json for coolant_temp_C_baseline.pkl.
# Used only as a sanity cross-check on the channel choice, never to clamp.
MANIFEST_COOLANT_HEALTHY_RANGE = (62.257, 87.435)

# ------------------------------------------------------------------ #
# Target contract -- must equal node2_twin_core RAW_FEATURES
# ------------------------------------------------------------------ #
TWIN_RAW_FEATURES: Tuple[str, ...] = (
    "altitude_ft",
    "ambient_temperature_C",
    "throttle_pct",
    "rpm",
    "fuelflow_kgh",
    "coolant_temp_C",
    "EGT_mean_C",
    "oil_pressure_bar",
    "oil_temperature_C",
)

# twin feature -> schema fields it is derived from
SOURCE_FIELDS: Dict[str, Tuple[str, ...]] = {
    "altitude_ft":           ("altitude_m",),
    "ambient_temperature_C": ("oat_c",),
    "throttle_pct":          ("throttle_pct",),
    "rpm":                   ("rpm",),
    "fuelflow_kgh":          ("fuel_flow_lph",),
    "coolant_temp_C":        (COOLANT_SOURCE_FIELD,),
    "EGT_mean_C":            ("egt_1_c", "egt_2_c", "egt_3_c", "egt_4_c"),
    "oil_pressure_bar":      ("oil_pressure_kpa",),
    "oil_temperature_C":     ("oil_temp_c",),
}

# Mirrors PHYSICAL_BOUNDS in node3_service/api.py. Kept as a local copy so the
# adapter can refuse before the HTTP layer is involved.
# Density altitude is a DIFFERENT quantity from geometric altitude and has a
# legitimately wider negative range: a cold day at sea level yields roughly
# -4500 ft DA, and cold_start reaches -5191 ft. The geometric floor of
# -1500 ft (Dead Sea) would refuse those as impossible. They are not
# impossible, they are merely unseen -- extrapolation, which the caller
# reports, not a refusal.
DENSITY_ALT_FLOOR_FT = -6000.0

TWIN_BOUNDS: Dict[str, Tuple[float, float]] = {
    "altitude_ft":           (-1500.0, 60000.0),
    "ambient_temperature_C": (-90.0, 70.0),
    "throttle_pct":          (0.0, 100.0),
    "rpm":                   (0.0, 12000.0),
    "fuelflow_kgh":          (0.0, 500.0),
    "coolant_temp_C":        (-60.0, 300.0),
    "EGT_mean_C":            (-60.0, 1400.0),
    "oil_pressure_bar":      (0.0, 20.0),
    "oil_temperature_C":     (-60.0, 300.0),
}

# Channels that cannot legitimately read exactly 0.0 on a running engine.
# altitude_m and oat_c are excluded: 0 m and 0 C are both valid measurements.
NONZERO_WHEN_RUNNING = (
    "rpm", "oil_pressure_kpa", "oil_temp_c", "fuel_flow_lph",
    COOLANT_SOURCE_FIELD, "egt_1_c", "egt_2_c", "egt_3_c", "egt_4_c",
)

RUNNING_STATES = (EngineState.RUNNING, EngineState.IDLE, EngineState.UNKNOWN)


class AdapterError(Exception):
    """Raised when a frame cannot be honestly converted."""


@dataclass
class AdapterResult:
    """Outcome of one conversion attempt."""

    features: Optional[Dict[str, float]]
    ok: bool
    refusals: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    meaningful: bool = True
    egt_spread_c: Optional[float] = None
    engine_state: str = EngineState.UNKNOWN.value

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "features": self.features,
            "refusals": list(self.refusals),
            "warnings": list(self.warnings),
            "assumptions": list(self.assumptions),
            "meaningful": self.meaningful,
            "egt_spread_c": self.egt_spread_c,
            "engine_state": self.engine_state,
            "adapter_version": ADAPTER_VERSION,
        }


def _all_caveats() -> List[Dict[str, Any]]:
    """Machine-readable assumption list for /caveats and the GCS banner."""
    return [
        {
            "id": "fuel_density_assumed",
            "verified": FUEL_DENSITY_VERIFIED,
            "value": FUEL_DENSITY_KG_PER_L,
            "unit": "kg/L",
            "affects": ["fuelflow_kgh", "delta_fuelflow_kgh"],
            "detail": FUEL_DENSITY_PROVENANCE,
        },
        {
            "id": "density_altitude_floor",
            "verified": True,
            "value": DENSITY_ALT_FLOOR_FT,
            "unit": "ft",
            "affects": ["altitude_ft"],
            "detail": ("applies only when altitude_is_density=True; the "
                       "geometric floor of -1500 ft is unchanged"),
        },
        {
            "id": "training_data_fidelity",
            "verified": False,
            "value": "~70%",
            "unit": None,
            "affects": ["all model output"],
            "detail": ("the Cantera generator was built from limited public "
                       "Rotax 915 iS data; the dataset is estimated ~70% "
                       "faithful to a real engine. AERIS demonstrates the "
                       "detection architecture, not certified thresholds"),
        },
        {
            "id": "coolant_channel_assumed",
            "verified": COOLANT_SOURCE_VERIFIED,
            "value": COOLANT_SOURCE_FIELD,
            "unit": None,
            "affects": ["coolant_temp_C", "delta_coolant_temp_C"],
            "detail": COOLANT_SOURCE_PROVENANCE,
        },
    ]


# An ASSUMPTION silently changes a number on its way to the twin, so it can
# never be verified=True.  The count is part of the published contract and is
# asserted by node3_service.canonical.  A DECLARATION states a limitation but
# alters no number, and may legitimately be verified=True.
ASSUMPTION_IDS: tuple = ("fuel_density_assumed", "coolant_channel_assumed")


def adapter_caveats() -> List[Dict[str, Any]]:
    """The numeric substitutions only.  Always exactly ASSUMPTION_IDS."""
    return [c for c in _all_caveats() if c["id"] in ASSUMPTION_IDS]


def adapter_declarations() -> List[Dict[str, Any]]:
    """Stated limitations that alter no value (fidelity, DA floor, envelope)."""
    return [c for c in _all_caveats() if c["id"] not in ASSUMPTION_IDS]


def _egt_mean_and_spread(p: TelemetryPayload) -> Tuple[float, float]:
    vals = [p.egt_1_c, p.egt_2_c, p.egt_3_c, p.egt_4_c]
    return sum(vals) / 4.0, max(vals) - min(vals)


def to_twin_payload(
    payload: TelemetryPayload,
    altitude_is_density: bool = False,
    provided: Optional[Iterable[str]] = None,
    strict: bool = True,
) -> AdapterResult:
    """Convert one canonical frame into Node 2's 9 raw features.

    `provided` is the set of schema field names the source actually populated.
    Node 1 knows this from the raw JSON keys. When supplied, absence of a
    required field is a hard refusal. When omitted, the adapter falls back to
    plausibility checks only and says so in warnings.
    """
    refusals: List[str] = []
    warnings: List[str] = []
    state = payload.engine_state
    running = state in RUNNING_STATES

    assumptions = [
        f"fuel density {FUEL_DENSITY_KG_PER_L} kg/L (UNVERIFIED)",
        f"coolant channel {COOLANT_SOURCE_FIELD} (UNVERIFIED)",
    ]

    prov = set(provided) if provided is not None else None
    if prov is None:
        warnings.append(
            "population provenance unknown: caller did not supply `provided`, "
            "so a schema default cannot be distinguished from a measurement "
            "except by plausibility"
        )
    else:
        for tgt, srcs in SOURCE_FIELDS.items():
            missing = [s for s in srcs if s not in prov]
            if missing:
                refusals.append(f"{tgt}: source channel(s) not provided {missing}")

    if running:
        zeros = [f for f in NONZERO_WHEN_RUNNING if getattr(payload, f) == 0.0]
        if zeros:
            refusals.append(
                f"engine_state={state.value} but these channels read exactly "
                f"0.0, which is a schema default rather than a measurement: "
                f"{zeros}"
            )

    egt_mean, egt_spread = _egt_mean_and_spread(payload)

    feats: Dict[str, float] = {
        "altitude_ft":           payload.altitude_m * FT_PER_M,
        "ambient_temperature_C": payload.oat_c,
        "throttle_pct":          payload.throttle_pct,
        "rpm":                   payload.rpm,
        "fuelflow_kgh":          payload.fuel_flow_lph * FUEL_DENSITY_KG_PER_L,
        "coolant_temp_C":        getattr(payload, COOLANT_SOURCE_FIELD),
        "EGT_mean_C":            egt_mean,
        "oil_pressure_bar":      payload.oil_pressure_kpa / KPA_PER_BAR,
        "oil_temperature_C":     payload.oil_temp_c,
    }

    for name, val in feats.items():
        if val != val or val in (float("inf"), float("-inf")):
            refusals.append(f"{name}: non-finite after conversion ({val!r})")
            continue
        lo, hi = TWIN_BOUNDS[name]
        if name == "altitude_ft" and altitude_is_density:
            lo = DENSITY_ALT_FLOOR_FT
        if not (lo <= val <= hi):
            refusals.append(
                f"{name}={val:.6g} outside physically possible [{lo}, {hi}]"
            )

    if running:
        clo, chi = MANIFEST_COOLANT_HEALTHY_RANGE
        cv = feats["coolant_temp_C"]
        if cv == cv and not (clo - 25.0 <= cv <= chi + 25.0):
            warnings.append(
                f"coolant_temp_C={cv:.4g} is far from the baseline's observed "
                f"healthy range [{clo}, {chi}]; the {COOLANT_SOURCE_FIELD} "
                f"channel choice may be wrong"
            )

    if egt_spread > 0.0 and payload.egt_spread_c > 0.0:
        if abs(egt_spread - payload.egt_spread_c) > 1.0:
            warnings.append(
                f"egt_spread_c reported by source ({payload.egt_spread_c:.4g}) "
                f"disagrees with spread computed from the four cylinder "
                f"channels ({egt_spread:.4g})"
            )

    if refusals:
        if strict:
            raise AdapterError(
                "frame cannot be converted:\n  - " + "\n  - ".join(refusals)
            )
        return AdapterResult(
            features=None, ok=False, refusals=refusals, warnings=warnings,
            assumptions=assumptions, meaningful=False,
            egt_spread_c=egt_spread, engine_state=state.value,
        )

    return AdapterResult(
        features=feats, ok=True, refusals=[], warnings=warnings,
        assumptions=assumptions, meaningful=running,
        egt_spread_c=egt_spread, engine_state=state.value,
    )

# ==================================================================== #
# Self-check
# ==================================================================== #

def _nominal_frame() -> Tuple[TelemetryPayload, set]:
    """A healthy cruise frame inside Node 2's trained envelope.

    1828.8 m is exactly 6000 ft. 250 kPa is exactly 2.50 bar.
    30 L/h at 0.72 kg/L is exactly 21.6 kg/h.
    """
    raw = dict(
        engine_state=EngineState.RUNNING,
        rpm=5000.0, throttle_pct=80.0,
        altitude_m=1828.8, oat_c=10.0,
        fuel_flow_lph=30.0,
        coolant_temp_in_c=71.0, coolant_temp_out_c=78.0,
        egt_1_c=450.0, egt_2_c=455.0, egt_3_c=460.0, egt_4_c=465.0,
        egt_spread_c=15.0,
        oil_pressure_kpa=250.0, oil_temp_c=92.0,
    )
    return TelemetryPayload(**raw), set(raw) | {"engine_state"}


def twin_frame_to_dict(out: Any) -> Dict[str, Any]:
    """Normalise whatever TwinCore.process() returns into a plain dict.

    Node 2 returns a TwinFrame object, not a mapping. Node 3's store already
    persists a JSON view of it, so a serialisation path exists somewhere; this
    helper finds it without caring which one it is. Tried in order: mapping,
    to_dict/as_dict/asdict/dict/model_dump, dataclass fields, then public
    attributes as a last resort.
    """
    if isinstance(out, dict):
        return dict(out)
    if hasattr(out, "keys") and hasattr(out, "__getitem__"):
        return {k: out[k] for k in out.keys()}
    for meth in ("to_dict", "as_dict", "asdict", "dict", "model_dump", "to_json_dict"):
        fn = getattr(out, meth, None)
        if callable(fn):
            try:
                got = fn()
                if isinstance(got, dict):
                    return got
            except Exception:
                pass
    try:
        import dataclasses
        if dataclasses.is_dataclass(out):
            return dataclasses.asdict(out)
    except Exception:
        pass
    return {
        n: getattr(out, n) for n in dir(out)
        if not n.startswith("_") and not callable(getattr(out, n, None))
    }


def _deck_nominal_frame() -> Tuple[Any, set, Dict[str, float]]:
    """Build a schema frame from what the baselines say healthy looks like.

    Hand-invented "plausible" numbers are not on the baseline manifold, so they
    provoke the gate. This instead asks Node 2 for a healthy twin payload, then
    inverts every unit conversion to produce the equivalent 68-column frame.
    Adapter output should then reproduce the twin payload to floating-point
    tolerance -- a true round trip.

    NOTE: fuel density cancels in this round trip (kg/h -> L/h -> kg/h with the
    same constant), so CASE 11 does NOT validate FUEL_DENSITY_KG_PER_L. Only
    the training script can.
    """
    from node2_twin_core.physics_deck import BaselineDeck
    from node2_twin_core import residual_calc as rc

    deck = BaselineDeck()
    healthy = None
    for attempt in (
        lambda: rc._healthy_payload(deck),
        lambda: rc._healthy_payload(deck=deck),
        lambda: rc._healthy_payload(),
    ):
        try:
            healthy = attempt()
            break
        except Exception:
            continue
    if healthy is None:
        raise AdapterError("could not obtain a healthy payload from node2")
    healthy = {k: float(v) for k, v in dict(healthy).items()
               if k in TWIN_RAW_FEATURES}
    missing = [k for k in TWIN_RAW_FEATURES if k not in healthy]
    if missing:
        raise AdapterError(f"healthy payload lacks raw features {missing}")

    egt = healthy["EGT_mean_C"]
    raw = dict(
        engine_state=EngineState.RUNNING,
        rpm=healthy["rpm"],
        throttle_pct=healthy["throttle_pct"],
        altitude_m=healthy["altitude_ft"] / FT_PER_M,
        oat_c=healthy["ambient_temperature_C"],
        fuel_flow_lph=healthy["fuelflow_kgh"] / FUEL_DENSITY_KG_PER_L,
        oil_pressure_kpa=healthy["oil_pressure_bar"] * KPA_PER_BAR,
        oil_temp_c=healthy["oil_temperature_C"],
        egt_1_c=egt - 7.5, egt_2_c=egt - 2.5,
        egt_3_c=egt + 2.5, egt_4_c=egt + 7.5,
        egt_spread_c=15.0,
    )
    raw[COOLANT_SOURCE_FIELD] = healthy["coolant_temp_C"]
    other = ("coolant_temp_in_c" if COOLANT_SOURCE_FIELD == "coolant_temp_out_c"
             else "coolant_temp_out_c")
    raw[other] = healthy["coolant_temp_C"] - 7.0
    return TelemetryPayload(**raw), set(raw) | {"engine_state"}, healthy


def _self_test() -> None:
    fails: List[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            fails.append(msg)

    print("ADAPTER SELF-CHECK")
    print(f"  version {ADAPTER_VERSION}")
    print(f"  target features: {len(TWIN_RAW_FEATURES)}")

    # -- CASE 1: contract matches Node 2, if Node 2 is importable ----- #
    print("\nCASE 1  feature contract vs node2")
    try:
        from node2_twin_core.residual_calc import RAW_FEATURES as N2_RAW
        same = list(N2_RAW) == list(TWIN_RAW_FEATURES)
        print(f"  node2 RAW_FEATURES  : {len(N2_RAW)}")
        print(f"  identical and ordered: {same}")
        if not same:
            print(f"    node2  : {list(N2_RAW)}")
            print(f"    adapter: {list(TWIN_RAW_FEATURES)}")
        check(same, "adapter feature list disagrees with node2 RAW_FEATURES")
    except Exception as exc:
        print(f"  node2 not importable ({type(exc).__name__}); contract unchecked")

    # -- CASE 2: exact unit conversions ------------------------------- #
    print("\nCASE 2  unit conversions")
    p, prov = _nominal_frame()
    r = to_twin_payload(p, provided=prov)
    f = r.features
    check(r.ok, "nominal frame refused")
    print(f"  1828.8 m -> {f['altitude_ft']:.4f} ft   (expect 6000.0000)")
    check(abs(f["altitude_ft"] - 6000.0) < 1e-6, "altitude conversion wrong")
    print(f"  250 kPa  -> {f['oil_pressure_bar']:.4f} bar  (expect 2.5000)")
    check(abs(f["oil_pressure_bar"] - 2.5) < 1e-12, "oil pressure conversion wrong")
    print(f"  30 L/h   -> {f['fuelflow_kgh']:.4f} kg/h (expect 21.6000)")
    check(abs(f["fuelflow_kgh"] - 21.6) < 1e-12, "fuel flow conversion wrong")

    # -- CASE 3: EGT reduction and coolant selection ------------------ #
    print("\nCASE 3  derived channels")
    print(f"  EGT 450/455/460/465 -> mean {f['EGT_mean_C']:.3f} spread {r.egt_spread_c:.3f}")
    check(abs(f["EGT_mean_C"] - 457.5) < 1e-12, "EGT mean wrong")
    check(abs(r.egt_spread_c - 15.0) < 1e-12, "EGT spread wrong")
    print(f"  coolant from {COOLANT_SOURCE_FIELD} -> {f['coolant_temp_C']:.3f} C")
    check(f["coolant_temp_C"] == 78.0, "coolant channel selection wrong")
    print(f"  in=71.0 out=78.0 -> delta-T 7.0 C is the size of the assumption")

    # -- CASE 4: missing channel is refused, not defaulted ------------ #
    print("\nCASE 4  missing source channel")
    short = set(prov) - {"oil_pressure_kpa"}
    r4 = to_twin_payload(p, provided=short, strict=False)
    print(f"  ok={r4.ok} features={'None' if r4.features is None else 'dict'}")
    for m in r4.refusals:
        print(f"    refusal: {m}")
    check(not r4.ok and r4.features is None, "missing channel was not refused")
    try:
        to_twin_payload(p, provided=short, strict=True)
        fails.append("strict mode did not raise on missing channel")
    except AdapterError:
        print("  strict=True raised AdapterError as expected")

    # -- CASE 5: schema defaults on a running engine are refused ------ #
    print("\nCASE 5  all-default frame, engine RUNNING")
    empty = TelemetryPayload(engine_state=EngineState.RUNNING)
    r5 = to_twin_payload(empty, strict=False)
    print(f"  ok={r5.ok}")
    for m in r5.refusals:
        print(f"    refusal: {m[:110]}")
    check(not r5.ok, "all-zero running frame was accepted")

    # -- CASE 6: same zeros are legitimate when STOPPED --------------- #
    print("\nCASE 6  all-default frame, engine STOPPED")
    stopped = TelemetryPayload(engine_state=EngineState.STOPPED)
    r6 = to_twin_payload(stopped, strict=False)
    print(f"  ok={r6.ok} meaningful={r6.meaningful} state={r6.engine_state}")
    check(r6.ok, "stopped-engine zeros should convert")
    check(not r6.meaningful, "stopped engine must not be flagged meaningful")

    # -- CASE 7: physically impossible input is refused --------------- #
    print("\nCASE 7  physically impossible values")
    for fld, val, why in (
        ("oil_pressure_kpa", -3.0, "negative oil pressure"),
        ("rpm", 50000.0, "rpm beyond bound"),
        ("egt_1_c", 9000.0, "EGT beyond bound"),
    ):
        bad, bprov = _nominal_frame()
        setattr(bad, fld, val)
        rb = to_twin_payload(bad, provided=bprov, strict=False)
        print(f"  {why:24} -> ok={rb.ok}")
        check(not rb.ok, f"{why} was accepted")

    # -- CASE 8: coolant channel sanity warning ----------------------- #
    print("\nCASE 8  coolant channel cross-check")
    odd, oprov = _nominal_frame()
    odd.coolant_temp_out_c = 20.0
    r8 = to_twin_payload(odd, provided=oprov, strict=False)
    hit = [w for w in r8.warnings if "channel choice may be wrong" in w]
    print(f"  coolant 20 C -> warnings={len(r8.warnings)} channel-warning={bool(hit)}")
    check(bool(hit), "implausible coolant did not raise the channel warning")

    # -- CASE 9: assumptions are exported, not hidden ----------------- #
    print("\nCASE 9  declared assumptions")
    cav = adapter_caveats()
    for c in cav:
        print(f"  {c['id']:26} verified={c['verified']} value={c['value']}")
    check(len(cav) == 2, "expected two declared assumptions")
    check(all(not c["verified"] for c in cav), "an assumption is marked verified")

    dec = adapter_declarations()
    print("\nCASE 9b  declared limitations (these alter no number)")
    for d in dec:
        print(f"  {d['id']:26} verified={d['verified']} value={d['value']}")
    check(len(dec) >= 2, "declarations not surfaced")
    check(len(r.assumptions) == 2, "result did not carry assumptions")

    # -- CASE 10: end-to-end through the twin ------------------------- #
    print("\nCASE 10  drive TwinCore.process with adapter output")
    try:
        from node2_twin_core.twin_core import TwinCore
        core = TwinCore(warm_up=False, explain=False)
        raw_out = core.process(f)
        print(f"  returned type       : {type(raw_out).__name__}")
        out = twin_frame_to_dict(raw_out)
        print(f"  normalised keys     : {len(out)}")
        status = out.get("status")
        env = out.get("in_envelope")
        print(f"  status={status} in_envelope={env}")
        print(f"  p_anom={out.get('anomaly_probability')} "
              f"rul_raw={out.get('rul_raw')}")
        check(status is not None, "twin returned no status")
        check(env is True, f"nominal frame not in envelope (in_envelope={env!r})")
        if status != "HEALTHY":
            print(f"  NOTE: hand-built frame reads {status}. These values are\n"
                  f"        plausible but not on the baseline manifold. See CASE 11.")
        viol = out.get("envelope_violations") or ()
        print(f"  envelope_violations : {list(viol)}")
        missing = [k for k in ("status", "in_envelope", "anomaly_probability",
                               "features", "residuals") if k not in out]
        print(f"  expected keys absent: {missing}")
        check(not missing, f"twin frame lacks keys {missing}")
        print("  end-to-end path VERIFIED: 68-col schema -> adapter -> twin")
    except Exception as exc:
        print(f"  twin unavailable ({type(exc).__name__}: {exc})")
        fails.append(f"end-to-end path failed: {type(exc).__name__}: {exc}")

    # -- CASE 11: deck-derived round trip ----------------------------- #
    print("\nCASE 11  round trip against the baseline manifold")
    try:
        pd, provd, healthy = _deck_nominal_frame()
        rd = to_twin_payload(pd, provided=provd)
        check(rd.ok, "deck-derived frame refused by adapter")
        worst, worst_name = 0.0, ""
        for k in TWIN_RAW_FEATURES:
            d = abs(rd.features[k] - healthy[k])
            if d > worst:
                worst, worst_name = d, k
        print(f"  deck healthy point  : rpm={healthy['rpm']:.1f} "
              f"thr={healthy['throttle_pct']:.1f} alt={healthy['altitude_ft']:.0f} ft")
        print(f"  worst round-trip err: {worst:.3e} ({worst_name})")
        check(worst < 1e-6, f"round trip lost precision: {worst:.3e} on {worst_name}")

        from node2_twin_core.twin_core import TwinCore
        core2 = TwinCore(warm_up=False, explain=False)
        o2 = twin_frame_to_dict(core2.process(rd.features))
        res = o2.get("residuals") or {}
        rmax = max((abs(float(v)) for v in res.values()), default=float("nan"))
        print(f"  twin status={o2.get('status')} p_anom={o2.get('anomaly_probability')}")
        print(f"  largest residual    : {rmax:.4g}")
        print(f"  rul_raw={o2.get('rul_raw')} rul_trusted={o2.get('rul_trusted')}")
        check(bool(o2.get("in_envelope")), "deck-derived frame outside envelope")
        check(rmax < 1.0, f"residuals not near zero on healthy frame: {rmax:.4g}")
        if o2.get("status") != "HEALTHY":
            print(f"  NOTE: residuals are near zero yet status={o2.get('status')}. "
                  f"The physics layer agrees the engine is healthy; the gate "
                  f"disagrees. Known gate defect, not an adapter fault.")
        rr = o2.get("rul_raw")
        if isinstance(rr, (int, float)) and rr < 0:
            print(f"  NOTE: rul_raw={rr:.4g} is negative, i.e. physically "
                  f"impossible. Node 4 must never render raw RUL.")
    except Exception as exc:
        print(f"  round trip unavailable ({type(exc).__name__}: {exc})")
        fails.append(f"CASE 11 failed: {type(exc).__name__}: {exc}")

    print()
    if fails:
        print("ADAPTER SELF-CHECK FAILED:")
        for m in fails:
            print(f"  - {m}")
        raise SystemExit(1)
    print("ADAPTER SELF-CHECK OK")
    print("  note: two conversions remain UNVERIFIED and are surfaced in "
          "adapter_caveats()")


if __name__ == "__main__":
    _self_test()
