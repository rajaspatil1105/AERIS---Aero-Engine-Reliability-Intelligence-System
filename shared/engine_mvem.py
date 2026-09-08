"""
AERIS -- Mean Value Engine Model for the Rotax 915 iS.

WHY THIS MODULE EXISTS
----------------------
node2_twin_core/physics_deck.py is FIVE RANDOM FORESTS trained on healthy rows
of master_dataset.csv. It predicts nine channels and cannot produce a tenth.
Per-cylinder EGT, CHT, manifold pressure, vibration and bus voltage are all
declared in shared/schema.py and all default to 0.0, which is why six tiles in
the UI read "--". No amount of retraining fixes that: a regressor cannot invent
a channel its training set never contained.

This module is the missing generator. Given throttle, altitude, ambient
temperature, humidity and airspeed it computes engine state from mass and
energy balance -- air in, fuel in, work out, heat out -- and emits every channel
the schema declares. It contains NO machine learning and imports nothing from
node2. The arrow runs one way: physics -> sensors -> bridge -> models -> UI.

CALIBRATION PROVENANCE
----------------------
[A] BRP-Rotax, Operators Manual, Rotax 915 i A Series, OM-915 i A, part no.
    898851, Rev. 1, June 2019, section 2.1 -- all operating limits.
[B] Nugroho R.C., Permana A.D., Fitrianto, Wahidin A., Mukti S., Hayoto V.,
    Chairunnisa, "Performance Simulation of Rotax 915iS PUNA MALE Engine Using
    AVL Boost", National Research and Innovation Agency (BRIN), Indonesia.
    Table 2 (plenum pressure/temperature vs rpm, WOT, sea level, 30 C),
    Table 3 (bore/stroke/displacement/compression ratio),
    Table 4 (manufacturer torque, power, fuel flow, BSFC at five speeds).
[C] Grabowski L., Siadkowska K., Skiba K., "Simulation Research of Aircraft
    Piston Engine Rotax 912", MATEC Web of Conferences 252, 05007 (2019) --
    FMEP-versus-speed shape, cited by [B] for the same engine family.
[D] flyrotax.com, 915 iS A / iSc A product data -- 141 hp take-off,
    135 hp continuous at 5500 rpm, bore 84 mm, 1352 cm3.

WHAT IS CALIBRATED AND WHAT IS NOT
----------------------------------
CALIBRATED, and reproduced by construction at the five table points:
  volumetric efficiency, indicated efficiency, plenum pressure and temperature,
  brake power, torque, fuel flow.
DERIVED from published data, verified self-consistent by CASE 0:
  fuel density 0.7503 kg/L (MOGAS), intercooler effectiveness 0.90,
  compressor pressure-ratio ceiling from the published critical altitude.
JUDGEMENT, physically shaped but not fitted to any Rotax measurement:
  heat split fractions, exhaust port conductance, radiator and oil-cooler UA,
  thermostat set points, oil pressure curve, per-cylinder EGT lambda
  sensitivity, vibration amplitudes, bus voltage droop, gearbox ratio.
NOT VALIDATED AT ALL:
  everything off the WOT line. [B] simulated wide-open throttle at sea level
  only -- five points on one curve. Part throttle and altitude come from the
  turbo and throttle models here, which no source constrains. [B]'s own
  conclusion 3 admits the same gap.

THE CIRCULARITY TRAP, AND THE ESCAPE
------------------------------------
If the baseline deck is refitted on this module's output, the deck learns this
module's quirks and residuals collapse to zero on everything this module does
wrong together. The reported accuracy then measures self-agreement and predicts
nothing about a real engine. mistuned() returns a deliberately wrong copy --
volumetric efficiency -12%, heat transfer -15%, FMEP +10%, intercooler -0.08 --
for generating a holdout the models never saw. Report BOTH numbers or neither.

FIXED IN v0.2.0, AND WHY EACH MATTERED
--------------------------------------
1. Mixture correction was applied relative to lambda 1.0, but the scheduled
   richness is already inside the calibrated indicated efficiency. Take-off
   lambda 0.857 was therefore charged twice, costing 5.8% of rated power.
   Correction is now relative to the SCHEDULED lambda and is exactly 1.0 at
   every calibration point, which is what makes CASE 2 a real round-trip.
2. Boost was held at constant PRESSURE with altitude. A turbo aero engine holds
   constant air MASS -- that is what rated power to a critical altitude means.
   Cold thin air needs less pressure for the same mass, so constant pressure
   gained 9% of power climbing to 15000 ft, which is nonsense.
3. Pumping loss did not exist. At part throttle the engine works against a
   throttled intake and that work was never charged.
4. Idle EGT read 1063 C. Two causes: the throttle floor put manifold pressure
   at the published MINIMUM (a shut plate at altitude, not idle at sea level),
   and a fixed exhaust heat fraction divided by a tiny mass flow ignores the
   port heat loss that dominates at light load.
5. The airspeed floor treated a static run-up as 10 m/s of cooling air. The
   propeller is a fan; the radiator sees far more than that.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from shared import atmosphere as atm

ENGINE_MVEM_VERSION = "0.2.0"

# ==================================================================== #
# Block A -- geometry, limits, and tunables
# ==================================================================== #

# ---- geometry, source [B] Table 3 and [D] --------------------------- #
BORE_M = 0.084
STROKE_M = 0.061
N_CYLINDERS = 4
DISPLACEMENT_L = 1.352
DISPLACEMENT_M3 = DISPLACEMENT_L / 1000.0
COMPRESSION_RATIO = 8.2
REVS_PER_CYCLE = 2.0                    # four-stroke
GEARBOX_RATIO = 2.4306                  # UNVERIFIED, confirm in [A] section 7

# ---- operating limits, source [A] section 2.1 ----------------------- #
RPM_IDLE_MIN = 1800.0
RPM_MAX_TAKEOFF = 5800.0                # 5 minute limit
RPM_MAX_CONTINUOUS = 5500.0
POWER_TAKEOFF_KW = 104.0
POWER_CONTINUOUS_KW = 99.0
CRITICAL_ALTITUDE_FT = 15000.0
CEILING_FT = 23000.0
MAP_MIN_BAR = 0.060                     # 60 hPa, shut throttle at altitude
MAP_MAX_BAR = 1.730                     # 1730 hPa
MANIFOLD_TEMP_MAX_C = 50.0
OIL_PRESS_MIN_LOW_RPM_BAR = 0.8         # below 3500 rpm
OIL_PRESS_MIN_HIGH_RPM_BAR = 2.0        # above 3500 rpm
OIL_PRESS_MAX_BAR = 5.0
OIL_TEMP_MIN_C = 50.0
OIL_TEMP_MAX_C = 130.0
COOLANT_TEMP_MAX_C = 120.0
COOLANT_TEMP_WARMUP_MAX_C = 90.0
EGT_MAX_C = 950.0
EGT_SPREAD_MAX_C = 200.0                # above 3 L/h fuel flow
FUEL_RAIL_BAR = (2.9, 3.1)
OAT_LIMIT_C = (-40.0, 50.0)
OIL_PRESS_RPM_BREAK = 3500.0

# ---- the demoted relation, source [A] section 2.1 ------------------- #
AIRFLOW_POWER_INTERCEPT_KW = -6.3264
AIRFLOW_POWER_SLOPE_KW_PER_G_MIN = 0.0169

# ---- fuel: [A] permits MOGAS and AVGAS, which differ in density ----- #
# Not interchangeable. [B] simulated gasoline and its own table implies
# 0.7503 kg/L, so the calibration is MOGAS. node1_ingestion/adapter.py uses
# 0.72, which is AVGAS -- a legitimate different choice, not a bug, but the
# two must never be mixed silently. CASE 13 checks which one the adapter means.
FUEL_DENSITY_MOGAS_KG_PER_L = 0.7503    # DERIVED from [B] Table 4, see CASE 0
FUEL_DENSITY_AVGAS_KG_PER_L = 0.72      # AVGAS 100LL nominal
FUEL_TYPE = "MOGAS"
FUEL_DENSITY_KG_PER_L = FUEL_DENSITY_MOGAS_KG_PER_L

FUEL_LHV_J_PER_KG = 43.5e6              # [B], gasoline lower calorific value
AFR_STOICH = 14.7                       # [B], simulation setting

# ---- heat split, JUDGEMENT ------------------------------------------ #
# Fractions of the NON-BRAKE remainder of fuel energy. The 915 iS is a hybrid:
# [A] describes AIR cooled cylinder barrels with LIQUID cooled heads, so only
# part of the rejected heat reaches the coolant. Chosen so the resulting total
# split is textbook-plausible for a turbocharged SI engine, take-off EGT lands
# near 800 C against the 950 C limit, and sea-level continuous power sits on
# the thermostat rather than above it.
HEAT_FRAC_EXHAUST = 0.45
HEAT_FRAC_COOLANT = 0.30
HEAT_FRAC_OIL = 0.09
HEAT_FRAC_MISC = 0.16                   # air-cooled barrels, radiation
CP_EXHAUST_J_PER_KG_K = 1150.0
EXHAUST_PORT_UA_W_PER_K = 6.0           # JUDGEMENT, sets the idle-to-WOT span

# ---- cooling, JUDGEMENT --------------------------------------------- #
UA_COOLANT_REF_W_PER_K = 1100.0
UA_OIL_REF_W_PER_K = 320.0
AIRSPEED_REF_MS = 50.0
AIRSPEED_MIN_MS = 25.0                  # prop wash at power, not freestream
UA_AIRSPEED_FLOOR_FRAC = 0.55           # the propeller is a fan
THERMOSTAT_COOLANT_C = 88.0
THERMOSTAT_OIL_C = 90.0
COOLANT_DELTA_T_C = 7.0                 # out minus in, matches adapter offset

# ---- compressor and intercooler ------------------------------------- #
COMPRESSOR_ISENTROPIC_EFF = 0.72        # JUDGEMENT, typical small automotive
GAMMA_AIR = 1.4
INTERCOOLER_EFFECTIVENESS = 0.90        # DERIVED from [B] Table 2, CASE 0

# ---- friction, shape from [C] --------------------------------------- #
FMEP_INTERCEPT_BAR = 0.45
FMEP_SLOPE_BAR_PER_RPM = 0.000155

# ---- throttle and rpm ----------------------------------------------- #
# Mirrors shared/throttle_dynamics.py rpm_for_throttle. Duplicated rather than
# imported because throttle_dynamics imports stress_sim which imports node2,
# and this module must stay free of any model dependency. CASE 8 asserts the
# two agree so they cannot drift apart silently.
RPM_IDLE = 1800.0
RPM_MAX = 5800.0
MAP_IDLE_BAR = 0.30                     # ABSOLUTE, leakage past a shut plate
THROTTLE_MAP_EXPONENT = 1.35

# ---- oil pressure, JUDGEMENT tuned to the [A] band ------------------ #
OIL_PRESS_INTERCEPT_BAR = 0.35
OIL_PRESS_SLOPE_BAR_PER_RPM = 0.00057
OIL_VISCOSITY_REF_TEMP_C = 90.0
OIL_VISCOSITY_EXPONENT = 0.25

# ---- vibration, UNVERIFIED ------------------------------------------ #
VIB_BASE_G = 0.35
VIB_SPAN_G = 1.10
VIB_CREST_FACTOR = 3.9

# ---- electrical ----------------------------------------------------- #
BUS_VOLTAGE_REGULATED_V = 14.2
BUS_VOLTAGE_BATTERY_V = 12.6
ALTERNATOR_CUTIN_RPM = 2200.0
ALTERNATOR_CAPACITY_A = 30.0            # UNVERIFIED, confirm in [A] section 7
ELECTRICAL_BASE_LOAD_A = 12.0

# ---- per-cylinder EGT sensitivity, JUDGEMENT ------------------------ #
# EGT peaks slightly lean of stoichiometric and falls away on both sides, so a
# large lean excursion can read the SAME EGT as a rich one. That is real engine
# behaviour and it is why EGT alone cannot diagnose mixture: CASE 9 shows a 28%
# trim cut producing a NARROWER spread than a 15% cut, while power and
# vibration both move monotonically.
EGT_LAMBDA_PEAK = 1.05
EGT_LAMBDA_WIDTH = 0.25
EGT_LAMBDA_GAIN = 0.30
EGT_BUILD_SCATTER_C: Tuple[float, ...] = (-7.5, -2.5, 2.5, 7.5)


class MvemError(ValueError):
    """Refusal: the requested operating point is outside this model's validity."""


# ==================================================================== #
# Block B -- calibration data, verbatim from [B]
# ==================================================================== #

CAL_RPM: Tuple[float, ...] = (3000.0, 4500.0, 5000.0, 5500.0, 5800.0)
CAL_PLENUM_BAR: Tuple[float, ...] = (0.807, 1.315, 1.367, 1.415, 1.511)
CAL_PLENUM_TEMP_C: Tuple[float, ...] = (32.4, 34.6, 34.5, 35.1, 34.9)
CAL_TORQUE_NM: Tuple[float, ...] = (104.5, 158.6, 167.8, 172.1, 171.9)
CAL_POWER_KW: Tuple[float, ...] = (32.8, 74.7, 87.8, 99.1, 104.4)
CAL_FUEL_LPH: Tuple[float, ...] = (10.0, 23.1, 27.8, 32.1, 33.8)
CAL_BSFC_G_PER_KWH: Tuple[float, ...] = (229.5, 231.7, 237.3, 242.8, 243.0)
CAL_AMBIENT_TEMP_C = 30.0               # [B] states typical Indonesian ops

# Air-fuel ratio schedule: rich at take-off for charge cooling, near
# stoichiometric at low power. JUDGEMENT -- no source publishes the 915 iS
# lambda map, and this choice is NOT separable from volumetric efficiency
# using public data. See caveat ve_afr_not_separable.
AFR_SCHEDULE_RPM: Tuple[float, ...] = CAL_RPM
AFR_SCHEDULE: Tuple[float, ...] = (14.7, 14.0, 13.4, 12.9, 12.6)


# ==================================================================== #
# Block C -- helpers
# ==================================================================== #

def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else (hi if x > hi else x)


def _interp(x: float, xs: Sequence[float], ys: Sequence[float]) -> float:
    """Linear interpolation, CLAMPED outside the calibration range.

    Clamping rather than extrapolating is deliberate: [B] gives five points
    between 3000 and 5800 rpm, and a linear extension of volumetric efficiency
    below 3000 runs past unity. Clamping is wrong in a bounded, declared way --
    see caveat calibration_range_clamped.
    """
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(len(xs) - 1):
        if xs[i] <= x <= xs[i + 1]:
            span = xs[i + 1] - xs[i]
            f = 0.0 if span == 0.0 else (x - xs[i]) / span
            return ys[i] + f * (ys[i + 1] - ys[i])
    return ys[-1]


def rpm_for_throttle(throttle_pct: float) -> float:
    """Throttle-to-rpm surrogate, identical to throttle_dynamics."""
    t = _clamp(float(throttle_pct), 0.0, 100.0)
    return RPM_IDLE + (RPM_MAX - RPM_IDLE) * t / 100.0


def fmep_bar(rpm: float) -> float:
    """Friction mean effective pressure. Shape from [C]."""
    return FMEP_INTERCEPT_BAR + FMEP_SLOPE_BAR_PER_RPM * max(0.0, rpm)


def friction_power_w(rpm: float, fmep_mult: float = 1.0) -> float:
    cycles_per_s = max(0.0, rpm) / REVS_PER_CYCLE / 60.0
    return fmep_bar(rpm) * fmep_mult * 1.0e5 * DISPLACEMENT_M3 * cycles_per_s


def pumping_power_w(rpm: float, map_bar: float, ambient_bar: float) -> float:
    """Work lost pumping against a throttled intake.

    Absent from v0.1.0, which is why part-throttle power was over-predicted.
    Zero whenever the engine is boosted above ambient, so it changes nothing at
    the top of the calibration and everything at the bottom.
    """
    dp = max(0.0, ambient_bar - map_bar) * 1.0e5
    cycles_per_s = max(0.0, rpm) / REVS_PER_CYCLE / 60.0
    return dp * DISPLACEMENT_M3 * cycles_per_s


def air_density(pressure_bar: float, temp_c: float) -> float:
    t_k = temp_c + atm.KELVIN
    if t_k <= 0.0:
        raise MvemError(f"temperature {temp_c} C is below absolute zero")
    return pressure_bar * 1.0e5 / (atm.R_DRY * t_k)


def ideal_air_flow_kgh(rpm: float, map_bar: float, charge_temp_c: float) -> float:
    """Air the cylinders would ingest at 100% volumetric efficiency."""
    rho = air_density(map_bar, charge_temp_c)
    cycles_per_min = max(0.0, rpm) / REVS_PER_CYCLE
    return rho * DISPLACEMENT_M3 * cycles_per_min * 60.0


def plenum_temperature_c(plen_bar: float, ambient_pressure_pa: float,
                         oat_c: float,
                         effectiveness: float = INTERCOOLER_EFFECTIVENESS) -> float:
    """Post-intercooler charge temperature.

    Isentropic compression at a fixed compressor efficiency, then an
    intercooler. Effectiveness 0.90 is DERIVED: it is the value reproducing
    [B] Table 2 plenum temperatures at 30 C ambient, and CASE 0 recovers
    0.86-0.91 from the four boosted points independently.
    """
    ambient_bar = max(ambient_pressure_pa / 1.0e5, 1.0e-4)
    pr = max(plen_bar / ambient_bar, 1.0)
    exponent = (GAMMA_AIR - 1.0) / (GAMMA_AIR * COMPRESSOR_ISENTROPIC_EFF)
    t_amb_k = oat_c + atm.KELVIN
    rise = t_amb_k * (pr ** exponent) - t_amb_k
    return oat_c + rise * (1.0 - _clamp(effectiveness, 0.0, 0.98))


# ==================================================================== #
# Block D -- calibration derived from [B] Table 4 at import time
# ==================================================================== #

def _cal_charge_temp_c(rpm: float) -> float:
    """Charge temperature THIS MODEL produces at the calibration condition.

    Deliberately not [B] Table 2's measured value. Using the model's own number
    makes the CASE 2 round-trip exact and pushes the small disagreement with
    Table 2 into volumetric efficiency, where CASE 0 already measures it.
    """
    plen = _interp(rpm, CAL_RPM, CAL_PLENUM_BAR)
    return plenum_temperature_c(plen, atm.P0_PA, CAL_AMBIENT_TEMP_C,
                                INTERCOOLER_EFFECTIVENESS)


def _solve_pr_max() -> Tuple[float, ...]:
    """Compressor pressure-ratio ceiling, DERIVED from the critical altitude.

    [A] and [D] both claim the turbo holds rated power to 15000 ft. Holding
    rated power means holding air MASS, so the pressure needed at 15000 ft ISA
    is computable, and the ratio it implies is the compressor's limit. No source
    publishes a compressor map; the critical altitude claim is the one number
    that pins this down. Charge temperature depends on the ratio and the ratio
    on the temperature, so iterate.

    Where the calibration sits BELOW sea-level ambient -- true at 3000 rpm,
    where flow losses dominate and there is no boost to hold -- the stored value
    is a LOSS ratio, not a compression ratio, and simply scales with ambient.
    Interpolating between a loss ratio and a compression ratio around 3500 rpm
    is a modelling artifact; it is harmless because regulation caps delivery
    well below the ceiling everywhere in that band.
    """
    a_crit = atm.solve(altitude_m=CRITICAL_ALTITUDE_FT / atm.FT_PER_M)
    p_crit_bar = a_crit.ambient_pressure_pa / 1.0e5
    p0_bar = atm.P0_PA / 1.0e5
    out: List[float] = []
    for rpm, map_cal in zip(CAL_RPM, CAL_PLENUM_BAR):
        if map_cal <= p0_bar:
            out.append(map_cal / p0_bar)
            continue
        t_ref_k = _cal_charge_temp_c(rpm) + atm.KELVIN
        pr = map_cal / p_crit_bar
        for _ in range(16):
            t_plen = plenum_temperature_c(p_crit_bar * pr,
                                          a_crit.ambient_pressure_pa,
                                          a_crit.oat_c,
                                          INTERCOOLER_EFFECTIVENESS)
            pr = (map_cal * (t_plen + atm.KELVIN) / t_ref_k) / p_crit_bar
        out.append(pr)
    return tuple(out)


CAL_PR_MAX = _solve_pr_max()


def _derive_calibration() -> Tuple[Tuple[float, ...], Tuple[float, ...],
                                   Tuple[float, ...]]:
    """Back out volumetric and indicated efficiency from [B] Table 4.

    Nothing here is hand-tuned. Air flow is published fuel flow times the
    declared AFR schedule; volumetric efficiency is that over ideal induction
    at the model's own charge condition; indicated power is published brake
    power plus friction plus pumping. Jagged curves would mean the published
    data or the friction model is wrong. CASE 1 asserts they are smooth.
    """
    ve: List[float] = []
    eta_i: List[float] = []
    rho_fuel: List[float] = []
    p0_bar = atm.P0_PA / 1.0e5

    for rpm, plen_bar, power_kw, lph, bsfc in zip(
            CAL_RPM, CAL_PLENUM_BAR, CAL_POWER_KW, CAL_FUEL_LPH,
            CAL_BSFC_G_PER_KWH):
        rho_fuel.append(bsfc * power_kw / 1000.0 / lph)

        m_fuel_kgh = lph * FUEL_DENSITY_KG_PER_L
        afr = _interp(rpm, AFR_SCHEDULE_RPM, AFR_SCHEDULE)
        m_air_kgh = m_fuel_kgh * afr

        ideal = ideal_air_flow_kgh(rpm, plen_bar, _cal_charge_temp_c(rpm))
        ve.append(m_air_kgh / ideal)

        p_ind_w = (power_kw * 1000.0
                   + friction_power_w(rpm)
                   + pumping_power_w(rpm, plen_bar, p0_bar))
        p_fuel_w = (m_fuel_kgh / 3600.0) * FUEL_LHV_J_PER_KG
        eta_i.append(p_ind_w / p_fuel_w)

    return tuple(ve), tuple(eta_i), tuple(rho_fuel)


CAL_VE, CAL_ETA_INDICATED, CAL_IMPLIED_FUEL_DENSITY = _derive_calibration()


def volumetric_efficiency(rpm: float, ve_mult: float = 1.0) -> float:
    return _interp(rpm, CAL_RPM, CAL_VE) * ve_mult


def scheduled_lambda(rpm: float) -> float:
    return _interp(rpm, AFR_SCHEDULE_RPM, AFR_SCHEDULE) / AFR_STOICH


def indicated_efficiency(rpm: float, lam: Optional[float] = None) -> float:
    """Indicated efficiency, corrected RELATIVE TO THE SCHEDULED lambda.

    v0.1.0 corrected relative to lambda 1.0 and so charged the scheduled
    take-off richness twice, costing 5.8% of rated power. The scheduled mixture
    is already inside the calibrated value; only DEPARTURE from it may be
    penalised. At every calibration point the correction is exactly 1.0.
    """
    base = _interp(rpm, CAL_RPM, CAL_ETA_INDICATED)
    if lam is None or not math.isfinite(lam):
        return base
    ratio = _clamp(lam / scheduled_lambda(rpm), 0.35, 2.5)
    corr = ratio ** 0.35 if ratio < 1.0 else 1.0
    return base * _clamp(corr, 0.55, 1.0)


def airflow_power_crosscheck(air_flow_kgh: float) -> float:
    """[A] section 2.1 relation. DEMOTED -- cross-check only, never a target."""
    g_per_min = air_flow_kgh * 1000.0 / 60.0
    return (AIRFLOW_POWER_INTERCEPT_KW
            + AIRFLOW_POWER_SLOPE_KW_PER_G_MIN * g_per_min)


# ==================================================================== #
# Block E -- fault and mistune state
# ==================================================================== #

@dataclass
class FaultState:
    """Multiplicative degradations applied INSIDE the physics.

    These are ENGINE faults, not sensor faults. A fouled injector changes the
    combustion and therefore every downstream channel coherently, which is what
    makes a residual meaningful. Sensor faults belong in the sensor model, where
    they must NOT move the rest of the engine -- that separation is what lets
    the validator tell a broken probe from a broken engine.
    """
    cylinder_fuel_trim: List[float] = field(
        default_factory=lambda: [1.0, 1.0, 1.0, 1.0])
    oil_pump_health: float = 1.0
    coolant_pump_health: float = 1.0
    intercooler_effectiveness: float = INTERCOOLER_EFFECTIVENESS
    wastegate_authority: float = 1.0
    bearing_wear: float = 0.0            # 0 healthy, 1 destroyed
    ve_mult: float = 1.0
    heat_mult: float = 1.0
    fmep_mult: float = 1.0
    alternator_health: float = 1.0
    label: str = "HEALTHY"

    def validate(self) -> None:
        if len(self.cylinder_fuel_trim) != N_CYLINDERS:
            raise MvemError(
                f"cylinder_fuel_trim needs {N_CYLINDERS} entries, "
                f"got {len(self.cylinder_fuel_trim)}")
        for i, t in enumerate(self.cylinder_fuel_trim):
            if not 0.0 < t <= 1.5:
                raise MvemError(f"cylinder {i + 1} trim {t} is implausible")
        for name in ("oil_pump_health", "coolant_pump_health",
                     "wastegate_authority", "alternator_health"):
            v = float(getattr(self, name))
            if not 0.0 <= v <= 1.5:
                raise MvemError(f"{name}={v} outside [0, 1.5]")
        if not 0.0 <= self.bearing_wear <= 1.0:
            raise MvemError(f"bearing_wear={self.bearing_wear} outside [0, 1]")


def mistuned() -> FaultState:
    """A deliberately WRONG engine, for the honest holdout.

    Not a fault -- a different engine. Volumetric efficiency down 12%, heat
    transfer down 15%, friction up 10%, intercooler down 0.08. Generate test
    sorties from this while the models keep the baseline trained on the nominal
    model, and the resulting accuracy measures fault detection instead of
    self-agreement. Report it NEXT TO the same-generator number, never instead.
    """
    return FaultState(ve_mult=0.88, heat_mult=0.85, fmep_mult=1.10,
                      intercooler_effectiveness=INTERCOOLER_EFFECTIVENESS - 0.08,
                      label="MISTUNED_HOLDOUT")


# ==================================================================== #
# Block F -- the gas path
# ==================================================================== #

def available_plenum_bar(rpm: float, ambient_pressure_pa: float,
                         wastegate_authority: float = 1.0) -> float:
    """The most the compressor can deliver here, before regulation."""
    pr_max = _interp(rpm, CAL_RPM, CAL_PR_MAX)
    ambient_bar = ambient_pressure_pa / 1.0e5
    ceiling = ambient_bar * pr_max
    if pr_max <= 1.0:
        return ceiling
    return ambient_bar + (ceiling - ambient_bar) * _clamp(
        wastegate_authority, 0.0, 1.5)


def regulated_plenum_bar(rpm: float, charge_temp_c: float,
                         available_bar: float) -> float:
    """Boost the ECU commands: whatever holds the CALIBRATED air mass.

    Density goes as P/T, so holding mass at a colder charge needs
    proportionally less pressure. This is why rated power is flat to the
    critical altitude instead of climbing, which was v0.1.0's CASE 5 failure.
    """
    map_cal = _interp(rpm, CAL_RPM, CAL_PLENUM_BAR)
    t_ref_k = _cal_charge_temp_c(rpm) + atm.KELVIN
    required = map_cal * (charge_temp_c + atm.KELVIN) / t_ref_k
    return min(required, available_bar)


def throttled_map_bar(plen_bar: float, throttle_pct: float,
                      ambient_bar: float) -> float:
    """Manifold pressure after the throttle plate.

    v0.1.0 used a FRACTION of plenum pressure, which put 10% throttle at
    0.060 bar -- the published MINIMUM, meaning a shut plate at altitude, not
    idle at sea level. Idle manifold pressure is an ABSOLUTE quantity set by
    leakage past a closed plate, so it is modelled that way and scaled with
    ambient. Wide open returns plenum pressure exactly.
    """
    t = _clamp(float(throttle_pct), 0.0, 100.0) / 100.0
    idle = MAP_IDLE_BAR * _clamp(ambient_bar / (atm.P0_PA / 1.0e5), 0.05, 1.2)
    idle = min(idle, plen_bar)
    span = max(0.0, plen_bar - idle)
    return _clamp(idle + span * (t ** THROTTLE_MAP_EXPONENT),
                  MAP_MIN_BAR, MAP_MAX_BAR)


def exhaust_gas_temp_c(q_exhaust_w: float, m_exhaust_kgs: float,
                       charge_temp_c: float, wall_temp_c: float) -> float:
    """EGT at the probe, WITH port heat loss.

    A fixed heat fraction divided by mass flow gives 1063 C at idle, because
    light load has little gas to carry the heat and the crude model ignores the
    port cooling that dominates there. The cooled-pipe exponential fixes both
    ends: at high flow the gas barely cools, at low flow it approaches wall
    temperature.
    """
    if m_exhaust_kgs <= 1.0e-9:
        return wall_temp_c
    mcp = m_exhaust_kgs * CP_EXHAUST_J_PER_KG_K
    t_pre = charge_temp_c + q_exhaust_w / mcp
    decay = math.exp(-EXHAUST_PORT_UA_W_PER_K / mcp)
    return wall_temp_c + (t_pre - wall_temp_c) * decay


def _egt_lambda_shape(lam: float) -> float:
    return math.exp(-(((lam - EGT_LAMBDA_PEAK) / EGT_LAMBDA_WIDTH) ** 2))


# ==================================================================== #
# Block G -- result
# ==================================================================== #

@dataclass
class EngineOutputs:
    """Full engine state. Feed to_schema_dict() to simulator_bridge."""

    # command and environment
    throttle_pct: float = 0.0
    rpm: float = 0.0
    prop_rpm: float = 0.0
    altitude_ft: float = 0.0
    oat_c: float = 0.0
    humidity_pct: float = 0.0
    airspeed_ms: float = 0.0
    ambient_pressure_bar: float = 0.0
    air_density_kgm3: float = 0.0

    # gas path
    manifold_pressure_bar: float = 0.0
    plenum_pressure_bar: float = 0.0
    plenum_available_bar: float = 0.0
    manifold_temp_c: float = 0.0
    volumetric_efficiency: float = 0.0
    air_flow_kgh: float = 0.0
    fuel_flow_kgh: float = 0.0
    fuel_flow_lph: float = 0.0
    lambda_actual: float = 0.0
    afr_actual: float = 0.0
    boost_limited: bool = False

    # work
    indicated_power_kw: float = 0.0
    friction_power_kw: float = 0.0
    pumping_power_kw: float = 0.0
    brake_power_kw: float = 0.0
    torque_nm: float = 0.0
    bsfc_g_per_kwh: float = 0.0
    brake_thermal_efficiency: float = 0.0

    # heat
    heat_exhaust_kw: float = 0.0
    heat_coolant_kw: float = 0.0
    heat_oil_kw: float = 0.0
    egt_mean_c: float = 0.0
    egt_c: List[float] = field(default_factory=lambda: [0.0] * N_CYLINDERS)
    egt_spread_c: float = 0.0
    cht_c: List[float] = field(default_factory=lambda: [0.0] * N_CYLINDERS)
    coolant_temp_out_c: float = 0.0
    coolant_temp_in_c: float = 0.0
    oil_temp_c: float = 0.0
    oil_pressure_bar: float = 0.0
    fuel_rail_bar: float = 0.0

    # mechanical and electrical
    vib_rms_g: float = 0.0
    vib_peak_g: float = 0.0
    vib_crest_factor: float = 0.0
    vib_f0_hz: float = 0.0
    vib_1x_g: float = 0.0
    vib_2x_g: float = 0.0
    vib_3x_g: float = 0.0
    vib_bearing_band_g: float = 0.0
    bus_voltage_v: float = 0.0
    alternator_current_a: float = 0.0

    # provenance
    fault_label: str = "HEALTHY"
    limit_breaches: List[str] = field(default_factory=list)
    provenance: str = "MVEM_SIMULATED"
    mvem_version: str = ENGINE_MVEM_VERSION
    fuel_type: str = FUEL_TYPE

    def to_schema_dict(self) -> Dict[str, Any]:
        """Field names exactly as shared/schema.py declares them.

        Units are SCHEMA units, not model units: kPa for pressures, L/h for
        fuel, metres for altitude. simulator_bridge does no arithmetic beyond
        the nine-channel projection it already performs.
        """
        d: Dict[str, Any] = {
            "rpm": self.rpm,
            "prop_rpm": self.prop_rpm,
            "throttle_pct": self.throttle_pct,
            "altitude_m": self.altitude_ft / atm.FT_PER_M,
            "oat_c": self.oat_c,
            "humidity_pct": self.humidity_pct,
            "airspeed_ms": self.airspeed_ms,
            "ambient_pressure_kpa": self.ambient_pressure_bar * 100.0,
            "air_density_kgm3": self.air_density_kgm3,
            "manifold_pressure_kpa": self.manifold_pressure_bar * 100.0,
            "fuel_flow_lph": self.fuel_flow_lph,
            "torque_nm": self.torque_nm,
            "power_kw": self.brake_power_kw,
            "oil_pressure_kpa": self.oil_pressure_bar * 100.0,
            "oil_temp_c": self.oil_temp_c,
            "coolant_temp_in_c": self.coolant_temp_in_c,
            "coolant_temp_out_c": self.coolant_temp_out_c,
            "egt_spread_c": self.egt_spread_c,
            "vib_rms_g": self.vib_rms_g,
            "vib_peak_g": self.vib_peak_g,
            "vib_crest_factor": self.vib_crest_factor,
            "vib_f0_hz": self.vib_f0_hz,
            "vib_1x_g": self.vib_1x_g,
            "vib_2x_g": self.vib_2x_g,
            "vib_3x_g": self.vib_3x_g,
            "vib_bearing_band_g": self.vib_bearing_band_g,
            "bus_voltage_v": self.bus_voltage_v,
            "alternator_current_a": self.alternator_current_a,
        }
        for i in range(N_CYLINDERS):
            d[f"egt_{i + 1}_c"] = self.egt_c[i]
            d[f"cht_{i + 1}_c"] = self.cht_c[i]
        return d

    def extras(self) -> Dict[str, Any]:
        """Real quantities with NO CONFIRMED schema field. Do not merge blind.

        v0.1.0 emitted these inside to_schema_dict and CASE 12 caught that the
        schema does not declare them. Find the true names with
        `Select-String -Path shared\\schema.py -Pattern 'manifold|rail|plenum'`
        and move them across once confirmed. Inventing field names silently is
        how a UI ends up displaying a channel nothing produces.
        """
        return {
            "manifold_temp_c": self.manifold_temp_c,
            "fuel_rail_pressure_bar": self.fuel_rail_bar,
            "plenum_pressure_bar": self.plenum_pressure_bar,
            "volumetric_efficiency": self.volumetric_efficiency,
            "lambda_actual": self.lambda_actual,
            "air_flow_kgh": self.air_flow_kgh,
        }

    def summary(self) -> Dict[str, Any]:
        return {
            "mvem_version": self.mvem_version,
            "rpm": round(self.rpm, 1),
            "throttle_pct": round(self.throttle_pct, 1),
            "altitude_ft": round(self.altitude_ft, 0),
            "map_bar": round(self.manifold_pressure_bar, 3),
            "power_kw": round(self.brake_power_kw, 2),
            "torque_nm": round(self.torque_nm, 1),
            "fuel_lph": round(self.fuel_flow_lph, 2),
            "bsfc": round(self.bsfc_g_per_kwh, 1),
            "egt_mean_c": round(self.egt_mean_c, 1),
            "egt_spread_c": round(self.egt_spread_c, 1),
            "coolant_c": round(self.coolant_temp_out_c, 1),
            "oil_c": round(self.oil_temp_c, 1),
            "oil_bar": round(self.oil_pressure_bar, 2),
            "vib_g": round(self.vib_rms_g, 2),
            "bus_v": round(self.bus_voltage_v, 2),
            "boost_limited": self.boost_limited,
            "fuel_type": self.fuel_type,
            "fault_label": self.fault_label,
            "limit_breaches": list(self.limit_breaches),
            "provenance": self.provenance,
        }


# ==================================================================== #
# Block H -- the solver
# ==================================================================== #

def solve(throttle_pct: float,
          altitude_ft: float = 0.0,
          oat_c: Optional[float] = None,
          humidity_pct: float = 0.0,
          airspeed_ms: float = AIRSPEED_REF_MS,
          rpm: Optional[float] = None,
          fault: Optional[FaultState] = None,
          electrical_load_a: float = ELECTRICAL_BASE_LOAD_A) -> EngineOutputs:
    """Steady-state engine solution at one operating point.

    Transient behaviour is NOT here. The lag model already exists in
    shared/throttle_dynamics.py with its own declared time constants, and
    duplicating it would give two sources of truth. This returns equilibrium;
    the caller lags it.
    """
    f = fault or FaultState()
    f.validate()

    if altitude_ft > CEILING_FT:
        raise MvemError(
            f"altitude {altitude_ft:.0f} ft exceeds the published operating "
            f"ceiling of {CEILING_FT:.0f} ft [A]")
    if altitude_ft < -1000.0:
        raise MvemError(f"altitude {altitude_ft:.0f} ft is implausible")

    a = atm.solve(altitude_m=altitude_ft / atm.FT_PER_M, oat_c=oat_c,
                  humidity_pct=humidity_pct)
    if not OAT_LIMIT_C[0] <= a.oat_c <= OAT_LIMIT_C[1]:
        raise MvemError(
            f"ambient {a.oat_c:.1f} C outside the certified range "
            f"{OAT_LIMIT_C} [A]")

    thr = _clamp(float(throttle_pct), 0.0, 100.0)
    n = rpm_for_throttle(thr) if rpm is None else float(rpm)
    if n < 0.0 or n > RPM_MAX_TAKEOFF * 1.05:
        raise MvemError(f"rpm {n:.0f} outside 0..{RPM_MAX_TAKEOFF * 1.05:.0f}")

    out = EngineOutputs(throttle_pct=thr, rpm=n,
                        prop_rpm=n / GEARBOX_RATIO,
                        altitude_ft=altitude_ft, oat_c=a.oat_c,
                        humidity_pct=humidity_pct, airspeed_ms=airspeed_ms,
                        ambient_pressure_bar=a.ambient_pressure_pa / 1.0e5,
                        air_density_kgm3=a.density_kgm3,
                        fault_label=f.label)

    # ---- gas path ---------------------------------------------------- #
    ambient_bar = a.ambient_pressure_pa / 1.0e5
    avail = available_plenum_bar(n, a.ambient_pressure_pa, f.wastegate_authority)

    # Charge temperature depends on the pressure ratio, and the regulated
    # pressure depends on charge temperature. Fixed point, converges in three.
    plen = avail
    t_plen = a.oat_c
    for _ in range(4):
        t_plen = plenum_temperature_c(plen, a.ambient_pressure_pa, a.oat_c,
                                      f.intercooler_effectiveness)
        plen = regulated_plenum_bar(n, t_plen, avail)
    t_plen = plenum_temperature_c(plen, a.ambient_pressure_pa, a.oat_c,
                                  f.intercooler_effectiveness)
    map_bar = throttled_map_bar(plen, thr, ambient_bar)

    ve = volumetric_efficiency(n, f.ve_mult)
    air_kgh = ideal_air_flow_kgh(n, map_bar, t_plen) * ve

    afr_sched = _interp(n, AFR_SCHEDULE_RPM, AFR_SCHEDULE)
    trim_mean = sum(f.cylinder_fuel_trim) / N_CYLINDERS
    fuel_kgh = (air_kgh / afr_sched) * trim_mean if afr_sched > 0.0 else 0.0

    afr_actual = air_kgh / fuel_kgh if fuel_kgh > 1.0e-9 else float("inf")
    lam = afr_actual / AFR_STOICH if math.isfinite(afr_actual) else float("inf")

    out.plenum_pressure_bar = plen
    out.plenum_available_bar = avail
    out.boost_limited = avail <= plen * (1.0 + 1.0e-9)
    out.manifold_pressure_bar = map_bar
    out.manifold_temp_c = t_plen
    out.volumetric_efficiency = ve
    out.air_flow_kgh = air_kgh
    out.fuel_flow_kgh = fuel_kgh
    out.fuel_flow_lph = fuel_kgh / FUEL_DENSITY_KG_PER_L
    out.afr_actual = afr_actual
    out.lambda_actual = lam

    # ---- work -------------------------------------------------------- #
    p_fuel_w = (fuel_kgh / 3600.0) * FUEL_LHV_J_PER_KG
    eta_i = indicated_efficiency(n, lam)
    p_ind_w = p_fuel_w * eta_i
    p_fric_w = friction_power_w(n, f.fmep_mult) * (1.0 + 0.45 * f.bearing_wear)
    p_pump_w = pumping_power_w(n, map_bar, ambient_bar)
    p_brake_w = max(0.0, p_ind_w - p_fric_w - p_pump_w)

    omega = 2.0 * math.pi * n / 60.0
    out.indicated_power_kw = p_ind_w / 1000.0
    out.friction_power_kw = p_fric_w / 1000.0
    out.pumping_power_kw = p_pump_w / 1000.0
    out.brake_power_kw = p_brake_w / 1000.0
    out.torque_nm = p_brake_w / omega if omega > 1.0e-6 else 0.0
    out.bsfc_g_per_kwh = (fuel_kgh * 1000.0 / (p_brake_w / 1000.0)
                          if p_brake_w > 1.0e3 else 0.0)
    out.brake_thermal_efficiency = p_brake_w / p_fuel_w if p_fuel_w > 0.0 else 0.0

    # ---- heat rejection ---------------------------------------------- #
    remainder_w = max(0.0, p_fuel_w - p_brake_w)
    q_exh = remainder_w * HEAT_FRAC_EXHAUST
    q_cool = remainder_w * HEAT_FRAC_COOLANT * f.heat_mult
    q_oil = remainder_w * HEAT_FRAC_OIL * f.heat_mult * (1.0 + 0.6 * f.bearing_wear)
    out.heat_exhaust_kw = q_exh / 1000.0
    out.heat_coolant_kw = q_cool / 1000.0
    out.heat_oil_kw = q_oil / 1000.0

    # ---- cooling ----------------------------------------------------- #
    # Airspeed floor represents propeller wash over the radiator, not
    # freestream. v0.1.0 used 10 m/s and produced 255 C oil in a static
    # tropical run-up, which was the model's error, not the engine's.
    v_eff = max(float(airspeed_ms), AIRSPEED_MIN_MS)
    rho_ratio = a.density_kgm3 / atm.RHO0
    speed_term = max(UA_AIRSPEED_FLOOR_FRAC, (v_eff / AIRSPEED_REF_MS) ** 0.6)
    ua_scale = (rho_ratio ** 0.6) * speed_term
    ua_cool = UA_COOLANT_REF_W_PER_K * ua_scale * max(0.05, f.coolant_pump_health)
    ua_oil = UA_OIL_REF_W_PER_K * ua_scale

    t_cool = max(THERMOSTAT_COOLANT_C, a.oat_c + q_cool / max(ua_cool, 1.0))
    t_oil = max(THERMOSTAT_OIL_C, a.oat_c + q_oil / max(ua_oil, 1.0))
    out.coolant_temp_out_c = t_cool
    out.coolant_temp_in_c = t_cool - COOLANT_DELTA_T_C
    out.oil_temp_c = t_oil

    # ---- exhaust temperatures ---------------------------------------- #
    # After cooling, because the port wall sits at coolant temperature.
    m_exh = (air_kgh + fuel_kgh) / 3600.0
    out.egt_mean_c = exhaust_gas_temp_c(q_exh, m_exh, t_plen, t_cool)

    lam_ref = lam if math.isfinite(lam) else 1.0
    g_ref = _egt_lambda_shape(lam_ref)
    egts: List[float] = []
    for trim in f.cylinder_fuel_trim:
        lam_i = lam_ref * (trim_mean / trim) if trim > 1.0e-6 else 3.0
        g_i = _egt_lambda_shape(lam_i)
        egts.append(out.egt_mean_c * (1.0 + EGT_LAMBDA_GAIN * (g_i - g_ref)))
    out.egt_c = [e + s for e, s in zip(egts, EGT_BUILD_SCATTER_C)]
    out.egt_spread_c = max(out.egt_c) - min(out.egt_c)

    # Head temperature. The 915 iS HAS NO CHT SENSOR -- barrels are air cooled
    # and heads are liquid cooled, and [A] expresses the head limit as coolant
    # temperature. These are DERIVED per-cylinder jacket temperatures, not
    # measurements. See caveat cht_is_derived_not_measured.
    out.cht_c = [t_cool + 12.0 + 0.045 * (out.egt_c[i] - out.egt_mean_c)
                 for i in range(N_CYLINDERS)]

    # ---- oil pressure ------------------------------------------------ #
    visc = (OIL_VISCOSITY_REF_TEMP_C / max(t_oil, 40.0)) ** OIL_VISCOSITY_EXPONENT
    p_oil = ((OIL_PRESS_INTERCEPT_BAR + OIL_PRESS_SLOPE_BAR_PER_RPM * n)
             * visc * f.oil_pump_health)
    out.oil_pressure_bar = _clamp(p_oil, 0.0, OIL_PRESS_MAX_BAR)
    out.fuel_rail_bar = sum(FUEL_RAIL_BAR) / 2.0

    # ---- vibration, UNVERIFIED AMPLITUDES ---------------------------- #
    frac = n / RPM_MAX
    rms = (VIB_BASE_G + VIB_SPAN_G * frac * frac) * (1.0 + 1.8 * f.bearing_wear)
    imbalance = max(0.0, out.egt_spread_c - 20.0) / 100.0
    rms *= (1.0 + 0.35 * imbalance)
    out.vib_rms_g = rms
    out.vib_crest_factor = VIB_CREST_FACTOR * (1.0 + 0.5 * f.bearing_wear)
    out.vib_peak_g = rms * out.vib_crest_factor
    out.vib_f0_hz = n / 60.0
    out.vib_1x_g = rms * 0.45
    out.vib_2x_g = rms * 0.62          # firing order dominates on a 4-cyl 4-stroke
    out.vib_3x_g = rms * 0.18
    out.vib_bearing_band_g = rms * (0.06 + 0.55 * f.bearing_wear)

    # ---- electrical -------------------------------------------------- #
    load = max(0.0, float(electrical_load_a))
    capacity = ALTERNATOR_CAPACITY_A * f.alternator_health
    if n >= ALTERNATOR_CUTIN_RPM and load <= capacity:
        out.bus_voltage_v = BUS_VOLTAGE_REGULATED_V - 0.02 * max(0.0, load - 10.0)
        out.alternator_current_a = load
    elif n >= ALTERNATOR_CUTIN_RPM:
        deficit = load - capacity
        out.bus_voltage_v = max(BUS_VOLTAGE_BATTERY_V - 0.05 * deficit, 10.5)
        out.alternator_current_a = capacity
    else:
        ramp = _clamp((n - RPM_IDLE_MIN) / (ALTERNATOR_CUTIN_RPM - RPM_IDLE_MIN),
                      0.0, 1.0)
        out.bus_voltage_v = BUS_VOLTAGE_BATTERY_V + ramp * 1.4
        out.alternator_current_a = capacity * ramp

    out.limit_breaches = check_limits(out)
    return out


def check_limits(o: EngineOutputs) -> List[str]:
    """Every check cites [A] section 2.1. REPORTING, not clamping.

    An operating point that exceeds a limit is not an error -- sustained
    wide-open throttle on a hot day genuinely overheats, which is why run-ups
    are time limited. The model's job is to say so, not to hide it.
    """
    b: List[str] = []
    if o.rpm > RPM_MAX_TAKEOFF:
        b.append(f"rpm {o.rpm:.0f} > {RPM_MAX_TAKEOFF:.0f} take-off limit")
    if o.manifold_pressure_bar > MAP_MAX_BAR:
        b.append(f"MAP {o.manifold_pressure_bar:.3f} bar > {MAP_MAX_BAR} bar")
    if o.manifold_pressure_bar < MAP_MIN_BAR:
        b.append(f"MAP {o.manifold_pressure_bar:.3f} bar < {MAP_MIN_BAR} bar")
    if o.manifold_temp_c > MANIFOLD_TEMP_MAX_C:
        b.append(f"manifold air {o.manifold_temp_c:.1f} C > "
                 f"{MANIFOLD_TEMP_MAX_C} C")
    if o.egt_mean_c > EGT_MAX_C:
        b.append(f"EGT {o.egt_mean_c:.0f} C > {EGT_MAX_C:.0f} C")
    if o.egt_spread_c > EGT_SPREAD_MAX_C and o.fuel_flow_lph > 3.0:
        b.append(f"EGT spread {o.egt_spread_c:.0f} C > {EGT_SPREAD_MAX_C:.0f} C")
    if o.coolant_temp_out_c > COOLANT_TEMP_MAX_C:
        b.append(f"coolant {o.coolant_temp_out_c:.1f} C > {COOLANT_TEMP_MAX_C} C")
    if o.oil_temp_c > OIL_TEMP_MAX_C:
        b.append(f"oil {o.oil_temp_c:.1f} C > {OIL_TEMP_MAX_C} C")
    floor = (OIL_PRESS_MIN_HIGH_RPM_BAR if o.rpm > OIL_PRESS_RPM_BREAK
             else OIL_PRESS_MIN_LOW_RPM_BAR)
    if o.rpm > RPM_IDLE_MIN * 0.9 and o.oil_pressure_bar < floor:
        b.append(f"oil pressure {o.oil_pressure_bar:.2f} bar < {floor} bar "
                 f"at {o.rpm:.0f} rpm")
    if o.oil_pressure_bar > OIL_PRESS_MAX_BAR:
        b.append(f"oil pressure {o.oil_pressure_bar:.2f} bar > "
                 f"{OIL_PRESS_MAX_BAR} bar")
    if o.brake_power_kw > POWER_TAKEOFF_KW * 1.05:
        b.append(f"power {o.brake_power_kw:.1f} kW > take-off "
                 f"{POWER_TAKEOFF_KW} kW +5%")
    return b


# ==================================================================== #
# Block I -- declared caveats
# ==================================================================== #

def mvem_caveats() -> List[Dict[str, Any]]:
    return [
        {"id": "wot_calibration_only", "verified": True,
         "value": "5 points, 3000-5800 rpm, sea level, 30 C, WOT",
         "note": ("[B] simulated wide open throttle at sea level only. Part "
                  "throttle and altitude come from the turbo and throttle "
                  "models here, which no source constrains. [B] conclusion 3 "
                  "states the same gap.")},
        {"id": "ve_afr_not_separable", "verified": False,
         "value": {"afr_schedule": dict(zip(AFR_SCHEDULE_RPM, AFR_SCHEDULE)),
                   "resulting_ve": [round(v, 4) for v in CAL_VE]},
         "note": ("air flow is inferred as published fuel flow times an ASSUMED "
                  "lambda schedule. A richer schedule gives lower volumetric "
                  "efficiency and identical fuel flow, so the two cannot be "
                  "separated from public data. Absolute air flow is uncertain "
                  "by roughly the schedule error; its RESPONSE to throttle and "
                  "altitude is not.")},
        {"id": "fuel_density_derived_not_assumed", "verified": True,
         "value": round(FUEL_DENSITY_KG_PER_L, 4),
         "note": ("[B] Table 4 over-determines itself: BSFC x power / flow "
                  "gives density at all five points, agreeing to 0.5%. That is "
                  "MOGAS. See fuel_type_changes_density.")},
        {"id": "fuel_type_changes_density", "verified": True,
         "value": {"mogas": FUEL_DENSITY_MOGAS_KG_PER_L,
                   "avgas_100ll": FUEL_DENSITY_AVGAS_KG_PER_L,
                   "calibration_uses": FUEL_TYPE},
         "note": ("[A] permits both fuels and they differ by 4% in density. "
                  "node1_ingestion/adapter.py uses 0.72, which is AVGAS -- a "
                  "legitimate different choice, not a bug. But every L/h to "
                  "kg/h conversion carries whichever value it assumes, so the "
                  "fuel type must be declared per session and must not be "
                  "mixed silently between generator and pipeline.")},
        {"id": "boost_regulated_to_air_mass", "verified": True,
         "value": {"pr_max_derived": [round(p, 4) for p in CAL_PR_MAX],
                   "critical_altitude_ft": CRITICAL_ALTITUDE_FT},
         "note": ("v0.1.0 held boost at constant PRESSURE and so gained 9% of "
                  "power climbing to 15000 ft. Rated power to a critical "
                  "altitude means holding air MASS, so the ECU now commands "
                  "whatever pressure achieves the calibrated mass and the "
                  "compressor ceiling is derived from the published critical "
                  "altitude. No compressor map exists in any source; that one "
                  "claim is what pins the ceiling down.")},
        {"id": "airflow_power_relation_demoted", "verified": True,
         "value": "P = -6.3264 + 0.0169 * airflow_g_per_min",
         "note": ("published in [A] section 2.1 but under-reads this "
                  "calibration by ~15% across the range, so it is a coarse ECU "
                  "diagnostic fit. Implemented as a cross-check only; never "
                  "used to compute anything. Transcription from the printed "
                  "page should be re-verified.")},
        {"id": "cht_is_derived_not_measured", "verified": True,
         "value": "cht_1_c..cht_4_c",
         "note": ("the 915 iS HAS NO CHT SENSOR. Barrels are air cooled, heads "
                  "are liquid cooled, and [A] expresses the head limit as "
                  "coolant temperature. These channels are derived "
                  "per-cylinder jacket temperatures and must be tagged DERIVED "
                  "wherever they are displayed. Presenting them as "
                  "measurements would be a false claim.")},
        {"id": "heat_split_is_judgement", "verified": False,
         "value": {"exhaust": HEAT_FRAC_EXHAUST, "coolant": HEAT_FRAC_COOLANT,
                   "oil": HEAT_FRAC_OIL, "misc_air_cooled": HEAT_FRAC_MISC},
         "note": ("fractions of non-brake fuel energy. The misc share carries "
                  "the AIR cooled barrels, which is why the coolant fraction "
                  "is lower than a fully liquid cooled engine would take. No "
                  "Rotax thermal data was available. Temperature TRENDS are "
                  "meaningful; absolute values carry the error of this split.")},
        {"id": "exhaust_port_ua_tuned_not_measured", "verified": False,
         "value": EXHAUST_PORT_UA_W_PER_K,
         "note": ("a fixed heat fraction over mass flow gave 1063 C at idle. "
                  "The cooled-pipe model fixes that, but its conductance was "
                  "chosen to put WOT near 800 C and idle near 500 C rather "
                  "than measured. EGT RESPONSE to mixture and load is "
                  "meaningful; the absolute level is tuned.")},
        {"id": "cooling_ua_unmeasured", "verified": False,
         "value": {"ua_coolant_w_per_k": UA_COOLANT_REF_W_PER_K,
                   "ua_oil_w_per_k": UA_OIL_REF_W_PER_K,
                   "airspeed_floor_ms": AIRSPEED_MIN_MS,
                   "thermostat_c": THERMOSTAT_COOLANT_C},
         "note": ("radiator and oil cooler conductance are properties of the "
                  "AIRFRAME, not the engine, and no source gives them. The "
                  "airspeed floor represents propeller wash during static "
                  "running. Tuned so sea-level continuous power sits on the "
                  "thermostat.")},
        {"id": "no_coolant_boiling_model", "verified": True,
         "value": f"valid to about {COOLANT_TEMP_MAX_C + 10:.0f} C",
         "note": ("there is no phase change, pressure cap or vapour lock "
                  "model. Above roughly 130 C the coolant and oil numbers are "
                  "fiction and only the fact of a breach is meaningful. A "
                  "predicted overheat is a correct prediction; its magnitude "
                  "is not.")},
                {"id": "thermostat_pinned_channels_carry_no_signal", "verified": True,
         "value": {"coolant_c": THERMOSTAT_COOLANT_C,
                   "oil_c": THERMOSTAT_OIL_C},
         "note": ("across the whole cruise band coolant and oil sit ON their "
                  "thermostats, so those two channels are constant and their "
                  "residuals are structurally zero. CASE 10 shows the mistuned "
                  "holdout differing by 16% in power and 0.0 C in coolant. Any "
                  "detector weighting coolant or oil temperature is weighting "
                  "a channel with no information until the cooling system "
                  "saturates. Two of the nine trained channels are affected.")},
        {"id": "no_hot_day_derate", "verified": False,
         "value": "104.4 kW at both SL/30 C and 8000 ft/45 C",
         "note": ("holding air mass holds power wherever the compressor can "
                  "deliver, which is what turbonormalising does, but no knock "
                  "margin, timing retard or charge-temperature limit is "
                  "modelled. The manifold-air breach above 50 C [A] is the "
                  "honest flag that a real ECU would be backing off here. Do "
                  "not present flat hot-day power as a prediction.")},
        {"id": "disagrees_with_existing_training_data", "verified": True,
         "value": "coolant ~88-105 C here vs 66.6 C in master_dataset.csv",
         "note": ("the existing dataset was declared ~70% fidelity from "
                  "limited public data. This module and that dataset disagree "
                  "on absolute temperatures, so refitting the baseline deck on "
                  "MVEM output WILL move every residual. Expected, and a "
                  "reason to regenerate rather than mix the two.")},
        {"id": "vibration_shape_unverified", "verified": False,
         "value": {"base_g": VIB_BASE_G, "span_g": VIB_SPAN_G},
         "note": ("no vibration data exists in any source consulted. "
                  "Amplitudes are invented; only the FREQUENCY content is "
                  "defensible, being fixed by shaft speed and the firing order "
                  "of a four-cylinder four-stroke.")},
        {"id": "egt_severity_not_monotonic", "verified": True,
         "value": "28% trim cut gives a NARROWER spread than 15%",
         "note": ("EGT peaks slightly lean of stoichiometric, so a cylinder "
                  "driven far lean crosses the peak and cools again. Severity "
                  "is therefore NOT recoverable from EGT spread alone, while "
                  "power and vibration do move monotonically. This is real "
                  "engine behaviour and it is the strongest argument in the "
                  "system for multi-channel diagnosis over a threshold rule.")},
        {"id": "calibration_range_clamped", "verified": True,
         "value": f"{CAL_RPM[0]:.0f}-{CAL_RPM[-1]:.0f} rpm",
         "note": ("interpolation clamps outside the calibration range rather "
                  "than extrapolating, because linear extension of volumetric "
                  "efficiency below 3000 rpm runs past unity. Idle and taxi "
                  "are held at the 3000 rpm calibration and are the least "
                  "trustworthy region of the model.")},
        {"id": "gearbox_ratio_unverified", "verified": False,
         "value": GEARBOX_RATIO,
         "note": ("prop rpm depends on the fitted reduction gearbox, which is "
                  "an option. Confirm against [A] section 7 before quoting "
                  "prop speed anywhere.")},
        {"id": "steady_state_only", "verified": True,
         "value": "no time constants in this module",
         "note": ("solve() returns equilibrium. Thermal lag lives in "
                  "shared/throttle_dynamics.py with its own declared and "
                  "unverified tau values. Two sources of truth for lag would "
                  "be worse than one.")},
        {"id": "mistuned_holdout_available", "verified": True,
         "value": {"ve_mult": 0.88, "heat_mult": 0.85, "fmep_mult": 1.10},
         "note": ("mistuned() returns a deliberately wrong engine for "
                  "generating test data the models never trained on. Accuracy "
                  "measured on the nominal model measures self-agreement, not "
                  "fault detection. Report both numbers or neither.")},
    ]


# ==================================================================== #
# Block J -- self-check
# ==================================================================== #

def _self_test() -> None:
    fails: List[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            fails.append(msg)
            print(f"  FAIL: {msg}")

    def near(a: float, b: float, tol: float) -> bool:
        return abs(a - b) <= tol

    print(f"engine_mvem v{ENGINE_MVEM_VERSION}  Rotax 915 iS  fuel {FUEL_TYPE}")
    print(f"  {DISPLACEMENT_L} L, {N_CYLINDERS} cyl, bore {BORE_M * 1000:.0f} mm, "
          f"stroke {STROKE_M * 1000:.0f} mm, CR {COMPRESSION_RATIO}:1")

    print("\nCASE 0  the published data is self-consistent")
    print("  fuel density implied independently at each of five points:")
    for rpm, d in zip(CAL_RPM, CAL_IMPLIED_FUEL_DENSITY):
        print(f"    {rpm:5.0f} rpm -> {d:.4f} kg/L")
    spread = max(CAL_IMPLIED_FUEL_DENSITY) - min(CAL_IMPLIED_FUEL_DENSITY)
    mean_d = sum(CAL_IMPLIED_FUEL_DENSITY) / len(CAL_IMPLIED_FUEL_DENSITY)
    print(f"  mean {mean_d:.4f} kg/L, spread {spread:.4f} "
          f"({100 * spread / mean_d:.2f}%)  module uses {FUEL_DENSITY_KG_PER_L}")
    check(spread / mean_d < 0.01,
          f"implied fuel density spread {100 * spread / mean_d:.2f}% > 1%, so "
          f"[B] Table 4 is not internally consistent and one column is wrong")
    check(near(mean_d, FUEL_DENSITY_KG_PER_L, 0.002),
          f"FUEL_DENSITY_KG_PER_L={FUEL_DENSITY_KG_PER_L} disagrees with the "
          f"derived {mean_d:.4f}")
    check(0.70 <= mean_d <= 0.78,
          f"derived density {mean_d:.4f} kg/L outside the MOGAS spec band")

    print("\n  torque and power must agree through omega at every point:")
    worst = 0.0
    for rpm, tq, pw in zip(CAL_RPM, CAL_TORQUE_NM, CAL_POWER_KW):
        implied = tq * 2.0 * math.pi * rpm / 60.0 / 1000.0
        worst = max(worst, abs(implied - pw))
    print(f"    worst torque/power closure error: {worst:.4f} kW")
    # Table 4 prints torque to 0.1 Nm, which at 5800 rpm is 0.06 kW. A tighter
    # tolerance than the published precision is not a test of anything.
    check(worst < 0.10,
          f"[B] Table 4 torque and power disagree by {worst:.3f} kW, which is "
          f"more than its 0.1 Nm print precision can explain")

    print("\n  intercooler effectiveness that reproduces Table 2 temperatures:")
    eps: List[float] = []
    for plen, t_plen in zip(CAL_PLENUM_BAR, CAL_PLENUM_TEMP_C):
        pr = max(plen / (atm.P0_PA / 1.0e5), 1.0)
        expo = (GAMMA_AIR - 1.0) / (GAMMA_AIR * COMPRESSOR_ISENTROPIC_EFF)
        t_out = (CAL_AMBIENT_TEMP_C + atm.KELVIN) * (pr ** expo) - atm.KELVIN
        rise = t_out - CAL_AMBIENT_TEMP_C
        if rise > 1.0:
            eps.append((t_out - t_plen) / rise)
    print(f"    boosted points -> {[round(e, 3) for e in eps]}  "
          f"module uses {INTERCOOLER_EFFECTIVENESS}")
    check(all(0.75 <= e <= 0.98 for e in eps),
          f"derived intercooler effectiveness {eps} outside a plausible band")

    print("\n  compressor pressure ratio ceiling, from the critical altitude:")
    for rpm, pr in zip(CAL_RPM, CAL_PR_MAX):
        kind = "loss ratio (no boost)" if pr < 1.0 else "compression ratio"
        print(f"    {rpm:5.0f} rpm -> {pr:.4f}   {kind}")
    check(all(pr > 0.0 for pr in CAL_PR_MAX), "non-positive pressure ratio")
    check(max(CAL_PR_MAX) < 4.0,
          f"derived pressure ratio ceiling {max(CAL_PR_MAX):.2f} is beyond any "
          f"single-stage automotive compressor")

    print("\nCASE 1  derived calibration must be smooth, not jagged")
    print(f"  {'rpm':>6} {'VE':>8} {'eta_ind':>9} {'FMEP bar':>9} {'pump W':>8}")
    p0_bar = atm.P0_PA / 1.0e5
    for rpm, ve, ei, plen in zip(CAL_RPM, CAL_VE, CAL_ETA_INDICATED,
                                 CAL_PLENUM_BAR):
        print(f"  {rpm:6.0f} {ve:8.4f} {ei:9.4f} {fmep_bar(rpm):9.4f} "
              f"{pumping_power_w(rpm, plen, p0_bar):8.0f}")
    check(all(0.70 <= v <= 1.02 for v in CAL_VE),
          f"volumetric efficiency {[round(v, 3) for v in CAL_VE]} implausible")
    check(all(0.30 <= e <= 0.45 for e in CAL_ETA_INDICATED),
          f"indicated efficiency {[round(e, 3) for e in CAL_ETA_INDICATED]} "
          f"outside 30-45%, so the friction model or the AFR schedule is wrong")
    # Take-off is a separate ECU tune, so a small rise at 5800 is expected and
    # real. v0.1.0 asserted strict monotonicity and failed on a 0.4% uptick.
    rises = [(b - a) / a for a, b in
             zip(CAL_ETA_INDICATED, CAL_ETA_INDICATED[1:]) if b > a]
    worst_rise = max(rises) if rises else 0.0
    print(f"  largest rise in indicated efficiency with rpm: "
          f"{100 * worst_rise:.2f}% (take-off is a separate tune)")
    check(worst_rise < 0.015,
          f"indicated efficiency rises {100 * worst_rise:.1f}% with rpm, too "
          f"much to explain as a take-off tune")
    check(CAL_VE[-1] < CAL_VE[1],
          "volumetric efficiency should fall toward peak rpm as flow losses rise")

    print("\nCASE 2  reproduce [B] Table 4 at the five calibration points")
    print(f"  {'rpm':>6} {'kW oem':>7} {'kW mvem':>8} {'err%':>6} "
          f"{'Nm oem':>7} {'Nm mvem':>8} {'err%':>6} "
          f"{'L/h oem':>8} {'L/h mvem':>9} {'err%':>6}")
    for rpm, tq, pw, lph in zip(CAL_RPM, CAL_TORQUE_NM, CAL_POWER_KW,
                                CAL_FUEL_LPH):
        o = solve(throttle_pct=100.0, altitude_ft=0.0,
                  oat_c=CAL_AMBIENT_TEMP_C, rpm=rpm)
        ep = 100.0 * (o.brake_power_kw - pw) / pw
        et = 100.0 * (o.torque_nm - tq) / tq
        ef = 100.0 * (o.fuel_flow_lph - lph) / lph
        print(f"  {rpm:6.0f} {pw:7.1f} {o.brake_power_kw:8.2f} {ep:+6.2f} "
              f"{tq:7.1f} {o.torque_nm:8.2f} {et:+6.2f} "
              f"{lph:8.1f} {o.fuel_flow_lph:9.2f} {ef:+6.2f}")
        check(abs(ep) < 1.5, f"{rpm:.0f} rpm power off by {ep:+.2f}%")
        check(abs(et) < 1.5, f"{rpm:.0f} rpm torque off by {et:+.2f}%")
        check(abs(ef) < 1.5, f"{rpm:.0f} rpm fuel off by {ef:+.2f}%")
    print("  NOTE these five points are reproduced BY CONSTRUCTION. This case "
          "proves the algebra closes, not that the model is accurate.")

    print("\nCASE 3  published limits [A] must not be violated at rated power")
    for label, rpm, cap in (("take-off ", RPM_MAX_TAKEOFF, POWER_TAKEOFF_KW),
                            ("continuous", RPM_MAX_CONTINUOUS,
                             POWER_CONTINUOUS_KW)):
        o = solve(throttle_pct=100.0, oat_c=CAL_AMBIENT_TEMP_C, rpm=rpm)
        print(f"  {label} {o.brake_power_kw:6.1f} kW (rated {cap})  "
              f"MAP {o.manifold_pressure_bar:.3f} bar  "
              f"EGT {o.egt_mean_c:5.0f} C  coolant {o.coolant_temp_out_c:5.1f} C  "
              f"oil {o.oil_temp_c:5.1f} C / {o.oil_pressure_bar:.2f} bar")
        if o.limit_breaches:
            print(f"    breaches: {o.limit_breaches}")
        check(near(o.brake_power_kw, cap, cap * 0.05),
              f"{label.strip()} power {o.brake_power_kw:.1f} kW is not within "
              f"5% of the rated {cap} kW")
        check(o.egt_mean_c < EGT_MAX_C,
              f"{label.strip()} EGT {o.egt_mean_c:.0f} C exceeds {EGT_MAX_C} C")
        check(o.coolant_temp_out_c < COOLANT_TEMP_MAX_C,
              f"{label.strip()} coolant {o.coolant_temp_out_c:.1f} C exceeds "
              f"{COOLANT_TEMP_MAX_C} C")
        check(o.oil_temp_c < OIL_TEMP_MAX_C,
              f"{label.strip()} oil {o.oil_temp_c:.1f} C exceeds {OIL_TEMP_MAX_C} C")
        check(o.oil_pressure_bar >= OIL_PRESS_MIN_HIGH_RPM_BAR,
              f"{label.strip()} oil pressure {o.oil_pressure_bar:.2f} bar below "
              f"the {OIL_PRESS_MIN_HIGH_RPM_BAR} bar floor")
        check(MAP_MIN_BAR <= o.manifold_pressure_bar <= MAP_MAX_BAR,
              f"{label.strip()} MAP {o.manifold_pressure_bar:.3f} bar outside "
              f"[{MAP_MIN_BAR}, {MAP_MAX_BAR}]")

    print("\nCASE 4  the [A] airflow relation, measured not assumed")
    print(f"  {'rpm':>6} {'air kg/h':>9} {'kW mvem':>8} {'kW [A] rel':>11} "
          f"{'disagreement':>13}")
    for rpm in CAL_RPM:
        o = solve(throttle_pct=100.0, oat_c=CAL_AMBIENT_TEMP_C, rpm=rpm)
        pred = airflow_power_crosscheck(o.air_flow_kgh)
        d = 100.0 * (pred - o.brake_power_kw) / o.brake_power_kw
        print(f"  {rpm:6.0f} {o.air_flow_kgh:9.1f} {o.brake_power_kw:8.1f} "
              f"{pred:11.1f} {d:+12.1f}%")
    print("  DECLARED, not asserted: the relation under-reads throughout, "
          "which is why it is a cross-check and not a calibration target.")
    check(any(c["id"] == "airflow_power_relation_demoted" and c["verified"]
              for c in mvem_caveats()),
          "the airflow relation discrepancy must stay a declared caveat")

    print("\nCASE 5  altitude: rated power flat to the critical altitude")
    print(f"  {'alt ft':>7} {'MAP bar':>8} {'avail':>7} {'kW':>7} {'% of SL':>8} "
          f"{'EGT C':>6} {'limited':>8}")
    sl = solve(throttle_pct=100.0, rpm=RPM_MAX_CONTINUOUS)
    for alt in (0.0, 5000.0, 10000.0, 15000.0, 18000.0, 21000.0, 23000.0):
        o = solve(throttle_pct=100.0, altitude_ft=alt, rpm=RPM_MAX_CONTINUOUS)
        pct = 100.0 * o.brake_power_kw / sl.brake_power_kw
        print(f"  {alt:7.0f} {o.manifold_pressure_bar:8.3f} "
              f"{o.plenum_available_bar:7.3f} {o.brake_power_kw:7.1f} "
              f"{pct:7.1f}% {o.egt_mean_c:6.0f} {str(o.boost_limited):>8}")
        if alt <= CRITICAL_ALTITUDE_FT:
            check(abs(pct - 100.0) < 3.0,
                  f"power at {alt:.0f} ft is {pct:.1f}% of sea level; the "
                  f"turbo is meant to hold rated power to "
                  f"{CRITICAL_ALTITUDE_FT:.0f} ft [A][D]")
    crit = solve(throttle_pct=100.0, altitude_ft=CRITICAL_ALTITUDE_FT,
                 rpm=RPM_MAX_CONTINUOUS)
    high = solve(throttle_pct=100.0, altitude_ft=21000.0,
                 rpm=RPM_MAX_CONTINUOUS)
    ceil_ = solve(throttle_pct=100.0, altitude_ft=CEILING_FT,
                  rpm=RPM_MAX_CONTINUOUS)
    print(f"  retained: {100 * crit.brake_power_kw / sl.brake_power_kw:.1f}% at "
          f"the critical altitude, "
          f"{100 * high.brake_power_kw / sl.brake_power_kw:.1f}% at 21000 ft, "
          f"{100 * ceil_.brake_power_kw / sl.brake_power_kw:.1f}% at the ceiling")
    check(high.brake_power_kw < crit.brake_power_kw * 0.95,
          "power did not fall above the critical altitude, so the compressor "
          "limit is not modelled")
    check(ceil_.brake_power_kw < high.brake_power_kw,
          "power did not keep falling toward the ceiling")

    print("\nCASE 6  throttle sweep: monotonic, MAP and EGT physically sane")
    print(f"  {'thr%':>5} {'rpm':>6} {'MAP bar':>8} {'kW':>7} {'L/h':>6} "
          f"{'lambda':>7} {'EGT C':>6} {'pump kW':>8}")
    prev = None
    for thr in (10.0, 25.0, 40.0, 55.0, 70.0, 85.0, 100.0):
        o = solve(throttle_pct=thr, altitude_ft=6000.0, oat_c=10.0)
        print(f"  {thr:5.0f} {o.rpm:6.0f} {o.manifold_pressure_bar:8.3f} "
              f"{o.brake_power_kw:7.1f} {o.fuel_flow_lph:6.2f} "
              f"{o.lambda_actual:7.3f} {o.egt_mean_c:6.0f} "
              f"{o.pumping_power_kw:8.2f}")
        check(MAP_MIN_BAR <= o.manifold_pressure_bar <= MAP_MAX_BAR,
              f"throttle {thr:.0f}% gave MAP {o.manifold_pressure_bar:.3f} bar "
              f"outside the published band")
        check(o.egt_mean_c < EGT_MAX_C,
              f"throttle {thr:.0f}% gave EGT {o.egt_mean_c:.0f} C, above the "
              f"{EGT_MAX_C:.0f} C limit at PART LOAD, which is unphysical")
        if prev is not None:
            check(o.brake_power_kw >= prev - 1e-6,
                  f"power fell as throttle rose at {thr:.0f}%")
        prev = o.brake_power_kw
    idle = solve(throttle_pct=10.0, altitude_ft=0.0, oat_c=15.0)
    print(f"  sea-level idle-ish: EGT {idle.egt_mean_c:.0f} C, "
          f"MAP {idle.manifold_pressure_bar:.3f} bar "
          f"(v0.1.0 gave 1063 C and 0.060 bar here)")
    check(idle.egt_mean_c < 700.0,
          f"light-load EGT {idle.egt_mean_c:.0f} C is still too hot; the port "
          f"heat loss model is not working")

    print("\nCASE 7  hot and high, and cold soak")
    for label, expect_breach, kw in (
            ("hot and high", False, dict(throttle_pct=100.0, altitude_ft=8000.0,
                                         oat_c=45.0, humidity_pct=30.0,
                                         airspeed_ms=40.0)),
            ("tropical static", True, dict(throttle_pct=100.0, altitude_ft=0.0,
                                           oat_c=38.0, humidity_pct=95.0,
                                           airspeed_ms=AIRSPEED_MIN_MS)),
            ("cold cruise", False, dict(throttle_pct=75.0, altitude_ft=15000.0,
                                        oat_c=-20.0, airspeed_ms=55.0))):
        o = solve(**kw)
        print(f"  {label:16s} {o.brake_power_kw:6.1f} kW  "
              f"EGT {o.egt_mean_c:5.0f} C  coolant {o.coolant_temp_out_c:5.1f} C  "
              f"oil {o.oil_temp_c:5.1f} C / {o.oil_pressure_bar:.2f} bar  "
              f"bus {o.bus_voltage_v:.2f} V")
        if o.limit_breaches:
            print(f"    breaches: {o.limit_breaches}")
        check(o.egt_mean_c < EGT_MAX_C, f"{label}: EGT {o.egt_mean_c:.0f} C")
        # A predicted overheat is a CORRECT prediction -- sustained WOT static
        # running in the tropics is time limited for exactly this reason. What
        # must not happen is a physically impossible number.
        check(o.coolant_temp_out_c < COOLANT_TEMP_MAX_C + 40.0,
              f"{label}: coolant {o.coolant_temp_out_c:.1f} C is past anything "
              f"this model can represent (no boiling model)")
        check(o.oil_temp_c < OIL_TEMP_MAX_C + 40.0,
              f"{label}: oil {o.oil_temp_c:.1f} C is past anything this model "
              f"can represent")
        check(11.0 < o.bus_voltage_v < 15.0,
              f"{label}: bus {o.bus_voltage_v:.2f} V implausible")
        if expect_breach:
            check(bool(o.limit_breaches),
                  f"{label}: sustained WOT here should breach a thermal limit; "
                  f"reporting none means the cooling model is too generous")

    print("\nCASE 8  the rpm surrogate must match throttle_dynamics exactly")
    try:
        from shared.throttle_dynamics import (
            RPM_IDLE as TD_IDLE, RPM_MAX as TD_MAX,
            rpm_for_throttle as td_rpm)
        worst_rpm = max(abs(rpm_for_throttle(t) - td_rpm(t))
                        for t in (0.0, 25.0, 56.5, 80.0, 100.0))
        print(f"  idle {RPM_IDLE}/{TD_IDLE}  max {RPM_MAX}/{TD_MAX}  "
              f"worst rpm difference {worst_rpm:.6f}")
        check(worst_rpm < 1e-9,
              f"rpm surrogate has drifted from throttle_dynamics by {worst_rpm}")
        check(RPM_IDLE == TD_IDLE and RPM_MAX == TD_MAX,
              "rpm endpoints disagree with throttle_dynamics")
    except ImportError as exc:
        print(f"  throttle_dynamics not importable here ({exc}); "
              f"cross-check skipped, NOT passed")
    print(f"  80% throttle -> {rpm_for_throttle(80.0):.0f} rpm "
          f"(deck reference point is 5000)")
    check(near(rpm_for_throttle(80.0), 5000.0, 1e-9),
          "80% throttle must give exactly 5000 rpm to match the deck reference")

    print("\nCASE 9  per-cylinder faults, and why EGT alone cannot diagnose them")
    base = solve(throttle_pct=95.0, altitude_ft=6000.0, oat_c=10.0)
    print(f"  healthy    spread {base.egt_spread_c:5.1f} C  "
          f"{base.brake_power_kw:6.2f} kW  vib {base.vib_rms_g:.3f} g  "
          f"EGT3 {base.egt_c[2]:.0f} C")
    check(base.egt_spread_c < 30.0,
          f"healthy EGT spread {base.egt_spread_c:.1f} C is already high")
    spreads: List[float] = []
    powers: List[float] = []
    vibs: List[float] = []
    for sev, note in ((0.06, "small trim loss"), (0.15, "moderate"),
                      (0.28, "severe, as ENGINE-TWIN injects")):
        trim = [1.0, 1.0, 1.0 - sev, 1.0]
        o = solve(throttle_pct=95.0, altitude_ft=6000.0, oat_c=10.0,
                  fault=FaultState(cylinder_fuel_trim=trim,
                                   label=f"LEAN_MIXTURE_CYL3_{int(sev * 100)}"))
        spreads.append(o.egt_spread_c)
        powers.append(o.brake_power_kw)
        vibs.append(o.vib_rms_g)
        d_egt = o.egt_c[2] - base.egt_c[2]
        d_pw = 100.0 * (o.brake_power_kw - base.brake_power_kw) / base.brake_power_kw
        print(f"  cyl3 -{sev:.0%} {note:28s} spread {o.egt_spread_c:5.1f} C  "
              f"dEGT3 {d_egt:+6.1f} C  power {d_pw:+5.2f}%  "
              f"vib {o.vib_rms_g:.3f} g")
        check(o.egt_spread_c > base.egt_spread_c,
              f"a {sev:.0%} trim cut on cylinder 3 did not widen the EGT spread")
        check(d_pw < 0.0, f"a {sev:.0%} trim cut did not reduce power")
    check(all(b <= a + 1e-9 for a, b in zip(powers, powers[1:])),
          "power is not monotonically falling with fault severity, so no "
          "channel is left that can rank severity")
    non_monotonic = not all(a <= b for a, b in zip(spreads, spreads[1:]))
    print(f"  EGT spread across severities: "
          f"{[round(s, 1) for s in spreads]} -> "
          f"{'NOT monotonic, as expected' if non_monotonic else 'monotonic'}")
    print("  NOTE EGT peaks slightly lean of stoichiometric, so a cylinder "
          "driven far lean crosses the peak and cools again. Power falls "
          "monotonically; EGT spread does not. This is real, and it is the "
          "reason a single-channel EGT rule is not a diagnosis.")
    check(any(c["id"] == "egt_severity_not_monotonic" for c in mvem_caveats()),
          "the non-monotonic EGT finding must be a declared caveat")

    print("\nCASE 10  the mistuned holdout must differ materially")
    m = mistuned()
    print(f"  {'point':>22} {'kW nom':>7} {'kW mis':>7} {'d%':>6} "
          f"{'EGT nom':>8} {'EGT mis':>8} {'coolant d':>10}")
    max_dev = 0.0
    for label, kw in ((" SL take-off", dict(throttle_pct=100.0, oat_c=30.0,
                                            rpm=RPM_MAX_TAKEOFF)),
                      (" 6000 ft cruise", dict(throttle_pct=80.0,
                                               altitude_ft=6000.0, oat_c=10.0)),
                      (" 15000 ft cruise", dict(throttle_pct=90.0,
                                                altitude_ft=15000.0,
                                                oat_c=-15.0))):
        a1 = solve(**kw)
        b1 = solve(**kw, fault=m)
        d = 100.0 * (b1.brake_power_kw - a1.brake_power_kw) / a1.brake_power_kw
        max_dev = max(max_dev, abs(d))
        print(f"  {label:>22} {a1.brake_power_kw:7.1f} {b1.brake_power_kw:7.1f} "
              f"{d:+6.1f} {a1.egt_mean_c:8.0f} {b1.egt_mean_c:8.0f} "
              f"{b1.coolant_temp_out_c - a1.coolant_temp_out_c:+9.1f}")
        check(abs(d) > 3.0,
              f"{label.strip()}: mistuned model differs by only {d:+.1f}%, too "
              f"close to the nominal to serve as an independent holdout")
    print(f"  worst power deviation {max_dev:.1f}% -- this is the gap the "
          f"models must survive, and the reason to report two accuracies")

    print("\nCASE 11  refusals")
    for label, fn in (
        ("above ceiling", lambda: solve(100.0, altitude_ft=CEILING_FT + 500.0)),
        ("absurd altitude", lambda: solve(100.0, altitude_ft=-5000.0)),
        ("ambient too hot", lambda: solve(100.0, oat_c=60.0)),
        ("ambient too cold", lambda: solve(100.0, oat_c=-50.0)),
        ("overspeed", lambda: solve(100.0, rpm=9000.0)),
        ("bad trim length", lambda: solve(
            100.0, fault=FaultState(cylinder_fuel_trim=[1.0, 1.0]))),
        ("negative trim", lambda: solve(
            100.0, fault=FaultState(cylinder_fuel_trim=[1.0, -0.2, 1.0, 1.0]))),
    ):
        try:
            fn()
            check(False, f"{label} was accepted")
            print(f"  {label:18s} -> ACCEPTED (wrong)")
        except MvemError:
            print(f"  {label:18s} -> refused")
        except atm.AtmosphereError:
            print(f"  {label:18s} -> refused by atmosphere")

    print("\nCASE 12  schema coverage: every declared channel is populated")
    o = solve(throttle_pct=85.0, altitude_ft=6000.0, oat_c=10.0,
              humidity_pct=40.0)
    d = o.to_schema_dict()
    print(f"  to_schema_dict emits {len(d)} fields, extras() holds "
          f"{len(o.extras())} more with unconfirmed names")
    zeros = [k for k, v in d.items()
             if isinstance(v, (int, float)) and v == 0.0]
    print(f"  fields still exactly zero: {zeros or 'none'}")
    for k in ("manifold_pressure_kpa", "bus_voltage_v", "vib_rms_g",
              "egt_1_c", "egt_4_c", "cht_1_c", "cht_4_c", "egt_spread_c"):
        check(k in d, f"schema field {k} missing from to_schema_dict")
        check(isinstance(d.get(k), float) and d[k] != 0.0,
              f"{k} is still zero -- this is one of the six dead UI tiles")
    try:
        from shared.schema import TelemetryPayload
        probe = TelemetryPayload()
        unknown = [k for k in d if not hasattr(probe, k)]
        print(f"  field names not present on TelemetryPayload: "
              f"{unknown or 'none'}")
        check(not unknown,
              f"to_schema_dict emits names the schema does not declare: "
              f"{unknown}")
        extras_known = [k for k in o.extras() if hasattr(probe, k)]
        if extras_known:
            print(f"  extras() names that DO exist on the schema and should be "
                  f"promoted: {extras_known}")
    except ImportError as exc:
        print(f"  schema not importable here ({exc}); name check skipped")

    print("\nCASE 13  fuel density agreement with the ingestion adapter")
    try:
        from node1_ingestion.adapter import FUEL_DENSITY_KG_PER_L as ADP
        d_mog = 100.0 * abs(ADP - FUEL_DENSITY_MOGAS_KG_PER_L) / \
            FUEL_DENSITY_MOGAS_KG_PER_L
        d_avg = 100.0 * abs(ADP - FUEL_DENSITY_AVGAS_KG_PER_L) / \
            FUEL_DENSITY_AVGAS_KG_PER_L
        match = "MOGAS" if d_mog < d_avg else "AVGAS"
        print(f"  adapter {ADP} -> {d_mog:.2f}% from MOGAS "
              f"({FUEL_DENSITY_MOGAS_KG_PER_L}), {d_avg:.2f}% from AVGAS "
              f"({FUEL_DENSITY_AVGAS_KG_PER_L}); closest match {match}")
        print(f"  this module generates {FUEL_TYPE}. If the adapter means a "
              f"different fuel, that is a declared choice, not a bug -- but "
              f"the session must record which.")
        check(min(d_mog, d_avg) < 1.0,
              f"adapter fuel density {ADP} matches NEITHER declared fuel "
              f"(MOGAS {FUEL_DENSITY_MOGAS_KG_PER_L}, AVGAS "
              f"{FUEL_DENSITY_AVGAS_KG_PER_L}), so its provenance is unknown")
    except ImportError as exc:
        print(f"  adapter not importable here ({exc}); comparison skipped")

    print("\nCASE 14  declared caveats")
    cav = mvem_caveats()
    for c in cav:
        print(f"  {c['id']:38s} verified={c['verified']}")
    for must in ("wot_calibration_only", "ve_afr_not_separable",
                 "cht_is_derived_not_measured", "heat_split_is_judgement",
                 "mistuned_holdout_available", "fuel_type_changes_density",
                 "no_coolant_boiling_model", "egt_severity_not_monotonic",
                 "thermostat_pinned_channels_carry_no_signal",
                 "no_hot_day_derate",
                 "disagrees_with_existing_training_data"):

        check(any(c["id"] == must for c in cav),
              f"caveat {must} is missing and must not be dropped")
    check(any(c["id"] == "ve_afr_not_separable" and not c["verified"]
              for c in cav),
          "the VE/AFR separability caveat must stay UNVERIFIED")
    unver = sum(1 for c in cav if not c["verified"])
    print(f"  {len(cav)} caveats, {unver} of them unverified")

    if fails:
        print(f"\nMVEM SELF-CHECK FAILED ({len(fails)} problem(s)):")
        for msg in fails:
            print(f"  - {msg}")
        raise SystemExit(1)
    print("\nMVEM SELF-CHECK OK")
    print("  calibrated to [B] Table 4, bounded by [A] section 2.1")
    print("  no model import, no ML: this is a data SOURCE, not a consumer")
    print("  mistuned() exists so the accuracy number can mean something")


if __name__ == "__main__":
    _self_test()
