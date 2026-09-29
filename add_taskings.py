import io, shutil
P = r"shared\mission_profile.py"
shutil.copy2(P, P + ".bak_tasking")
src = io.open(P, encoding="utf-8").read()
n = 0

def sub(old, new, label):
    global src, n
    if old not in src:
        print("MISS  %s" % label); return
    src = src.replace(old, new, 1); n += 1
    print("ok    %s" % label)

sub("ALT_GROUND_FT, ALT_TRANSIT_FT, ALT_LOITER_FT = 500.0, 18000.0, 12000.0",
    '''ALT_GROUND_FT, ALT_TRANSIT_FT, ALT_LOITER_FT = 500.0, 18000.0, 12000.0

# Taskings differ in how hard the engine works. High surveillance loiters
# cold and lean and barely wears anything; low patrol sits in warm air at
# higher power, which is where season starts to matter.
TASKINGS = {
    "high_surveillance": {"transit_ft": 18000.0, "loiter_ft": 12000.0,
                          "loiter_thr": 62.0,
                          "note": "high-altitude ISR, gentle on the engine"},
    "low_patrol": {"transit_ft": 9000.0, "loiter_ft": 3000.0,
                   "loiter_thr": 75.0,
                   "note": "border patrol / convoy escort, warm and working"},
    "contested": {"transit_ft": 6000.0, "loiter_ft": 1500.0,
                  "loiter_thr": 85.0,
                  "note": "low and fast, hardest duty the envelope allows"},
}''', "tasking table")

sub("def build(takeoff, landing, area_centre, area_radius_km=40.0,\n"
    "          target_h=30.0, thr_loiter=THR_LOITER) -> MissionPlan:\n"
    '    """takeoff/landing/area_centre are (lat, lon)."""\n'
    "    log: List[str] = []",
    "def build(takeoff, landing, area_centre, area_radius_km=40.0,\n"
    "          target_h=30.0, thr_loiter=None,\n"
    '          tasking="high_surveillance") -> MissionPlan:\n'
    '    """takeoff/landing/area_centre are (lat, lon)."""\n'
    "    log: List[str] = []\n"
    "    tk = TASKINGS.get(tasking)\n"
    "    if tk is None:\n"
    '        raise ValueError("unknown tasking %r; have %s"\n'
    '                         % (tasking, ", ".join(sorted(TASKINGS))))\n'
    '    log.append("tasking %s -- %s" % (tasking, tk["note"]))\n'
    '    transit_ft, loiter_ft = tk["transit_ft"], tk["loiter_ft"]\n'
    '    if thr_loiter is None:\n'
    '        thr_loiter = tk["loiter_thr"]',
    "build takes a tasking")

for a, b, lbl in (
    ("climb_s = (ALT_TRANSIT_FT - ALT_GROUND_FT) / CLIMB_FPM * 60.0",
     "climb_s = (transit_ft - ALT_GROUND_FT) / CLIMB_FPM * 60.0", "climb_s"),
    ("desc_s = (ALT_TRANSIT_FT - ALT_LOITER_FT) / DESCENT_FPM * 60.0",
     "desc_s = abs(transit_ft - loiter_ft) / DESCENT_FPM * 60.0", "desc_s"),
    ("land_s = (ALT_LOITER_FT - ALT_GROUND_FT) / DESCENT_FPM * 60.0",
     "land_s = (loiter_ft - ALT_GROUND_FT) / DESCENT_FPM * 60.0", "land_s"),
    ("a_t = _clamp(ALT_TRANSIT_FT,", "a_t = _clamp(transit_ft,", "a_t"),
    ("a_l = _clamp(ALT_LOITER_FT,", "a_l = _clamp(loiter_ft,", "a_l")):
    sub(a, b, lbl)

sub('''if __name__ == "__main__":''',
    '''if __name__ == "__main__":
    for _tk in sorted(TASKINGS):
        _p = build((26.251, 73.049), (26.889, 70.865), (27.200, 70.200),
                   target_h=30.0, tasking=_tk)
        _lo = [x for x in _p.phases if x["phase"].startswith("loiter")][0]
        print("%-18s loiter %5.0f ft at %4.1f%%   %.2f h aloft"
              % (_tk, _lo["altitude_ft"], _lo["throttle_pct"], _p.total_h))
    print("")
''', "demo all taskings")

io.open(P, "w", encoding="utf-8", newline="\n").write(src)
print("\n%d edits" % n)
