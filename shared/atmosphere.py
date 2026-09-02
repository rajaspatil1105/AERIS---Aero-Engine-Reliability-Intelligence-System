"""
AERIS Phase 1 -- atmosphere and density-altitude solver.

WHY THIS MODULE EXISTS
----------------------
The trained models see only two environmental inputs: altitude_ft and oat_c.
The schema also carries ambient_pressure_kpa, humidity_pct, air_density_kgm3
and airspeed_ms, but NO MODEL WAS TRAINED ON THEM. Sweeping humidity in a
stress test would therefore move nothing, which would be a lie by omission.

Density altitude is the physically defensible bridge. Hot air and humid air
are both less dense, and reduced density is what the engine actually feels.
So we compute true density from pressure, temperature and humidity, convert
it to the ISA altitude having that same density, and hand that EFFECTIVE
altitude to the twin. Humidity then influences the prediction through real
physics rather than through an untrained channel.

HONESTY NOTES
-------------
* Valid in the troposphere only (<= 11000 m). Above that we refuse.
* TRAINING_ALT_FT / TRAINING_OAT_C are UNVERIFIED placeholders. They must be
  replaced with the true min/max of the Cantera training set. Until then
  in_training_envelope is advisory, and says so via envelope_verified=False.
* If the source supplies air_density_kgm3 we do not trust it blindly; we
  compute density ourselves and warn on disagreement.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

ATMOSPHERE_VERSION = "0.1.0"

# ---- exact constants -------------------------------------------------- #
FT_PER_M = 3.280839895013123      # identical to adapter.py, deliberately
R_DRY = 287.058                   # J/(kg K), specific gas constant, dry air
R_VAPOUR = 461.495                # J/(kg K), water vapour
T0_K = 288.15                     # ISA sea-level temperature
P0_PA = 101325.0                  # ISA sea-level pressure
RHO0 = 1.225                      # ISA sea-level density, kg/m^3
LAPSE_K_PER_M = 0.0065            # ISA tropospheric lapse rate
TROPOPAUSE_M = 11000.0
KELVIN = 273.15

# ISA exponents, derived from g/(R*L)
_P_EXP = 5.25588                  # pressure
_RHO_EXP = 4.25588                # density  ( = _P_EXP - 1 )
_H_COEF = 2.25577e-5              # = L / T0

# ---- training envelope, measured from the Cantera master dataset ------ #
# Source: models/training_envelope.json
#   master_dataset.csv  3,121,218 rows, 100% calibrated
#   fingerprint b39ae5203ea2905c...
# altitude_ft in training is GEOMETRIC (verified: max deviation from
# altitude_m * FT_PER_M was 1.78e-03 ft).
#
# CAUTION -- these are MARGINAL bounds. Training tied ambient temperature to
# altitude near ISA (median 6056 ft at 8.5 C), so the models saw a
# correlated ribbon, not a filled rectangle. A point inside both bounds may
# still be an unseen COMBINATION. joint_envelope_checked is therefore False.
TRAINING_ALT_FT: Tuple[float, float] = (0.0, 21881.3)
TRAINING_OAT_C: Tuple[float, float] = (-28.325, 34.351)
CORE_ALT_FT: Tuple[float, float] = (2.1, 20346.4)    # p1..p99
CORE_OAT_C: Tuple[float, float] = (-25.22, 29.77)    # p1..p99
ENVELOPE_VERIFIED = True
JOINT_ENVELOPE_CHECKED = False
ENVELOPE_SOURCE = "master_dataset.csv"
ENVELOPE_FINGERPRINT = "b39ae5203ea2905c365186dd1dc7d0bb07d5bb469f9277635a1dbe9a72e6a120"
ENVELOPE_ROWS = 3121218

# The dataset floor is exactly 0 ft: no sub-sea-level altitude was ever
# trained. Cold days produce NEGATIVE density altitude, so those scenarios
# are extrapolation by construction, not merely near the edge.
TRAINING_ALT_FLOOR_IS_SEA_LEVEL = True


class AtmosphereError(ValueError):
    """Refusal: input outside the region where this solver is valid."""


# ==================================================================== #
# Block A -- physics
# ==================================================================== #

def saturation_vapour_pressure_pa(t_c: float) -> float:
    """Buck (1996) over liquid water. Pa. Accurate to ~0.05% from -30..100 C."""
    if t_c < -80.0 or t_c > 120.0:
        raise AtmosphereError(f"temperature {t_c} C outside Buck validity")
    hpa = 6.1121 * math.exp((18.678 - t_c / 234.5) * t_c / (257.14 + t_c))
    return hpa * 100.0


def humid_air_density(p_pa: float, t_c: float, rh_pct: float) -> float:
    """Density of moist air, kg/m^3, by Dalton partial pressures.

    Moist air is LESS dense than dry air at the same pressure, because a
    water molecule (18 g/mol) is lighter than the N2/O2 it displaces.
    """
    if p_pa <= 0.0:
        raise AtmosphereError(f"ambient pressure {p_pa} Pa must be positive")
    rh = min(max(rh_pct, 0.0), 100.0)
    t_k = t_c + KELVIN
    if t_k <= 0.0:
        raise AtmosphereError(f"temperature {t_c} C is below absolute zero")
    p_v = (rh / 100.0) * saturation_vapour_pressure_pa(t_c)
    p_v = min(p_v, p_pa)              # cannot exceed total pressure
    p_d = p_pa - p_v
    return p_d / (R_DRY * t_k) + p_v / (R_VAPOUR * t_k)


def isa_temperature_c(h_m: float) -> float:
    if h_m > TROPOPAUSE_M:
        raise AtmosphereError(f"{h_m} m is above the tropopause")
    return (T0_K - LAPSE_K_PER_M * h_m) - KELVIN


def isa_pressure_pa(h_m: float) -> float:
    if h_m > TROPOPAUSE_M:
        raise AtmosphereError(f"{h_m} m is above the tropopause")
    return P0_PA * (1.0 - _H_COEF * h_m) ** _P_EXP


def isa_density(h_m: float) -> float:
    return RHO0 * (1.0 - _H_COEF * h_m) ** _RHO_EXP


def pressure_altitude_m(p_pa: float) -> float:
    """The ISA altitude at which ambient pressure equals p_pa."""
    if p_pa <= 0.0:
        raise AtmosphereError(f"ambient pressure {p_pa} Pa must be positive")
    return (1.0 - (p_pa / P0_PA) ** (1.0 / _P_EXP)) / _H_COEF


def density_altitude_m(rho: float) -> float:
    """The ISA altitude at which density equals rho. This is the key result."""
    if rho <= 0.0:
        raise AtmosphereError(f"density {rho} kg/m^3 must be positive")
    return (1.0 - (rho / RHO0) ** (1.0 / _RHO_EXP)) / _H_COEF


# ==================================================================== #
# Result
# ==================================================================== #

@dataclass
class Atmosphere:
    """Solved atmospheric state. Feed effective_altitude_ft to the twin."""

    geometric_altitude_m: float
    oat_c: float
    ambient_pressure_pa: float
    humidity_pct: float

    density_kgm3: float = 0.0
    dry_density_kgm3: float = 0.0
    vapour_pressure_pa: float = 0.0

    pressure_altitude_m: float = 0.0
    density_altitude_m: float = 0.0
    isa_temperature_c: float = 0.0
    isa_deviation_c: float = 0.0

    humidity_penalty_ft: float = 0.0
    in_training_envelope: bool = True
    envelope_verified: bool = ENVELOPE_VERIFIED
    warnings: List[str] = field(default_factory=list)

    @property
    def geometric_altitude_ft(self) -> float:
        return self.geometric_altitude_m * FT_PER_M

    @property
    def pressure_altitude_ft(self) -> float:
        return self.pressure_altitude_m * FT_PER_M

    @property
    def density_altitude_ft(self) -> float:
        return self.density_altitude_m * FT_PER_M

    @property
    def effective_altitude_ft(self) -> float:
        """What the twin should be told. Density altitude, in feet."""
        return self.density_altitude_ft

    def summary(self) -> Dict[str, Any]:
        return {
            "atmosphere_version": ATMOSPHERE_VERSION,
            "geometric_altitude_ft": round(self.geometric_altitude_ft, 1),
            "pressure_altitude_ft": round(self.pressure_altitude_ft, 1),
            "density_altitude_ft": round(self.density_altitude_ft, 1),
            "effective_altitude_ft": round(self.effective_altitude_ft, 1),
            "oat_c": round(self.oat_c, 2),
            "isa_temperature_c": round(self.isa_temperature_c, 2),
            "isa_deviation_c": round(self.isa_deviation_c, 2),
            "density_kgm3": round(self.density_kgm3, 5),
            "humidity_pct": round(self.humidity_pct, 1),
            "humidity_penalty_ft": round(self.humidity_penalty_ft, 1),
            "in_training_envelope": self.in_training_envelope,
            "envelope_verified": self.envelope_verified,
            "warnings": list(self.warnings),
        }


def solve(
    altitude_m: float = 0.0,
    oat_c: Optional[float] = None,
    ambient_pressure_kpa: Optional[float] = None,
    humidity_pct: float = 0.0,
    measured_density_kgm3: Optional[float] = None,
) -> Atmosphere:
    """Solve the atmospheric state.

    Any of oat_c / ambient_pressure_kpa left as None default to the ISA
    value for the given geometric altitude, so a bare altitude gives a
    standard day.
    """
    if altitude_m > TROPOPAUSE_M:
        raise AtmosphereError(
            f"altitude {altitude_m} m exceeds tropopause {TROPOPAUSE_M} m")
    if altitude_m < -500.0:
        raise AtmosphereError(f"altitude {altitude_m} m is implausible")

    p_pa = isa_pressure_pa(altitude_m) if ambient_pressure_kpa is None \
        else float(ambient_pressure_kpa) * 1000.0
    t_c = isa_temperature_c(altitude_m) if oat_c is None else float(oat_c)

    warnings: List[str] = []
    rho = humid_air_density(p_pa, t_c, humidity_pct)
    rho_dry = humid_air_density(p_pa, t_c, 0.0)

    pa_m = pressure_altitude_m(p_pa)
    da_m = density_altitude_m(rho)
    da_dry_m = density_altitude_m(rho_dry)
    isa_t = isa_temperature_c(max(min(pa_m, TROPOPAUSE_M), -500.0))

    if measured_density_kgm3 is not None:
        drift = abs(measured_density_kgm3 - rho)
        if drift > 0.02:
            warnings.append(
                f"source air_density_kgm3={measured_density_kgm3:.4f} "
                f"disagrees with computed {rho:.4f} (drift {drift:.4f}); "
                f"computed value used")

    da_ft = da_m * FT_PER_M
    in_env = (TRAINING_ALT_FT[0] <= da_ft <= TRAINING_ALT_FT[1]
              and TRAINING_OAT_C[0] <= t_c <= TRAINING_OAT_C[1])
    if not in_env:
        warnings.append(
            f"density altitude {da_ft:.0f} ft / OAT {t_c:.1f} C is outside the "
            f"declared training envelope; model output is extrapolation")

    return Atmosphere(
        geometric_altitude_m=altitude_m,
        oat_c=t_c,
        ambient_pressure_pa=p_pa,
        humidity_pct=humidity_pct,
        density_kgm3=rho,
        dry_density_kgm3=rho_dry,
        vapour_pressure_pa=(humidity_pct / 100.0)
        * saturation_vapour_pressure_pa(t_c),
        pressure_altitude_m=pa_m,
        density_altitude_m=da_m,
        isa_temperature_c=isa_t,
        isa_deviation_c=t_c - isa_t,
        humidity_penalty_ft=(da_m - da_dry_m) * FT_PER_M,
        in_training_envelope=in_env,
        warnings=warnings,
    )


def from_payload(payload: Any) -> Atmosphere:
    """Solve from a shared.schema.TelemetryPayload."""
    return solve(
        altitude_m=getattr(payload, "altitude_m", 0.0),
        oat_c=getattr(payload, "oat_c", None),
        ambient_pressure_kpa=getattr(payload, "ambient_pressure_kpa", None),
        humidity_pct=getattr(payload, "humidity_pct", 0.0) or 0.0,
        measured_density_kgm3=getattr(payload, "air_density_kgm3", None),
    )


# ---- named stress scenarios ------------------------------------------- #
SCENARIOS: Dict[str, Dict[str, float]] = {
    "standard_day":    {"altitude_m": 0.0,    "humidity_pct": 0.0},
    "hot_and_high":    {"altitude_m": 2438.4, "oat_c": 38.0, "humidity_pct": 20.0},
    "humid_tropical":  {"altitude_m": 0.0,    "oat_c": 34.0, "humidity_pct": 95.0},
    "cold_start":      {"altitude_m": 0.0,    "oat_c": -25.0, "humidity_pct": 60.0},
    "desert_noon":     {"altitude_m": 500.0,  "oat_c": 48.0, "humidity_pct": 5.0},
    "cruise_nominal":  {"altitude_m": 1828.8, "oat_c": 5.0,  "humidity_pct": 30.0},
}


def scenario(name: str) -> Atmosphere:
    if name not in SCENARIOS:
        raise AtmosphereError(f"unknown scenario {name!r}; "
                              f"have {sorted(SCENARIOS)}")
    return solve(**SCENARIOS[name])


def atmosphere_caveats() -> List[Dict[str, Any]]:
    return [
        {
            "id": "training_envelope",
            "verified": ENVELOPE_VERIFIED,
            "value": {"alt_ft": TRAINING_ALT_FT, "oat_c": TRAINING_OAT_C},
            "note": "placeholder bounds; set from the Cantera training set",
        },
        {
            "id": "density_altitude_substitution",
            "verified": False,
            "value": "density_altitude -> altitude_ft",
            "note": ("training altitude_ft is GEOMETRIC; the stress simulator "
                     "feeds DENSITY altitude into that same feature. Physically "
                     "defensible but a semantic substitution, unvalidated "
                     "against the Cantera runs"),
        },
        {
            "id": "joint_envelope_unchecked",
            "verified": False,
            "value": {"alt_ft": TRAINING_ALT_FT, "oat_c": TRAINING_OAT_C},
            "note": ("marginal bounds only; training correlated OAT with "
                     "altitude near ISA, so in-bounds combinations may still "
                     "be unseen"),
        },
        {
            "id": "training_data_fidelity",
            "verified": False,
            "value": "~70%",
            "note": ("dataset generated from limited public Rotax 915 iS data; "
                     "absolute values are indicative, not validated"),
        },
        {
            "id": "humidity_via_density_altitude",
            "verified": True,
            "value": "indirect",
            "note": ("no model was trained on humidity; it acts only by "
                     "reducing air density and raising effective altitude"),
        },
    ]


# ==================================================================== #
# Block B -- self-check
# ==================================================================== #

def _self_test() -> None:
    fails: List[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            fails.append(msg)

    def near(a: float, b: float, tol: float) -> bool:
        return abs(a - b) <= tol

    print(f"atmosphere v{ATMOSPHERE_VERSION}")

    print("\nCASE 0  Buck saturation vapour pressure against known values")
    p20 = saturation_vapour_pressure_pa(20.0)
    p100 = saturation_vapour_pressure_pa(100.0)
    print(f"  20 C  -> {p20:.1f} Pa (table 2339)")
    print(f"  100 C -> {p100:.0f} Pa (must equal 1 atm 101325)")
    check(near(p20, 2339.0, 10.0), f"Psat(20C)={p20:.1f}, expected ~2339")
    check(near(p100, 101325.0, 300.0), f"Psat(100C)={p100:.0f}, expected ~101325")

    print("\nCASE 1  ISA at sea level")
    print(f"  T={isa_temperature_c(0.0):.2f} C  P={isa_pressure_pa(0.0):.0f} Pa  "
          f"rho={isa_density(0.0):.4f}")
    check(near(isa_temperature_c(0.0), 15.0, 0.01), "ISA SL temp wrong")
    check(near(isa_pressure_pa(0.0), 101325.0, 1.0), "ISA SL pressure wrong")
    check(near(isa_density(0.0), 1.225, 0.001), "ISA SL density wrong")

    print("\nCASE 2  ISA at the tropopause")
    t11 = isa_temperature_c(11000.0)
    print(f"  11000 m -> {t11:.2f} C (known -56.5)")
    check(near(t11, -56.5, 0.1), f"tropopause temp {t11:.2f}, expected -56.5")

    print("\nCASE 3  pressure-altitude round trip")
    worst = 0.0
    for h in (0.0, 500.0, 1828.8, 3048.0, 6000.0, 10000.0):
        err = abs(pressure_altitude_m(isa_pressure_pa(h)) - h)
        worst = max(worst, err)
    print(f"  worst round-trip error: {worst:.3e} m")
    check(worst < 1e-6, f"round trip error {worst:.3e} m too large")

    print("\nCASE 4  on a standard day, density alt == pressure alt")
    a = solve(altitude_m=1828.8, humidity_pct=0.0)
    print(f"  PA={a.pressure_altitude_ft:.1f} ft  DA={a.density_altitude_ft:.1f} ft"
          f"  ISA dev={a.isa_deviation_c:+.2f} C")
    check(near(a.density_altitude_ft, a.pressure_altitude_ft, 1.0),
          "DA != PA on a standard day")
    check(near(a.isa_deviation_c, 0.0, 0.01), "ISA deviation nonzero on std day")

    print("\nCASE 5  hot day at sea level raises density altitude")
    hot = solve(altitude_m=0.0, oat_c=35.0, humidity_pct=0.0)
    rule = 118.8 * hot.isa_deviation_c          # airman's rule of thumb, ft/C
    print(f"  35 C -> DA={hot.density_altitude_ft:.0f} ft  "
          f"ISA dev={hot.isa_deviation_c:+.1f} C  rule-of-thumb {rule:.0f} ft")
    check(hot.density_altitude_ft > 1800.0, "hot day did not raise DA")
    check(abs(hot.density_altitude_ft - rule) < 400.0,
          f"DA {hot.density_altitude_ft:.0f} ft far from rule {rule:.0f} ft")

    print("\nCASE 6  cold day lowers density altitude below sea level")
    cold = solve(altitude_m=0.0, oat_c=-20.0, humidity_pct=0.0)
    print(f"  -20 C -> DA={cold.density_altitude_ft:.0f} ft "
          f"(denser than ISA, so negative)")
    check(cold.density_altitude_ft < -3000.0, "cold day did not lower DA")

    print("\nCASE 7  humidity reduces density and raises DA")
    dry = solve(altitude_m=0.0, oat_c=30.0, humidity_pct=0.0)
    wet = solve(altitude_m=0.0, oat_c=30.0, humidity_pct=100.0)
    dd = wet.density_altitude_ft - dry.density_altitude_ft
    print(f"  30 C dry  rho={dry.density_kgm3:.5f} DA={dry.density_altitude_ft:.0f} ft")
    print(f"  30 C 100% rho={wet.density_kgm3:.5f} DA={wet.density_altitude_ft:.0f} ft")
    print(f"  humidity penalty: {dd:+.0f} ft (reported {wet.humidity_penalty_ft:+.0f})")
    check(wet.density_kgm3 < dry.density_kgm3, "moist air came out denser")
    check(200.0 < dd < 1200.0, f"humidity penalty {dd:.0f} ft implausible")
    check(near(dd, wet.humidity_penalty_ft, 1.0), "humidity_penalty_ft inconsistent")

    print("\nCASE 8  monotonicity")
    das_t = [solve(oat_c=t).density_altitude_ft for t in (-10, 0, 15, 30, 45)]
    das_h = [solve(oat_c=30.0, humidity_pct=h).density_altitude_ft
             for h in (0, 25, 50, 75, 100)]
    das_a = [solve(altitude_m=h).density_altitude_ft
             for h in (0, 1000, 2000, 3000)]
    print(f"  DA vs OAT      : {[round(x) for x in das_t]}")
    print(f"  DA vs humidity : {[round(x) for x in das_h]}")
    print(f"  DA vs altitude : {[round(x) for x in das_a]}")
    for label, seq in (("OAT", das_t), ("humidity", das_h), ("altitude", das_a)):
        check(all(b > a for a, b in zip(seq, seq[1:])),
              f"DA not monotonic in {label}")

    print("\nCASE 9  refusals")
    for label, fn in (
        ("above tropopause", lambda: solve(altitude_m=12000.0)),
        ("negative pressure", lambda: solve(ambient_pressure_kpa=-1.0)),
        ("absurd altitude", lambda: solve(altitude_m=-9999.0)),
        ("unknown scenario", lambda: scenario("moon")),
    ):
        try:
            fn()
            check(False, f"{label} was accepted")
            print(f"  {label:18s} -> ACCEPTED (wrong)")
        except AtmosphereError:
            print(f"  {label:18s} -> refused")

    print("\nCASE 10  solve from a TelemetryPayload")
    try:
        from shared.schema import TelemetryPayload
        p = TelemetryPayload()
        b = from_payload(p)
        print(f"  schema defaults -> DA={b.density_altitude_ft:.1f} ft "
              f"OAT={b.oat_c} C P={b.ambient_pressure_pa:.0f} Pa")
        check(near(b.density_altitude_ft, 0.0, 30.0),
              f"schema defaults should be ~ISA SL, got {b.density_altitude_ft:.1f} ft")
        p2 = TelemetryPayload(altitude_m=1500.0, oat_c=32.0, humidity_pct=80.0,
                              ambient_pressure_kpa=84.6, air_density_kgm3=1.225)
        b2 = from_payload(p2)
        print(f"  hot/humid 1500 m -> DA={b2.density_altitude_ft:.0f} ft "
              f"warnings={len(b2.warnings)}")
        check(b2.density_altitude_ft > 1500.0 * FT_PER_M,
              "hot humid DA should exceed geometric altitude")
        check(any("air_density" in w for w in b2.warnings),
              "stale source density was not cross-checked")
    except ImportError as exc:
        check(False, f"schema import failed: {exc}")

    print("\nCASE 11  named scenarios")
    for name in sorted(SCENARIOS):
        s = scenario(name)
        flag = "" if s.in_training_envelope else "  <- OUTSIDE envelope"
        print(f"  {name:15s} DA={s.density_altitude_ft:8.0f} ft  "
              f"ISAdev={s.isa_deviation_c:+6.1f} C  "
              f"rho={s.density_kgm3:.4f}{flag}")
        check(math.isfinite(s.density_altitude_ft), f"{name} gave non-finite DA")

    print("\nCASE 12  declared caveats")
    for c in atmosphere_caveats():
        print(f"  {c['id']:34s} verified={c['verified']}")
    check(ENVELOPE_VERIFIED, "envelope should now be verified")
    check(any(c["id"] == "density_altitude_substitution" and not c["verified"]
              for c in atmosphere_caveats()),
          "density-altitude substitution must stay UNVERIFIED")

    if fails:
        print("\nATMOSPHERE SELF-CHECK FAILED:")
        for m in fails:
            print(f"  - {m}")
        raise SystemExit(1)
    print("\nATMOSPHERE SELF-CHECK OK")
    print("  density altitude is the only honest path from humidity to the twin")


if __name__ == "__main__":
    _self_test()
