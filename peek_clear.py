import inspect, sys
sys.path.insert(0, ".")
from shared import mission_engine as me
src = inspect.getsource(me.run_mission).splitlines()
for i, l in enumerate(src, 1):
    if any(k in l for k in ("forced", "clear", "active_fault", "fs ", "fault",
                            "emit", "yield")):
        print("%3d: %s" % (i, l.rstrip()))
