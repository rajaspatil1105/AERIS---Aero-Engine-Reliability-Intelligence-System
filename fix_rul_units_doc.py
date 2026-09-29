import io, shutil, pathlib
P = r"node2_twin_core\rul_engine.py"
shutil.copy2(P, P + ".bak_units")
src = io.open(P, encoding="utf-8").read()

old = '''UNITS ARE UNKNOWN. No artifact records them. Rendering this as "hours"
would be an invention. Displayed unitless until the training script
confirms.'''
new = '''UNITS ARE HOURS. The labels come from gen_rul_histories.py, which counts
remaining flight hours to an oil-pump health of 0.70, so the figure is
dimensionally real. It is a modelled life, not a certified one.'''

if old in src:
    io.open(P, "w", encoding="utf-8", newline="\n").write(src.replace(old, new, 1))
    print("ok -- docstring units paragraph rewritten")
else:
    print("MISS -- paste lines 24-30 verbatim")
