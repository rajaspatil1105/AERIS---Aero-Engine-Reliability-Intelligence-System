import ast, pathlib
p = pathlib.Path("shared/throttle_dynamics.py")
s = p.read_text(encoding="utf-8-sig")
old = "check(abs((last.anomaly_probability or 0) - 0.5443998040908319) < 1e-9,"
new = "check(abs((last.anomaly_probability or 0) - HEALTHY_P_ANOM) < 1e-9,"
if s.count(old) != 1:
    raise SystemExit("anchor %d times" % s.count(old))
s = s.replace(old, new)
if "HEALTHY_P_ANOM" not in s.split("def ")[0]:
    s = s.replace("from shared.stress_sim import",
                  "from shared.fault_injection import HEALTHY_P_ANOM\nfrom shared.stress_sim import", 1)
ast.parse(s); p.write_text(s, encoding="utf-8")
print("throttle_dynamics imports the invariant")
