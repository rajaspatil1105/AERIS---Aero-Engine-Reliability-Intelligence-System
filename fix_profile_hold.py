import io, re, shutil
P = r"shared\mission_profile.py"
shutil.copy2(P, P + ".bak_hold")
src = io.open(P, encoding="utf-8").read()

old = src[src.index("    legs = ["):src.index("    orbits =")]
new = '''    # (name, duration, throttle, altitude at start, altitude at end).
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

'''
io.open(P, "w", encoding="utf-8", newline="\n").write(src.replace(old, new, 1))
print("ok -- each leg now holds its throttle instead of ramping across")
