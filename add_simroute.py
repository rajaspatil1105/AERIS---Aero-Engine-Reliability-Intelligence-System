import pathlib
p = pathlib.Path("node3_service/api.py")
s = p.read_text(encoding="utf-8")

anchor = '    @app.get("/live", tags=["history"])'
if anchor not in s: raise SystemExit("NOT FOUND: /live anchor")

route = '''    # -- simulator (tabs 1-3) --------------------------------------------
    @app.post("/sim/run", tags=["simulator"], status_code=201)
    def sim_run(body: SimRunIn, bg: BackgroundTasks,
                st: ServiceState = Depends(get_state)) -> dict[str, Any]:
        """Start a mission. Frames are scored through the same path as
        POST /frames, so a simulated flight is indistinguishable downstream.

        Runs are serialised: the twin's RUL engine carries EWMA state across
        frames, so two concurrent runs would interleave into one trend. A
        second request while one is active gets 409 rather than quiet garbage.
        """
        import sys, time as _t
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
        from shared import mission_engine as me
        from shared import engine_mvem as mvem

        if any(not v["done"] for v in _SIM_RUNS.values()):
            raise HTTPException(409, "a mission is already running; "
                                     "DELETE /sim/run/{session_id} to stop it")

        fleet = {e["serial"]: e for e in me.fleet_listing()}
        if body.engine_serial not in fleet:
            raise HTTPException(422, "unknown engine %s; see GET /sim/fleet"
                                % body.engine_serial)
        spec = fleet[body.engine_serial]
        engine = me.FleetEngine(**{k: v for k, v in spec.items()
                                   if k in me.FleetEngine.__dataclass_fields__})

        if body.setpoints:
            profile = [me.Setpoint(**sp.model_dump(exclude_none=True))
                       for sp in body.setpoints]
        else:
            profile = [me.Setpoint(t_s=0.0, throttle_pct=body.throttle_pct,
                                   altitude_ft=body.altitude_ft, oat_c=body.oat_c),
                       me.Setpoint(t_s=body.duration_s, throttle_pct=body.throttle_pct,
                                   altitude_ft=body.altitude_ft, oat_c=body.oat_c)]
        if len(profile) < 2:
            raise HTTPException(422, "a profile needs at least two setpoints")

        forced = None
        if body.fault:
            if body.fault not in me.FORCED_FAULTS:
                raise HTTPException(422, "fault must be one of %s"
                                    % sorted(me.FORCED_FAULTS))
            forced = me.FORCED_FAULTS[body.fault](body.fault_severity)

        st.core.reset()
        st.prev_payload = None
        st.prev_monotonic = None
        st.last_throttle_change_monotonic = None
        st.store.close_session()
        note = "sim %s on %s%s" % (body.fault or "healthy", body.engine_serial,
                                   "" if not body.fault
                                   else " @%.0fs" % (body.fault_at_s or 0.0))
        sid = st.store.open_session(note=note[:200], manifest=st.manifest)
        _SIM_RUNS[sid] = {"done": False, "cancel": False, "frames": 0,
                          "faults": 0, "note": note, "error": None}

        def _fly() -> None:
            rec_prev_t = None
            try:
                for rec in me.run_mission(
                        profile, engine, dt_s=1.0,
                        stress_enabled=body.stress_enabled,
                        forced_fault=forced, forced_at_s=body.fault_at_s,
                        forced_clear_s=body.fault_clear_s,
                        emit_cruise_s=body.emit_cruise_s,
                        emit_event_s=body.emit_event_s):
                    if _SIM_RUNS[sid]["cancel"]:
                        break
                    # Admission measures ARRIVAL dt (perf_counter between
                    # POSTs), but these frames arrive ~30 ms apart while
                    # representing up to 60 simulated seconds. Left alone, any
                    # climb reads as an instant throttle slam and every frame
                    # is refused "transient". Backdate the clock so the rule
                    # sees the dt the flight actually had.
                    sim_dt = (rec["t_s"] - rec_prev_t) if rec_prev_t is not None else None
                    rec_prev_t = rec["t_s"]
                    if sim_dt:
                        now = _t.perf_counter()
                        st.prev_monotonic = now - sim_dt
                        if st.last_throttle_change_monotonic is not None:
                            st.last_throttle_change_monotonic -= sim_dt
                    out = _process_and_store(st, rec["frame"])
                    _SIM_RUNS[sid]["frames"] += 1
                    if out.get("status") == "FAULT":
                        _SIM_RUNS[sid]["faults"] += 1
                    if body.speed > 0:
                        _t.sleep(min(sim_dt or 1.0, 60.0) / body.speed)
            except Exception as exc:
                _SIM_RUNS[sid]["error"] = "%s: %s" % (type(exc).__name__, exc)
            finally:
                _SIM_RUNS[sid]["done"] = True

        bg.add_task(_fly)
        return {"session_id": sid, "note": note,
                "poll": "/sim/run/%d" % sid,
                "frames": "/frames?session_id=%d" % sid}

    @app.get("/sim/run/{session_id}", tags=["simulator"])
    def sim_status(session_id: int) -> dict[str, Any]:
        if session_id not in _SIM_RUNS:
            raise HTTPException(404, "no sim run for session %d" % session_id)
        return dict(_SIM_RUNS[session_id], session_id=session_id)

    @app.delete("/sim/run/{session_id}", tags=["simulator"])
    def sim_cancel(session_id: int) -> dict[str, Any]:
        if session_id not in _SIM_RUNS:
            raise HTTPException(404, "no sim run for session %d" % session_id)
        _SIM_RUNS[session_id]["cancel"] = True
        return {"session_id": session_id, "cancelling": True}

    @app.get("/sim/fleet", tags=["simulator"])
    def sim_fleet() -> dict[str, Any]:
        import sys
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
        from shared import mission_engine as me
        return {"engines": me.fleet_listing(),
                "caveat": "Wear states are hand-set starting conditions, not "
                          "measured histories. They make a worn engine run "
                          "hotter with lower oil pressure, which is directional "
                          "physics; the numbers are invented."}

'''
s = s.replace(anchor, route + anchor, 1)

model = '''class SimRunIn(BaseModel):
    engine_serial: str
    throttle_pct: float = Field(default=80.0, ge=20.0, le=100.0)
    altitude_ft: float = Field(default=6000.0, ge=0.0, le=22800.0)
    oat_c: float | None = Field(default=None, ge=-39.5, le=39.8)
    duration_s: float = Field(default=1800.0, gt=0.0, le=180000.0)
    setpoints: list[SetpointIn] | None = None
    fault: str | None = None
    fault_severity: str = Field(default="moderate")
    fault_at_s: float | None = None
    fault_clear_s: float | None = None
    stress_enabled: bool = True
    emit_cruise_s: float = Field(default=60.0, gt=0.0)
    emit_event_s: float = Field(default=1.0, gt=0.0)
    speed: float = Field(default=0.0, ge=0.0, le=1000.0,
                         description="0 = flat out; 10 = 10x real time")


class SetpointIn(BaseModel):
    t_s: float
    throttle_pct: float = Field(ge=20.0, le=100.0)
    altitude_ft: float = Field(ge=0.0, le=22800.0)
    oat_c: float | None = None


'''
s = s.replace("class SessionIn(BaseModel):", model + "class SessionIn(BaseModel):", 1)
s = s.replace("app = create_app()",
              "_SIM_RUNS: dict[int, dict[str, Any]] = {}\n\napp = create_app()", 1)
if "_SIM_RUNS" not in s.split("def create_app")[0]:
    s = s.replace("def create_app", "_SIM_RUNS: dict[int, dict[str, Any]] = {}\n\n\ndef create_app", 1)
    s = s.replace("_SIM_RUNS: dict[int, dict[str, Any]] = {}\n\napp = create_app()", "app = create_app()", 1)
for imp in ("import pathlib", "from fastapi import BackgroundTasks"):
    if imp not in s:
        s = s.replace("import time", imp + "\nimport time", 1)
compile(s.lstrip("\ufeff"), str(p), "exec")
p.write_text(s, encoding="utf-8")
print("sim routes added, syntax OK")
