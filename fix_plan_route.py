import io, shutil
edits = 0
P = r"shared\mission_profile.py"
shutil.copy2(P, P + ".bak_route")
src = io.open(P, encoding="utf-8").read()

old = "    clamped: List[str]"
new = ("    clamped: List[str]\n"
       "    route: list = None          # [(lat, lon), ...] flown track\n"
       "    orbit: tuple = None         # (lat, lon, radius_km) of the area")
if old in src and "route: list" not in src:
    src = src.replace(old, new, 1); edits += 1; print("ok    plan carries geography")
else:
    print("MISS  dataclass fields")

helper = '''

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

'''
anchor = 'if __name__ == "__main__":'
if "def route_points" in src:
    print("ok    route_points already present")
elif anchor in src:
    src = src.replace(anchor, helper.lstrip("\n") + "\n" + anchor, 1)
    edits += 1; print("ok    route_points helper")
else:
    src = src + helper; edits += 1; print("ok    route_points appended")

io.open(P, "w", encoding="utf-8", newline="\n").write(src)

W = r"shared\mission_weather.py"
shutil.copy2(W, W + ".bak_frac")
w = io.open(W, encoding="utf-8").read()
oldf = "        frac = i / max(1, n - 1)"
newf = ("        # position by TIME along the sortie, not by setpoint index:\n"
        "        # the loiter setpoints cluster and index fraction lies.\n"
        "        _end = float(plan.profile[-1].t_s) or 1.0\n"
        "        frac = min(1.0, max(0.0, float(sp.t_s) / _end))")
if oldf in w:
    w = w.replace(oldf, newf, 1); edits += 1; print("ok    weather sampled by time fraction")
else:
    print("MISS  frac line -- paste it verbatim")
io.open(W, "w", encoding="utf-8", newline="\n").write(w)
print("%d edits" % edits)
