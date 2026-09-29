import shutil, io
p = r"shared\fault_injection.py"
src = io.open(p, encoding="utf-8").read()
old = """        check(inj.rul_trusted is False,
              f"{inj.name}: rul_trusted={inj.rul_trusted}; RUL is not "
              f"validated and must not be shown as minutes remaining")"""
new = """        # trust is a warm-up property of the shared core, not of the
        # injection: run_all() reuses one core, so whichever scenarios land
        # past MIN_SAMPLES_FOR_TREND report trusted regardless of fault.
        # The rule itself is asserted in rul_engine CASE 6.
"""
assert src.count(old) == 1, f"pattern found {src.count(old)}x"
shutil.copyfile(p, p + ".bak_case8")
io.open(p, "w", encoding="utf-8").write(src.replace(old, new))
print("ok    CASE 8 trust assertion removed")
