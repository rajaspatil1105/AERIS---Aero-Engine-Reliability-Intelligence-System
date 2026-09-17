import ast, pathlib
p = pathlib.Path("shared/fault_injection.py")
s = p.read_text(encoding="utf-8-sig")
old = '''    print("\\nCASE 7  the class that never wins under injection")
    everything = results + crossed
    dead_wins = [x.name for x in everything
                 if str(x.fault_label) in NEVER_ARGMAX_UNDER_INJECTION]
    mass = max((x.fault_probabilities.get(NEVER_ARGMAX_UNDER_INJECTION[0], 0.0)
                for x in everything if x.fault_probabilities), default=0.0)
    print(f"  {NEVER_ARGMAX_UNDER_INJECTION[0]}: max probability mass seen {mass:.4f}, "
          f"times it won: {len(dead_wins)}")
    check(not dead_wins,
          f"{NEVER_ARGMAX_UNDER_INJECTION[0]} was reported as the label for {dead_wins} -- it "
          f"is never argmax under injection at these offsets")'''
new = '''    print("\\nCASE 7  every declared class should be reachable")
    everything = results + crossed
    # Rewritten 2026-09-12. This indexed NEVER_ARGMAX_UNDER_INJECTION[0] and
    # raised IndexError once that tuple emptied at e49cf96, killing CASES 7-9.
    # The property worth protecting is the inverse: no class should be
    # unreachable. fuel_pressure_dev left the set when SIGNED residuals gave it
    # back a discriminating direction (egt_high 0.969, egt_low 0.507).
    labelled = [x for x in everything if x.fault_probabilities]
    won = sorted({str(x.fault_label) for x in everything if x.fault_label})
    print(f"  labels produced under injection: {won}")
    for cls in sorted({k for x in labelled for k in x.fault_probabilities}):
        mass = max(x.fault_probabilities.get(cls, 0.0) for x in labelled)
        tally = sum(1 for x in everything if str(x.fault_label) == cls)
        flag = "" if tally else "   <-- never argmax at these offsets"
        print(f"    {cls:<24} max mass {mass:.4f}  won {tally}x{flag}")
    if NEVER_ARGMAX_UNDER_INJECTION:
        regained = [c for c in NEVER_ARGMAX_UNDER_INJECTION if c in won]
        check(not regained,
              f"{regained} is pinned as never-argmax but won under injection -- "
              f"that is an improvement; update NEVER_ARGMAX_UNDER_INJECTION")
    check(len(won) >= 3,
          f"only {len(won)} distinct class(es) reachable under injection: {won}. "
          f"Single-channel offsets should spread across the multiclass stage.")'''
if s.count(old) != 1:
    raise SystemExit("anchor %d times" % s.count(old))
s = s.replace(old, new)

s = s.replace('"value": list(NEVER_ARGMAX_UNDER_INJECTION),',
              '"value": (list(NEVER_ARGMAX_UNDER_INJECTION) or\n'
              '                  "none -- set emptied at e49cf96 when SIGNED residuals "\n'
              '                  "restored fuel_pressure_dev as argmax for EGT offsets"),', 1)
ast.parse(s); p.write_text(s, encoding="utf-8")
print("CASE 7 rewritten, syntax OK")
