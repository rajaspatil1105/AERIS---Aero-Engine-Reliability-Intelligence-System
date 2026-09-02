"""
AERIS Phase 1 — universal telemetry data contract.

This module is the single shared schema between Node 1 (ingestion/DSP),
Node 2 (twin core), Node 3 (gateway/persistence) and Node 4 (GCS UI).

STATUS / HONESTY NOTES
----------------------
* Field names here are an AERIS-internal canonical contract. They have NOT
  been reconciled against the Cantera simulator's emitted JSON. All name
  translation belongs in node1_ingestion/simulator_bridge.py, NOT here.
* Parameter set is modelled on a Rotax 915 iS class engine: 4 cylinders,
  turbocharged with wastegate, dual redundant ECU lanes (A/B), twin lambda
  sensors, reduction gearbox driving the propeller.
* Defaults are neutral placeholders so test objects can be constructed
  without supplying all fields. A default is NOT a measurement.
* Fields documented as "Node 1 DSP output" default to None. None means
  "not computed yet". It must never be interpreted as 0.0 downstream.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field


class EngineState(str, Enum):
    """Coarse engine operating mode, as reported by the source."""

    UNKNOWN = "UNKNOWN"
    STOPPED = "STOPPED"
    CRANKING = "CRANKING"
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    SHUTDOWN = "SHUTDOWN"


class TelemetryPayload(BaseModel):
    """One telemetry frame. Nominal rate 10 Hz.

    Declaration order below defines the canonical column order used for
    persistence and for any positional serialisation. Do not reorder.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",       # unknown source keys dropped; see unknown_fields()
        validate_assignment=True,
    )

    # ---------------------------------------------------------------- #
    # METADATA (5)
    # ---------------------------------------------------------------- #
    timestamp: float = Field(0.0, description="Unix epoch seconds, source clock")
    frame_id: int = Field(0, description="Monotonic frame counter from source")
    session_id: str = Field("unset", description="Mission/session identifier")
    flight_time_hr: float = Field(
        0.0, description="Cumulative engine running hours. RUL feature."
    )
    engine_state: EngineState = Field(EngineState.UNKNOWN)

    # ---------------------------------------------------------------- #
    # MECHANICAL / PERFORMANCE (10)
    # ---------------------------------------------------------------- #
    rpm: float = Field(0.0, description="Crankshaft speed, rev/min")
    throttle_pct: float = Field(0.0, description="Throttle lever position, 0-100")
    manifold_pressure_kpa: float = Field(0.0, description="Intake manifold abs pressure")
    plenum_temp_c: float = Field(0.0, description="Post-intercooler plenum air temp")
    torque_nm: float = Field(0.0, description="Crankshaft torque")
    power_kw: float = Field(0.0, description="Shaft power")
    prop_rpm: float = Field(0.0, description="Propeller speed after reduction gearbox")
    turbo_rpm: float = Field(0.0, description="Turbocharger shaft speed")
    wastegate_pct: float = Field(0.0, description="Wastegate actuator position, 0-100")
    gearbox_temp_c: float = Field(0.0, description="Reduction gearbox temperature")

    # ---------------------------------------------------------------- #
    # THERMAL (14)
    # ---------------------------------------------------------------- #
    cht_1_c: float = Field(0.0, description="Cylinder head temp, cyl 1")
    cht_2_c: float = Field(0.0, description="Cylinder head temp, cyl 2")
    cht_3_c: float = Field(0.0, description="Cylinder head temp, cyl 3")
    cht_4_c: float = Field(0.0, description="Cylinder head temp, cyl 4")
    egt_1_c: float = Field(0.0, description="Exhaust gas temp, cyl 1")
    egt_2_c: float = Field(0.0, description="Exhaust gas temp, cyl 2")
    egt_3_c: float = Field(0.0, description="Exhaust gas temp, cyl 3")
    egt_4_c: float = Field(0.0, description="Exhaust gas temp, cyl 4")
    egt_spread_c: float = Field(
        0.0, description="Max-min EGT across cylinders. Misfire indicator."
    )
    coolant_temp_in_c: float = Field(0.0, description="Coolant temp entering engine")
    coolant_temp_out_c: float = Field(0.0, description="Coolant temp leaving engine")
    coolant_pressure_kpa: float = Field(0.0, description="Coolant circuit pressure")
    coolant_flow_lpm: float = Field(0.0, description="Coolant volumetric flow")
    oil_cooler_out_temp_c: float = Field(0.0, description="Oil temp leaving oil cooler")

    # ---------------------------------------------------------------- #
    # FUEL / INJECTION (11)
    # ---------------------------------------------------------------- #
    fuel_flow_lph: float = Field(0.0, description="Instantaneous fuel flow, L/h")
    fuel_pressure_kpa: float = Field(0.0, description="Low-pressure fuel supply")
    fuel_rail_pressure_kpa: float = Field(0.0, description="Injector rail pressure")
    fuel_temp_c: float = Field(0.0, description="Fuel temperature")
    fuel_used_l: float = Field(0.0, description="Cumulative fuel consumed")
    fuel_remaining_l: float = Field(0.0, description="Estimated fuel on board")
    injector_pulse_width_ms: float = Field(0.0, description="Commanded injection duration")
    injection_timing_deg: float = Field(0.0, description="Injection timing, deg BTDC")
    lambda_1: float = Field(0.0, description="Lambda sensor 1, bank 1")
    lambda_2: float = Field(0.0, description="Lambda sensor 2, bank 2")
    misfire_count: int = Field(0, description="ECU-reported misfire events this frame")

    # ---------------------------------------------------------------- #
    # LUBRICATION (4)
    # ---------------------------------------------------------------- #
    oil_pressure_kpa: float = Field(0.0, description="Oil gallery pressure")
    oil_temp_c: float = Field(0.0, description="Oil temperature")
    oil_level_pct: float = Field(0.0, description="Oil tank level, 0-100")
    oil_filter_dp_kpa: float = Field(0.0, description="Oil filter differential pressure")

    # ---------------------------------------------------------------- #
    # ELECTRICAL (10)
    # ---------------------------------------------------------------- #
    bus_voltage_v: float = Field(0.0, description="Main DC bus voltage")
    alternator_current_a: float = Field(0.0, description="Alternator output current")
    alternator_temp_c: float = Field(0.0, description="Alternator body temperature")
    battery_voltage_v: float = Field(0.0, description="Battery terminal voltage")
    battery_current_a: float = Field(0.0, description="Battery current, + = discharge")
    battery_soc_pct: float = Field(0.0, description="State of charge, 0-100")
    battery_temp_c: float = Field(0.0, description="Battery temperature")
    ecu_lane_a_ok: bool = Field(True, description="ECU lane A health flag")
    ecu_lane_b_ok: bool = Field(True, description="ECU lane B health flag")
    fuel_pump_current_a: float = Field(0.0, description="Electric fuel pump current")

    # ---------------------------------------------------------------- #
    # VIBRATION (8)  -- 1x/2x/3x/f0/crest are Node 1 DSP outputs
    # ---------------------------------------------------------------- #
    vib_rms_g: float = Field(0.0, description="Broadband vibration RMS, g")
    vib_peak_g: float = Field(0.0, description="Peak vibration amplitude, g")
    vib_crest_factor: Optional[float] = Field(
        None, description="Node 1 DSP output: peak/RMS. None = not computed."
    )
    vib_f0_hz: Optional[float] = Field(
        None, description="Node 1 DSP output: rotational fundamental, RPM/60."
    )
    vib_1x_g: Optional[float] = Field(
        None, description="Node 1 DSP output: 1x order amplitude. None = not computed."
    )
    vib_2x_g: Optional[float] = Field(
        None, description="Node 1 DSP output: 2x order amplitude. None = not computed."
    )
    vib_3x_g: Optional[float] = Field(
        None, description="Node 1 DSP output: 3x order amplitude. None = not computed."
    )
    vib_bearing_band_g: Optional[float] = Field(
        None, description="Node 1 DSP output: high-frequency band energy."
    )

    # ---------------------------------------------------------------- #
    # ENVIRONMENTAL (6)
    # ---------------------------------------------------------------- #
    altitude_m: float = Field(0.0, description="Pressure altitude, metres")
    oat_c: float = Field(15.0, description="Outside air temperature")
    ambient_pressure_kpa: float = Field(101.325, description="Static ambient pressure")
    airspeed_ms: float = Field(0.0, description="True airspeed")
    air_density_kgm3: float = Field(1.225, description="Ambient air density")
    humidity_pct: float = Field(0.0, description="Relative humidity, 0-100")

    # ================================================================ #
    # TRANSPORT-ONLY FIELD -- NOT one of the 68 columns.
    # A variable-length array cannot be a scalar DB column. Node 1
    # consumes this to produce the vib_* DSP outputs above, then it is
    # dropped before persistence.
    # ================================================================ #
    vib_samples: Optional[List[float]] = Field(
        None,
        exclude=True,
        description="Raw vibration time-series window from the source. Not persisted.",
    )


# -------------------------------------------------------------------- #
# Canonical column contract
# -------------------------------------------------------------------- #

_NON_COLUMN_FIELDS = frozenset({"vib_samples"})

COLUMN_NAMES: Tuple[str, ...] = tuple(
    name
    for name in TelemetryPayload.model_fields
    if name not in _NON_COLUMN_FIELDS
)

EXPECTED_COLUMN_COUNT = 68

if len(COLUMN_NAMES) != EXPECTED_COLUMN_COUNT:
    raise AssertionError(
        f"AERIS schema contract violated: expected {EXPECTED_COLUMN_COUNT} "
        f"columns, found {len(COLUMN_NAMES)}. Adding or removing a telemetry "
        f"field changes the shared contract for all four nodes and the "
        f"database schema. Update EXPECTED_COLUMN_COUNT deliberately."
    )


def unknown_fields(raw: Dict[str, Any]) -> List[str]:
    """Return keys in `raw` that the schema does not define.

    Because the model is configured extra="ignore", unrecognised source keys
    are dropped during validation. Node 1 should call this and log the result
    so that dropped data is visible rather than silent.
    """
    known = set(TelemetryPayload.model_fields)
    return sorted(k for k in raw if k not in known)


def to_column_row(payload: TelemetryPayload) -> Tuple[Any, ...]:
    """Flatten a payload into a tuple ordered exactly as COLUMN_NAMES.

    Enums are reduced to their string value. None is preserved as None so
    that "not computed" survives into storage as NULL.
    """
    values = []
    for name in COLUMN_NAMES:
        v = getattr(payload, name)
        values.append(v.value if isinstance(v, Enum) else v)
    return tuple(values)


if __name__ == "__main__":
    # Self-check only. Prints structure; asserts nothing about real data.
    p = TelemetryPayload()
    print(f"pydantic model     : {TelemetryPayload.__name__}")
    print(f"declared fields    : {len(TelemetryPayload.model_fields)}")
    print(f"contract columns   : {len(COLUMN_NAMES)}")
    print(f"transport-only     : {sorted(_NON_COLUMN_FIELDS)}")

    row = to_column_row(p)
    print(f"row tuple length   : {len(row)}")

    dsp_pending = [n for n in COLUMN_NAMES if getattr(p, n) is None]
    print(f"None by default    : {dsp_pending}")

    p2 = TelemetryPayload(rpm=5500.0, throttle_pct=85.0, vib_samples=[0.1, -0.2, 0.3])
    print(f"partial construct  : rpm={p2.rpm} samples={len(p2.vib_samples)}")
    print(f"vib_samples in dump: {'vib_samples' in p2.model_dump()}")

    print(f"unknown_fields test: {unknown_fields({'rpm': 1, 'bogus_key': 2})}")
    print("SCHEMA SELF-CHECK OK")
