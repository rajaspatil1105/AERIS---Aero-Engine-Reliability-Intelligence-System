# AERIS - declared caveats

Generated 2026-09-02 18:21 by make_caveats.py. Do not edit by hand.

## Node 1 ingestion adapter - unverified assumptions

Source: `node1_ingestion.adapter.adapter_caveats()`

### fuel_density_assumed  [UNVERIFIED]
- **value**: 0.72
- **unit**: kg/L
- **affects**: ['fuelflow_kgh', 'delta_fuelflow_kgh']
- **detail**: UNVERIFIED. 0.72 kg/L is avgas 100LL at 15 C. The training script that produced fuelflow_kgh_baseline.pkl has not been read, so the density the training data assumed is unknown. If the simulator emitted mass flow directly, or assumed MOGAS (~0.745) or Jet-A (~0.80), every fuel residual is scaled by the ratio of the two densities.

### coolant_channel_assumed  [UNVERIFIED]
- **value**: coolant_temp_out_c
- **unit**: None
- **affects**: ['coolant_temp_C', 'delta_coolant_temp_C']
- **detail**: UNVERIFIED. The schema exposes coolant_temp_in_c and coolant_temp_out_c; coolant_temp_C_baseline.pkl was trained on ONE unlabelled coolant channel. Outlet is selected because it is the conventional single-point coolant reading and it is the hotter of the two, matching the manifest's observed healthy output range. Selecting the wrong channel biases every coolant residual by the engine's coolant delta-T.

## Node 1 ingestion adapter - verified declarations

Source: `node1_ingestion.adapter.adapter_declarations()`

### density_altitude_floor  [VERIFIED]
- **value**: -6000.0
- **unit**: ft
- **affects**: ['altitude_ft']
- **detail**: applies only when altitude_is_density=True; the geometric floor of -1500 ft is unchanged

### training_data_fidelity  [UNVERIFIED]
- **value**: ~70%
- **unit**: None
- **affects**: ['all model output']
- **detail**: the Cantera generator was built from limited public Rotax 915 iS data; the dataset is estimated ~70% faithful to a real engine. AERIS demonstrates the detection architecture, not certified thresholds

## Shared - atmosphere / density altitude

Source: `shared.atmosphere.atmosphere_caveats()`

### training_envelope  [VERIFIED]
- **value**: {'alt_ft': (0.0, 21881.3), 'oat_c': (-28.325, 34.351)}
- **note**: placeholder bounds; set from the Cantera training set

### density_altitude_substitution  [UNVERIFIED]
- **value**: density_altitude -> altitude_ft
- **note**: training altitude_ft is GEOMETRIC; the stress simulator feeds DENSITY altitude into that same feature. Physically defensible but a semantic substitution, unvalidated against the Cantera runs

### joint_envelope_unchecked  [UNVERIFIED]
- **value**: {'alt_ft': (0.0, 21881.3), 'oat_c': (-28.325, 34.351)}
- **note**: marginal bounds only; training correlated OAT with altitude near ISA, so in-bounds combinations may still be unseen

### training_data_fidelity  [UNVERIFIED]
- **value**: ~70%
- **note**: dataset generated from limited public Rotax 915 iS data; absolute values are indicative, not validated

### humidity_via_density_altitude  [VERIFIED]
- **value**: indirect
- **note**: no model was trained on humidity; it acts only by reducing air density and raising effective altitude

## Shared - envelope stress sweep

Source: `shared.stress_sim.stress_caveats()`

### gate_untrusted_pre_retrain  [UNVERIFIED]
- **value**: 0.65
- **detail**: a known-healthy deck point scores 0.5444 against the 0.65 gate: +0.1056 headroom. Margins are relative indicators, not airworthiness statements.

### cells_are_synthesised_healthy  [VERIFIED]
- **value**: BaselineDeck.predict
- **detail**: each cell is the deck's own healthy prediction at that operating point, so residuals are ~0 by construction. This maps model competence, it does NOT simulate degradation.

### two_disagreeing_envelopes  [UNVERIFIED]
- **value**: {'dataset_oat_c': [-28.325, 34.351], 'deck_oat_c': [-27.9845, 30.0], 'residual_calc_alt_ft': [0.0, 21709.3], 'deck_throttle_pct': [56.5, 100.0]}
- **detail**: FOUR competence boundaries, not two, each read directly from code or config and none of them agreeing. The 3.12M-row scan of master_dataset.csv gives OAT [-28.325, 34.351] C. BaselineDeck.check_envelope enforces OAT [-27.9845, 30] C, which is the binding ceiling. residual_calc enforces altitude [0, 21709.3] ft against the dataset's 21881.3. And the deck also enforces throttle [56.5, 100] %, discovered while testing transients -- so idle and low cruise cannot be scored at all, however steady they are. All four are reported; none is averaged away or silently preferred. What stays UNVERIFIED is which authority is right for a real engine: the dataset is ~70% faithful, so a boundary being stricter is not the same as it being correct.

### gate_resolution_is_leaf_local  [VERIFIED]
- **value**: leaf boundary, not a global step
- **detail**: gate_resolution_ft() finds the smallest ALTITUDE step that moves p_anom from the reference point and reports about 500 ft. That number is a property of ONE leaf boundary in a tree ensemble, not a global sensitivity floor, and it must not be read as one. fault_injection measured the same effect per channel and found it non-monotonic: a coolant offset of 0.0383 C crosses the 0.65 gate while -10 C scores 0.5965 and does not; rpm crosses at +88.96 while +250 does not; oil pressure never crosses by bisection despite a measured residual resolution of 0.00019 bar. So a null result from a small perturbation means 'this landed inside a leaf', never 'the model is insensitive to this'.

### cold_days_are_extrapolation_by_construction  [VERIFIED]
- **value**: 0.0
- **detail**: the training altitude floor is exactly sea level, so any below-ISA day yields a negative density altitude the models have never seen. The twin declines these; that is a training-data gap, not a defect.

### training_envelope  [VERIFIED]
- **value**: {'alt_ft': (0.0, 21881.3), 'oat_c': (-28.325, 34.351)}
- **note**: placeholder bounds; set from the Cantera training set

### density_altitude_substitution  [UNVERIFIED]
- **value**: density_altitude -> altitude_ft
- **note**: training altitude_ft is GEOMETRIC; the stress simulator feeds DENSITY altitude into that same feature. Physically defensible but a semantic substitution, unvalidated against the Cantera runs

### joint_envelope_unchecked  [UNVERIFIED]
- **value**: {'alt_ft': (0.0, 21881.3), 'oat_c': (-28.325, 34.351)}
- **note**: marginal bounds only; training correlated OAT with altitude near ISA, so in-bounds combinations may still be unseen

### training_data_fidelity  [UNVERIFIED]
- **value**: ~70%
- **note**: dataset generated from limited public Rotax 915 iS data; absolute values are indicative, not validated

### humidity_via_density_altitude  [VERIFIED]
- **value**: indirect
- **note**: no model was trained on humidity; it acts only by reducing air density and raising effective altitude

## Shared - throttle dynamics / admission gate

Source: `shared.throttle_dynamics.dynamics_caveats()`

### thermal_lag_model_unverified  [UNVERIFIED]
- **value**: {'rpm': 1.5, 'EGT_mean_C': 2.0, 'coolant_temp_C': 15.0, 'oil_temperature_C': 25.0, 'fuelflow_kgh': 0.8, 'oil_pressure_bar': 1.0}
- **detail**: the baselines are steady-state regressors with no time constant. These tau values come from general piston-engine behaviour, NOT from AERIS training data. Transient shape is meaningful; the seconds are not validated.

### admission_thresholds_are_judgement  [UNVERIFIED]
- **value**: {'throttle_rate_pct_s': 0.5, 'gate_resid_tol': {'rpm': 1.5, 'EGT_mean_C': 0.25, 'coolant_temp_C': 0.0029, 'oil_temperature_C': 0.00035, 'fuelflow_kgh': 0.0007, 'oil_pressure_bar': 3.8e-05}}
- **detail**: the steady-state gate uses a throttle rate limit of 0.5 %/s and an absolute residual tolerance per channel. The rate limit is judgement. The residual tolerances are judgement CONSTRAINED by measurement: CASE 3a bisects the smallest residual that moves the score and asserts every tolerance sits below it. They depend on the unverified tau values only through how long settling takes, not through the admission decision.

### resolution_measured_at_one_point_only  [VERIFIED]
- **value**: 95% throttle, 6000 ft, 10 C
- **detail**: CASE 3a bisects the gate's residual resolution at a single operating point. The gate is tree-based, so leaf boundaries differ elsewhere and these tolerances are validated THERE, not globally. Resolution was also found to be asymmetric: rpm reads 38.84 probing upward and 6.82 probing downward from the same point. A sweep of resolution across the envelope is outstanding work, not a completed check.

### gate_is_hypersensitive_to_residuals  [VERIFIED]
- **value**: 0.157 C oil -> p_anom 0.5477 to 0.8049
- **detail**: measured, not assumed. The gate is tree-based and healthy residuals sit at ~0, so a decision boundary lives very close to zero: a sixth of a degree on oil temperature moved p_anom by 0.257. Same phenomenon as the 500 ft altitude leaf width recorded in stress_sim. This is why admission is absolute rather than a fraction of the step, and it is another reason the gate is untrusted pre-retrain.

### admission_cost_on_missions  [VERIFIED]
- **value**: ~250 s of settling after a throttle step
- **detail**: consequence of the two facts above, measured not assumed: oil temperature moves the score at 1.74 mK and has tau=25 s, so closing a ~8 C step to tolerance takes about 250 s. Pre-retrain the twin is therefore honestly a CRUISE-ONLY monitor -- a real mission changing throttle every few minutes will read UNAVAILABLE for most of its duration. That is a property of the untrusted gate, not of the lag model, and retraining on transient data is what fixes it.

### transients_are_not_scored_by_design  [VERIFIED]
- **value**: TRANSIENT outcome
- **detail**: a lagging channel produces a residual by construction, so a steady-state regressor asked about a manoeuvre is out of distribution: it either declines or reports near-certain fault on a healthy engine. Frames failing the admission gate are recorded as TRANSIENT and the twin is never called. Nothing is clamped and no probability is invented.

### four_outcomes_three_wire_states  [VERIFIED]
- **value**: TRANSIENT|DECLINED -> UNAVAILABLE
- **detail**: internally SCORED/TRANSIENT/DECLINED/REFUSED are four different answers. On the service contract TRANSIENT and DECLINED both map to UNAVAILABLE with a reason string via wire_status(), leaving twin_core, canonical, the api status enum and the WS hello frame unchanged.

### deck_throttle_envelope  [VERIFIED]
- **value**: throttle_pct [56.5, 100]
- **detail**: discovered while testing: the deck declines throttle below 56.5% -- 'throttle_pct=40 outside trained range [56.5, 100]'. So idle and low-cruise settings cannot be scored at all, however steady they are. This is a STATIC envelope limit, a different answer from TRANSIENT, and it means the chop half of a chop-and-slam is unscoreable on principle.

### rpm_throttle_surrogate_unverified  [UNVERIFIED]
- **value**: linear 1800..5800 rpm
- **detail**: no throttle-to-rpm map exists in the repo. Linear is used, anchored by 80% throttle -> 5000 rpm which matches the deck reference point exactly. Part-throttle rpm is a guess.

### trend_state_reset_per_profile  [VERIFIED]
- **value**: TwinCore.reset()
- **detail**: reset() clears rul_engine's EWMA and 50-frame deque, the core's only mutable state, so profiles cannot inherit each other's RUL trend history.

## Shared - synthetic fault injection

Source: `shared.fault_injection.injection_caveats()`

### injections_are_offsets_not_failures  [VERIFIED]
- **value**: 10 scenarios
- **detail**: an injection displaces one or more channels from the deck's healthy prediction by a fixed amount. It does NOT simulate a failure mechanism: a real cooling failure couples coolant, EGT and oil over time, whereas here only the named channels move and the rest stay at equilibrium. These cases test the DETECTION PATH; they are not evidence about accuracy on real hardware.

### injection_magnitudes_are_chosen  [UNVERIFIED]
- **value**: {'coolant_temp_C': 10.0, 'EGT_mean_C': 50.0, 'oil_temperature_C': 15.0, 'oil_pressure_bar': 0.8, 'fuelflow_kgh': 1.5, 'rpm': 250.0}
- **detail**: magnitudes were picked to be plainly visible -- hundreds to thousands of times the gate's measured residual resolution ({'rpm': 1.5, 'EGT_mean_C': 0.25, 'coolant_temp_C': 0.0029, 'oil_temperature_C': 0.00035, 'fuelflow_kgh': 0.0007, 'oil_pressure_bar': 3.8e-05} is the admission tolerance derived from it). They are not calibrated to any observed Rotax failure severity. CASE 5 reports the smallest offset that actually crosses the gate, which is the honest number.

### label_mapping_is_dataset_property  [UNVERIFIED]
- **value**: channel offset -> fault_label
- **detail**: which label an offset produces is a property of the Cantera-generated training set, declared ~70% faithful to a real engine, and of a gate that is untrusted pre-retrain. CASE 6 scans and prints the mapping rather than asserting one. A label being plausible is not the same as it being correct.

### dead_class_cannot_be_diagnosed  [VERIFIED]
- **value**: ['fuel_pressure_dev']
- **detail**: fuel_pressure_dev appears in fault_probabilities with small nonzero mass but never wins, including for direct fuel-flow injections. One of five advertised labels is therefore undiagnosable. CASE 7 asserts it never becomes fault_label instead of hiding it, and it must not be used in a demo.

### rul_collapses_under_injection  [VERIFIED]
- **value**: 182.48 healthy -> -0.06 raw under lubrication
- **detail**: measured: rul_raw falls from 182.4773909895507 at the healthy point to about -0.056 for the lubrication case, i.e. past zero. rul_trusted stays False and rul_units is 'unknown', so RUL must never be rendered as minutes remaining. It is a direction, not a duration.

### residuals_reported_unsigned  [VERIFIED]
- **value**: -0.8 bar injected reads 0.8
- **detail**: measured: the twin's residuals dict carries magnitudes, not signed deviations, so a pressure DROP and a pressure RISE of equal size are indistinguishable downstream. Anything comparing residuals to injected offsets must compare absolute values, and a UI cannot infer direction from residuals alone -- use expected vs features.

### four_twin_statuses_not_three  [VERIFIED]
- **value**: ['HEALTHY', 'ADVISORY', 'FAULT', 'UNAVAILABLE']
- **detail**: discovered by injection: oil_pressure_low returns status='ADVISORY' with is_healthy=False and fault_label=None. ADVISORY is produced by the stats-range advisory channel, independently of the 0.65 gate, so the twin has FOUR statuses and any UI must render all four. Not the same vocabulary as throttle_dynamics.wire_status().

### oil_pressure_sensitivity_gap  [VERIFIED]
- **value**: -1.0 bar of 3.162 -> p_anom 0.5679, no crossing
- **detail**: measured: losing 32% of oil pressure does NOT cross the gate; it scores 0.5679, only +0.0235 above the healthy 0.5444, and reports ADVISORY. Meanwhile the measured residual resolution for that channel is 0.00019 bar, so the gate is strongly NON-MONOTONIC in offset: tiny changes move the score, a large one barely does. A real oil pressure failure could be missed. Pinned in KNOWN_SUBGATE.

### p_anom_is_not_severity_or_direction  [VERIFIED]
- **value**: coolant +10 == +25; fuel -1.5 == +1.5
- **detail**: measured: a +10 C and a +25 C coolant excursion both score exactly 0.7229, so p_anom carries no severity information. A 1.5 kg/h fuel DEFICIT and a 1.5 kg/h EXCESS both score exactly 0.6651 with the same label, because residuals are reported unsigned -- the gate cannot distinguish opposite physical faults. p_anom answers 'is something wrong', not 'how badly' or 'which way'. Pinned in IDENTICAL_PAIRS.

### labels_reflect_channel_count_not_mechanism  [UNVERIFIED]
- **value**: oil_hot -> sensor_drift; oil_hot+press_low -> lubrication
- **detail**: measured: a lone oil-temperature excursion is labelled sensor_drift, while the same excursion combined with a pressure loss is labelled lubrication_degradation. Reading a single implausible channel as an instrumentation problem is plausible behaviour, but it is a property of the Cantera-generated training set (~70% faithful), not validated physics.

### gate_is_non_monotonic  [VERIFIED]
- **value**: coolant crosses at 0.038 C but not at -10 C
- **detail**: MEASURED, and it invalidates any reading of p_anom as severity. CASE 5 bisects the smallest crossing offset, CASE 6 applies a large one, and they disagree: coolant crosses at 0.0383 C yet -10 C scores 0.5965 and does not cross; rpm crosses at +88.96 yet +250 scores 0.5457 and does not; oil pressure never crosses by bisection although its residual resolution is 0.00019 bar. The gate is tree-based, so a threshold is a LEAF BOUNDARY, not a floor above which detection is guaranteed. Same phenomenon as the 500 ft altitude leaf width in stress_sim.

### oil_temperature_hypersensitive  [VERIFIED]
- **value**: 0.0017 C crosses the gate
- **detail**: MEASURED: an oil-temperature offset of 1.7 mK reaches the 0.65 gate and reports FAULT, against an admission tolerance of 0.35 mK -- a band of about 5x in millikelvin between 'admitted as healthy' and 'reported as faulty'. This is the quantitative justification for the steady-state admission gate in throttle_dynamics: the transient lag it was previously admitting at 2% of step was 0.157 C, which is 92x this detection threshold, so every transient frame was guaranteed to read FAULT. Note the safety asymmetry: oil TEMPERATURE is hypersensitive (false positives) while oil PRESSURE misses a 32% loss (false negatives).

### safety_alert_never_observed  [UNVERIFIED]
- **value**: 0 of 10 injections
- **detail**: safety_alert was False and safety_breaches empty for every injection, including cases with rul_raw at -0.06 and -25.26 and with advisories present. So the safety channel is either unreachable through the six channels injected here or it is a second dead path alongside fuel_pressure_dev. NOT yet established which; do not present safety_alert as a working feature until a case is found that fires it.

### rul_grades_severity_where_p_anom_does_not  [VERIFIED]
- **value**: 182.5 healthy -> -25.3 overheat_coupled
- **detail**: measured across injections: rul_raw orders as 182.5 healthy, 165.9 fuel, 163.7 oil pressure, 120.3 coolant (identical for +10 and +25 C, so it saturates too), 53.0 EGT, 18.4 oil hot, -0.06 lubrication, -25.3 coupled overheat. It carries more severity information than p_anom, which is flat. But it goes NEGATIVE, rul_units is 'unknown' and rul_trusted is False throughout, so it is a direction of travel and must never be rendered as minutes remaining.

## Summary

- 43 declared caveats, 18 marked UNVERIFIED.
- Regression invariant: p_anom = 0.5443998040908319 at rpm 5000, throttle 80 %, 6000 ft, 10 C.
- Fault gate threshold 0.65 is UNTRUSTED pre-retrain.
- Multiclass label 'fuel_pressure_dev' is a dead class and is never predicted.
- RUL is emitted with rul_trusted = False and rul_units = 'unknown'.
- Transient frames are not scored by design; they surface as UNAVAILABLE with a reason.
