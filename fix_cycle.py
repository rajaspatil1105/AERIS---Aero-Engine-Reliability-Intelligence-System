import ast, pathlib

# 1. define the invariant in stress_sim, next to GATE_THRESHOLD
p = pathlib.Path("shared/stress_sim.py")
s = p.read_text(encoding="utf-8-sig")
anchor = "GATE_THRESHOLD = _load_gate_threshold()"
if s.count(anchor) != 1:
    raise SystemExit("stress_sim anchor %d times" % s.count(anchor))
s = s.replace(anchor, anchor + '''

# The zero-residual regression invariant, shared by fault_injection CASE 0 and
# throttle_dynamics CASE 1. Both harnesses set measured == expected, so all five
# residuals are exactly 0.0 and both must see the same score. Lives here because
# stress_sim is the base module both import; keeping a copy in each created a
# circular import. Re-pinned 2026-09-12: was 0.5443998040908319, measured
# pre-MVEM-retrain. NOTE this is NOT the live service anchor -- a real MVEM
# healthy cruise frame carries a +0.0163 bar oil pressure residual and scores
# 0.3702. Both numbers are correct for their respective inputs.
HEALTHY_P_ANOM = 0.36390550779530195''')
ast.parse(s); p.write_text(s, encoding="utf-8")

# 2. throttle_dynamics: take it from stress_sim, not fault_injection
p = pathlib.Path("shared/throttle_dynamics.py")
s = p.read_text(encoding="utf-8-sig")
s = s.replace("from shared.fault_injection import HEALTHY_P_ANOM\n", "")
if "HEALTHY_P_ANOM" not in s.split("def ")[0]:
    s = s.replace("from shared.stress_sim import",
                  "from shared.stress_sim import HEALTHY_P_ANOM\nfrom shared.stress_sim import", 1)
ast.parse(s); p.write_text(s, encoding="utf-8")

# 3. fault_injection: import it instead of defining its own
p = pathlib.Path("shared/fault_injection.py")
s = p.read_text(encoding="utf-8-sig")
i = s.index("HEALTHY_P_ANOM = 0.36390550779530195")
j = s.index("NEVER_ARGMAX_UNDER_INJECTION", i)
s = s[:i] + s[j:]
s = s.replace("    GATE_THRESHOLD, build_core, deck, envelope_verdict, reference_op, _extract,",
              "    GATE_THRESHOLD, HEALTHY_P_ANOM, build_core, deck, envelope_verdict,\n"
              "    reference_op, _extract,", 1)
ast.parse(s); p.write_text(s, encoding="utf-8")
print("HEALTHY_P_ANOM now owned by stress_sim")
