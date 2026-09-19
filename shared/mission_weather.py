"""Real weather along a mission route, from Open-Meteo. No API key.

Source is picked from the date: archive for the past (real recorded
weather, a few days behind), forecast for up to about 16 days ahead.
There is no forecast beyond that -- to rehearse next December, use last
December, which is what this is for.

Falls back to ISA if the network is down. The simulator must never
refuse to run because weather did not load.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import pathlib
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

from shared.atmosphere import isa_temperature_c
from shared import mission_engine as me

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"
CACHE = pathlib.Path.home() / ".aeris" / "wx_cache"
TIMEOUT_S = 12.0

# Open-Meteo gives temperature on pressure levels, in hPa. Map the
# altitudes we fly to the nearest level.
LEVELS_HPA = (1000, 925, 850, 700, 600, 500, 400, 300, 250, 200)
LEVEL_ALT_FT = {1000: 360, 925: 2500, 850: 4780, 700: 9880, 600: 13800,
                500: 18290, 400: 23570, 300: 30070, 250: 33990, 200: 38660}


def _nearest_level(alt_ft: float) -> int:
    return min(LEVELS_HPA, key=lambda h: abs(LEVEL_ALT_FT[h] - alt_ft))


def _cache_path(url: str) -> pathlib.Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    return CACHE / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".json")


def _get(url: str) -> Optional[dict]:
    p = _cache_path(url)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_S) as r:
            data = json.loads(r.read().decode("utf-8"))
        p.write_text(json.dumps(data), encoding="utf-8")
        return data
    except Exception as exc:
        print("[weather] fetch failed (%s); using ISA" % exc)
        return None


LAPSE_C_PER_M = 0.0065          # ISA troposphere lapse rate


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


def apply_to_plan(plan, waypoints: List[Tuple[float, float]],
                  date: str, hour: int = 12) -> Dict[str, object]:
    """Overwrite each setpoint's oat_c with real weather. Mutates plan."""
    notes: List[str] = []
    src_seen: set = set()
    n = len(plan.profile)
    for i, sp in enumerate(plan.profile):
        # position by TIME along the sortie, not by setpoint index:
        # the loiter setpoints cluster and index fraction lies.
        _end = float(plan.profile[-1].t_s) or 1.0
        frac = min(1.0, max(0.0, float(sp.t_s) / _end))
        wi = min(len(waypoints) - 1, int(frac * (len(waypoints) - 1) + 0.5))
        lat, lon = waypoints[wi]
        t, src = sample(lat, lon, date, sp.altitude_ft, hour)
        src_seen.add(src)
        if t < me.ENV_OAT_MIN_C or t > me.ENV_OAT_MAX_C:
            notes.append("%.1f C at %.0f ft clamped into the trained range"
                         % (t, sp.altitude_ft))
            t = max(me.ENV_OAT_MIN_C, min(me.ENV_OAT_MAX_C, t))
        plan.profile[i] = type(sp)(
            t_s=sp.t_s, throttle_pct=sp.throttle_pct,
            altitude_ft=sp.altitude_ft, oat_c=round(t, 2),
            humidity_pct=sp.humidity_pct, airspeed_ms=sp.airspeed_ms,
            electrical_load_a=sp.electrical_load_a)
    return {"sources": sorted(src_seen), "clamped": notes,
            "date": date, "hour_utc": hour}


if __name__ == "__main__":
    from shared import mission_profile as mp
    TO, LD, AC = (26.251, 73.049), (26.889, 70.865), (27.200, 70.200)
    for date in ("2025-12-15", "2025-06-15"):
        plan = mp.build(TO, LD, AC, target_h=30.0)
        info = apply_to_plan(plan, [TO, AC, AC, LD], date)
        oats = [sp.oat_c for sp in plan.profile]
        print("%s  source=%s  OAT %.1f to %.1f C  clamped=%d"
              % (date, ",".join(info["sources"]), min(oats), max(oats),
                 len(info["clamped"])))
        for sp in plan.profile[:4]:
            print("    %6.0f s  %6.0f ft  %6.1f C"
                  % (sp.t_s, sp.altitude_ft, sp.oat_c))
