import io, shutil
P = r"shared\mission_profile.py"
src = io.open(P, encoding="utf-8-sig").read()
if "AIRFRAMES" in src:
    raise SystemExit("ok    already present")
shutil.copy2(P, P + ".bak_airframe")

block = '''

# ==================================================================== #
# Airframes -- published specs, not derived from our fuel model
# ==================================================================== #

AIRFRAMES = {
    "heron_mk2": {"name": "IAI Heron Mk II", "endurance_h": 45.0,
                  "reserve_h": 2.0, "transit_kt": 120.0, "loiter_kt": 70.0,
                  "ceiling_ft": 35000.0},
}


def _gc_km(a, b) -> float:
    """Great-circle distance between (lat, lon) pairs, kilometres."""
    import math
    la1, lo1, la2, lo2 = [math.radians(float(x))
                          for x in (a[0], a[1], b[0], b[1])]
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2.0 * 6371.0088 * math.asin(min(1.0, math.sqrt(h)))


def auto_target_h(takeoff, landing, area_centre,
                  airframe: str = "heron_mk2", target_h: float = 0.0):
    """Sortie length from the airframe, unless the caller forces one.

    Returns (hours, info). The endurance figure is the manufacturer's
    published number. We do not model fuel capacity, so this is an
    assumption about the airframe bolted onto a simulation of the
    engine -- it is not a result.
    """
    af = AIRFRAMES.get(airframe)
    if af is None:
        raise ValueError("unknown airframe %r; have %s"
                         % (airframe, ", ".join(sorted(AIRFRAMES))))
    out_km = _gc_km(takeoff, area_centre)
    home_km = _gc_km(area_centre, landing)
    transit_h = (out_km + home_km) / (af["transit_kt"] * 1.852)
    usable_h = af["endurance_h"] - af["reserve_h"]
    overhead_h = 0.6                      # climb, descent, recovery

    if target_h and target_h > 0:
        hours, source = float(target_h), "set by hand"
    else:
        hours, source = usable_h, "airframe endurance less reserve"
        if transit_h + overhead_h >= usable_h - 0.5:
            raise ValueError(
                "area is %.0f km away: %.1f h of transit leaves no useful "
                "time on station within %.0f h of endurance"
                % (out_km, transit_h, usable_h))

    return hours, {
        "airframe": airframe, "name": af["name"],
        "endurance_h": af["endurance_h"], "reserve_h": af["reserve_h"],
        "transit_kt": af["transit_kt"], "loiter_kt": af["loiter_kt"],
        "transit_km": round(out_km + home_km, 1),
        "transit_h": round(transit_h, 2),
        "hours": round(hours, 2), "source": source,
        "caveat": ("Endurance is the published airframe figure. The twin "
                   "models no fuel capacity, so duration is assumed, not "
                   "computed. Ceiling %.0f ft is unreachable here: the "
                   "baselines stop at 22800 ft."
                   % af["ceiling_ft"])}
'''

anchor = 'if __name__ == "__main__":'
src = (src.replace(anchor, block.lstrip("\n") + "\n\n" + anchor, 1)
       if anchor in src else src + block)
io.open(P, "w", encoding="utf-8", newline="\n").write(src)
print("ok    AIRFRAMES + auto_target_h")
