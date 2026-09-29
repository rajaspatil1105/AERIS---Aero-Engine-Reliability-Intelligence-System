import sys, pathlib
sys.path.insert(0, str(pathlib.Path(".").resolve()))
from shared import mission_engine as me, mission_profile as mp, mission_weather as mw

TO, LD, AC = (26.251, 73.049), (26.889, 70.865), (27.200, 70.200)
for tk in ("high_surveillance", "low_patrol", "contested"):
    plan = mp.build(TO, LD, AC, target_h=30.0, tasking=tk)
    mw.apply_to_plan(plan, [TO, AC, AC, LD], "2025-06-15")
    mid = plan.profile[len(plan.profile) // 2]
    print("%-18s setpoints=%d   midpoint  thr %5.1f%%  alt %6.0f ft  oat %s"
          % (tk, len(plan.profile), mid.throttle_pct, mid.altitude_ft, mid.oat_c))
    for sp in plan.profile:
        if 4 * 3600.0 < sp.t_s < 26 * 3600.0:
            print("      loiter sp: t=%.0f s  thr %.1f  alt %.0f  oat %s"
                  % (sp.t_s, sp.throttle_pct, sp.altitude_ft, sp.oat_c))
            break
