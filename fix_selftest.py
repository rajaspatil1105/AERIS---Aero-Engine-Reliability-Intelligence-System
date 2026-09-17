import ast, pathlib
p = pathlib.Path("node2_twin_core/residual_calc.py")
s = p.read_text(encoding="utf-8")
old = 'if abs(r2.features["delta_EGT_mean_C"] - r3.features["delta_EGT_mean_C"]) > 1e-6:\n        failures.append("ABSOLUTE mode is not symmetric")'
new = ('if abs(r2.features["delta_EGT_mean_C"] + r3.features["delta_EGT_mean_C"]) > 1e-6:\n'
       '        failures.append("SIGNED mode lost direction: +45 and -45 must be opposite")')
if s.count(old) != 1:
    raise SystemExit("anchor %d times; patch CASE 2/3 assertion by hand" % s.count(old))
s = s.replace(old, new).replace("CASE 2  EGT +45 C (ABSOLUTE)", "CASE 2  EGT +45 C (SIGNED)")
ast.parse(s); p.write_text(s, encoding="utf-8")
print("self-test now asserts direction is preserved")
