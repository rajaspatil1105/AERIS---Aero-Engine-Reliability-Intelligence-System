import ast, pathlib
p = pathlib.Path("shared/fault_injection.py")
s = p.read_text(encoding="utf-8")
old = "HEALTHY_P_ANOM = 0.5443998040908319      # the regression invariant"
new = ('HEALTHY_P_ANOM = 0.36390550779530195     # the regression invariant\n'
       '# Re-pinned 2026-09-12. Was 0.5443998040908319, measured pre-MVEM-retrain;\n'
       '# it moved at the retrain (commit 8f163c3), not at the SIGNED residual fix\n'
       '# (e49cf96) -- a zero-residual frame is unaffected by abs(). Both this suite\n'
       '# and throttle_dynamics CASE 1 independently measure 0.36390550779530195 at\n'
       '# the reference op with all five residuals exactly 0.0. The live service\n'
       '# anchor is 0.3702 for the MVEM healthy cruise frame, which carries a real\n'
       '# +0.0163 bar oil pressure residual; both numbers are correct.')
if s.count(old) != 1:
    raise SystemExit("anchor %d times" % s.count(old))
s = s.replace(old, new); ast.parse(s); p.write_text(s, encoding="utf-8")
print("fault_injection invariant re-pinned")
