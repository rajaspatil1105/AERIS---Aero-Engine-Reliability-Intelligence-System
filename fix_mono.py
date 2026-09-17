import ast, pathlib
p = pathlib.Path("shared/fault_injection.py")
s = p.read_text(encoding="utf-8-sig")
old = '''    check(nonmono > 0,
          "no non-monotonicity found; the gate may have become monotone, in "
          "which case revisit caveat gate_is_non_monotonic")'''
new = '''    # Inverted 2026-09-12. This asserted nonmono > 0 because the ABSOLUTE
    # serving path folded +offset and -offset onto one value, so bisection and
    # the large-offset scan probed different physical states under the same
    # number and disagreed. With SIGNED residuals (e49cf96) no channel is
    # non-monotonic, and a REGRESSION would be a channel reappearing here.
    check(nonmono == 0,
          f"{nonmono} channel(s) non-monotonic: a small offset crosses the gate "
          f"while a larger one in the same direction does not. Under SIGNED "
          f"residuals this should not happen; suspect a serving-path transform "
          f"or a classifier swap. See caveat gate_monotone_where_measured")'''
if s.count(old) != 1:
    raise SystemExit("anchor %d times" % s.count(old))
s = s.replace(old, new); ast.parse(s); p.write_text(s, encoding="utf-8")
print("CASE 6 now asserts monotonicity")
