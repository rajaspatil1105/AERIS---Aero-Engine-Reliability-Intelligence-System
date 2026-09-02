"""
AERIS Node 3 -- WebSocket ingest + live fan-out.

    uvicorn node3_service.ingest:app --port 8000

Serves the full REST API from api.py plus:

    /ws/ingest   producer connects and pushes telemetry frames (JSON)
    /ws/live     dashboards connect and receive every verdict
    /stats/ingest   counters, drops, latency

TOPOLOGY
--------
This service is the SERVER; the simulator is a client that pushes. That way
the service owns its lifecycle: the simulator can die and reconnect
mid-demo without taking the dashboard down. A pull-mode client is provided
(--pull) if your simulator can only act as a server.

BACKPRESSURE
------------
Ingest must never be stalled by a slow dashboard. Each subscriber has a
bounded queue; when it is full the OLDEST frame is dropped and a counter
increments. Losing a frame on a laggy dashboard is acceptable; delaying
engine processing is not. Drops are reported, never silent.

CONCURRENCY
-----------
TwinCore holds state (the RUL engine's rolling window), so frames are
processed one at a time under a lock and the ~18 ms of sklearn work runs in
a threadpool so the event loop keeps serving sockets.
"""
from __future__ import annotations

import asyncio
import json
import math
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from node3_service.api import (RAW_FIELDS, ServiceState, TelemetryIn, _as_dict,
                               create_app)
from node3_service.store import StoreError

QUEUE_MAX = 16
LATENCY_WINDOW = 200
DEFAULT_WS_URL = "ws://127.0.0.1:8000/ws/ingest"


@dataclass(eq=False)  # identity hash: needed for set() membership
class Subscriber:
    """One dashboard connection with a bounded outbound queue."""

    ws: WebSocket
    queue: asyncio.Queue
    label: str = "dashboard"
    sent: int = 0
    dropped: int = 0


@dataclass
class IngestStats:
    frames_received: int = 0
    frames_accepted: int = 0
    frames_rejected: int = 0
    producers: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)

    def record(self, ms: float) -> None:
        self.latencies_ms.append(ms)
        if len(self.latencies_ms) > LATENCY_WINDOW:
            del self.latencies_ms[0]

    def snapshot(self) -> dict[str, Any]:
        lat = sorted(self.latencies_ms)
        out: dict[str, Any] = {
            "frames_received": self.frames_received,
            "frames_accepted": self.frames_accepted,
            "frames_rejected": self.frames_rejected,
            "producers_connected": self.producers,
            "uptime_s": round(time.monotonic() - self.started, 1),
        }
        if lat:
            out["latency_ms"] = {
                "mean": round(statistics.mean(lat), 2),
                "p95": round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 2),
                "max": round(lat[-1], 2),
                "samples": len(lat),
            }
        return out


class Hub:
    """Fan-out to dashboards with bounded queues and drop accounting."""

    def __init__(self, queue_max: int = QUEUE_MAX) -> None:
        self.subs: set[Subscriber] = set()
        self.queue_max = queue_max
        self.total_dropped = 0

    async def add(self, ws: WebSocket, label: str = "dashboard") -> Subscriber:
        sub = Subscriber(ws=ws, queue=asyncio.Queue(maxsize=self.queue_max),
                         label=label)
        self.subs.add(sub)
        return sub

    async def remove(self, sub: Subscriber) -> None:
        self.subs.discard(sub)

    def broadcast(self, message: dict[str, Any]) -> int:
        """Never awaits. A full queue loses its oldest frame, not the newest."""
        for sub in list(self.subs):
            try:
                sub.queue.put_nowait(message)
            except asyncio.QueueFull:
                try:
                    sub.queue.get_nowait()
                    sub.queue.put_nowait(message)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
                sub.dropped += 1
                self.total_dropped += 1
        return len(self.subs)

    async def pump(self, sub: Subscriber) -> None:
        """Outbound loop for one subscriber. Cancelled on disconnect."""
        try:
            while True:
                msg = await sub.queue.get()
                await sub.ws.send_text(json.dumps(msg, default=str))
                sub.sent += 1
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            return

    def snapshot(self) -> dict[str, Any]:
        return {
            "subscribers": len(self.subs),
            "queue_max": self.queue_max,
            "total_dropped": self.total_dropped,
            "detail": [{"label": s.label, "sent": s.sent, "dropped": s.dropped,
                        "queued": s.queue.qsize()} for s in self.subs],
        }


def _err(stage: str, message: str, detail: Any = None) -> dict[str, Any]:
    out = {"type": "error", "stage": stage, "message": message}
    if detail is not None:
        out["detail"] = detail
    return out


def synthetic_stream(n: int = 600, fault_at: float = 0.5,
                     severity: float = 1.2) -> Iterator[dict[str, float]]:
    """A healthy engine that slowly loses oil pressure. Demo feed.

    SIMULATED: this is a healthy baseline with a ramp applied, not Cantera
    output and not measured data. Use it to exercise the pipeline, never to
    claim detection performance.
    """
    from node2_twin_core.physics_deck import BaselineDeck
    from node2_twin_core.residual_calc import _healthy_payload

    deck = BaselineDeck()
    start = int(n * fault_at)
    for i in range(n):
        throttle = 70.0 + 12.0 * math.sin(i / 40.0)
        rpm = 4400.0 + 700.0 * math.sin(i / 55.0)
        frame = _healthy_payload(deck=deck, op={"throttle_pct": throttle,
                                                "rpm": rpm})
        if i >= start:
            frac = (i - start) / max(1, n - start)
            # Clamp to a physically possible floor. An unclamped linear ramp
            # drove this to -0.027 bar in the first live run, which the old
            # schema happily accepted; negative absolute pressure is not a
            # fault, it is a broken sensor.
            frame["oil_pressure_bar"] = max(
                0.35, frame["oil_pressure_bar"] - severity * frac)
            frame["oil_temperature_C"] += 6.0 * frac
        yield {k: float(frame[k]) for k in RAW_FIELDS}

def attach_ws(app: FastAPI) -> FastAPI:
    """Attach ingest + live routes to an existing app."""
    hub = Hub()
    stats = IngestStats()
    lock = asyncio.Lock()
    app.state.hub = hub
    app.state.ingest_stats = stats

    async def _handle(state: ServiceState, payload: dict[str, float]) -> dict[str, Any]:
        """Process + persist one frame. Serialised: TwinCore is stateful."""
        async with lock:
            t0 = time.perf_counter()
            frame = await run_in_threadpool(
                lambda: _as_dict(state.core.process(payload)))
            seq = await run_in_threadpool(state.store.add_frame, frame)
            elapsed = (time.perf_counter() - t0) * 1000.0
        state.frames_processed += 1
        state.last_frame = frame
        stats.frames_accepted += 1
        stats.record(elapsed)
        out = dict(frame)
        out["seq"] = seq
        out["models_trusted"] = False
        out["server_ms"] = round(elapsed, 2)
        return out

    @app.get("/stats/ingest", tags=["meta"])
    def ingest_stats() -> dict[str, Any]:
        return {"ingest": stats.snapshot(), "fanout": hub.snapshot()}

    @app.websocket("/ws/ingest")
    async def ws_ingest(ws: WebSocket) -> None:
        """Producer pushes telemetry frames; receives a verdict per frame."""
        await ws.accept()
        state: ServiceState = ws.app.state.aeris
        if not state.ready:
            await ws.send_text(json.dumps(_err(
                "startup", f"service not ready: {state.boot_error}")))
            await ws.close(code=1011)
            return
        stats.producers += 1
        await ws.send_text(json.dumps({
            "type": "hello",
            "required_fields": list(RAW_FIELDS),
            "models_trusted": False,
            "caveat": "placeholder models trained on simulated data; "
                      "see GET /caveats",
        }))
        try:
            while True:
                raw = await ws.receive_text()
                stats.frames_received += 1
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError as exc:
                    stats.frames_rejected += 1
                    await ws.send_text(json.dumps(
                        _err("parse", f"invalid JSON: {exc}")))
                    continue
                if isinstance(body, dict) and body.get("type") == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
                    continue
                try:
                    tel = TelemetryIn(**body)
                except (ValidationError, TypeError) as exc:
                    stats.frames_rejected += 1
                    detail = (exc.errors() if isinstance(exc, ValidationError)
                              else str(exc))
                    safe = json.loads(json.dumps(detail, default=str)
                                      .replace("NaN", '"NaN"')
                                      .replace("Infinity", '"Infinity"'))
                    await ws.send_text(json.dumps(
                        _err("validation", "frame rejected", safe)))
                    continue
                try:
                    verdict = await _handle(state, tel.payload())
                except StoreError as exc:
                    stats.frames_rejected += 1
                    await ws.send_text(json.dumps(
                        _err("persistence", str(exc))))
                    continue
                except Exception as exc:
                    stats.frames_rejected += 1
                    await ws.send_text(json.dumps(
                        _err("processing", f"{type(exc).__name__}: {exc}")))
                    continue
                hub.broadcast({"type": "frame", **verdict})
                await ws.send_text(json.dumps(
                    {"type": "ack", "seq": verdict["seq"],
                     "status": verdict.get("status"),
                     "server_ms": verdict["server_ms"]}, default=str))
        except WebSocketDisconnect:
            pass
        finally:
            stats.producers = max(0, stats.producers - 1)

    @app.websocket("/ws/live")
    async def ws_live(ws: WebSocket) -> None:
        """Dashboard subscribes to every verdict. Slow clients drop frames."""
        await ws.accept()
        state: ServiceState = ws.app.state.aeris
        sub = await hub.add(ws)
        snapshot: dict[str, Any] = {
            "type": "snapshot",
            "models_trusted": False,
            "last_frame": state.last_frame,
            "caveat": "gate is at chance and RUL R2 is negative; "
                      "statuses are pipeline output, not validated diagnosis",
        }
        if state.manifest is not None:
            snapshot["open_issues"] = list(
                state.manifest.data.get("open_issues", []))
        await ws.send_text(json.dumps(snapshot, default=str))
        pump = asyncio.create_task(hub.pump(sub))
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            pump.cancel()
            await hub.remove(sub)

    return app


app = attach_ws(create_app())


async def pull_client(url: str, hz: float = 10.0) -> None:
    """Pull mode: connect to a simulator that is itself a WS server."""
    try:
        import websockets
    except ImportError:
        print("pull mode needs `pip install websockets`")
        return
    state = None
    from node3_service.api import build_state
    state = build_state()
    if not state.ready:
        print(f"cannot start: {state.boot_error}")
        return
    lock = asyncio.Lock()
    print(f"[pull] connecting to {url}")
    async with websockets.connect(url) as ws:
        async for raw in ws:
            try:
                tel = TelemetryIn(**json.loads(raw))
            except Exception as exc:
                print(f"[pull] rejected: {exc}")
                continue
            async with lock:
                frame = _as_dict(state.core.process(tel.payload()))
                state.store.add_frame(frame)
            print(f"[pull] {frame.get('status')}")


async def simulate(url: str = DEFAULT_WS_URL, n: int = 600,
                   hz: float = 10.0) -> None:
    """Push the synthetic degradation feed at a running server."""
    try:
        import websockets
    except ImportError:
        print("simulate mode needs `pip install websockets`")
        return
    period = 1.0 / hz
    print(f"[sim] connecting to {url} ({n} frames at {hz} Hz)")
    async with websockets.connect(url) as ws:
        print(f"[sim] {json.loads(await ws.recv()).get('type')}")
        seen: dict[str, int] = {}
        for i, payload in enumerate(synthetic_stream(n=n)):
            t0 = time.perf_counter()
            await ws.send(json.dumps(payload))
            ack = json.loads(await ws.recv())
            st = ack.get("status") or ack.get("type")
            seen[st] = seen.get(st, 0) + 1
            if i % 50 == 0:
                print(f"[sim] frame {i:>4} status={st} "
                      f"server={ack.get('server_ms')} ms")
            await asyncio.sleep(max(0.0, period - (time.perf_counter() - t0)))
        print(f"[sim] done. statuses: {seen}")


def _self_test() -> None:
    import os
    import tempfile
    from pathlib import Path

    from fastapi.testclient import TestClient

    dbp = os.path.join(tempfile.gettempdir(), f"aeris_ws_{os.getpid()}.db")
    os.environ["AERIS_DB"] = dbp
    os.environ.setdefault("AERIS_EXPLAIN", "0")
    print(f"INGEST SELF-CHECK\ntest db: {dbp}\n")
    failures: list[str] = []

    from node2_twin_core.physics_deck import BaselineDeck
    from node2_twin_core.residual_calc import _healthy_payload
    deck = BaselineDeck()
    healthy = {k: float(v) for k, v in
               _healthy_payload(deck=deck).items() if k in RAW_FIELDS}

    test_app = attach_ws(create_app())
    with TestClient(test_app) as c:
        print("CASE 1  dashboard receives a snapshot with caveats")
        with c.websocket_connect("/ws/live") as dash:
            snap = dash.receive_json()
            print(f"  type={snap['type']} models_trusted={snap['models_trusted']}")
            print(f"  issues carried: {len(snap.get('open_issues', []))}")
            if snap["models_trusted"] is not False:
                failures.append("live snapshot claims trusted models")

            print("\nCASE 2  producer pushes a frame; both sides see it")
            with c.websocket_connect("/ws/ingest") as prod:
                hello = prod.receive_json()
                print(f"  hello fields={len(hello['required_fields'])}")
                prod.send_json(healthy)
                ack = prod.receive_json()
                live = dash.receive_json()
                print(f"  ack  seq={ack['seq']} status={ack['status']} "
                      f"server={ack['server_ms']} ms")
                print(f"  live type={live['type']} status={live.get('status')}")
                if ack["status"] != "HEALTHY" or live["type"] != "frame":
                    failures.append("healthy frame not delivered correctly")

                print("\nCASE 3  a bad frame is rejected, socket stays open")
                for label, body in (("missing", {"rpm": 5000.0}),
                                    ("NaN", dict(healthy, rpm=float("nan"))),
                                    ("garbage", "not json at all")):
                    if isinstance(body, str):
                        prod.send_text(body)
                    else:
                        prod.send_text(json.dumps(body, allow_nan=True))
                    e = prod.receive_json()
                    print(f"  {label:<8} -> {e['type']}/{e.get('stage')}")
                    if e["type"] != "error":
                        failures.append(f"{label} was not rejected")
                prod.send_json(healthy)
                ack = prod.receive_json()
                print(f"  socket survived: next ack seq={ack['seq']}")
                if ack.get("type") == "error":
                    failures.append("socket did not recover after a bad frame")

                print("\nCASE 4  critical breach reaches the dashboard")
                dash.receive_json()
                prod.send_json(dict(healthy, oil_pressure_bar=0.6))
                ack = prod.receive_json()
                live = dash.receive_json()
                print(f"  ack={ack['status']} live={live.get('status')} "
                      f"alert={live.get('safety_alert')}")
                if ack["status"] != "CRITICAL":
                    failures.append("hard limit did not surface over WS")

                print("\nCASE 5  sustained 10 Hz")
                lat = []
                for i in range(40):
                    t0 = time.perf_counter()
                    prod.send_json(dict(healthy, rpm=4800.0 + i))
                    prod.receive_json()
                    lat.append((time.perf_counter() - t0) * 1000.0)
                lat.sort()
                print(f"  40 frames mean={statistics.mean(lat):.1f} ms "
                      f"p95={lat[38]:.1f} ms max={lat[-1]:.1f} ms budget=100 ms")
                if statistics.mean(lat) > 100.0:
                    failures.append("round trip exceeds the 10 Hz budget")

        print("\nCASE 6  a slow dashboard drops frames, ingest continues")
        with c.websocket_connect("/ws/live") as slow:
            slow.receive_json()
            with c.websocket_connect("/ws/ingest") as prod:
                prod.receive_json()
                lat = []
                for i in range(60):
                    t0 = time.perf_counter()
                    prod.send_json(dict(healthy, rpm=4900.0 + i))
                    prod.receive_json()
                    lat.append((time.perf_counter() - t0) * 1000.0)
                s = c.get("/stats/ingest").json()
                print(f"  ingest mean={statistics.mean(lat):.1f} ms "
                      f"(unaffected)  dropped={s['fanout']['total_dropped']}")
                print(f"  fanout: {s['fanout']['detail']}")
                if statistics.mean(lat) > 100.0:
                    failures.append("slow consumer stalled ingest")

        print("\nCASE 7  counters and persistence agree")
        s = c.get("/stats/ingest").json()["ingest"]
        print(f"  received={s['frames_received']} accepted={s['frames_accepted']} "
              f"rejected={s['frames_rejected']}")
        print(f"  server latency: {s.get('latency_ms')}")
        hist = c.get("/frames?limit=500").json()
        print(f"  frames persisted={hist['count']}")
        if s["frames_rejected"] != 3:
            failures.append(f"expected 3 rejects, got {s['frames_rejected']}")
        if hist["count"] != s["frames_accepted"]:
            failures.append(
                f"persisted {hist['count']} != accepted {s['frames_accepted']}")
        ev = c.get("/events?limit=20&severity=CRITICAL").json()
        print(f"  critical events={ev['count']}")
        if ev["count"] < 1:
            failures.append("critical event not persisted")

        print("\nCASE 8  synthetic degradation feed is usable")
        gen = synthetic_stream(n=20, fault_at=0.5, severity=1.2)
        frames = list(gen)
        first, last = frames[0], frames[-1]
        print(f"  oil_pressure {first['oil_pressure_bar']:.3f} -> "
              f"{last['oil_pressure_bar']:.3f} bar over {len(frames)} frames")
        if last["oil_pressure_bar"] >= first["oil_pressure_bar"]:
            failures.append("synthetic feed does not degrade")

    for sfx in ("", "-wal", "-shm"):
        try:
            os.unlink(dbp + sfx)
        except OSError:
            pass

    if failures:
        print("\nINGEST SELF-CHECK FAILED")
        for f in failures:
            print(f"  - {f}")
        raise SystemExit(1)
    print("\nINGEST SELF-CHECK OK")
    print("NOTE: the transport is verified. The verdicts it carries come from")
    print("      placeholder models; every message says models_trusted=false.")


if __name__ == "__main__":
    import sys

    if "--simulate" in sys.argv:
        url = next((a for a in sys.argv if a.startswith("ws://")), DEFAULT_WS_URL)
        asyncio.run(simulate(url))
    elif "--pull" in sys.argv:
        url = next((a for a in sys.argv if a.startswith("ws://")), None)
        if not url:
            print("usage: --pull ws://host:port/path")
            raise SystemExit(2)
        asyncio.run(pull_client(url))
    else:
        _self_test()