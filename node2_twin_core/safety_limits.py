#!/usr/bin/env python
"""
node2_twin_core/safety_limits.py -- deterministic hard-limit guard.

Runs BEFORE the ML gate. A statistical model is never the sole guard on
a hard safety limit, especially one measured at chance performance.

Limits are catastrophic-event thresholds, well outside the baseline
training envelope. They will NOT catch mild degradation -- e.g. oil
pressure at 2.2 bar is abnormal (baseline 2.20-4.26) yet far above the
1.0 bar alarm. Hard limits and the ML gate cover different regimes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Limit:
    field: str
    kind: str          # "min" or "max"
    value: float
    unit: str
    message: str


HARD_LIMITS: tuple[Limit, ...] = (
    Limit("oil_pressure_bar", "min", 1.0, "bar",
          "Oil pressure critically low -- lubrication loss imminent"),
    Limit("coolant_temp_C", "max", 120.0, "C",
          "Coolant over-temperature -- overheating"),
    Limit("EGT_mean_C", "max", 950.0, "C",
          "EGT over-temperature -- turbine/valve damage risk"),
)


@dataclass(frozen=True)
class Breach:
    field: str
    measured: float
    limit: float
    kind: str
    unit: str
    message: str

    def describe(self) -> str:
        op = "<" if self.kind == "min" else ">"
        return (f"{self.field}={self.measured:g}{self.unit} {op} "
                f"{self.limit:g}{self.unit}: {self.message}")


def check_limits(payload: Mapping) -> tuple[Breach, ...]:
    """Return every breached hard limit. Empty tuple means none breached.

    Missing or non-finite fields are skipped, not treated as safe --
    callers should use missing_fields() to detect unmonitored channels.
    """
    out = []
    for lim in HARD_LIMITS:
        v = payload.get(lim.field)
        if v is None:
            continue
        v = float(v)
        if not math.isfinite(v):
            continue
        hit = v < lim.value if lim.kind == "min" else v > lim.value
        if hit:
            out.append(Breach(lim.field, v, lim.value, lim.kind,
                              lim.unit, lim.message))
    return tuple(out)


def missing_fields(payload: Mapping) -> tuple[str, ...]:
    """Limit-monitored fields absent or non-finite in this payload."""
    out = []
    for lim in HARD_LIMITS:
        v = payload.get(lim.field)
        if v is None or not math.isfinite(float(v)):
            out.append(lim.field)
    return tuple(out)

def _self_test() -> None:
    fails = []
    print("configured hard limits:")
    for l in HARD_LIMITS:
        print(f"  {l.field:<20} {l.kind:<4} {l.value:>7.1f} {l.unit}")

    ok = {"oil_pressure_bar": 3.2, "coolant_temp_C": 85.0, "EGT_mean_C": 520.0}

    print("\nCASE 1  nominal -> no breach")
    b = check_limits(ok)
    print(f"  breaches={len(b)}")
    if b:
        fails.append("nominal payload breached")

    print("\nCASE 2  each limit individually")
    for fld, val in (("oil_pressure_bar", 0.7), ("coolant_temp_C", 131.0),
                     ("EGT_mean_C", 980.0)):
        b = check_limits(dict(ok, **{fld: val}))
        print(f"  {fld:<20}={val:<7} breaches={len(b)}")
        for x in b:
            print(f"      {x.describe()}")
        if len(b) != 1:
            fails.append(f"{fld} should yield exactly 1 breach")

    print("\nCASE 3  boundary values (limit itself is NOT a breach)")
    for fld, val, exp in (("oil_pressure_bar", 1.0, 0),
                          ("oil_pressure_bar", 0.999, 1),
                          ("coolant_temp_C", 120.0, 0),
                          ("coolant_temp_C", 120.001, 1)):
        n = len(check_limits(dict(ok, **{fld: val})))
        print(f"  {fld:<20}={val:<9} breaches={n} expected={exp}")
        if n != exp:
            fails.append(f"boundary {fld}={val}")

    print("\nCASE 4  multiple simultaneous breaches")
    b = check_limits({"oil_pressure_bar": 0.4, "coolant_temp_C": 140.0,
                      "EGT_mean_C": 1010.0})
    print(f"  breaches={len(b)}")
    if len(b) != 3:
        fails.append("expected 3 simultaneous breaches")

    print("\nCASE 5  missing / non-finite are flagged, not silently safe")
    part = {"oil_pressure_bar": 3.2}
    print(f"  missing_fields={missing_fields(part)}")
    print(f"  breaches={len(check_limits(part))}")
    if len(missing_fields(part)) != 2:
        fails.append("missing_fields should report 2")
    nan = dict(ok, EGT_mean_C=float('nan'))
    if "EGT_mean_C" not in missing_fields(nan):
        fails.append("NaN not reported as missing")
    print(f"  with NaN EGT -> missing_fields={missing_fields(nan)}")

    print("\nCASE 6  mild degradation is NOT caught (documented gap)")
    b = check_limits(dict(ok, oil_pressure_bar=2.16))
    print(f"  oil_pressure_bar=2.16 (below baseline min 2.198) breaches={len(b)}")
    print("  -> by design: hard limits are catastrophic thresholds only")

    if fails:
        print("\nSAFETY LIMITS SELF-CHECK FAILED")
        for f in fails:
            print(f"  - {f}")
        raise SystemExit(1)
    print("\nSAFETY LIMITS SELF-CHECK OK")


if __name__ == "__main__":
    _self_test()