import pathlib, shutil
P = pathlib.Path("node3_service/api.py")
src = P.read_text(encoding="utf-8")
shutil.copy(P, pathlib.Path("node3_service/api.py.bak_record"))

OLD1 = '                    out = _process_and_store(st, rec["frame"])\n'
NEW1 = ('                    last_rec = rec\n'
        '                    out = _process_and_store(st, rec["frame"])\n')

OLD2 = ('            finally:\n'
        '                _SIM_RUNS[sid]["done"] = True\n')
NEW2 = ('            finally:\n'
        '                # Fold this mission\'s damage into the permanent record.\n'
        '                # Runs on cancel too: the abuse really happened, so the\n'
        '                # engine ages by however long it actually flew.\n'
        '                try:\n'
        '                    if body.stress_enabled and last_rec is not None:\n'
        '                        sd = last_rec["stress"]\n'
        '                        stt = me.StressState(\n'
        '                            thermal=sd["thermal"], oil=sd["oil"],\n'
        '                            power=sd["power"], cycles=sd["cycles"],\n'
        '                            triggered=list(sd["triggered"]))\n'
        '                        flown_h = last_rec["t_s"] / 3600.0\n'
        '                        me.record_wear(engine.serial, stt, flown_h)\n'
        '                        _SIM_RUNS[sid]["aged_h"] = round(flown_h, 2)\n'
        '                except Exception as exc:\n'
        '                    _SIM_RUNS[sid]["wear_error"] = str(exc)\n'
        '                _SIM_RUNS[sid]["done"] = True\n')

OLD3 = '            rec_prev_t = None\n'
NEW3 = '            rec_prev_t = None\n            last_rec = None\n'

for old, new, tag in ((OLD1, NEW1, "capture"), (OLD2, NEW2, "finally"), (OLD3, NEW3, "init")):
    if old not in src:
        raise SystemExit("anchor not found: " + tag)
    src = src.replace(old, new, 1)

P.write_text(src, encoding="utf-8")
print("patched -- missions now age the engine")
