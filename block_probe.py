import time, requests
B = "http://localhost:8000"
for i in range(20):
    t0 = time.time()
    try:
        r = requests.get(B + "/sim/fleet", timeout=10)
        ok = "%d in %.2fs" % (r.status_code, time.time() - t0)
    except Exception as e:
        ok = "BLOCKED (%s)" % type(e).__name__
    try:
        s = requests.get(B + "/sim/run/68", timeout=10).json()
        st = "done=%s frames=%s" % (s.get("done"), s.get("frames"))
    except Exception:
        st = "poll blocked"
    print("%2d  fleet %-18s  run68 %s" % (i, ok, st))
    if "done=True" in st:
        break
    time.sleep(5.0)
