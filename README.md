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
(43 declared caveats, 18 of them UNVERIFIED). Do not edit `CAVEATS.md` by hand.
The five that matter most for interpreting any output:

The anomaly gate threshold of 0.65 is untrusted pre-retrain, and the gate was
measured non-monotonic and direction-blind - coolant crosses at +0.038 C but
-10 C does not cross at all, and oil pressure never crosses in either direction.
The multiclass label `fuel_pressure_dev` is a dead class and is never predicted,
so a genuine fuel pressure deviation cannot be diagnosed. RUL is emitted with
`rul_trusted = False` and `rul_units = 'unknown'`; treat it as an ordering, not
a time. Transient frames are not scored by design and surface as `UNAVAILABLE`
with a reason, which costs roughly 250 s of settling after a throttle step. The
deck accepts throttle only in [56.5, 100] % and ambient only in
[-27.98, 30] C, so idle, low cruise and hot days are refused rather than scored.

Training data is Cantera-generated from limited public Rotax 915 iS figures and
is estimated ~70 % faithful. AERIS demonstrates the detection architecture, not
certified thresholds.
