import io, shutil
P = r"shared\mission_weather.py"
shutil.copy2(P, P + ".bak_lapse")
src = io.open(P, encoding="utf-8").read()

old = src[src.index("def sample("):src.index("def apply_to_plan(")]
new = '''LAPSE_C_PER_M = 0.0065          # ISA troposphere lapse rate


def sample(lat: float, lon: float, date: str, alt_ft: float,
           hour: int = 12) -> Tuple[float, str]:
    """Temperature in C at one place, date and altitude. (value, source).

    Two paths, because Open-Meteo splits them:
      - past dates: archive serves temperature_2m only, no upper air, so
        we take the surface reading and apply the ISA lapse rate. The
        seasonal signal lives in the surface value, which is the point.
      - next ~16 days: forecast serves real pressure-level temperature,
        so we use it directly.
    """
    today = _dt.date.today()
    want = _dt.date.fromisoformat(date)
    past = want < today - _dt.timedelta(days=5)
    alt_m = alt_ft * 0.3048

    if not past:
        lvl = _nearest_level(alt_ft)
        q = {"latitude": "%.4f" % lat, "longitude": "%.4f" % lon,
             "start_date": date, "end_date": date,
             "hourly": "temperature_%dhPa" % lvl}
        data = _get(FORECAST + "?" + urllib.parse.urlencode(q))
        vals = []
        if data and "hourly" in data:
            vals = [v for v in (data["hourly"].get("temperature_%dhPa" % lvl)
                                or []) if v is not None]
        if vals:
            v = vals[hour] if hour < len(vals) else vals[len(vals) // 2]
            return float(v), "forecast"
        # fall through to the archive path if the window rejected us

    q = {"latitude": "%.4f" % lat, "longitude": "%.4f" % lon,
         "start_date": date, "end_date": date, "hourly": "temperature_2m"}
    data = _get(ARCHIVE + "?" + urllib.parse.urlencode(q))
    vals = []
    if data and "hourly" in data:
        vals = [v for v in (data["hourly"].get("temperature_2m") or [])
                if v is not None]
    if not vals:
        return isa_temperature_c(alt_m), "isa"
    sfc = float(vals[hour] if hour < len(vals) else vals[len(vals) // 2])
    elev = float((data or {}).get("elevation", 0.0) or 0.0)
    return sfc - LAPSE_C_PER_M * max(0.0, alt_m - elev), "archive+lapse"


'''
io.open(P, "w", encoding="utf-8", newline="\n").write(src.replace(old, new, 1))
print("ok -- archive uses surface + lapse, forecast uses pressure levels")
