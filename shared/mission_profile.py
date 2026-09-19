"""Build a MALE surveillance sortie as a Setpoint profile.

Engine-focused, not a flight planner. Airspeed is NOT interpolated by
mission_engine._interp, so every phase flies at the reference airspeed and
leg times come from a nominal ground speed rather than the engine solution.
Endurance here is indicative.

oat_c is left None so the bridge fills ISA at altitude; weather overwrites
it later.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Tuple

from node1_ingestion.simulator_bridge import Setpoint
from shared import mission_engine as me

R_EARTH_KM = 6371.0
GROUND_SPEED_KMH = 120.0          # nominal MALE transit speed
CLIMB_FPM = 700.0
DESCENT_FPM = 500.0

THR_TAKEOFF, THR_CLIMB, THR_TRANSIT, THR_LOITER, THR_DESCENT = (
    95.0, 90.0, 78.0, 62.0, 35.0)
ALT_GROUND_FT, ALT_TRANSIT_FT, ALT_LOITER_FT = 500.0, 18000.0, 12000.0


def great_circle_km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2.0 * R_EARTH_KM * math.asin(min(1.0, math.sqrt(h)))


@dataclass
class MissionPlan:
    profile: List[Setpoint]
    phases: List[dict]
    total_h: float
    transit_km: float
    loiter_h: float
    clamped: List[str]


def _clamp(v: float, lo: float, hi: float, name: str, log: List[str]) -> float:
    if v < lo or v > hi:
        log.append("%s %.1f clamped into [%.1f, %.1f]" % (name, v, lo, hi))
        return max(lo, min(hi, v))
    return v


def build(takeoff, landing, area_centre, area_radius_km=40.0,
          target_h=30.0, thr_loiter=THR_LOITER) -> MissionPlan:
    """takeoff/landing/area_centre are (lat, lon)."""
    log: List[str] = []
    out_km = great_circle_km(takeoff, area_centre)
    back_km = great_circle_km(area_centre, landing)

    climb_s = (ALT_TRANSIT_FT - ALT_GROUND_FT) / CLIMB_FPM * 60.0
    desc_s = (ALT_TRANSIT_FT - ALT_LOITER_FT) / DESCENT_FPM * 60.0
    land_s = (ALT_LOITER_FT - ALT_GROUND_FT) / DESCENT_FPM * 60.0
    out_s = out_km / GROUND_SPEED_KMH * 3600.0
    back_s = back_km / GROUND_SPEED_KMH * 3600.0

    fixed_s = 300.0 + climb_s + out_s + desc_s + back_s + land_s
    loiter_s = target_h * 3600.0 - fixed_s
    if loiter_s < 600.0:
        log.append("target %.1f h too short for the legs; loiter set to 10 min"
                   % target_h)
        loiter_s = 600.0

    a_t = _clamp(ALT_TRANSIT_FT, me.ENV_ALT_MIN_FT, me.ENV_ALT_MAX_FT,
                 "transit altitude", log)
    a_l = _clamp(ALT_LOITER_FT, me.ENV_ALT_MIN_FT, me.ENV_ALT_MAX_FT,
                 "loiter altitude", log)
    t_l = _clamp(thr_loiter, me.ENV_THR_MIN_PCT, me.ENV_THR_MAX_PCT,
                 "loiter throttle", log)

    # (name, duration, throttle, altitude at start, altitude at end).
    # Each leg gets a point at BOTH ends or _interp ramps the whole way
    # across it -- that bug made a 26 h loiter drift 36% -> 62% throttle.
    legs = [("takeoff", 300.0, THR_TAKEOFF, ALT_GROUND_FT, ALT_GROUND_FT),
            ("climb", climb_s, THR_CLIMB, ALT_GROUND_FT, a_t),
            ("transit out", out_s, THR_TRANSIT, a_t, a_t),
            ("descend to loiter", desc_s, THR_DESCENT, a_t, a_l),
            ("loiter / surveillance", loiter_s, t_l, a_l, a_l),
            ("transit home", back_s, THR_TRANSIT, a_l, a_t),
            ("recovery", land_s, THR_DESCENT, a_t, ALT_GROUND_FT)]

    RAMP_S = 60.0          # throttle moves over a minute, then holds
    prof: List[Setpoint] = [Setpoint(t_s=0.0, throttle_pct=THR_TAKEOFF,
                                     altitude_ft=ALT_GROUND_FT)]
    phases, t = [], 0.0
    for name, dur, thr, a0, a1 in legs:
        ramp = min(RAMP_S, dur * 0.25)
        if ramp > 0.0:
            prof.append(Setpoint(t_s=t + ramp, throttle_pct=thr,
                                 altitude_ft=a0 + (ramp / dur) * (a1 - a0)))
        t += dur
        prof.append(Setpoint(t_s=t, throttle_pct=thr, altitude_ft=a1))
        phases.append({"phase": name, "ends_h": round(t / 3600.0, 2),
                       "throttle_pct": thr, "altitude_ft": a1})

    orbits = loiter_s / 3600.0 * GROUND_SPEED_KMH / max(
        1.0, 2.0 * math.pi * area_radius_km)
    log.append("area radius %.0f km -> about %.0f orbits during loiter"
               % (area_radius_km, orbits))

    return MissionPlan(prof, phases, t / 3600.0, out_km + back_km,
                       loiter_s / 3600.0, log)


if __name__ == "__main__":
    # Jodhpur -> border patrol box -> Jaisalmer
    plan = build((26.251, 73.049), (26.889, 70.865), (27.200, 70.200),
                 area_radius_km=40.0, target_h=30.0)
    print("total %.2f h   transit %.0f km   loiter %.2f h   setpoints %d"
          % (plan.total_h, plan.transit_km, plan.loiter_h, len(plan.profile)))
    for p in plan.phases:
        print("  %-22s ends %6.2f h  thr %5.1f%%  alt %7.0f ft"
              % (p["phase"], p["ends_h"], p["throttle_pct"], p["altitude_ft"]))
    for c in plan.clamped:
        print("  note: %s" % c)
