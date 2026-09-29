import pathlib, shutil, re
P = pathlib.Path("node3_service/api.py")
src = P.read_text(encoding="utf-8")
shutil.copy(P, pathlib.Path("node3_service/api.py.bak_wear"))

OLD = """        spec = fleet[body.engine_serial]
        engine = me.FleetEngine(**{k: v for k, v in spec.items()
                                   if k in me.FleetEngine.__dataclass_fields__})
"""
NEW = """        # Start from the AGED engine, not the factory row: wear accumulated
        # by previous missions is layered on by mission_engine's overlay.
        engine = me.engine_now(body.engine_serial)
"""
if OLD not in src:
    raise SystemExit("engine lookup anchor not found -- paste lines 530-540")
src = src.replace(OLD, NEW, 1)
P.write_text(src, encoding="utf-8")
print("patched -- runs start from aged engine")
