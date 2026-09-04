"""
AERIS Phase 1 -- Node 3 canonical (68-column) ingest and mission replay.

WHY
---
api.py's /frames and /ws/ingest accept the 9 raw fields the twin consumes.
That is all the twin needs, but it is NOT enough to record a mission: 59 of
the 68 canonical columns never arrive, so a stored flight cannot be replayed,
cannot be re-scored after a gate retrain, and has nowhere to put the vibration
features dsp_fft.py already computes.

This module adds a SECOND door that accepts the full canonical frame:

    68-col TelemetryPayload  ->  raw_store   (all 68 columns persisted)
                             ->  adapter     (9 twin features derived)
                             ->  TwinCore    (unchanged)
                             ->  store       (34-key twin output persisted)

The existing 9-field endpoints are untouched and keep working, so demo_ramp.py
and every existing self-test are unaffected.

DESIGN NOTES
------------
* Additive. This is an APIRouter with injected dependencies. api.py is not
  modified by this file; wiring is a single include_router() call.
* `provided` is taken from the raw JSON keys, not from the parsed model. That
  is what lets the adapter distinguish "the source sent 0.0" from "the source
  omitted the field and pydantic supplied a default".
* Adapter refusals become HTTP 422 / a WS error message. A frame that cannot be
  honestly converted is never processed and never stored.
* One `seq` per accepted frame, shared by both tables, so raw_telemetry and the
  twin frames table can be joined row-for-row.
* The twin store's exact API is discovered at runtime rather than assumed, and
  what was discovered is reported. If persistence of the twin frame is not
  possible, raw storage and replay still work and the limitation is stated.
"""

from __future__ import annotations

import inspect
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.schema import COLUMN_NAMES, EngineState, TelemetryPayload  # noqa: E402
from node1_ingestion.adapter import (  # noqa: E402
    ADAPTER_VERSION,
    AdapterError,
    adapter_caveats,
    adapter_declarations,
    to_twin_payload,
    twin_frame_to_dict,
)
from node3_service.raw_store import RawStore, RawStoreError  # noqa: E402

CANONICAL_VERSION = "0.1.0"

router = APIRouter(tags=["canonical"])


class CanonicalError(Exception):
    """Raised when the canonical path is misconfigured."""


@dataclass
class CanonicalState:
    """Injected dependencies plus counters."""

    core: Any = None
    store: Any = None
    raw: Optional[RawStore] = None
    db_session_id: int = 0
    seq: int = 0
    accepted: int = 0
    refused: int = 0
    raw_written: int = 0
    twin_written: int = 0
    twin_store_mode: str = "unconfigured"
    latencies: List[float] = field(default_factory=list)

    def snapshot(self) -> Dict[str, Any]:
        lat = sorted(self.latencies)
        def pct(q: float) -> Optional[float]:
            return lat[min(int(q * len(lat)), len(lat) - 1)] if lat else None
        return {
            "canonical_version": CANONICAL_VERSION,
            "adapter_version": ADAPTER_VERSION,
            "canonical_columns": len(COLUMN_NAMES),
            "twin_features": 9,
            "db_session_id": self.db_session_id,
            "seq": self.seq,
            "frames_accepted": self.accepted,
            "frames_refused": self.refused,
            "raw_rows_written": self.raw_written,
            "twin_rows_written": self.twin_written,
            "twin_store_mode": self.twin_store_mode,
            "latency_ms": {
                "mean": (sum(lat) / len(lat)) if lat else None,
                "p95": pct(0.95),
                "max": lat[-1] if lat else None,
                "samples": len(lat),
            },
            "assumptions": adapter_caveats(),
            "declarations": adapter_declarations(),
        }


STATE = CanonicalState()


def _discover_twin_writer(store: Any) -> Tuple[str, Optional[Callable]]:
    """Work out how to hand a twin frame to store.py without assuming."""
    if store is None:
        return "absent (twin output not persisted)", None
    fn = getattr(store, "add_frame", None)
    if not callable(fn):
        return "no add_frame method", None
    try:
        sig = inspect.signature(fn)
        params = [
            n for n, p in sig.parameters.items()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            and n != "self"
        ]
    except (TypeError, ValueError):
        params = []
    return f"add_frame({', '.join(params) or '...'})", fn


def configure(
    core: Any,
    store: Any = None,
    raw: Optional[RawStore] = None,
    db_session_id: int = 0,
    db_path: Optional[str] = None,
) -> CanonicalState:
    """Inject the twin, the twin store, and the raw store."""
    if core is None:
        raise CanonicalError("a TwinCore instance is required")
    STATE.core = core
    STATE.store = store
    STATE.raw = raw if raw is not None else RawStore(db_path)
    STATE.db_session_id = int(db_session_id)
    STATE.seq = 0
    STATE.accepted = STATE.refused = 0
    STATE.raw_written = STATE.twin_written = 0
    STATE.latencies.clear()
    mode, _ = _discover_twin_writer(store)
    STATE.twin_store_mode = mode
    return STATE


_twin_persist_errors: List[str] = []


def _try_persist_twin(out: Dict[str, Any], seq: int) -> bool:
    """Write the twin frame through store.py's own API.

    Unlike the previous version this records *why* a write failed instead of
    collapsing every exception into False. A TypeError from a signature
    mismatch is retried with different arguments; a TypeError raised inside
    the store is reported, not retried past.
    """
    mode, fn = _discover_twin_writer(STATE.store)
    if fn is None:
        _twin_persist_errors.append(f"seq={seq}: no writer discovered")
        return False

    candidates = (
        ("fn(out)", lambda: fn(out)),
        ("fn(out, seq)", lambda: fn(out, seq)),
        ("fn(frame=out)", lambda: fn(frame=out)),
        ("fn(out, session_id=...)", lambda: fn(out, session_id=STATE.db_session_id)),
    )
    tried = []
    for label, attempt in candidates:
        try:
            attempt()
            return True
        except TypeError as exc:
            msg = str(exc)
            # only a genuine binding failure justifies trying the next shape
            if ("argument" in msg or "positional" in msg or "keyword" in msg):
                tried.append(f"{label}: {msg}")
                continue
            _twin_persist_errors.append(
                f"seq={seq}: {label} raised TypeError inside store: {msg}")
            return False
        except Exception as exc:
            _twin_persist_errors.append(
                f"seq={seq}: {label} raised {type(exc).__name__}: {exc}")
            return False
    _twin_persist_errors.append(
        f"seq={seq}: no call shape bound. tried -> " + " | ".join(tried))
    return False


def process_canonical(
    body: Dict[str, Any],
    require_all: bool = True,
) -> Dict[str, Any]:
    """Validate -> store raw -> adapt -> twin -> store twin. Shared by REST/WS.

    Raises AdapterError or ValidationError; callers translate to 422.
    """
    if STATE.core is None:
        raise CanonicalError("canonical path not configured; call configure()")

    t0 = time.perf_counter()
    payload = TelemetryPayload(**body)
    provided = set(body.keys()) if require_all else None

    result = to_twin_payload(payload, provided=provided, strict=False)
    if not result.ok:
        STATE.refused += 1
        raise AdapterError("; ".join(result.refusals))

    seq = STATE.seq
    STATE.seq += 1

    raw_ok = False
    if STATE.raw is not None:
        try:
            STATE.raw.add_frame(payload, STATE.db_session_id, seq)
            STATE.raw_written += 1
            raw_ok = True
        except RawStoreError:
            raw_ok = False

    out = twin_frame_to_dict(STATE.core.process(result.features))
    twin_ok = _try_persist_twin(out, seq)
    if twin_ok:
        STATE.twin_written += 1

    dt = (time.perf_counter() - t0) * 1000.0
    STATE.latencies.append(dt)
    STATE.accepted += 1

    return {
        "seq": seq,
        "status": out.get("status"),
        "anomaly_probability": out.get("anomaly_probability"),
        "rul_trusted": out.get("rul_trusted"),
        "in_envelope": out.get("in_envelope"),
        "meaningful": result.meaningful,
        "engine_state": result.engine_state,
        "egt_spread_c": result.egt_spread_c,
        "raw_persisted": raw_ok,
        "twin_persisted": twin_ok,
        "warnings": result.warnings,
        "server_ms": round(dt, 2),
        "twin": out,
    }


# ------------------------------------------------------------------ #
# REST
# ------------------------------------------------------------------ #

@router.get("/canonical/meta")
def canonical_meta() -> Dict[str, Any]:
    """What this door accepts, and what it cannot verify."""
    return {
        **STATE.snapshot(),
        "columns": list(COLUMN_NAMES),
        "note": (
            "vib_samples is accepted but not persisted. Replay reproduces DSP "
            "outputs, not the raw accelerometer window."
        ),
    }


@router.post("/frames/canonical", status_code=201)
async def post_canonical_frame(body: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return process_canonical(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=json.loads(exc.json()))
    except AdapterError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "frame refused by adapter", "refusals": str(exc)},
        )
    except CanonicalError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/replay/{db_session_id}")
def replay_session(
    db_session_id: int,
    start_seq: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=5000),
    include_twin_features: bool = Query(False),
) -> Dict[str, Any]:
    """Stream a recorded mission back out of raw_telemetry.

    This is the mission replay primitive: the frames returned are the exact
    canonical telemetry that was ingested, so they can be re-fed to the twin
    (e.g. after a gate retrain) and produce a fresh verdict.
    """
    if STATE.raw is None:
        raise HTTPException(status_code=503, detail="raw store not configured")
    span = STATE.raw.session_span(db_session_id)
    if span["frames"] == 0:
        raise HTTPException(
            status_code=404,
            detail=f"no raw telemetry recorded for session {db_session_id}",
        )
    frames: List[Dict[str, Any]] = []
    for seq, payload in STATE.raw.replay(db_session_id, start_seq, limit):
        item: Dict[str, Any] = {"seq": seq, "telemetry": payload.model_dump(mode="json")}
        if include_twin_features:
            r = to_twin_payload(payload, strict=False)
            item["twin_features"] = r.features
            item["convertible"] = r.ok
        frames.append(item)
    return {
        "db_session_id": db_session_id,
        "span": span,
        "returned": len(frames),
        "start_seq": start_seq,
        "never_recorded_columns": STATE.raw.null_columns(db_session_id),
        "frames": frames,
    }


@router.get("/replay/{db_session_id}/rescore")
def rescore_session(
    db_session_id: int,
    limit: int = Query(200, ge=1, le=5000),
) -> Dict[str, Any]:
    """Re-run the current twin over a recorded mission.

    The point of storing raw telemetry: when the models change, old missions
    can be scored again. Status counts here come from the models loaded NOW,
    not from what was recorded at flight time.
    """
    if STATE.raw is None or STATE.core is None:
        raise HTTPException(status_code=503, detail="canonical path not configured")
    counts: Dict[str, int] = {}
    refused = 0
    n = 0
    for _seq, payload in STATE.raw.replay(db_session_id, 0, limit):
        r = to_twin_payload(payload, strict=False)
        if not r.ok:
            refused += 1
            continue
        out = twin_frame_to_dict(STATE.core.process(r.features))
        s = str(out.get("status"))
        counts[s] = counts.get(s, 0) + 1
        n += 1
    if n == 0 and refused == 0:
        raise HTTPException(status_code=404, detail="nothing to rescore")
    return {
        "db_session_id": db_session_id,
        "rescored": n,
        "refused": refused,
        "status_counts": counts,
        "caveat": (
            "verdicts produced by the models currently loaded; gate F1 is at "
            "chance and RUL R2 is negative, so these counts are not a "
            "trustworthy diagnosis"
        ),
    }


# ------------------------------------------------------------------ #
# WebSocket
# ------------------------------------------------------------------ #

@router.websocket("/ws/ingest/canonical")
async def ws_ingest_canonical(ws: WebSocket) -> None:
    await ws.accept()
    await ws.send_json({
        "hello": "canonical ingest",
        "expects": len(COLUMN_NAMES),
        "adapter_version": ADAPTER_VERSION,
        "assumptions": [c["id"] for c in adapter_caveats()],
    })
    try:
        while True:
            text = await ws.receive_text()
            try:
                body = json.loads(text)
                if not isinstance(body, dict):
                    raise ValueError("frame must be a JSON object")
                ack = process_canonical(body)
                await ws.send_json({
                    "ok": True,
                    "seq": ack["seq"],
                    "status": ack["status"],
                    "raw_persisted": ack["raw_persisted"],
                    "server_ms": ack["server_ms"],
                })
            except WebSocketDisconnect:
                raise
            except (ValidationError, AdapterError, ValueError, json.JSONDecodeError) as exc:
                await ws.send_json({
                    "ok": False,
                    "error": type(exc).__name__,
                    "detail": str(exc)[:400],
                })
    except WebSocketDisconnect:
        return

# ==================================================================== #
# Self-check
# ==================================================================== #

def _canonical_frame(i: int, oil_kpa: float = 250.0) -> Dict[str, Any]:
    """A full canonical frame, hand-built raw JSON. Plausible values, NOT
    guaranteed on the baseline manifold -- see CASE 2b."""
    return {
        "timestamp": 1_760_000_000.0 + i * 0.1,
        "frame_id": i,
        "session_id": "canonical-selftest",
        "flight_time_hr": i * 0.1 / 3600.0,
        "engine_state": "RUNNING",
        "rpm": 5000.0,
        "throttle_pct": 80.0,
        "altitude_m": 1828.8,
        "oat_c": 10.0,
        "fuel_flow_lph": 30.0,
        "coolant_temp_in_c": 71.0,
        "coolant_temp_out_c": 78.0,
        "egt_1_c": 450.0, "egt_2_c": 455.0, "egt_3_c": 460.0, "egt_4_c": 465.0,
        "egt_spread_c": 15.0,
        "oil_pressure_kpa": oil_kpa,
        "oil_temp_c": 92.0,
        "cht_1_c": 165.0, "cht_2_c": 168.0, "cht_3_c": 170.0, "cht_4_c": 167.0,
        "bus_voltage_v": 13.8,
        "vib_1x_g": 0.42, "vib_2x_g": 0.11, "vib_3x_g": 0.05,
        "vib_f0_hz": 83.33, "vib_crest_factor": 3.1,
    }


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    fails: List[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            fails.append(msg)

    print("CANONICAL SELF-CHECK")
    print(f"  version {CANONICAL_VERSION} | adapter {ADAPTER_VERSION}")

    tmp = Path(os.environ.get("TEMP", ".")) / f"aeris_canon_{os.getpid()}.db"
    for suf in ("", "-wal", "-shm"):
        Path(str(tmp) + suf).unlink(missing_ok=True)

    # -- build dependencies ------------------------------------------- #
    print("\nCASE 0  wiring")
    from node2_twin_core.twin_core import TwinCore
    core = TwinCore(warm_up=False, explain=False)

    store = None
    try:
        from node3_service.store import Store
        store = Store(tmp)
        for opener in ("open_session", "start_session", "new_session"):
            fn = getattr(store, opener, None)
            if callable(fn):
                try:
                    fn(note="canonical self-test")
                except TypeError:
                    fn()
                break
    except Exception as exc:
        print(f"  store.py unavailable ({type(exc).__name__}); twin output not persisted")

    raw = RawStore(tmp, flush_every=10, flush_seconds=1.0,
                   create_sessions_if_absent=True)
    sid_row = raw.conn.execute(
        "SELECT id FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
    sid = int(sid_row[0]) if sid_row else 1

    st = configure(core, store=store, raw=raw, db_session_id=sid)
    print(f"  db              : {tmp.name}")
    print(f"  db_session_id   : {sid}")
    print(f"  twin store mode : {st.twin_store_mode}")
    print(f"  canonical cols  : {len(COLUMN_NAMES)}")

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # -- CASE 1: meta -------------------------------------------------- #
    print("\nCASE 1  GET /canonical/meta")
    r = client.get("/canonical/meta")
    m = r.json()
    print(f"  {r.status_code} columns={m['canonical_columns']} "
          f"twin_features={m['twin_features']}")
    print(f"  assumptions declared: {[a['id'] for a in m['assumptions']]}")
    check(r.status_code == 200, "meta failed")
    check(m["canonical_columns"] == 68, "not 68 columns")
    check(len(m["assumptions"]) == 2, "assumptions not surfaced")

    # -- CASE 2: healthy canonical frame ------------------------------ #
    print("\nCASE 2  POST a full 68-column frame")
    r = client.post("/frames/canonical", json=_canonical_frame(0))
    b = r.json()
    print(f"  {r.status_code} seq={b.get('seq')} status={b.get('status')} "
          f"p_anom={b.get('anomaly_probability')}")
    print(f"  raw_persisted={b.get('raw_persisted')} "
          f"twin_persisted={b.get('twin_persisted')} "
          f"server={b.get('server_ms')} ms")
    check(r.status_code == 201, f"canonical post failed: {r.status_code}")
    check(b.get("raw_persisted") is True, "raw telemetry not persisted")
    print(f"  NOTE: this hand-built frame reads {b.get('status')} -- plausible but off the\n        baseline manifold (known gate defect). See CASE 2b.")

    # -- CASE 3: the 59 columns that used to be lost ------------------ #
    print("\nCASE 3  columns the 9-field door would have discarded")
    got = raw.get_frame(sid, 0)
    check(got is not None, "raw frame 0 not readable")
    if got is not None:
        print(f"  cht_1_c        = {got.cht_1_c}  (not in the 14-feature vector)")
        print(f"  bus_voltage_v  = {got.bus_voltage_v}")
        print(f"  vib_1x_g       = {got.vib_1x_g}  (DSP output, now stored)")
        print(f"  egt_1..4       = {got.egt_1_c}/{got.egt_2_c}/"
              f"{got.egt_3_c}/{got.egt_4_c} (twin sees only the mean)")
        check(got.cht_1_c == 165.0, "CHT lost")
        check(got.vib_1x_g == 0.42, "vibration lost")
        check(got.egt_1_c == 450.0 and got.egt_4_c == 465.0, "per-cylinder EGT lost")

    # -- CASE 4: refusal is 422, nothing stored ----------------------- #
    print("\nCASE 4  impossible and incomplete frames")
    before = raw.count(sid)
    bad = _canonical_frame(1, oil_kpa=-3.0)
    r = client.post("/frames/canonical", json=bad)
    print(f"  oil -3 kPa      -> {r.status_code}")
    check(r.status_code == 422, "negative pressure accepted")
    short = {k: v for k, v in _canonical_frame(2).items() if k != "oil_pressure_kpa"}
    r2 = client.post("/frames/canonical", json=short)
    print(f"  missing channel -> {r2.status_code}")
    check(r2.status_code == 422, "missing channel accepted")
    r3 = client.post("/frames/canonical", json={"rpm": "banana"})
    print(f"  wrong type      -> {r3.status_code}")
    check(r3.status_code == 422, "wrong type accepted")
    after = raw.count(sid)
    print(f"  raw rows {before} -> {after} (refused frames stored nothing)")
    check(before == after, "a refused frame was persisted")

    # -- CASE 5: seq alignment across a mission ----------------------- #
    print("\nCASE 5  ingest 60 frames, oil decaying")
    for i in range(1, 61):
        rr = client.post("/frames/canonical", json=_canonical_frame(i, 250.0 - i * 2.0))
        if rr.status_code != 201:
            fails.append(f"frame {i} rejected: {rr.status_code} {rr.text[:120]}")
            break
    raw.flush()
    span = raw.session_span(sid)
    print(f"  raw rows={span['frames']} seq={span['seq_first']}..{span['seq_last']}")
    print(f"  duration={span['duration_s']:.1f} s rate={span['rate_hz']:.2f} Hz")
    print(f"  accepted={STATE.accepted} refused={STATE.refused} "
          f"raw_written={STATE.raw_written}")
    check(span["frames"] == 61, f"expected 61 raw rows, got {span['frames']}")
    check(STATE.raw_written == STATE.accepted,
          "raw rows and accepted frames diverged")

    # -- CASE 6: replay -------------------------------------------- #
    print("\nCASE 6  GET /replay -- mission replay")
    r = client.get(f"/replay/{sid}?limit=5&include_twin_features=true")
    rp = r.json()
    print(f"  {r.status_code} returned={rp['returned']} of {rp['span']['frames']}")
    f0 = rp["frames"][0]
    print(f"  seq 0 telemetry keys : {len(f0['telemetry'])}")
    print(f"  seq 0 twin features  : {len(f0['twin_features'])} convertible="
          f"{f0['convertible']}")
    print(f"  never recorded       : {len(rp['never_recorded_columns'])} columns")
    check(r.status_code == 200, "replay failed")
    check(len(f0["telemetry"]) == 68, f"replay lost columns: {len(f0['telemetry'])}")
    check(len(f0["twin_features"]) == 9, "twin features not reconstructible")
    oils = [fr["telemetry"]["oil_pressure_kpa"] for fr in rp["frames"]]
    print(f"  oil kPa first 5      : {oils}")
    check(oils == sorted(oils, reverse=True), "replay lost flight order")

    # -- CASE 7: rescore ---------------------------------------------- #
    print("\nCASE 7  GET /replay/rescore -- re-run current models")
    r = client.get(f"/replay/{sid}/rescore?limit=61")
    rs = r.json()
    print(f"  {r.status_code} rescored={rs['rescored']} refused={rs['refused']}")
    print(f"  status counts: {rs['status_counts']}")
    check(r.status_code == 200, "rescore failed")
    check(rs["rescored"] == 61, f"rescored {rs['rescored']} of 61")
    print("  this is the payoff: a recorded mission can be scored again")

    # -- CASE 8: replay of an unknown session is 404 ------------------ #
    print("\nCASE 8  unknown session")
    r = client.get("/replay/99999")
    print(f"  /replay/99999 -> {r.status_code}")
    check(r.status_code == 404, "unknown session did not 404")

    # -- CASE 9: WebSocket canonical ingest --------------------------- #
    print("\nCASE 9  WS /ws/ingest/canonical")
    with client.websocket_connect("/ws/ingest/canonical") as ws:
        hello = ws.receive_json()
        print(f"  hello expects={hello['expects']} "
              f"assumptions={hello['assumptions']}")
        ws.send_text(json.dumps(_canonical_frame(200)))
        a1 = ws.receive_json()
        print(f"  frame ack ok={a1['ok']} seq={a1['seq']} status={a1['status']} "
              f"raw={a1['raw_persisted']}")
        check(a1["ok"] is True, "WS rejected a good frame")
        ws.send_text("{not json")
        a2 = ws.receive_json()
        print(f"  garbage   ok={a2['ok']} error={a2['error']}")
        check(a2["ok"] is False, "WS accepted garbage")
        ws.send_text(json.dumps(_canonical_frame(201, oil_kpa=-5.0)))
        a3 = ws.receive_json()
        print(f"  bad frame ok={a3['ok']} error={a3['error']}")
        check(a3["ok"] is False, "WS accepted impossible frame")
        ws.send_text(json.dumps(_canonical_frame(202)))
        a4 = ws.receive_json()
        print(f"  socket survived -> ok={a4['ok']} seq={a4['seq']}")
        check(a4["ok"] is True, "socket did not survive a bad frame")

    # -- CASE 10: cost -------------------------------------------- #
    print("\nCASE 10  cost of the canonical path")
    snap = STATE.snapshot()
    lat = snap["latency_ms"]
    print(f"  mean={lat['mean']:.2f} ms p95={lat['p95']:.2f} ms "
          f"max={lat['max']:.2f} ms samples={lat['samples']}")
    print(f"  budget 100 ms; twin alone was ~18-23 ms")
    check((lat["p95"] or 0) < 100.0, f"p95 {lat['p95']:.1f} ms over budget")
    print(f"  raw_written={snap['raw_rows_written']} "
          f"twin_written={snap['twin_rows_written']} "
          f"mode={snap['twin_store_mode']}")
    if snap["twin_rows_written"] == 0:
        print("  NOTE twin output not persisted on this path; raw telemetry and "
              "replay are unaffected. Wire-in to api.py will use its own store.")
    # -- CASE 2b: a genuinely on-manifold frame ----------------------- #
    print("\nCASE 2b  deck-derived on-manifold frame")
    from node1_ingestion.adapter import _deck_nominal_frame
    deck_payload = _deck_nominal_frame()[0]
    body2b = deck_payload.model_dump(mode="json")
    print(f"  body keys={len(body2b)} rpm={body2b.get('rpm')} "
          f"oil_kpa={body2b.get('oil_pressure_kpa')}")
    r = client.post("/frames/canonical", json=body2b)
    print(f"  {r.status_code} posting the BaselineDeck healthy point")
    if r.status_code != 201:
        fails.append(f"deck frame rejected: {r.status_code} {r.text[:200]}")
    else:
        d = r.json()
        st = d.get("status")
        pa = d.get("anomaly_probability")
        print(f"  status={st} p_anom={pa}")
        print(f"  raw_persisted={d.get('raw_persisted')} "
              f"twin_persisted={d.get('twin_persisted')}")
        check(st == "HEALTHY", f"on-manifold deck frame read {st} (p_anom={pa})")
        if isinstance(pa, float):
            print(f"  margin to 0.65 gate: {0.65 - pa:+.4f}")
            if pa > 0.60:
                print("  WARNING: healthy frame sits close to the gate; "
                      "retrain still outstanding")


    print(f"  integrity: {raw.integrity_check()}")
    check(raw.integrity_check() == "ok", "database integrity failed")

    raw.close()
    if store is not None:
        for closer in ("close", "shutdown"):
            fn = getattr(store, closer, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
                break
    for suf in ("", "-wal", "-shm"):
        Path(str(tmp) + suf).unlink(missing_ok=True)

    print()

    if _twin_persist_errors:
        print("\n  twin-persist failures recorded:")
        for m in _twin_persist_errors[:10]:
            print(f"    {m}")
    else:
        print("\n  twin-persist failures recorded: none")

    if fails:
        print("CANONICAL SELF-CHECK FAILED:")
        for m in fails:
            print(f"  - {m}")
        raise SystemExit(1)
    print("CANONICAL SELF-CHECK OK")
    print("  68 columns in, 9 to the twin, all 68 recorded, mission replayable")


if __name__ == "__main__":
    _self_test()
