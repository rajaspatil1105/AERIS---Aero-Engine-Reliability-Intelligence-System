import io, shutil
P = r"node3_service\api.py"
shutil.copy2(P, P + ".bak_age")
src = io.open(P, encoding="utf-8").read()
n = 0

def sub(old, new, label):
    global src, n
    if old not in src:
        print("MISS  %s" % label); return
    src = src.replace(old, new, 1); n += 1
    print("ok    %s" % label)

sub('''        return {"engines": me.fleet_listing(),
                "caveat": "Wear states are hand-set starting conditions, not "
                          "measured histories. They make a worn engine run "
                          "hotter with lower oil pressure, which is directional "
                          "physics; the numbers are invented."}''',
    '''        return {"engines": me.fleet_listing(),
                "caveat": "Factory rows are hand-set starting conditions. Wear "
                          "on top of them is accumulated from missions actually "
                          "flown and persisted between runs, so a worn engine "
                          "runs hotter with lower oil pressure. The wear MODEL "
                          "is calibrated, not measured against a real engine."}''',
    "fleet caveat reflects cumulative wear")

sub('    @app.get("/live", tags=["history"])',
    '''    @app.post("/sim/age", tags=["simulator"])
    def sim_age(serial: str, hours: float) -> dict[str, Any]:
        """Age an engine without flying it, for demos.

        Wear is DERIVED, not invented: a short hard segment is flown
        offline (100%, 1000 ft, 38 C) and its stress scaled to the hours
        requested. So 200 h costs what 200 h of that duty actually costs.
        """
        import sys
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
        from shared import mission_engine as me
        from node1_ingestion.simulator_bridge import Setpoint

        if serial not in me.FLEET_BY_SERIAL:
            raise HTTPException(422, "unknown engine %s; see GET /sim/fleet"
                                % serial)
        if not (0.0 < hours <= 2000.0):
            raise HTTPException(422, "hours must be in (0, 2000], got %r"
                                % hours)

        before = me.engine_now(serial)
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

        k = hours / seg_h
        sd = last["stress"]
        stt = me.StressState()
        for name in ("thermal", "oil", "power", "cycles"):
            if hasattr(stt, name):
                setattr(stt, name, float(sd.get(name, 0.0)) * k)
        me.record_wear(serial, stt, hours)
        after = me.engine_now(serial)

        return {"serial": serial, "hours_added": hours,
                "before": {"hours": before.hours,
                           "oil_pump_health": before.oil_pump_health,
                           "coolant_pump_health": before.coolant_pump_health,
                           "bearing_wear": before.bearing_wear},
                "after": {"hours": after.hours,
                          "oil_pump_health": after.oil_pump_health,
                          "coolant_pump_health": after.coolant_pump_health,
                          "bearing_wear": after.bearing_wear},
                "cost_per_hour": {
                    "oil_pump_health": round(
                        (before.oil_pump_health - after.oil_pump_health)
                        / hours, 8),
                    "coolant_pump_health": round(
                        (before.coolant_pump_health
                         - after.coolant_pump_health) / hours, 8),
                    "bearing_wear": round(
                        (after.bearing_wear - before.bearing_wear)
                        / hours, 8)},
                "caveat": "hard-duty hours; a gentle sortie costs far less"}

    @app.post("/sim/age/reset", tags=["simulator"])
    def sim_age_reset(serial: str | None = None) -> dict[str, Any]:
        """Drop accumulated wear, for one engine or the whole fleet."""
        import sys
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
        from shared import mission_engine as me
        if serial is not None and serial not in me.FLEET_BY_SERIAL:
            raise HTTPException(422, "unknown engine %s" % serial)
        me.reset_wear(serial) if serial else me.reset_wear()
        return {"reset": serial or "entire fleet"}

    @app.get("/live", tags=["history"])''', "age + reset endpoints")

io.open(P, "w", encoding="utf-8", newline="\n").write(src)
print("\n%d edits -> %s" % (n, P))
