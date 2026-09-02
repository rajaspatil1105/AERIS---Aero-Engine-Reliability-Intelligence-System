#!/usr/bin/env python
"""
node2_twin_core/plausibility.py -- healthy-range advisory tier.

Sits between the catastrophic hard limits (safety_limits.py) and the
ML gate, covering the band where a fault is real but sub-catastrophic.
Motivating case: oil pressure 3.16 -> 2.16 bar was reported HEALTHY.
The gate scored 0.568 (below the 0.65 threshold) and 2.16 bar is far
above the 1.0 bar hard limit, so nothing caught it. 2.16 is however
below the documented healthy minimum of 2.198.

Baselines were fit on HEALTHY-ONLY rows, so baseline_stats min/max is
the observed healthy range over 50,000 samples.

ONE-SIDED TEST. Outside the range  => definitely abnormal (no healthy
sample ever reached it). Inside the range => NOT a clean bill of health:
a value can be globally normal yet wrong for its operating point. This
tier therefore raises advisories, it does not clear anything.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Advisory:
    field: str
    measured: float
    lo: float
    hi: float
    side: str          # "below" or "above"
    excess: float      # how far outside, in absolute units
    excess_sigma: float | None

    def describe(self) -> str:
        s = (f"{self.excess_sigma:.1f} sigma"
             if self.excess_sigma is not None else "sigma unknown")
        return (f"{self.field}={self.measured:g} is {self.side} the healthy "
                f"range [{self.lo:g}, {self.hi:g}] by {self.excess:g} ({s})")


def check_healthy_range(payload: Mapping, stats: Mapping) -> tuple:
    """Advisories for measurements outside the observed healthy range."""
    out = []
    for name, st in stats.items():
        v = payload.get(name)
        if v is None:
            continue
        v = float(v)
        if not math.isfinite(v):
            continue
        lo, hi = float(st["min"]), float(st["max"])
        sd = float(st.get("std") or 0.0)
        if v < lo:
            ex = lo - v
            side = "below"
        elif v > hi:
            ex = v - hi
            side = "above"
        else:
            continue
        out.append(Advisory(name, v, lo, hi, side, ex,
                            (ex / sd) if sd > 0 else None))
    return tuple(out)

def _self_test() -> None:
    from node2_twin_core.physics_deck import BaselineDeck
    from node2_twin_core.residual_calc import _healthy_payload, ResidualCalculator

    calc = ResidualCalculator()
    stats = calc.deck.stats
    fails = []
    p = _healthy_payload(calc)

    print("documented healthy ranges:")
    for n, st in stats.items():
        print(f"  {n:<20} [{st['min']:.3f}, {st['max']:.3f}]  std={st['std']:.4f}")

    print("\nCASE 1  healthy point -> no advisories")
    a = check_healthy_range(p, stats)
    print(f"  advisories={len(a)}")
    if a:
        fails.append("healthy payload raised advisories")

    print("\nCASE 2  the case that slipped through the gate")
    q = dict(p, oil_pressure_bar=p["oil_pressure_bar"] - 1.0)
    a = check_healthy_range(q, stats)
    print(f"  oil_pressure_bar={q['oil_pressure_bar']:.3f} -> advisories={len(a)}")
    for x in a:
        print(f"    {x.describe()}")
    if not a:
        fails.append("2.16 bar must raise an advisory")

    print("\nCASE 3  boundary is not an advisory")
    for fld, val, exp in (("oil_pressure_bar", stats["oil_pressure_bar"]["min"], 0),
                          ("oil_pressure_bar", stats["oil_pressure_bar"]["min"] - 1e-6, 1),
                          ("EGT_mean_C", stats["EGT_mean_C"]["max"], 0),
                          ("EGT_mean_C", stats["EGT_mean_C"]["max"] + 1e-6, 1)):
        n = len(check_healthy_range(dict(p, **{fld: val}), stats))
        print(f"  {fld:<18}={val:<12.6f} advisories={n} expected={exp}")
        if n != exp:
            fails.append(f"boundary {fld}={val}")

    print("\nCASE 4  documented blind spot -- in-range but wrong for the point")
    mid = (stats["EGT_mean_C"]["min"] + stats["EGT_mean_C"]["max"]) / 2.0
    idle = dict(p, throttle_pct=60.0)
    exp_egt = calc.deck.predict({k: idle[k] for k in
                                 ("rpm", "throttle_pct", "altitude_ft",
                                  "ambient_temperature_C")}).expected["EGT_mean_C"]
    a = check_healthy_range(dict(idle, EGT_mean_C=mid), stats)
    print(f"  EGT={mid:.1f} at 60% throttle (expected {exp_egt:.1f}) "
          f"-> advisories={len(a)}")
    print(f"  residual would be {abs(mid - exp_egt):.1f} C, yet globally in range")
    print("  -> by design: this tier cannot see operating-point context")

    print("\nCASE 5  missing / non-finite are skipped, never 'safe'")
    print(f"  empty payload  advisories={len(check_healthy_range({}, stats))}")
    nan = dict(p, EGT_mean_C=float('nan'))
    print(f"  NaN EGT        advisories={len(check_healthy_range(nan, stats))}")

    if fails:
        print("\nPLAUSIBILITY SELF-CHECK FAILED")
        for f in fails:
            print(f"  - {f}")
        raise SystemExit(1)
    print("\nPLAUSIBILITY SELF-CHECK OK")
    print("NOTE: one-sided. Advisories mean abnormal; their absence means")
    print("      nothing. Operating-point context comes from residuals only.")


if __name__ == "__main__":
    _self_test()