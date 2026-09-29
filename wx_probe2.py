import json, urllib.parse, urllib.request

def hit(tag, base, q):
    url = base + "?" + urllib.parse.urlencode(q)
    print("\n== %s" % tag)
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            d = json.loads(r.read().decode("utf-8"))
        u = d.get("hourly_units", {})
        h = d.get("hourly", {})
        for k, v in u.items():
            if k == "time":
                continue
            vals = [x for x in (h.get(k) or []) if x is not None]
            print("  %-22s units=%-8s non-null %d/%d  first=%s"
                  % (k, v, len(vals), len(h.get(k) or []),
                     vals[0] if vals else "-"))
    except Exception as exc:
        print("  EXCEPTION %s: %s" % (type(exc).__name__, exc))
        try:
            print("  " + exc.read().decode("utf-8")[:200])
        except Exception:
            pass

LL = {"latitude": "26.2510", "longitude": "73.0490"}
D = {"start_date": "2025-12-15", "end_date": "2025-12-15"}

hit("archive temperature_2m", "https://archive-api.open-meteo.com/v1/archive",
    dict(LL, **D, hourly="temperature_2m,surface_pressure"))
hit("archive pressure levels (era5 explicit)",
    "https://archive-api.open-meteo.com/v1/era5",
    dict(LL, **D, hourly="temperature_500hPa,temperature_700hPa"))
hit("forecast in-range pressure levels",
    "https://api.open-meteo.com/v1/forecast",
    dict(LL, hourly="temperature_500hPa,temperature_2m", forecast_days="3"))
