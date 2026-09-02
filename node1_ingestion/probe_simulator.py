"""
AERIS Phase 1 -- Node 1 development probe (DIAGNOSTIC TOOL, not a pipeline node).

Purpose: discover the actual JSON structure emitted by the Cantera UAV
simulator so that the field mapping in simulator_bridge.py can be written
against ground truth instead of assumption.

Read-only. Sends nothing to the simulator. Writes nothing to disk.

It reports:
  * frame structure, flattened to dotted keys, with types and sample values
  * list-valued keys and their lengths  -> vibration window candidates
  * measured inter-frame interval       -> confirms the 10 Hz claim
  * reconciliation against shared.schema.TelemetryPayload
  * result of attempting real schema validation on a live frame
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from typing import Any, Dict, List, Optional, Tuple

import websockets
from pydantic import ValidationError

from shared.schema import TelemetryPayload, unknown_fields

DEFAULT_URL = "ws://localhost:8765"
_LEAF_PREVIEW = 60


def flatten(obj: Any, prefix: str = "") -> Dict[str, Any]:
    """Flatten nested dicts to dotted keys. Lists are kept whole as leaves."""
    out: Dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                out.update(flatten(v, key))
            else:
                out[key] = v
    else:
        out[prefix or "<root>"] = obj
    return out


def describe(value: Any) -> str:
    if isinstance(value, list):
        kind = type(value[0]).__name__ if value else "empty"
        return f"list[{kind}] len={len(value)}"
    text = repr(value)
    if len(text) > _LEAF_PREVIEW:
        text = text[:_LEAF_PREVIEW] + "..."
    return f"{type(value).__name__} = {text}"


async def collect(
    url: str, n_frames: int, timeout: float
) -> Tuple[List[Dict[str, Any]], List[float]]:
    """Connect and collect n_frames decoded JSON frames plus arrival times."""
    frames: List[Dict[str, Any]] = []
    arrivals: List[float] = []

    print(f"connecting to {url} ...")
    async with websockets.connect(url, open_timeout=timeout) as ws:
        print("connected. collecting frames...\n")
        while len(frames) < n_frames:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            arrivals.append(time.perf_counter())
            if isinstance(raw, bytes):
                print(f"NOTE: binary frame ({len(raw)} bytes), decoding as utf-8")
                raw = raw.decode("utf-8", errors="replace")
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError as exc:
                print(f"FRAME {len(frames)} IS NOT VALID JSON: {exc}")
                print(f"  raw head: {raw[:200]!r}")
                raise SystemExit(2)
            if not isinstance(decoded, dict):
                print(f"WARNING: top level is {type(decoded).__name__}, not object")
                print(f"  value head: {repr(decoded)[:200]}")
            frames.append(decoded)
    return frames, arrivals


def report(frames: List[Dict[str, Any]], arrivals: List[float]) -> None:
    first = frames[0]
    flat = flatten(first)

    print("=" * 66)
    print(f"FRAME STRUCTURE  ({len(flat)} leaf keys, nested={any('.' in k for k in flat)})")
    print("=" * 66)
    for k in sorted(flat):
        print(f"  {k:<38} {describe(flat[k])}")

    lists = {k: v for k, v in flat.items() if isinstance(v, list)}
    print("\n" + "=" * 66)
    print("LIST-VALUED KEYS  (vibration window candidates)")
    print("=" * 66)
    if not lists:
        print("  none -- simulator sends no raw sample arrays.")
        print("  => vib_1x/2x/3x cannot be computed from this source.")
        print("     They will remain None. They will NOT be fabricated.")
    else:
        for k, v in lists.items():
            lengths = {
                len(flatten(f).get(k, []))
                for f in frames
                if isinstance(flatten(f).get(k), list)
            }
            print(f"  {k}: len={sorted(lengths)} across {len(frames)} frames")
        print("\n  ACTION: the vibration sample rate is REQUIRED by DSPConfig and")
        print("  cannot be inferred from window length alone. Check whether the")
        print("  simulator publishes it as a field, or state it explicitly.")

    if len(arrivals) > 2:
        gaps = [b - a for a, b in zip(arrivals, arrivals[1:])]
        mean = statistics.mean(gaps)
        print("\n" + "=" * 66)
        print("FRAME RATE")
        print("=" * 66)
        print(f"  frames      : {len(arrivals)}")
        print(f"  mean gap    : {mean * 1000:.1f} ms  -> {1.0 / mean:.2f} Hz")
        print(f"  min/max gap : {min(gaps) * 1000:.1f} / {max(gaps) * 1000:.1f} ms")
        if len(gaps) > 1:
            print(f"  stdev       : {statistics.stdev(gaps) * 1000:.1f} ms")

    # ---- reconciliation against the shared contract ----
    schema_fields = set(TelemetryPayload.model_fields)
    top_level = set(first)
    flat_keys = set(flat)

    print("\n" + "=" * 66)
    print("RECONCILIATION vs shared.schema.TelemetryPayload")
    print("=" * 66)
    matched = sorted(schema_fields & flat_keys)
    print(f"  exact name matches      : {len(matched)} / {len(schema_fields)}")
    if matched:
        print(f"    {matched}")
    print(f"  schema fields unmatched : {len(schema_fields - flat_keys)}")
    print(f"    {sorted(schema_fields - flat_keys)}")
    print(f"  source keys not in schema (need mapping): {len(flat_keys - schema_fields)}")
    print(f"    {sorted(flat_keys - schema_fields)}")

    print("\n  unknown_fields() on top level:")
    print(f"    {unknown_fields(first)}")

    print("\n  direct validation attempt (no mapping applied):")
    try:
        p = TelemetryPayload(**first)
        print(f"    SUCCEEDED. rpm={p.rpm} egt_1_c={p.egt_1_c}")
        print("    Values may still be defaults if names did not match --")
        print("    check the match count above before trusting this.")
    except ValidationError as exc:
        errs = exc.errors()
        print(f"    FAILED with {len(errs)} error(s) (expected pre-mapping):")
        for e in errs[:8]:
            print(f"      {'.'.join(str(x) for x in e['loc'])}: {e['msg']}")
        if len(errs) > 8:
            print(f"      ... and {len(errs) - 8} more")

    print("\n" + "=" * 66)
    print("RAW FIRST FRAME (paste this back for mapping construction)")
    print("=" * 66)
    print(json.dumps(first, indent=2, default=str)[:4000])


def main() -> None:
    ap = argparse.ArgumentParser(description="Probe the Cantera simulator stream.")
    ap.add_argument("--url", default=DEFAULT_URL, help=f"default {DEFAULT_URL}")
    ap.add_argument("--frames", type=int, default=20)
    ap.add_argument("--timeout", type=float, default=10.0)
    args = ap.parse_args()

    try:
        frames, arrivals = asyncio.run(collect(args.url, args.frames, args.timeout))
    except asyncio.TimeoutError:
        print(f"\nTIMEOUT: connected but no frame within {args.timeout}s.")
        print("The server may be idle, or may expect a subscribe/start message.")
        raise SystemExit(3)
    except (OSError, websockets.exceptions.WebSocketException) as exc:
        print(f"\nCONNECTION FAILED: {type(exc).__name__}: {exc}")
        print("Check: is the simulator running? Is the URL and port correct?")
        print("Is it a WebSocket *server*, or does it expect to connect outward?")
        raise SystemExit(3)

    report(frames, arrivals)
    print("\nPROBE COMPLETE")


if __name__ == "__main__":
    main()
