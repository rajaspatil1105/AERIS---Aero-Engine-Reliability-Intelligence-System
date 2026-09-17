import ast, pathlib
p = pathlib.Path("shared/fault_injection.py")
s = p.read_text(encoding="utf-8-sig")

def sub(old, new):
    global s
    if s.count(old) != 1:
        raise SystemExit("anchor %d times: %r" % (s.count(old), old[:60]))
    s = s.replace(old, new)

# 1. the headline finding that the sign fix retired
sub('''        "id": "oil_pressure_sensitivity_gap", "verified": True,
        "value": "-1.0 bar of 3.162 -> p_anom 0.5679, no crossing",
        "detail": "measured: losing 32% of oil pressure does NOT cross the "
                  "gate; it scores 0.5679, only +0.0235 above the healthy "
                  "0.5444, and reports ADVISORY. Meanwhile the measured "
                  "residual resolution for that channel is 0.00019 bar, so "
                  "the gate is strongly NON-MONOTONIC in offset: tiny changes "
                  "move the score, a large one barely does. A real oil "
                  "pressure failure could be missed. Pinned in KNOWN_SUBGATE.",''',
'''        "id": "fuel_flow_sensitivity_gap", "verified": True,
        "value": "+/-1.5 kg/h of 16.45 -> 0.0034 / 0.0845, no crossing",
        "detail": "RESCOPED 2026-09-12. This caveat previously recorded that a "
                  "32% oil pressure loss scored 0.5679 and did not cross. That "
                  "was the abs() serving bug (e49cf96): -1.0 bar arrived as "
                  "+1.0 bar, i.e. pressure HIGH, a region full of healthy "
                  "training rows. Signed, -1.0 bar scores 0.9999 and labels "
                  "lubrication_degradation, so the oil pressure gap does not "
                  "exist. A REAL gap remains on fuel flow: -1.5 kg/h scores "
                  "0.0845 and +1.5 kg/h scores 0.0034 on a 16.45 kg/h nominal "
                  "(9%), neither crossing 0.50, and CASE 5 bisection finds no "
                  "crossing in either direction. Fuel flow alone is not a "
                  "detection channel at this operating point. Pinned in "
                  "KNOWN_SUBGATE.",''')

# 2. the caveat titled for an empty set
sub('"id": "class_never_argmax_under_injection", "verified": True,',
    '"id": "all_classes_reachable_under_injection", "verified": True,')

# 3. the asymmetry claim, half of which is now false
sub('''                  "this detection threshold, so every transient frame was "
                  "guaranteed to read FAULT. Note the safety asymmetry: oil "
                  "TEMPERATURE is hypersensitive (false positives) while oil "
                  "PRESSURE misses a 32% loss (false negatives).",''',
'''                  "this detection threshold, so every transient frame was "
                  "guaranteed to read FAULT. The oil pressure half of the "
                  "asymmetry once claimed here is gone: pressure loss is "
                  "detected at 0.9999 since e49cf96. Oil temperature remains "
                  "hypersensitive, and fuel flow is now the insensitive "
                  "channel -- see fuel_flow_sensitivity_gap.",''')

# 4. the closing summary
sub('''    print("  p_anom grades neither severity nor direction, and a 32% oil "
          "pressure loss does not cross it")''',
'''    print("  p_anom grades severity not at all and direction only since "
          "e49cf96; fuel flow alone does not cross the gate")''')

ast.parse(s); p.write_text(s, encoding="utf-8")
print("stale findings retired, syntax OK")
