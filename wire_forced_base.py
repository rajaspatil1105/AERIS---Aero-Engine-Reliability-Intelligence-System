import pathlib, shutil

OLD = '''            forced = me.FORCED_FAULTS[body.fault](body.fault_severity)'''
NEW = '''            # Pass the resolved fleet engine so the fault composes with its
            # wear instead of overwriting it. Without this a factory-fresh
            # engine and one at 2010 h both reported oil_pressure 2.2400 bar
            # under severe lubrication, and the engine selector had no effect
            # once a fault was active.
            forced = me.FORCED_FAULTS[body.fault](body.fault_severity, engine)'''

p = pathlib.Path("node3_service/api.py")
s = p.read_text(encoding="utf-8-sig")
if "so the fault composes with its" in s:
    print("already patched -- no change")
elif OLD not in s:
    print("ANCHOR NOT FOUND -- no change")
else:
    shutil.copy(p, p.with_suffix(".py.bak"))
    p.write_text(s.replace(OLD, NEW, 1), encoding="utf-8")
    print("patched -- api passes the fleet engine into the forced fault")
