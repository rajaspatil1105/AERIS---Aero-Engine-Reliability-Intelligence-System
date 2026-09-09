"""AERIS mission demo -- a flyable sortie through the full pipeline.

WHAT THIS IS
------------
A segmented mission (level, constant-throttle legs) walked frame by frame:
deck equilibrium -> first-order thermal lag -> optional injected fault ->
adapter -> twin -> verdict.

WHY THE DECK AND NOT THE MVEM
-----------------------------
smoke_mission.py measured it: MVEM frames score p_anom ~0.98 (FAULT) on a
healthy engine at EVERY operating point, because the baselines were trained on
master_dataset.csv and that dataset is not thermodynamically consistent with a
Rotax 915 iS -- its BSFC implies 53% thermal efficiency, cruise EGT is 456 C,
and coolant and oil sit below any thermostat. The MVEM is calibrated to
published Rotax data and disagrees by 200-11000x the gate's own resolution.
Until the baselines are retrained on MVEM output, the deck is the only source
the twin can be scored against without the verdict being an artefact of that
disagreement. See docs/finding_deck_vs_mvem.txt.

ADMISSION IS IMPLEMENTED HERE, DELIBERATELY
-------------------------------------------
throttle_dynamics._admit() rejects a frame when any channel sits further than
GATE_RESID_TOL from its target. A developing fault IS a channel sitting away
from its target, so that gate would classify every fault as "still settling"
and the twin would never be asked. The rule below gates on throttle rate and
time since the last command change only, and never inspects a residual. Six
lines, in one place, so what the gate does is readable rather than inherited.

PREFLIGHT
---------
Each leg is probed at equilibrium BEFORE the mission runs. A leg that cannot
be scored at equilibrium will never be scored in flight, so it is reported up
front instead of producing 140 seconds of UNAVAILABLE and no explanation.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.stress_sim import (                                  # noqa: E402
    GATE_THRESHOLD, build_core, deck, _extract,
)
from shared.throttle_dynamics import (                           # noqa: E402
    LAGGED, TAU_S, _frame_from_state, _lag, rpm_for_throttle,
)
from node1_ingestion.adapter import (                            # noqa: E402
    to_twin_payload, twin_frame_to_dict,
)

MISSION_VERSION = "0.2.0"

# Measured, not assumed: deck_throttle_envelope caveat in throttle_dynamics.
DECK_THROTTLE_MIN = 56.5

# Four time constants of the slowest channel (oil, 25 s) leaves ~2% of any
# step outstanding. Anything less and the frame is still moving.
SETTLE_REQUIRED_S = 300.0  # measured, not 4*tau
                            # residual still pushes a healthy p_anom to 0.78


# Command is "moving" above this rate. A step change is thousands of %/s.
THROTTLE_RATE_TOL_PCT_S = 0.5


# ------------------------------------------------------------------ #
# Mission definition
# ------------------------------------------------------------------ #

@dataclass
class Leg:
    label: str
    seconds: float
    throttle_pct: float
    altitude_ft: float
    oat_c: float

    def op(self) -> Dict[str, float]:
        return {"altitude_ft": self.altitude_ft,
                "ambient_temperature_C": self.oat_c,
                "throttle_pct": self.throttle_pct,
                "rpm": rpm_for_throttle(self.throttle_pct)}


def sortie(hold_s: float = 450.0) -> List[Leg]:
    """Three level legs stepping down through the deck's usable band.

    Altitude is constant within a leg. The deck target is a function of
    altitude, so a real climb moves the target continuously and nothing ever
    settles -- climbs are therefore honestly unscoreable, not omitted for
    convenience.
    """
    return [
        Leg("climb power", hold_s, 95.0, 6000.0, 10.0),
        Leg("high cruise", hold_s, 80.0, 6000.0, 10.0),
        Leg("econ cruise", hold_s, 70.0, 8000.0, 6.0),
    ]


# ------------------------------------------------------------------ #
# Injected faults -- these are faults of the ENGINE
# ------------------------------------------------------------------ #

FAULT_NONE = "none"

# channel, final magnitude (twin units), ramp seconds. Magnitudes are chosen
# to be PLAUSIBLE degradations, not to be easy to detect: a fouling radiator
# core raising coolant 6 C, oil running 5 C hot, fuel metering ~4% rich.
FAULT_SPECS: Dict[str, Tuple[str, float, float]] = {
    "cooling_degradation":     ("coolant_temp_C",    6.00, 120.0),
    "lubrication_degradation": ("oil_temperature_C", 5.00, 120.0),
    "fuel_pressure_dev":       ("fuelflow_kgh",      0.42,  90.0),
}


def fault_offset(kind: str, t_s: float, onset_s: float) -> Dict[str, float]:
    """Additive offset applied AFTER the lag, in twin units.

    After the lag rather than by shifting the deck target, because the fault is
    a change in the ENGINE, not a change in what a healthy engine would do. The
    lag model describes the healthy plant, so it must not relax the fault away.
    """
    if kind == FAULT_NONE or t_s < onset_s:
        return {}
    ch, mag, ramp = FAULT_SPECS[kind]
    frac = min(1.0, (t_s - onset_s) / ramp) if ramp > 0 else 1.0
    return {ch: mag * frac}


# ------------------------------------------------------------------ #
# Admission -- rate and elapsed time only, never residuals
# ------------------------------------------------------------------ #

def admit(prev_thr: Optional[float], thr: float, dt_s: float,
          since_change_s: float) -> Tuple[bool, str]:
    if prev_thr is not None and dt_s > 0.0:
        rate = abs(thr - prev_thr) / dt_s
        if rate > THROTTLE_RATE_TOL_PCT_S:
            return False, f"throttle moving {rate:+.1f} %/s"
    if since_change_s < SETTLE_REQUIRED_S:
        return False, (f"settling {since_change_s:.0f}/"
                       f"{SETTLE_REQUIRED_S:.0f}s since command change")
    return True, ""


# ------------------------------------------------------------------ #
# One frame through adapter + twin
# ------------------------------------------------------------------ #

def score_state(core: Any, op: Dict[str, float], meas: Dict[str, float],
                humidity_pct: float = 0.0) -> Dict[str, Any]:
    """Returns {wire, p_anom, status, reason, worst_channel, residual}."""
    r: Dict[str, Any] = {"wire": "REFUSED", "p_anom": None, "status": None,
                         "reason": "", "worst_channel": None,
                         "residual": None}
    try:
        payload, provided = _frame_from_state(op, meas, humidity_pct)
    except Exception as exc:
        r["reason"] = f"synthesis {type(exc).__name__}: {str(exc)[:70]}"
        return r

    res = to_twin_payload(payload, provided=provided, strict=False)
    if not res.ok:
        r["reason"] = (res.refusals[0][:88] if res.refusals
                       else "adapter refused")
        return r

    try:
        out = twin_frame_to_dict(core.process(res.features))
    except Exception as exc:
        r["reason"] = f"twin {type(exc).__name__}: {str(exc)[:70]}"
        return r

    r["status"] = out.get("status")
    resid = out.get("residuals")
    if isinstance(resid, dict) and resid:
        try:
            w = max(resid, key=lambda k: abs(float(resid[k])))
            r["worst_channel"] = w
            r["residual"] = abs(float(resid[w]))
        except (TypeError, ValueError):
            pass

    pa = out.get("anomaly_probability")
    if pa is None:
        r["wire"] = "UNAVAILABLE"
        v = out.get("envelope_violations") or []
        r["reason"] = str(v[0])[:88] if v else "twin withheld a diagnosis"
    else:
        r["wire"] = "SCORED"
        r["p_anom"] = float(pa)
    return r


# ------------------------------------------------------------------ #
# Preflight: which legs can be scored at all?
# ------------------------------------------------------------------ #

def preflight(core: Any, legs: List[Leg]) -> List[Dict[str, Any]]:
    """Probe each leg at exact equilibrium. Cheap, and it answers up front."""
    rows = []
    for lg in legs:
        op = lg.op()
        row: Dict[str, Any] = {"leg": lg.label, "scoreable": False,
                               "p_anom": None, "reason": ""}
        if lg.throttle_pct < DECK_THROTTLE_MIN:
            row["reason"] = (f"throttle {lg.throttle_pct}% below the deck's "
                             f"trained minimum {DECK_THROTTLE_MIN}%")
            rows.append(row)
            continue
        target = _extract(deck().predict(dict(op)))
        target["rpm"] = op["rpm"]
        got = score_state(core, op, target)
        row["p_anom"] = got["p_anom"]
        row["reason"] = got["reason"]
        row["scoreable"] = got["p_anom"] is not None
        rows.append(row)
    return rows


# ------------------------------------------------------------------ #
# Frames and mission
# ------------------------------------------------------------------ #

@dataclass
class Frame:
    t_s: float = 0.0
    leg: str = ""
    throttle_pct: float = 0.0
    altitude_ft: float = 0.0
    wire: str = "UNAVAILABLE"
    reason: str = ""
    p_anom: Optional[float] = None
    status: Optional[str] = None
    worst_channel: Optional[str] = None
    residual: Optional[float] = None
    fault_applied: Dict[str, float] = field(default_factory=dict)

    def p(self) -> str:
        return "  --  " if self.p_anom is None else f"{self.p_anom:.4f}"


@dataclass
class Mission:
    frames: List[Frame] = field(default_factory=list)
    fault_kind: str = FAULT_NONE
    fault_onset_s: float = 0.0

    def scored(self) -> List[Frame]:
        return [f for f in self.frames if f.p_anom is not None]

    def first_detection(self) -> Optional[Frame]:
        for f in self.frames:
            if f.p_anom is not None and f.p_anom >= GATE_THRESHOLD:
                return f
        return None

    def summary(self) -> Dict[str, Any]:
        sc = self.scored()
        det = self.first_detection()
        healthy = [f for f in sc if f.p_anom < GATE_THRESHOLD]
        refused = [f for f in self.frames if f.wire == "REFUSED"]
        return {"frames": len(self.frames), "scored": len(sc),
                "unavailable": len(self.frames) - len(sc) - len(refused),
                "refused": len(refused),
                "healthy": len(healthy), "above_gate": len(sc) - len(healthy),
                "first_detection_s": None if det is None else det.t_s,
                "detection_lag_s": (None if det is None else
                                    round(det.t_s - self.fault_onset_s, 1))}


def fly(core: Any, legs: List[Leg], fault_kind: str = FAULT_NONE,
        fault_onset_s: float = 0.0, hz: float = 10.0,
        humidity_pct: float = 0.0) -> Mission:
    if fault_kind != FAULT_NONE and fault_kind not in FAULT_SPECS:
        raise ValueError(f"unknown fault {fault_kind!r}; "
                         f"have {[FAULT_NONE] + sorted(FAULT_SPECS)}")
    try:
        core.reset()
    except Exception:
        pass

    dt = 1.0 / hz
    m = Mission(fault_kind=fault_kind, fault_onset_s=fault_onset_s)
    state: Optional[Dict[str, float]] = None
    prev_op: Optional[Dict[str, float]] = None
    since_change_s = SETTLE_REQUIRED_S
    t_s = 0.0

    for lg in legs:
        op = lg.op()
        for _ in range(int(round(lg.seconds * hz))):
            fr = Frame(t_s=t_s, leg=lg.label, throttle_pct=lg.throttle_pct,
                       altitude_ft=lg.altitude_ft)

            target = _extract(deck().predict(dict(op)))
            target["rpm"] = op["rpm"]

            if state is None:
                state = dict(target)          # warm start on leg 1
                since_change_s = SETTLE_REQUIRED_S
            else:
                moved = any(op[k] != prev_op[k] for k in op) if prev_op else False
                since_change_s = 0.0 if moved else since_change_s + dt
                for k in LAGGED:
                    state[k] = _lag(state[k], target[k], TAU_S[k], dt)

            off = fault_offset(fault_kind, t_s, fault_onset_s)
            meas = dict(state)
            for k, v in off.items():
                meas[k] = meas[k] + v
            fr.fault_applied = dict(off)

            prev_thr = None if prev_op is None else prev_op["throttle_pct"]
            ok, why = admit(prev_thr, op["throttle_pct"], dt, since_change_s)
            prev_op = dict(op)

            if not ok:
                fr.wire, fr.reason = "UNAVAILABLE", why
            else:
                got = score_state(core, op, meas, humidity_pct)
                fr.wire = got["wire"]
                fr.reason = got["reason"]
                fr.p_anom = got["p_anom"]
                fr.status = got["status"]
                fr.worst_channel = got["worst_channel"]
                fr.residual = got["residual"]

            m.frames.append(fr)
            t_s += dt

    return m


# ------------------------------------------------------------------ #
# Reporting
# ------------------------------------------------------------------ #

def print_timeline(m: Mission, every_s: float = 10.0) -> None:
    print(f"\n{'t (s)':>7}  {'leg':<12}{'thr':>6}{'alt':>7}  "
          f"{'wire':<12}{'p_anom':>9}  note")
    nxt = 0.0
    for f in m.frames:
        if f.t_s + 1e-9 < nxt:
            continue
        nxt = f.t_s + every_s
        note = f.reason if f.p_anom is None else (f.status or "")
        if f.fault_applied:
            k, v = next(iter(f.fault_applied.items()))
            note = f"{note} [{k} {v:+.2f}]".strip()
        print(f"{f.t_s:7.1f}  {f.leg:<12}{f.throttle_pct:6.1f}"
              f"{f.altitude_ft:7.0f}  {f.wire:<12}{f.p():>9}  {note[:56]}")


def print_summary(m: Mission) -> None:
    s = m.summary()
    print(f"\nmission summary")
    print(f"  frames {s['frames']}: scored {s['scored']}, unavailable "
          f"{s['unavailable']}, refused {s['refused']}")
    print(f"  of the scored: {s['healthy']} healthy, {s['above_gate']} above "
          f"the gate ({GATE_THRESHOLD})")
    if m.fault_kind != FAULT_NONE:
        ch, mag, ramp = FAULT_SPECS[m.fault_kind]
        print(f"  injected {m.fault_kind}: {ch} +{mag} over {ramp:.0f}s from "
              f"t={m.fault_onset_s:.0f}s")
        if s["first_detection_s"] is None:
            print(f"  NOT DETECTED: the fault stayed below the gate for the "
                  f"whole mission. Report this as the detection floor for "
                  f"this fault size, do not enlarge the fault to force a hit.")
        else:
            print(f"  crossed the gate at t={s['first_detection_s']:.1f}s, "
                  f"{s['detection_lag_s']}s after onset")
    print(f"  UNAVAILABLE is a rate or settling rejection, not an error: "
          f"{SETTLE_REQUIRED_S:.0f}s required after any command change")


def caveats() -> List[Dict[str, Any]]:
    return [
        {"id": "deck_is_the_source_not_the_mvem", "verified": True,
         "note": ("the MVEM is calibrated to published Rotax data and the "
                  "baselines are not, so they disagree by 200-11000x the "
                  "gate's resolution. This demo scores the deck against "
                  "itself: internally consistent, demonstrates the PIPELINE, "
                  "and does not demonstrate agreement with a real 915 iS.")},
        {"id": "no_climbs_or_descents_are_scoreable", "verified": True,
         "note": ("the deck target depends on altitude, so changing altitude "
                  "moves the target continuously and nothing settles. Legs "
                  "are level by necessity. Throttle below 56.5% is outside "
                  "the trained band, so idle, descent and approach cannot be "
                  "scored at all.")},
        {"id": "fault_is_an_additive_offset", "verified": True,
         "note": ("an injected fault shifts one measured channel after the "
                  "lag; it is not a degradation propagated through the "
                  "engine. The coupled signature a real fault produces -- "
                  "coolant and oil and power moving together -- is absent. "
                  "Magnitudes are plausible, the coupling is not modelled.")},
        {"id": "admission_never_inspects_residuals", "verified": True,
         "note": ("_admit in throttle_dynamics rejects frames whose channels "
                  "sit away from target, which is what a fault looks like, so "
                  "it would hide every fault as 'still settling'. The rule "
                  "here uses throttle rate and elapsed time only, which is "
                  "why faults are detectable at all.")},
        {"id": "no_sensor_noise_in_this_path", "verified": True,
         "note": ("shared/sensor_model.py is deliberately NOT wired in. With "
                  "realistic instrument error the trained gate's decision "
                  "boundaries sit inside the noise floor on 5 of 6 channels, "
                  "up to 1185x, and the verdict becomes noise-decided. That "
                  "is the retraining task: measured and declared, not "
                  "hidden.")},
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description="AERIS mission demo")
    ap.add_argument("--fault", default=FAULT_NONE,
                    choices=[FAULT_NONE] + sorted(FAULT_SPECS))
    ap.add_argument("--onset", type=float, default=300.0)
    ap.add_argument("--hold", type=float, default=140.0,
                    help=f"seconds per leg (>= {SETTLE_REQUIRED_S:.0f} to settle)")
    ap.add_argument("--hz", type=float, default=10.0)
    ap.add_argument("--every", type=float, default=10.0)
    ap.add_argument("--preflight-only", action="store_true")
    args = ap.parse_args()

    print(f"AERIS mission demo v{MISSION_VERSION}")
    print(f"  gate {GATE_THRESHOLD}, settling {SETTLE_REQUIRED_S:.0f}s, "
          f"{args.hz:g} Hz, {args.hold:.0f}s per leg")
    legs = sortie(hold_s=args.hold)
    core = build_core()

    print("\npreflight: can each leg be scored at equilibrium?")
    rows = preflight(core, legs)
    for r in rows:
        pa = "  --  " if r["p_anom"] is None else f"{r['p_anom']:.4f}"
        mark = "yes" if r["scoreable"] else "NO "
        print(f"  {r['leg']:<12} scoreable={mark}  p_anom={pa}  "
              f"{r['reason'][:52]}")
    usable = [r for r in rows if r["scoreable"]]
    print(f"  {len(usable)} of {len(rows)} legs are scoreable")
    if not usable:
        print("  STOP: no leg can be scored, so the mission would be entirely "
              "UNAVAILABLE. Fix the operating points before flying.")
        return
    if args.preflight_only:
        return
    if args.hold < SETTLE_REQUIRED_S:
        print(f"  WARNING: hold {args.hold:.0f}s < {SETTLE_REQUIRED_S:.0f}s, "
              f"so no frame after the first leg can be admitted")

    m = fly(core, legs, fault_kind=args.fault, fault_onset_s=args.onset,
            hz=args.hz)
    print_timeline(m, every_s=args.every)
    print_summary(m)
    print("\ndeclared caveats")
    for c in caveats():
        print(f"  {c['id']:36s} verified={c['verified']}")


if __name__ == "__main__":
    main()
