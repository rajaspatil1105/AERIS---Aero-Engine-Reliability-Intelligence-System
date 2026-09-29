import pathlib, shutil, re
p = pathlib.Path("shared/mission_engine.py")
s = p.read_text(encoding="utf-8-sig")
m = re.search(r"^OIL_KNEE_C\s*=\s*105\.0.*$", s, re.M)
if "OIL_KNEE_C = 102.0" in s:
    print("already patched")
elif not m:
    print("ANCHOR NOT FOUND -- paste the OIL_KNEE_C line")
else:
    new = ('OIL_KNEE_C = 102.0   # was 105.0, which this thermal model can never\n'
           '# reach: MVEM oil temperature peaks at 103.08 C even at 18000 ft WOT on a\n'
           '# 35 C day, so lubrication_degradation could only ever arrive by injection.\n'
           '# Real Rotax 915iS limits are 130 C oil / 120 C coolant, with service\n'
           '# guidance to stay under 120 C and a normal range of ~88-110 C, so 105 was\n'
           '# the defensible number and it is the MODEL that under-predicts oil\n'
           '# temperature, not the knee that was wrong. 102 C sits just under the\n'
           '# model ceiling so damage accrues only under sustained abuse and never in\n'
           '# normal cruise, preserving the "damage only above a knee" property.\n'
           '# Revisit if the thermal model is recalibrated to realistic hot-day temps.')
    shutil.copy(p, p.with_suffix(".py.bak8"))
    p.write_text(s[:m.start()] + new + s[m.end():], encoding="utf-8")
    print("patched -- OIL_KNEE_C 105.0 -> 102.0")
