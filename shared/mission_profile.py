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

# Taskings differ in how hard the engine works. High surveillance loiters
# cold and lean and barely wears anything; low patrol sits in warm air at
# higher power, which is where season starts to matter.
TASKINGS = {
    "high_surveillance": {"transit_ft": 18000.0, "loiter_ft": 12000.0,
                          "loiter_thr": 62.0,
                          "note": "high-altitude ISR, gentle on the engine"},
    "low_patrol": {"transit_ft": 9000.0, "loiter_ft": 3000.0,
                   "loiter_thr": 75.0,
                   "note": "border patrol / convoy escort, warm and working"},
    "contested": {"transit_ft": 6000.0, "loiter_ft": 1500.0,
                  "loiter_thr": 85.0,
                  "note": "low and fast, hardest duty the envelope allows"},
}


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
    route: list = None          # [(lat, lon), ...] flown track
    orbit: tuple = None         # (lat, lon, radius_km) of the area


def _clamp(v: float, lo: float, hi: float, name: str, log: List[str]) -> float:
    if v < lo or v > hi:
        log.append("%s %.1f clamped into [%.1f, %.1f]" % (name, v, lo, hi))
        return max(lo, min(hi, v))
    return v


def build(takeoff, landing, area_centre, area_radius_km=40.0,
          target_h=30.0, thr_loiter=None,
          tasking="high_surveillance") -> MissionPlan:
    """takeoff/landing/area_centre are (lat, lon)."""
    log: List[str] = []
    tk = TASKINGS.get(tasking)
    if tk is None:
        raise ValueError("unknown tasking %r; have %s"
                         % (tasking, ", ".join(sorted(TASKINGS))))
    log.append("tasking %s -- %s" % (tasking, tk["note"]))
    transit_ft, loiter_ft = tk["transit_ft"], tk["loiter_ft"]
    if thr_loiter is None:
        thr_loiter = tk["loiter_thr"]
    out_km = great_circle_km(takeoff, area_centre)
    back_km = great_circle_km(area_centre, landing)

    climb_s = (transit_ft - ALT_GROUND_FT) / CLIMB_FPM * 60.0
    desc_s = abs(transit_ft - loiter_ft) / DESCENT_FPM * 60.0
    land_s = (loiter_ft - ALT_GROUND_FT) / DESCENT_FPM * 60.0
    out_s = out_km / GROUND_SPEED_KMH * 3600.0
    back_s = back_km / GROUND_SPEED_KMH * 3600.0

    fixed_s = 300.0 + climb_s + out_s + desc_s + back_s + land_s
    loiter_s = target_h * 3600.0 - fixed_s
    if loiter_s < 600.0:
        log.append("target %.1f h too short for the legs; loiter set to 10 min"
                   % target_h)
        loiter_s = 600.0

    a_t = _clamp(transit_ft, me.ENV_ALT_MIN_FT, me.ENV_ALT_MAX_FT,
                 "transit altitude", log)
    a_l = _clamp(loiter_ft, me.ENV_ALT_MIN_FT, me.ENV_ALT_MAX_FT,
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


def route_points(takeoff, landing, area_centre, area_radius_km=40.0,
                 n_orbit=24) -> list:
    """The track the aircraft actually flies, as (lat, lon) pairs.

    Takeoff -> area entry -> n_orbit points round the surveillance
    circle -> landing. Flat-earth offsets; fine at these distances,
    wrong near the poles.
    """
    import math
    lat0 = float(area_centre[0])
    dlat = area_radius_km / 111.32
    dlon = area_radius_km / (111.32 * max(0.2, math.cos(math.radians(lat0))))
    ring = []
    for k in range(n_orbit):
        a = 2.0 * math.pi * k / n_orbit
        ring.append((lat0 + dlat * math.cos(a),
                     float(area_centre[1]) + dlon * math.sin(a)))
    return ([(float(takeoff[0]), float(takeoff[1]))] + ring
            + [ring[0], (float(landing[0]), float(landing[1]))])


if __name__ == "__main__":
    for _tk in sorted(TASKINGS):
        _p = build((26.251, 73.049), (26.889, 70.865), (27.200, 70.200),
                   target_h=30.0, tasking=_tk)
        _lo = [x for x in _p.phases if x["phase"].startswith("loiter")][0]
        print("%-18s loiter %5.0f ft at %4.1f%%   %.2f h aloft"
              % (_tk, _lo["altitude_ft"], _lo["throttle_pct"], _p.total_h))
    print("")

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
