"""Generate RUL training histories by aging the fleet across many missions.

Offline: calls run_mission directly, never touches the API, the demo DB or
the wear overlay. Each row is one engine at one point in its service life,
with the hours remaining until it crossed the failure threshold -- which is
the label a RUL model needs and has never had.
"""
import csv, random, sys, pathlib, dataclasses, time
sys.path.insert(0, str(pathlib.Path(".").resolve()))
from shared import mission_engine as me

random.seed(7)
OUT = pathlib.Path("data/rul_histories.csv")
OUT.parent.mkdir(parents=True, exist_ok=True)

# Duty mixes: how an operator actually flies. Weighted toward benign.
DUTIES = [
    ("gentle",  0.35, dict(thr=70.0,  alt=8000.0, oat=6.0)),
    ("normal",  0.30, dict(thr=80.0,  alt=6000.0, oat=15.0)),
    ("warm",    0.20, dict(thr=90.0,  alt=4000.0, oat=28.0)),
    ("hot_wot", 0.15, dict(thr=100.0, alt=1000.0, oat=38.0)),
]
_W = [d[1] for d in DUTIES]

FAIL_OIL = 0.70          # oil pump health at or below this = failed
FAIL_COOL = 0.70
FAIL_BEAR = 0.45
MAX_MISSIONS = 400
MISSION_H = 8.0          # a long sortie, not a 30 h torture test


def failed(e):
    return (e.oil_pump_health <= FAIL_OIL or e.coolant_pump_health <= FAIL_COOL
            or e.bearing_wear >= FAIL_BEAR)


def fly_one(e, duty_kw, hours):
    prof = [me.Setpoint(t_s=0.0, throttle_pct=duty_kw["thr"],
                        altitude_ft=duty_kw["alt"], oat_c=duty_kw["oat"]),
            me.Setpoint(t_s=hours * 3600.0, throttle_pct=duty_kw["thr"],
                        altitude_ft=duty_kw["alt"], oat_c=duty_kw["oat"])]
    last = None
    for rec in me.run_mission(prof, e, dt_s=1.0, stress_enabled=True,
                              emit_cruise_s=600.0):
        last = rec
    return last["stress"]


def age(e, st):
    return dataclasses.replace(
        e,
        hours=e.hours + MISSION_H,
        coolant_pump_health=max(0.05, e.coolant_pump_health - 0.30 * min(1.0, st["thermal"])),
        oil_pump_health=max(0.05, e.oil_pump_health - 0.25 * min(1.0, st["oil"])),
        bearing_wear=min(1.0, e.bearing_wear + 0.35 * min(1.0, st["power"])))


t0 = time.time()
rows = []
for spec in me.fleet_listing():
    e = me.FleetEngine(**{k: v for k, v in spec.items()
                          if k in me.FleetEngine.__dataclass_fields__})
    start_h = e.hours
    hist = []
    n = 0
    while n < MAX_MISSIONS and not failed(e):
        duty = random.choices(DUTIES, weights=_W, k=1)[0]
        st = fly_one(e, duty[2], MISSION_H)
        e = age(e, st)
        n += 1
        hist.append(dict(serial=e.serial, mission=n, duty=duty[0],
                         hours=round(e.hours, 1),
                         coolant=round(e.coolant_pump_health, 6),
                         oil=round(e.oil_pump_health, 6),
                         bearing=round(e.bearing_wear, 6)))
    eol = e.hours
    for h in hist:
        h["rul_h"] = round(eol - h["hours"], 1)
        h["failed"] = 1 if not (eol - h["hours"]) else 0
    rows.extend(hist)
    print("%-12s %3d missions  %6.0f -> %6.0f h  oil %.3f cool %.3f bear %.3f  %s"
          % (e.serial, n, start_h, e.hours, e.oil_pump_health,
             e.coolant_pump_health, e.bearing_wear,
             "FAILED" if failed(e) else "cap reached"))

with OUT.open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
print("\n%d rows -> %s  (%.0f s)" % (len(rows), OUT, time.time() - t0))
