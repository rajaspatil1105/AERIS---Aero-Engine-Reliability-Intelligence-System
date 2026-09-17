import ast, pathlib
p = pathlib.Path("shared/fault_injection.py")
s = p.read_text(encoding="utf-8-sig")
n = 0

def sub(old, new):
    global s, n
    if s.count(old) != 1:
        raise SystemExit("anchor %d times: %r" % (s.count(old), old[:70]))
    s = s.replace(old, new); n += 1

sub('FAULT_INJECTION_VERSION = "0.1.2"', 'FAULT_INJECTION_VERSION = "0.2.0"')

sub('NEVER_ARGMAX_UNDER_INJECTION = ("fuel_pressure_dev",)',
    '# Emptied 2026-09-12 (e49cf96): with SIGNED residuals fuel_pressure_dev IS\n'
    '# argmax for egt_high (+50 C, conf 0.969) and egt_low (-50 C, conf 0.507).\n'
    '# Under ABSOLUTE both directions folded onto one point and the class could\n'
    '# never win. No class is currently never-argmax under injection.\n'
    'NEVER_ARGMAX_UNDER_INJECTION: tuple = ()')

sub('''# Scenarios that were MEASURED to cross the 0.65 gate.
MUST_CROSS = ("coolant_hot", "coolant_very_hot", "egt_high", "egt_low",
              "oil_hot", "lubrication", "fuel_lean", "fuel_rich",
              "overheat_coupled")''',
    '''# Scenarios MEASURED to cross the gate (threshold owned by stress_sim).
# Re-pinned 2026-09-12: oil_pressure_low ADDED (0.5679 -> 0.9999 once the sign
# reached the classifier); fuel_lean and fuel_rich REMOVED -- they were pinned
# as crossing but measure 0.0845 and 0.0034, so the old assertion was wrong in
# both directions and the 0.65-vs-0.50 mismatch masked it.
MUST_CROSS = ("coolant_hot", "coolant_very_hot", "egt_high", "egt_low",
              "oil_hot", "oil_pressure_low", "lubrication",
              "overheat_coupled")''')

i = s.index("# Scenarios measured NOT to cross")
j = s.index("KNOWN_SUBGATE: Dict[str, float] = ")
k = s.index("\n", j)
s = s[:i] + '''# Scenarios measured NOT to cross, pinned with the value observed.
# Re-pinned 2026-09-12. oil_pressure_low left this set: -1.0 bar on a 3.162 bar
# nominal now reads 0.9999 and labels lubrication_degradation, because
# residual_calc serves signed deltas (e49cf96). The old 0.5679 was the score for
# "+1.0 bar of pressure", a region full of healthy training rows.
# Fuel flow entered it: +/-1.5 kg/h on 16.45 with every other channel at
# equilibrium does not reach 0.50 in either direction. That is a real
# sensitivity gap in the fuel channel, not a sign artifact -- pinned so a
# retrain that closes it fails loudly.
KNOWN_SUBGATE: Dict[str, float] = {"fuel_lean": 0.08449641608147526,
                                   "fuel_rich": 0.0034335051375889427}''' + s[k:]
n += 1

i = s.index("# Pairs measured to score IDENTICALLY")
j = s.index("IDENTICAL_PAIRS = ")
k = s.index("\n\n", j)
s = s[:i] + '''# Pairs measured to score IDENTICALLY, pinned for the same reason.
#   saturation: 2.5x the coolant excursion, same score -- p_anom carries no
#               severity information. Still true.
# The direction pair (fuel_lean/fuel_rich) was removed 2026-09-12: they scored
# identically only because residuals were served as absolute values. Signed,
# they read 0.0845 vs 0.0034. p_anom still carries no SEVERITY, but it is no
# longer direction-blind.
IDENTICAL_PAIRS = (("coolant_hot", "coolant_very_hot", "severity saturation"),)''' + s[k:]
n += 1

sub('    print("  (the twin reports residuals UNSIGNED -- compare absolute values)")',
    '    print("  (residuals are SIGNED since e49cf96; magnitudes compared here)")')

ast.parse(s); p.write_text(s, encoding="utf-8")
print("re-pinned %d blocks, syntax OK" % n)
