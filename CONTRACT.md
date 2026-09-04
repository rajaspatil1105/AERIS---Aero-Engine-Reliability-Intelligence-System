# AERIS response contract

Frozen at commit 11d1efa. `POST /frames` returns 34 keys, always all 34:
the 32 fields of `TwinFrame` plus `seq` and `models_trusted`. A refused
frame has the same keys as a scored one. Consumers check for null, never
for absence. Pinned by CASE 12 in `node3_service/api.py::_self_test`;
adding a field means updating that tuple and this file in one commit.

## Reading a frame

`refusal_class` decides everything else. When it is null the frame was
scored and the ML fields are populated. When it is set, `ml_evaluated` is
false and `anomaly_probability`, `fault_label` and `rul` are all null.

- `transient` -- the throttle is moving faster than 0.5 %/s, or fewer than
  4*tau (100 s, set by oil temperature) have passed since it last moved.
  `in_envelope` stays true and `admit_reason` carries prose for the
  operator. This clears itself; show a settling indicator, not an alarm.
- `envelope_recoverable` -- outside the trained range on a channel the
  pilot controls: throttle, rpm, altitude. `envelope_violations` names the
  channel and the range. The operator can fly back into the envelope.
- `envelope_persistent` -- outside the trained range on a channel nobody
  controls, i.e. ambient temperature. Will not clear on its own.
- `telemetry_unusable` -- residuals could not be computed at all.

Hard safety limits are evaluated before admission, so a refused frame can
still return `status = CRITICAL` with `safety_alert` true and
`safety_breaches` populated. Refusal suppresses diagnosis, never safety.

## Status precedence

CRITICAL, FAULT, ADVISORY, HEALTHY, UNAVAILABLE. Both envelope exits and
transient refusals report UNAVAILABLE; `refusal_class` is what separates
them, which is why the UI must branch on it and not on `status` alone.

## Trust flags

`models_trusted` is false in this build: the gate sits near chance, RUL R2
is negative, and `fuel_pressure_dev` is a dead class. `rul_trusted` and
`caveats` carry the same warning per frame. The plumbing is verified; the
models are placeholders and the UI must say so on screen.

## Session semantics

`POST /sessions` resets the RUL EWMA and the admission history
(`prev_payload`, `prev_monotonic`, `last_throttle_change_monotonic`), so a
new session starts settled and scores its first frame immediately.

Admission state is process-wide, and `dt` is measured from frame arrival,
not from a client timestamp, because `TelemetryIn` carries no time field
and `contract_probe.py` pins it at nine. One feeder posts frames; browsers
poll. Concurrent producers interleave `dt` and corrupt the rate check.
See CAVEATS.md.