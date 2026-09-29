import io, re, shutil, textwrap
P = r"node3_service\api.py"
src = io.open(P, encoding="utf-8-sig").read()

m = re.search(r'^([ \t]*)@app\.get\("/sim/fleet", tags=\["simulator"\]\)',
              src, re.M)
if not m:
    raise SystemExit("MISS  anchor not found")
pad = m.group(1)
print("anchor indent = %d spaces" % len(pad))

owner = None
for mm in re.finditer(r'^def\s+(\w+)', src[:m.start()], re.M):
    owner = mm.group(1)
print("enclosing def =", owner)

if "/sim/mission/plan" in src:
    raise SystemExit("ok    endpoint already present")

body = '''@app.post("/sim/mission/plan", tags=["simulator"])
def sim_mission_plan(tk_lat: float, tk_lon: float,
                     ld_lat: float, ld_lon: float,
                     ac_lat: float, ac_lon: float,
                     area_radius_km: float = 40.0,
                     target_h: float = 30.0,
                     tasking: str = "high_surveillance",
                     date: str = "",
                     hour: int = 12) -> dict[str, Any]:
    """Plan a sortie. Does not fly it -- this is what the map draws.

    Weather is best effort. If the fetch fails the profile falls back to
    ISA and the source field says so; the caller is told, not protected.
    """
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    import shared.mission_profile as mp
    import shared.mission_weather as mw

    try:
        plan = mp.build((tk_lat, tk_lon), (ld_lat, ld_lon), (ac_lat, ac_lon),
                        area_radius_km=area_radius_km, target_h=target_h,
                        tasking=tasking)
    except ValueError as exc:
        raise HTTPException(422, str(exc))

    plan.route = mp.route_points((tk_lat, tk_lon), (ld_lat, ld_lon),
                                 (ac_lat, ac_lon), area_radius_km)
    plan.orbit = (ac_lat, ac_lon, area_radius_km)

    wx = {"source": "isa", "notes": ["no date given"]}
    if date:
        try:
            wx = mw.apply_to_plan(plan, plan.route, date, hour=hour)
        except Exception as exc:                      # network, parse, range
            wx = {"source": "isa", "notes": ["weather unavailable: %s" % exc]}

    return {"route": [[round(a, 5), round(b, 5)] for a, b in plan.route],
            "orbit": {"lat": ac_lat, "lon": ac_lon,
                      "radius_km": area_radius_km},
            "phases": plan.phases,
            "total_h": round(plan.total_h, 3),
            "transit_km": round(plan.transit_km, 1),
            "loiter_h": round(plan.loiter_h, 3),
            "tasking": tasking,
            "date": date,
            "weather": wx,
            "clamped": plan.clamped,
            "setpoints": [{"t_s": s.t_s, "throttle_pct": s.throttle_pct,
                           "altitude_ft": s.altitude_ft, "oat_c": s.oat_c}
                          for s in plan.profile],
            "caveat": ("Plan only. Level legs, constant throttle per phase, "
                       "no wind and no bank angle. Past dates use surface "
                       "temperature with an ISA lapse rate, not measured "
                       "upper air.")}

'''

shutil.copy2(P, P + ".bak_mplan2")
block = textwrap.indent(body, pad) + "\n"
src = src[:m.start()] + block + src[m.start():]
io.open(P, "w", encoding="utf-8", newline="\n").write(src)
print("ok    POST /sim/mission/plan added, indented to match")
