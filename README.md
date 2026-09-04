# AERIS - Aero Engine Reliability Intelligence System

A three-node pipeline that scores live engine telemetry against a steady-state
digital twin: Node 1 ingests and adapts raw frames, Node 2 runs the physics deck,
residual calculation, anomaly gate, multiclass fault predictor and RUL engine,
Node 3 persists all 68 columns and serves REST + WebSocket.

## Verify

    $env:AERIS_DB = "C:\aeris_data\verify.db"
    C:\venvs\aeris_verify\Scripts\python.exe -u verify_all.py

19 module self-tests, ~250 s (throttle_dynamics dominates at ~170 s).
Use `--fast` for a 55 s check that skips the four heavy modules, `--only <substr>`
to run one, `--list` to enumerate. Results are written to `verify_all.log`.
Run without `--fast` before committing.

## Demo path

    python -u -m shared.stress_sim          # envelope sweep: scored / declined / refused
    python -u -m shared.throttle_dynamics   # transient admission gate, four outcomes
    python -u -m shared.fault_injection     # synthetic degradations crossing the gate

## Regression invariant

At rpm 5000, throttle 80 %, altitude 6000 ft, OAT 10 C the twin must return
`anomaly_probability = 0.5443998040908319` exactly. Any change to the adapter,
deck, baselines or residual calculation that moves this number is a regression
until proven otherwise.

## Known limits

The full set is generated from source by `make_caveats.py` into `CAVEATS.md`
(46 declared caveats, 20 of them UNVERIFIED). Do not edit `CAVEATS.md` by hand.
The five that matter most for interpreting any output:

The anomaly gate threshold of 0.65 is untrusted pre-retrain, and the gate was
measured non-monotonic and direction-blind - coolant crosses at +0.038 C but
-10 C does not cross at all, and oil pressure never crosses in either direction.
The multiclass label `fuel_pressure_dev` is a dead class and is never predicted,
so a genuine fuel pressure deviation cannot be diagnosed. RUL is emitted with
`rul_trusted = False` and `rul_units = 'unknown'`; treat it as an ordering, not
a time. Transient frames are not scored by design and surface as `UNAVAILABLE`
with a reason, which costs roughly 100 s of settling after a throttle step. The
deck accepts throttle only in [56.5, 100] % and ambient only in
[-27.98, 30] C, so idle, low cruise and hot days are refused rather than scored.

Training data is Cantera-generated from limited public Rotax 915 iS figures and
is estimated ~70 % faithful. AERIS demonstrates the detection architecture, not
certified thresholds.

## Frontend integration notes

`POST /frames` takes `TelemetryIn` - 9 required fields, no defaults:
altitude_ft, ambient_temperature_C, throttle_pct, rpm, fuelflow_kgh,
coolant_temp_C, EGT_mean_C, oil_pressure_bar, oil_temperature_C.
The other 59 stored columns are derived server-side. Returns 201 with a
34-key payload; the shape is identical for every status.

Four statuses, not three: HEALTHY, ADVISORY, FAULT, UNAVAILABLE.
ADVISORY has `is_healthy=false` and `fault_label=null` - a non-healthy frame
does not always carry a label. UNAVAILABLE sets is_healthy, anomaly_probability
and rul to null and puts the reason in `envelope_violations`.

Display `rul_raw`, not `rul`. `rul` is EWMA-smoothed and lags badly on a single
frame (oil-hot: rul_raw 18.4 vs rul 164.4). Both carry `rul_trusted=false` and
`rul_units="unknown"`; rul_raw can go negative.

`residuals` are unsigned - do not infer direction from them. `headline` already
contains "(unvalidated)". `safety_alert` was false across all ten synthetic
injections; do not build a UI element that depends on it firing.

`POST /explain` returns 200 - the twin runs with `explain=True`. Response is
`status, fault_label, explanation, caveat, elapsed_ms`. The first call after
startup pays a ~7-10 s SHAP warm-up (done during app startup, not on the
request); subsequent calls are ~2 ms. The `caveat` field is not decoration:
SHAP attributions over a gate that measures at chance explain the model, not
the engine.

The steady-state admission pre-filter runs SERVER-SIDE: `api.py` calls
`admit_frame()` from `shared/throttle_dynamics.py` on every POST. A frame that
arrives during a throttle transient is refused, not scored - status
`UNAVAILABLE` with a machine-readable `refusal_class`. Clients do not need to
pre-filter, and must not render a refusal as a fault.

`refusal_class` is `transient` (throttle moving faster than 0.5 %/s, or still
inside the settling window after a step), `envelope_recoverable` (throttle or
ambient outside the deck range, will score again on return),
`envelope_persistent` (outside a range that will not recover in this flight),
or `telemetry_unusable` (non-finite input - unreachable over HTTP, since
`TelemetryIn` rejects it at the edge with 422). It is `null` on scored frames.

Admission `dt` is measured from ARRIVAL time, not sample time, so network
jitter is indistinguishable from a slower sample rate, and the state is
process-wide - it assumes ONE producer. Both are in `CAVEATS.md`.

`GET /caveats` returns session provenance, not the 46 declared caveats - see
`CAVEATS.md` for those. Captured live payloads for every status are in
`contract/`.
`GET /live` is a JSON snapshot endpoint, not a stream - poll it, no WebSocket or
SSE client needed. It echoes the last processed frame. Note `gate_threshold` is
null on UNAVAILABLE frames, so the UI cannot rely on it always being present.

Every frame response embeds a `caveats` block with measured model metrics: gate
precision 0.511 / recall 0.9982 / F1 0.676 (equals the always-fault baseline at
a 0.511 prior), RUL R2 -0.103 / MAE 107. Surface this in the UI rather than
hiding it - the backend states its own limits on every frame by design.

## UI

Plain static files in `static/`, mounted at `/ui` with `html=True`. No build
step, no npm. The mount is guarded on a missing directory, so deleting
`static/` degrades the UI without stopping the service.

`GET /frames` returns a thin projection, not the 34-key frame:
id, seq, ts_utc, status, refusal_class, meaningful, p_anom, fault_label,
confidence, rul_raw, rul_smoothed, rul_trusted, latency_ms. Schema version 2
added `refusal_class`; rows written before the migration carry `null` there
even when UNAVAILABLE, so treat null as "unknown, fetch the detail" rather
than "not refused". Full detail is `GET /frames/{seq}`.
