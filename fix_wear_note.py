import pathlib, shutil
P = pathlib.Path("shared/mission_engine.py")
src = P.read_text(encoding="utf-8")
shutil.copy(P, pathlib.Path("shared/mission_engine.py.bak_note"))
OLD = '            note=e.note + " +%.0f h flown" % a.get("hours", 0.0)))'
NEW = ('            note="%.0f h flown since factory" % a.get("hours", 0.0)))')
if OLD not in src:
    raise SystemExit("note anchor not found")
src = src.replace(OLD, NEW, 1)
P.write_text(src, encoding="utf-8")
print("patched -- note reflects service, not factory")
