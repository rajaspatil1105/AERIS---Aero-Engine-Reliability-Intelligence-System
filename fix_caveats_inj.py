import ast, pathlib
p = pathlib.Path("shared/fault_injection.py")
s = p.read_text(encoding="utf-8-sig")

def block(marker):
    i = s.index('"id": "%s"' % marker)
    i = s.rindex("{", 0, i) + 1
    j = s.index("}, {", i)
    return i, j

# 1. residuals are signed now; the caveat said the opposite
i, j = block("residuals_reported_unsigned")
s = s[:i] + '''
        "id": "residuals_are_signed", "verified": True,
        "value": "-0.8 bar injected reads -0.8; +0.8 does not cross",
        "detail": "the twin's residuals dict carries SIGNED deviations "
                  "(measured - expected) as of e49cf96. It previously carried "
                  "magnitudes: residual_calc applied abs() per channel while "
                  "the deployed gate trains on signed deltas, so a 0.91 bar oil "
                  "pressure COLLAPSE arrived as +0.91 and scored 0.3532 "
                  "HEALTHY. Direction now reaches the classifier: -0.8 bar "
                  "scores 0.9999 and labels lubrication_degradation, +0.8 bar "
                  "scores 0.4411 and does not cross. A UI may read direction "
                  "from the residual sign.",
    ''' + s[j:]

# 2. the non-monotonicity was the folding, not the trees
i, j = block("gate_is_non_monotonic")
s = s[:i] + '''
        "id": "gate_monotone_where_measured", "verified": True,
        "value": "CASE 6 scan found no non-monotonic channel",
        "detail": "RETIRED 2026-09-12. The pinned non-monotonicity -- coolant "
                  "crossing at 0.0383 C yet -10 C not crossing, rpm crossing at "
                  "+88.96 yet +250 not crossing, oil pressure never crossing by "
                  "bisection -- was an artifact of ABSOLUTE residuals folding "
                  "both directions onto one value, so bisection and the large "
                  "offset were probing different physical states under the same "
                  "number. With signed residuals the CASE 6 scan finds no "
                  "non-monotonic channel. p_anom still carries no SEVERITY "
                  "(coolant_hot 0.9998 == coolant_very_hot 0.9998), and the "
                  "gate is still tree-based, so a threshold remains a leaf "
                  "boundary rather than a guaranteed detection floor. Measured "
                  "at ONE operating point.",
    ''' + s[j:]

# 3. duplicate dict key, and provenance that no longer exists
i, j = block("labels_reflect_channel_count_not_mechanism")
s = s[:i] + '''
        "id": "labels_reflect_channel_count_not_mechanism", "verified": True,
        "value": "oil_hot -> sensor_drift; oil_hot+press_low -> lubrication",
        "detail": "measured: a lone oil-temperature excursion is labelled "
                  "sensor_drift, while the same excursion combined with a "
                  "pressure loss is labelled lubrication_degradation. Reading "
                  "a single implausible channel as an instrumentation problem "
                  "is plausible behaviour, but it is a property of the training "
                  "set, not validated physics. (This block previously carried "
                  "two 'value' keys, the second silently shadowing the first, "
                  "and cited a Cantera training set that no longer exists -- "
                  "the data is MVEM, mvem_v3.parquet, validated only at 5800 "
                  "rpm WOT sea level.)",
    ''' + s[j:]

# 4. new: misfire and fuel_pressure_dev are not separable by these sensors
i = s.index('"id": "labels_reflect_channel_count_not_mechanism"')
i = s.rindex("{", 0, i)
s = s[:i] + '''{
        "id": "misfire_not_identifiable_from_mean_value_sensors",
        "verified": True,
        "value": "1 cyl at 0.62 trim == uniform 0.905 == fuel_pressure_dev frac 0.59",
        "detail": "MEASURED and arithmetic, not a classifier weakness. A severe "
                  "single-cylinder misfire (cylinder_fuel_trim 0.62 on one of "
                  "four) delivers 0.905 of nominal fuel flow; fuel_pressure_dev "
                  "applies a UNIFORM trim of 1.0 +/- 0.16*frac, so frac 0.59 "
                  "delivers the same 0.905. MVEM reports one MEAN EGT across "
                  "four cylinders and no crank-speed irregularity, so the two "
                  "faults are the same point in the measured space. A forced "
                  "severe misfire is detected at p_anom 0.9999 but labelled "
                  "fuel_pressure_dev with dEGT -60.5 C and dFF -1.57 kg/h. The "
                  "gate result is trustworthy; the type assignment between "
                  "these two classes is UNIDENTIFIABLE and explains the "
                  "misfire precision 0.754 / fuel_pressure_dev recall 0.734 "
                  "pair in retrain_metrics_mvem.json. Separating them needs a "
                  "sensor channel that does not exist yet: per-cylinder EGT or "
                  "rpm irregularity. Present either as 'fuel/combustion fault, "
                  "type uncertain' with fault_probabilities shown.",
    }, ''' + s[i:]

ast.parse(s); p.write_text(s, encoding="utf-8")
print("caveats rewritten, syntax OK")
