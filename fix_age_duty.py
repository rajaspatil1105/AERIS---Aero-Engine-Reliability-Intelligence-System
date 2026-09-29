import io, shutil
P = r"node3_service\api.py"
shutil.copy2(P, P + ".bak_duty")
src = io.open(P, encoding="utf-8").read()

old = '''        before = me.engine_now(serial)
        seg_h = 2.0
        prof = [Setpoint(t_s=0.0, throttle_pct=100.0, altitude_ft=1000.0,
                         oat_c=38.0),
                Setpoint(t_s=seg_h * 3600.0, throttle_pct=100.0,
                         altitude_ft=1000.0, oat_c=38.0)]
        last = None
        for rec in me.run_mission(prof, before, dt_s=1.0, stress_enabled=True,
                                  emit_cruise_s=600.0, emit_event_s=60.0):
            last = rec
        if last is None:
            raise HTTPException(500, "aging segment produced no frames")

        k = hours / seg_h'''
new = '''        before = me.engine_now(serial)

        # Service duty mix, matching gen_rul_histories.py: mostly gentle
        # and normal cruise with a little hot low-level work. Aging on
        # continuous hot WOT was ~4x too harsh -- it reached end of life
        # in 300 h where the histories take ~1200.
        DUTY = ((0.35, 70.0, 8000.0, 6.0),
                (0.30, 80.0, 6000.0, 15.0),
                (0.20, 90.0, 4000.0, 28.0),
                (0.15, 100.0, 1000.0, 38.0))
        seg_h, last, agg = 2.0, None, {}
        for share, thr, alt, oat in DUTY:
            prof = [Setpoint(t_s=0.0, throttle_pct=thr, altitude_ft=alt,
                             oat_c=oat),
                    Setpoint(t_s=seg_h * 3600.0, throttle_pct=thr,
                             altitude_ft=alt, oat_c=oat)]
            for rec in me.run_mission(prof, before, dt_s=1.0,
                                      stress_enabled=True,
                                      emit_cruise_s=600.0, emit_event_s=60.0):
                last = rec
            if last is None:
                raise HTTPException(500, "aging segment produced no frames")
            for name, val in last["stress"].items():
                if isinstance(val, (int, float)):
                    agg[name] = agg.get(name, 0.0) + share * float(val)
        last = {"stress": agg}

        k = hours / seg_h'''

if old in src:
    io.open(P, "w", encoding="utf-8", newline="\n").write(src.replace(old, new, 1))
    print("ok -- aging uses a service duty mix")
else:
    print("MISS -- paste the sim_age body")
