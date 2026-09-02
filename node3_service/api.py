"""
AERIS Node 3 -- REST service over the Node 2 twin core.

    uvicorn node3_service.api:app --port 8000

DESIGN NOTES
------------
* One TwinCore and one Store per process, built during lifespan startup.
  TwinCore's SHAP warm-up costs ~6 s, so it happens once at boot and never
  on an operator's first click.
* /caveats is not decoration. The manifest says the gate is at chance and
  RUL R2 is negative; the dashboard is expected to render this, and the
  field `models_trusted` is false for every response produced by these
  artifacts.
* Every processed frame is persisted before the response is returned, so
  what the operator saw is what the database holds.
* create_app() returns the instance; node3_service/ingest.py attaches the
  WebSocket routes to the same app.

ENVIRONMENT
-----------
    AERIS_DB       sqlite path (default <root>/data/aeris.db)
    AERIS_EXPLAIN  "0" to skip SHAP warm-up (faster boot, /explain 503)
"""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import (BaseModel, Field, field_validator,
                      model_validator)

from node3_service.store import Store, StoreError

API_VERSION = "0.1.0"

RAW_FIELDS = (
    "altitude_ft", "ambient_temperature_C", "throttle_pct", "rpm",
    "fuelflow_kgh", "coolant_temp_C", "EGT_mean_C", "oil_pressure_bar",
    "oil_temperature_C",
)


def _json_safe(obj: Any) -> Any:
    """Replace non-finite floats with a string so the value can be reported.

    The client needs to see WHICH value was bad; it just cannot be sent as a
    JSON number. nan -> "NaN", inf -> "Infinity".
    """
    if isinstance(obj, float):
        if obj != obj:
            return "NaN"
        if obj == float("inf"):
            return "Infinity"
        if obj == float("-inf"):
            return "-Infinity"
        return obj
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (str, int, bool)) or obj is None:
        return obj
    return str(obj)


# Physical possibility bounds -- NOT the training envelope (which is much
# narrower and enforced separately by physics_deck). A value inside these
# bounds may still be abnormal; a value outside means the sensor is broken.
# Mirrors TWIN_BOUNDS in node1_ingestion/adapter.py. Keep both in sync.
# The density-altitude floor lives there too; scenario frames carrying
# density altitude must be validated with altitude_is_density=True.
DENSITY_ALT_FLOOR_FT = -6000.0

PHYSICAL_BOUNDS: dict[str, tuple[float, float]] = {
    "altitude_ft":           (-1500.0, 60000.0),   # Dead Sea to above ceiling
    "ambient_temperature_C": (-90.0, 70.0),        # Vostok to Death Valley
    "throttle_pct":          (0.0, 100.0),         # definitional
    "rpm":                   (0.0, 12000.0),       # cannot be negative
    "fuelflow_kgh":          (0.0, 500.0),         # cannot be negative
    "coolant_temp_C":        (-60.0, 300.0),
    "EGT_mean_C":            (-60.0, 1400.0),      # above this, metal fails
    "oil_pressure_bar":      (0.0, 20.0),          # ABSOLUTE: never negative
    "oil_temperature_C":     (-60.0, 300.0),
}


class TelemetryIn(BaseModel):
    """One engine telemetry frame. All nine channels are required."""

    altitude_ft: float
    ambient_temperature_C: float
    throttle_pct: float
    rpm: float
    fuelflow_kgh: float
    coolant_temp_C: float
    EGT_mean_C: float
    oil_pressure_bar: float
    oil_temperature_C: float

    @field_validator("*")
    @classmethod
    def finite(cls, v: float) -> float:
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("must be a finite number")
        return v

    @model_validator(mode="after")
    def physically_possible(self) -> "TelemetryIn":
        """Reject broken-sensor readings before they reach any model."""
        bad: list[str] = []
        for name, (lo, hi) in PHYSICAL_BOUNDS.items():
            v = float(getattr(self, name))
            if not (lo <= v <= hi):
                bad.append(f"{name}={v:g} outside physically possible "
                           f"[{lo:g}, {hi:g}]")
        if bad:
            raise ValueError("; ".join(bad))
        return self

    def payload(self) -> dict[str, float]:
        return {k: float(getattr(self, k)) for k in RAW_FIELDS}


class SessionIn(BaseModel):
    note: str | None = Field(default=None, max_length=200)


@dataclass
class ServiceState:
    """Process-wide singletons. Populated during lifespan startup."""

    core: Any = None
    store: Store | None = None
    manifest: Any = None
    explain_enabled: bool = False
    started_monotonic: float = field(default_factory=time.monotonic)
    boot_error: str | None = None
    frames_processed: int = 0
    last_frame: dict[str, Any] | None = None

    @property
    def ready(self) -> bool:
        return self.core is not None and self.store is not None

    def uptime_s(self) -> float:
        return round(time.monotonic() - self.started_monotonic, 1)


STATE = ServiceState()


def get_state(request: Request) -> ServiceState:
    st: ServiceState = request.app.state.aeris
    if not st.ready:
        raise HTTPException(
            status_code=503,
            detail=f"service not ready: {st.boot_error or 'starting up'}")
    return st


def _as_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    for attr in ("to_dict", "as_dict", "dict"):
        fn = getattr(result, attr, None)
        if callable(fn):
            return fn()
    return dict(vars(result))


def _process_and_store(st: ServiceState, payload: dict[str, float]) -> dict[str, Any]:
    try:
        frame = _as_dict(st.core.process(payload))
    except Exception as exc:  # twin core raises its own typed errors
        raise HTTPException(status_code=422,
                            detail=f"{type(exc).__name__}: {exc}") from exc
    try:
        seq = st.store.add_frame(frame)
    except StoreError as exc:
        raise HTTPException(status_code=500,
                            detail=f"persistence failed: {exc}") from exc
    st.frames_processed += 1
    st.last_frame = frame
    out = dict(frame)
    out["seq"] = seq
    out["models_trusted"] = False
    return out


def build_state(explain: bool | None = None) -> ServiceState:
    """Construct the singletons. Separated so tests can build synchronously."""
    st = ServiceState()
    if explain is None:
        explain = os.environ.get("AERIS_EXPLAIN", "1") != "0"
    try:
        from node2_twin_core.twin_core import TwinCore
        try:
            from node2_twin_core.manifest import ModelManifest
            st.manifest = ModelManifest.load()
        except Exception as exc:
            print(f"[api] manifest unavailable: {exc}")
        st.core = TwinCore(explain=explain)
        st.explain_enabled = bool(explain)
        st.store = Store()
        st.store.open_session(note="api service", manifest=st.manifest)
    except Exception as exc:
        st.boot_error = f"{type(exc).__name__}: {exc}"
        print(f"[api] STARTUP FAILED: {st.boot_error}")
    return st


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[api] starting up (twin core + shap warm-up) ...")
    t0 = time.monotonic()
    app.state.aeris = build_state()
    st: ServiceState = app.state.aeris
    if st.ready:
        print(f"[api] ready in {time.monotonic() - t0:.1f} s "
              f"| explain={st.explain_enabled}")
    yield
    if st.store is not None:
        try:
            st.store.close()
            print("[api] store closed cleanly")
        except Exception as exc:
            print(f"[api] store close failed: {exc}")

def create_app() -> FastAPI:
    app = FastAPI(
        title="AERIS Node 3",
        version=API_VERSION,
        description="Engine health twin core service. Models are placeholders "
                    "trained on simulated data; see /caveats.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request,
                                 exc: RequestValidationError) -> JSONResponse:
        """422 with a serialisable body even when the input was NaN/inf."""
        errors = []
        for e in exc.errors():
            errors.append({
                "field": ".".join(str(p) for p in e.get("loc", ())[1:]) or None,
                "message": str(e.get("msg", "invalid")),
                "input": _json_safe(e.get("input")),
                "type": str(e.get("type", "")),
            })
        return JSONResponse(status_code=422, content={
            "detail": "telemetry frame rejected",
            "errors": errors,
            "required_fields": list(RAW_FIELDS),
        })

    @app.get("/", tags=["meta"])
    def root() -> dict[str, Any]:
        st: ServiceState = app.state.aeris
        return {
            "service": "AERIS Node 3",
            "api_version": API_VERSION,
            "ready": st.ready,
            "boot_error": st.boot_error,
            "docs": "/docs",
            "warning": "models are placeholders trained on simulated data; "
                       "see /caveats before interpreting any output",
        }

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, Any]:
        st: ServiceState = app.state.aeris
        out: dict[str, Any] = {
            "ready": st.ready,
            "uptime_s": st.uptime_s(),
            "frames_processed": st.frames_processed,
            "explain_enabled": st.explain_enabled,
            "boot_error": st.boot_error,
        }
        if st.store is not None:
            out["db_path"] = str(st.store.path)
            out["db_integrity"] = st.store.integrity_check()
            out["session_id"] = st.store.session_id
            out["frames_written"] = st.store.frames_written
        return out

    @app.get("/caveats", tags=["meta"])
    def caveats(st: ServiceState = Depends(get_state)) -> dict[str, Any]:
        """Model trust surface. The dashboard is expected to render this."""
        out: dict[str, Any] = {
            "models_trusted": False,
            "data_provenance": "unknown",
            "measured_engine_data": False,
            "artifacts": [],
            "open_issues": [],
        }
        if st.manifest is not None:
            m = st.manifest
            out["artifacts"] = m.trust_report()
            out["open_issues"] = list(m.data.get("open_issues", []))
            prov = m.data.get("data_provenance", {})
            out["data_provenance"] = prov.get("source", "unknown")
            out["measured_engine_data"] = bool(prov.get("measured_engine_data"))
            out["models_trusted"] = all(a.trusted for a in m.artifacts)
        else:
            out["open_issues"] = ["model manifest unavailable; "
                                  "provenance cannot be verified"]
        return out

    @app.get("/manifest", tags=["meta"])
    def manifest(st: ServiceState = Depends(get_state)) -> dict[str, Any]:
        if st.manifest is None:
            raise HTTPException(404, "no model manifest loaded")
        d = st.manifest.data
        return {
            "schema_version": d.get("schema_version"),
            "generated_at_utc": d.get("generated_at_utc"),
            "feature_order": d.get("feature_order"),
            "baseline_input_order": d.get("baseline_input_order"),
            "training_envelope": d.get("training_envelope"),
            "safety_limits": d.get("safety_limits"),
            "data_provenance": d.get("data_provenance"),
            "artifact_count": len(st.manifest.artifacts),
        }

    # -- inference -------------------------------------------------------
    @app.post("/frames", tags=["inference"], status_code=201)
    def post_frame(t: TelemetryIn,
                   st: ServiceState = Depends(get_state)) -> dict[str, Any]:
        """Process one telemetry frame, persist it, return the verdict."""
        return _process_and_store(st, t.payload())

    @app.post("/explain", tags=["inference"])
    def post_explain(t: TelemetryIn,
                     top_n: int = Query(5, ge=1, le=14),
                     st: ServiceState = Depends(get_state)) -> dict[str, Any]:
        """Attribution for one frame. Costs ~2 ms once warm."""
        if not st.explain_enabled:
            raise HTTPException(
                503, "explanations disabled (AERIS_EXPLAIN=0)")
        t0 = time.perf_counter()
        try:
            frame = _as_dict(st.core.process(t.payload(), explain=True))
        except TypeError:
            frame = _as_dict(st.core.process(t.payload()))
        except Exception as exc:
            raise HTTPException(422, f"{type(exc).__name__}: {exc}") from exc
        expl = frame.get("explanation")
        if not expl:
            raise HTTPException(
                409, "no attribution available for this frame "
                     "(ML skipped outside the training envelope)")
        return {
            "explanation": expl,
            "fault_label": frame.get("fault_label"),
            "status": frame.get("status"),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 2),
            "caveat": "SHAP describes model behaviour, not physical evidence. "
                      "The classifier is unvalidated.",
        }

    # -- history ---------------------------------------------------------
    @app.get("/frames", tags=["history"])
    def get_frames(limit: int = Query(50, ge=1, le=1000),
                   session_id: int | None = None,
                   st: ServiceState = Depends(get_state)) -> dict[str, Any]:
        rows = st.store.recent_frames(limit=limit, session_id=session_id)
        return {"count": len(rows), "frames": rows}

    @app.get("/frames/{seq}", tags=["history"])
    def get_frame(seq: int, session_id: int | None = None,
                  st: ServiceState = Depends(get_state)) -> dict[str, Any]:
        sid = session_id or st.store.session_id
        d = st.store.get_frame(seq, session_id=sid)
        if d is None:
            raise HTTPException(404, f"no frame seq={seq} in session {sid}")
        return d

    @app.get("/events", tags=["history"])
    def get_events(limit: int = Query(50, ge=1, le=1000),
                   severity: str | None = None,
                   session_id: int | None = None,
                   st: ServiceState = Depends(get_state)) -> dict[str, Any]:
        rows = st.store.recent_events(limit=limit, severity=severity,
                                     session_id=session_id)
        return {"count": len(rows), "events": rows}

    @app.get("/summary", tags=["history"])
    def get_summary(session_id: int | None = None,
                    st: ServiceState = Depends(get_state)) -> dict[str, Any]:
        try:
            return st.store.session_summary(session_id)
        except StoreError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/sessions", tags=["history"])
    def get_sessions(limit: int = Query(20, ge=1, le=200),
                     st: ServiceState = Depends(get_state)) -> dict[str, Any]:
        rows = st.store.sessions(limit=limit)
        return {"count": len(rows), "sessions": rows}

    @app.post("/sessions", tags=["history"], status_code=201)
    def new_session(body: SessionIn,
                    st: ServiceState = Depends(get_state)) -> dict[str, Any]:
        st.store.close_session()
        sid = st.store.open_session(note=body.note or "api", manifest=st.manifest)
        return {"session_id": sid}

    @app.get("/live", tags=["history"])
    def live(st: ServiceState = Depends(get_state)) -> dict[str, Any]:
        """Last processed frame, for a dashboard poll without WebSockets."""
        if st.last_frame is None:
            raise HTTPException(404, "no frame processed yet")
        return st.last_frame

    return app


app = create_app()


def _self_test() -> None:
    from fastapi.testclient import TestClient

    print("API SELF-CHECK (in-process TestClient)\n")
    failures: list[str] = []
    os.environ.setdefault("AERIS_DB",
                          str(Store.__module__ and os.path.join(
                              os.environ.get("TEMP", "."),
                              f"aeris_api_test_{os.getpid()}.db")))
    dbp = os.environ["AERIS_DB"]
    print(f"test db: {dbp}")

    # Build the healthy frame from the ACTUAL baseline predictions.
    # Hardcoded rounded values (456.20 vs 456.197) leave residuals of a
    # few thousandths, which is enough to move the gate across its
    # threshold -- see CASE 3b.
    from node2_twin_core.residual_calc import _healthy_payload
    from node2_twin_core.physics_deck import BaselineDeck
    healthy = {k: float(v) for k, v in
               _healthy_payload(deck=BaselineDeck()).items()
               if k in RAW_FIELDS}
    rounded = {k: round(v, 2) for k, v in healthy.items()}

    with TestClient(create_app()) as c:
        print("CASE 1  service metadata")
        r = c.get("/")
        print(f"  GET /            {r.status_code}  ready={r.json()['ready']}")
        h = c.get("/health").json()
        print(f"  GET /health      session={h.get('session_id')} "
              f"integrity={h.get('db_integrity')}")
        if r.status_code != 200 or not r.json()["ready"]:
            failures.append("service not ready")

        print("\nCASE 2  caveats are exposed, not buried")
        cav = c.get("/caveats").json()
        print(f"  models_trusted={cav['models_trusted']}  "
              f"measured_data={cav['measured_engine_data']}")
        print(f"  provenance: {cav['data_provenance']}")
        for issue in cav["open_issues"][:3]:
            print(f"    - {issue}")
        if cav["models_trusted"] is not False:
            failures.append("placeholder models reported as trusted")
        if not cav["open_issues"]:
            failures.append("open issues not surfaced")

        print("\nCASE 3  healthy frame")
        r = c.post("/frames", json=healthy)
        f = r.json()
        print(f"  {r.status_code}  status={f.get('status')} "
              f"p_anom={f.get('anomaly_probability'):.4f} "
              f"seq={f.get('seq')} latency={f.get('latency_ms'):.1f} ms")
        if r.status_code != 201:
            failures.append(f"healthy frame rejected: {r.text[:120]}")

        print("\nCASE 3b  gate fires on rounding error (documented defect)")
        fr_exact = c.post("/frames", json=healthy).json()
        fr_round = c.post("/frames", json=rounded).json()
        pe = fr_exact.get("anomaly_probability")
        pr = fr_round.get("anomaly_probability")
        worst = max(abs(healthy[k] - rounded[k]) for k in healthy)
        print(f"  exact residuals   p_anom={pe:.4f} status={fr_exact.get('status')}")
        print(f"  rounded to 2 dp   p_anom={pr:.4f} status={fr_round.get('status')}")
        print(f"  largest input change: {worst:.4f} -> p_anom moved {pr - pe:+.4f}")
        if abs(pr - pe) > 0.05:
            print("  CONFIRMED: the gate has no usable sensitivity gradient.")
            print("  It reacts to sub-0.01 rounding as strongly as to a real")
            print("  fault, then saturates. Not a decision source. Retrain.")
        else:
            print("  gate response is proportionate here -- recheck the sweep")

        print("\nCASE 4  hard limit breach -> CRITICAL")
        bad = dict(healthy, oil_pressure_bar=0.6)
        f2 = c.post("/frames", json=bad).json()
        print(f"  status={f2.get('status')} alert={f2.get('safety_alert')} "
              f"label={f2.get('fault_label')}")
        if f2.get("status") != "CRITICAL":
            failures.append("hard limit did not produce CRITICAL over HTTP")

        print("\nCASE 5  outside envelope -> honest refusal, not a guess")
        idle = dict(healthy, rpm=1200.0, throttle_pct=20.0)
        f3 = c.post("/frames", json=idle).json()
        print(f"  status={f3.get('status')} ml_evaluated={f3.get('ml_evaluated')} "
              f"violations={len(f3.get('envelope_violations') or [])}")
        if f3.get("status") != "UNAVAILABLE":
            failures.append("out-of-envelope frame was not marked UNAVAILABLE")

        print("\nCASE 6  malformed input is rejected by the schema")
        import json as _js
        cases = [
            ("missing field", {k: v for k, v in list(healthy.items())[:5]}, False),
            ("wrong type", dict(healthy, rpm="fast"), False),
            ("NaN value", dict(healthy, rpm=float("nan")), True),
            ("inf value", dict(healthy, rpm=float("inf")), True),
        ]
        for label, body, raw in cases:
            if raw:
                # httpx will not serialise NaN/inf, so post the raw body
                rr = c.post("/frames",
                            content=_js.dumps(body, allow_nan=True),
                            headers={"content-type": "application/json"})
            else:
                rr = c.post("/frames", json=body)
            print(f"  {label:<14} -> {rr.status_code}")
            if rr.status_code != 422:
                failures.append(f"{label} not rejected (got {rr.status_code})")

        print("\nCASE 7  history endpoints")
        fr = c.get("/frames?limit=5").json()
        ev = c.get("/events?limit=10").json()
        cr = c.get("/events?limit=10&severity=CRITICAL").json()
        print(f"  frames={fr['count']}  events={ev['count']}  "
              f"critical={cr['count']}")
        one = c.get(f"/frames/{fr['frames'][0]['seq']}").json()
        print(f"  frame detail keys={len(one['frame'])}  "
              f"status={one['status']}")
        s = c.get("/summary").json()
        print(f"  summary statuses={s['status_counts']} events={s['event_counts']}")
        if fr["count"] < 3 or cr["count"] < 1:
            failures.append("history did not persist the posted frames")
        if c.get("/frames/9999").status_code != 404:
            failures.append("missing frame did not 404")

        print("\nCASE 8  attribution on demand")
        r = c.post("/explain", json=dict(healthy, EGT_mean_C=536.2))
        if r.status_code == 200:
            e = r.json()
            top = list(e["explanation"])[:3] if isinstance(e["explanation"], dict) else e["explanation"][:3]
            print(f"  200  {e['elapsed_ms']} ms  label={e['fault_label']}")
            print(f"  top: {top}")
        elif r.status_code == 503:
            print("  503 explanations disabled (AERIS_EXPLAIN=0)")
        else:
            print(f"  {r.status_code} {r.text[:120]}")
            failures.append("explain endpoint failed")

        print("\nCASE 9  throughput over HTTP")
        ts = []
        for i in range(30):
            t0 = time.perf_counter()
            rr = c.post("/frames", json=dict(healthy, rpm=5000.0 + i))
            ts.append((time.perf_counter() - t0) * 1000.0)
            if rr.status_code != 201:
                failures.append("frame failed under load")
                break
        ts.sort()
        import statistics as _st
        print(f"  30 frames  mean={_st.mean(ts):.1f} ms  p95={ts[28]:.1f} ms "
              f"max={ts[-1]:.1f} ms  (10 Hz budget = 100 ms)")
        if _st.mean(ts) > 100.0:
            failures.append("HTTP round trip exceeds the frame budget")

        print("\nCASE 10  new session isolates history")
        sid = c.post("/sessions", json={"note": "second"}).json()["session_id"]
        c.post("/frames", json=healthy)
        fr2 = c.get("/frames?limit=50").json()
        print(f"  session {sid} holds {fr2['count']} frame(s)")
        if fr2["count"] != 1:
            failures.append("new session did not isolate frames")

    for sfx in ("", "-wal", "-shm"):
        try:
            os.unlink(dbp + sfx)
        except OSError:
            pass

    if failures:
        print("\nAPI SELF-CHECK FAILED")
        for f in failures:
            print(f"  - {f}")
        raise SystemExit(1)
    print("\nAPI SELF-CHECK OK")
    print("NOTE: every response carries models_trusted=false. The service is")
    print("      correct; the models behind it are not yet validated.")


if __name__ == "__main__":
    _self_test()