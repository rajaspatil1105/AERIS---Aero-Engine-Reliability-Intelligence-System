import json, pathlib, shutil, urllib.parse, urllib.request

CACHE = pathlib.Path.home() / ".aeris" / "wx_cache"
if CACHE.exists():
    shutil.rmtree(CACHE)
    print("cleared cache %s" % CACHE)

q = {"latitude": "26.2510", "longitude": "73.0490",
     "start_date": "2025-12-15", "end_date": "2025-12-15",
     "hourly": "temperature_500hPa"}
for tag, base in (("archive", "https://archive-api.open-meteo.com/v1/archive"),
                  ("forecast", "https://api.open-meteo.com/v1/forecast")):
    url = base + "?" + urllib.parse.urlencode(q)
    print("\n== %s\n%s" % (tag, url))
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            body = r.read().decode("utf-8")
        print("HTTP %s  %d bytes" % (r.status, len(body)))
        print(body[:400])
    except Exception as exc:
        print("EXCEPTION %s: %s" % (type(exc).__name__, exc))
        try:
            print(exc.read().decode("utf-8")[:300])
        except Exception:
            pass
