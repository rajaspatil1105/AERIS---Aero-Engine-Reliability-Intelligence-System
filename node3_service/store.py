"""
AERIS Node 3 -- persistence layer (stdlib sqlite3, no ORM).

Stores every processed frame, every safety/advisory event, and the model
provenance of the session that produced them.

DESIGN NOTES
------------
* Ingest takes the frame DICT produced by twin_core's JSON round-trip, not
  a Python object. Node 3 must not depend on Node 2's attribute names.
* The complete frame is stored verbatim in frame_json. Indexed columns are
  a convenience projection of it, never the source of truth. Anything the
  projection cannot map is reported at ingest, not silently dropped.
* Each session records the manifest sha256 and a models_trusted flag. Data
  captured with untrusted models stays marked as such forever, so a
  recorded run can never be mistaken for validated evidence later.
* Writes are batched. At 10 Hz a per-frame commit would fsync 10x/second
  for no benefit; frames buffer and flush as one transaction.

DURABILITY WARNING
------------------
A WAL-mode SQLite file inside a cloud-synced folder can be corrupted by the
sync client copying the .db and -wal out of step. If the resolved path is
under OneDrive/Dropbox/GDrive, this module prints a warning. Set the AERIS_DB
environment variable to a local path (e.g. C:\aeris_data\aeris.db).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "aeris.db"
SCHEMA_VERSION = 1

SYNC_MARKERS = ("onedrive", "dropbox", "google drive", "gdrive", "icloud")

DEFAULT_FLUSH_EVERY = 20
DEFAULT_FLUSH_SECONDS = 2.0

DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    started_utc      TEXT    NOT NULL,
    ended_utc        TEXT,
    manifest_sha256  TEXT,
    models_trusted   INTEGER NOT NULL DEFAULT 0,
    data_provenance  TEXT    NOT NULL,
    sklearn_version  TEXT,
    schema_version   INTEGER NOT NULL,
    note             TEXT
);

CREATE TABLE IF NOT EXISTS frames (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,
    ts_utc        TEXT    NOT NULL,
    status        TEXT    NOT NULL,
    meaningful    INTEGER,
    p_anom        REAL,
    fault_label   TEXT,
    confidence    REAL,
    rul_raw       REAL,
    rul_smoothed  REAL,
    rul_trusted   INTEGER,
    latency_ms    REAL,
    frame_json    TEXT    NOT NULL,
    UNIQUE(session_id, seq)
);

CREATE INDEX IF NOT EXISTS ix_frames_session_seq ON frames(session_id, seq);
CREATE INDEX IF NOT EXISTS ix_frames_status      ON frames(session_id, status);
CREATE INDEX IF NOT EXISTS ix_frames_ts          ON frames(ts_utc);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    frame_seq   INTEGER NOT NULL,
    ts_utc      TEXT    NOT NULL,
    severity    TEXT    NOT NULL,
    channel     TEXT,
    message     TEXT    NOT NULL,
    value       REAL,
    threshold   REAL
);

CREATE INDEX IF NOT EXISTS ix_events_session ON events(session_id, id DESC);
CREATE INDEX IF NOT EXISTS ix_events_sev     ON events(session_id, severity);
"""

# Candidate names for the projected columns, verified against a real
# twin_core frame dict. First candidate whose KEY EXISTS wins.
PROJECTION: dict[str, tuple[str, ...]] = {
    "status":       ("status", "state", "verdict"),
    "meaningful":   ("in_envelope", "ml_evaluated", "meaningful"),
    "p_anom":       ("anomaly_probability", "p_anom", "p_anomaly"),
    "fault_label":  ("fault_label", "fault", "label"),
    "confidence":   ("fault_confidence", "confidence"),
    "rul_raw":      ("rul_raw", "rul.raw"),
    "rul_smoothed": ("rul", "rul_smoothed", "rul.smoothed"),
    "rul_trusted":  ("rul_trusted", "rul.trusted"),
    "latency_ms":   ("latency_ms", "latency", "frame_ms"),
}

EVENT_KEYS = ("events", "safety_breaches", "breaches", "advisories",
              "envelope_violations", "violations", "alerts")

EVENT_SEVERITY = {
    "safety_breaches": "CRITICAL",
    "breaches": "CRITICAL",
    "envelope_violations": "ENVELOPE",
}


_CHANNEL_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*[=<>:]")


def _sniff_channel(message: str) -> str | None:
    """Best-effort channel name from a free-text event message.

    twin_core emits some events as plain strings, e.g.
    "oil_pressure_bar=0.6bar < 1bar: ...". The leading identifier is the
    channel. DERIVED, not structured: if the message format changes this
    returns None rather than a wrong channel, and the full text is always
    kept in `message`.
    """
    m = _CHANNEL_RE.match(message)
    if not m:
        return None
    name = m.group(1)
    return name if name in KNOWN_CHANNELS else None


KNOWN_CHANNELS = frozenset({
    "rpm", "throttle_pct", "altitude_ft", "ambient_temperature_C",
    "fuelflow_kgh", "coolant_temp_C", "EGT_mean_C", "oil_pressure_bar",
    "oil_temperature_C",
})


class StoreError(RuntimeError):
    """Database unusable, or a frame that cannot be stored honestly."""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


_MISSING = object()


def _dig(d: Any, path: str) -> Any:
    cur = d
    for part in path.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def _first(frame: Mapping[str, Any], candidates: Sequence[str]) -> tuple[Any, str | None]:
    """First candidate whose KEY EXISTS.

    A present-but-None value is data: fault_label is None on a healthy
    frame, and treating that as a missing column hid four columns.
    """
    for c in candidates:
        v = _dig(frame, c)
        if v is not _MISSING:
            return (None if v is _MISSING else v), c
    return None, None


def _as_num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _as_flag(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, str):
        return 1 if v.strip().lower() in ("1", "true", "yes", "ok") else 0
    n = _as_num(v)
    return None if n is None else int(bool(n))


@dataclass
class Projection:
    """What the column projection managed to map for a frame."""
    values: dict[str, Any] = field(default_factory=dict)
    resolved: dict[str, str] = field(default_factory=dict)
    unmapped: list[str] = field(default_factory=list)


def project(frame: Mapping[str, Any]) -> Projection:
    p = Projection()
    for col, cands in PROJECTION.items():
        v, src = _first(frame, cands)
        if src is None:
            p.unmapped.append(col)
            p.values[col] = None
            continue
        p.resolved[col] = src
        if col in ("meaningful", "rul_trusted"):
            p.values[col] = _as_flag(v)
        elif col in ("status", "fault_label"):
            p.values[col] = None if v is None else str(v)
        else:
            p.values[col] = _as_num(v)
    if p.values.get("status") is None:
        raise StoreError(
            "frame has no status field; refusing to store an unlabelled frame. "
            f"top-level keys: {sorted(frame)[:12]}")
    return p


def extract_events(frame: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Pull safety/advisory items out of a frame, whatever shape they take."""
    out: list[dict[str, Any]] = []
    for key in EVENT_KEYS:
        items = _dig(frame, key)
        if not isinstance(items, (list, tuple)):
            continue
        default_sev = EVENT_SEVERITY.get(key, "ADVISORY")
        for it in items:
            if isinstance(it, Mapping):
                out.append({
                    "severity": str(it.get("severity") or it.get("kind") or default_sev).upper(),
                    "channel": (str(it["channel"]) if it.get("channel") else
                                str(it["name"]) if it.get("name") else None),
                    "message": str(it.get("message") or it.get("text") or it),
                    "value": _as_num(it.get("value") or it.get("measured")),
                    "threshold": _as_num(it.get("threshold") or it.get("limit")),
                })
            else:
                msg = str(it)
                out.append({"severity": default_sev,
                            "channel": _sniff_channel(msg),
                            "message": msg, "value": None,
                            "threshold": None})
    return out

class Store:
    """Batched SQLite writer + read queries for the dashboard."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        flush_every: int = DEFAULT_FLUSH_EVERY,
        flush_seconds: float = DEFAULT_FLUSH_SECONDS,
        warn_on_sync_folder: bool = True,
    ) -> None:
        env = os.environ.get("AERIS_DB")
        path = Path(db_path or env or DEFAULT_DB)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path

        if warn_on_sync_folder:
            low = str(path).lower()
            hit = next((m for m in SYNC_MARKERS if m in low), None)
            if hit:
                print(f"[store] WARNING database is inside a synced folder "
                      f"({hit}). WAL + cloud sync can corrupt SQLite. "
                      f"Set AERIS_DB to a local path.")

        try:
            self.db = sqlite3.connect(str(path), timeout=10.0,
                                      isolation_level=None,
                                      check_same_thread=False)
        except sqlite3.Error as exc:
            raise StoreError(f"cannot open {path}: {exc}") from exc
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.execute("PRAGMA synchronous = NORMAL")
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(DDL)
        self.db.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),))

        self.flush_every = int(flush_every)
        self.flush_seconds = float(flush_seconds)
        self._buf: list[tuple] = []
        self._ebuf: list[tuple] = []
        self._last_flush = time.monotonic()
        self.session_id: int | None = None
        self._seq = 0
        self.unmapped_reported = False
        self.frames_written = 0

    # -- sessions --------------------------------------------------------
    def open_session(self, note: str | None = None,
                     manifest: Any = None) -> int:
        sha, trusted, prov, skl = None, 0, "unknown", None
        if manifest is not None:
            try:
                arts = manifest.artifacts
                trusted = int(all(a.trusted for a in arts))
                prov = str(manifest.data["data_provenance"]["source"])
                skl = str(manifest.data.get("sklearn_version_at_build"))
                blob = json.dumps(manifest.data, sort_keys=True).encode()
                import hashlib
                sha = hashlib.sha256(blob).hexdigest()
            except Exception as exc:  # manifest shape is Node 2's business
                print(f"[store] could not read manifest ({exc}); "
                      "session marked untrusted")
        cur = self.db.execute(
            "INSERT INTO sessions(started_utc, manifest_sha256, models_trusted,"
            " data_provenance, sklearn_version, schema_version, note)"
            " VALUES(?,?,?,?,?,?,?)",
            (utcnow(), sha, trusted, prov, skl, SCHEMA_VERSION, note))
        self.session_id = int(cur.lastrowid)
        self._seq = 0
        if not trusted:
            print(f"[store] session {self.session_id} flagged "
                  "models_trusted=0 (placeholder models)")
        return self.session_id

    def close_session(self) -> None:
        self.flush()
        if self.session_id is not None:
            self.db.execute("UPDATE sessions SET ended_utc=? WHERE id=?",
                            (utcnow(), self.session_id))
        self.session_id = None

    # -- ingest ----------------------------------------------------------
    def add_frame(self, frame: Mapping[str, Any],
                  ts_utc: str | None = None) -> int:
        if not isinstance(frame, Mapping):
            raise StoreError(f"frame must be a mapping, got {type(frame).__name__}")
        if self.session_id is None:
            self.open_session(note="auto-opened by add_frame")

        p = project(frame)
        if p.unmapped and not self.unmapped_reported:
            print(f"[store] columns not present in frame dict "
                  f"(kept in frame_json only): {p.unmapped}")
            self.unmapped_reported = True

        self._seq += 1
        seq, ts = self._seq, ts_utc or utcnow()
        v = p.values
        self._buf.append((
            self.session_id, seq, ts, v["status"], v["meaningful"],
            v["p_anom"], v["fault_label"], v["confidence"], v["rul_raw"],
            v["rul_smoothed"], v["rul_trusted"], v["latency_ms"],
            json.dumps(frame, separators=(",", ":"), default=str)))
        for e in extract_events(frame):
            self._ebuf.append((self.session_id, seq, ts, e["severity"],
                               e["channel"], e["message"], e["value"],
                               e["threshold"]))

        if (len(self._buf) >= self.flush_every
                or time.monotonic() - self._last_flush >= self.flush_seconds):
            self.flush()
        return seq

    def flush(self) -> int:
        if not self._buf and not self._ebuf:
            return 0
        n = len(self._buf)
        try:
            self.db.execute("BEGIN")
            if self._buf:
                self.db.executemany(
                    "INSERT OR REPLACE INTO frames(session_id, seq, ts_utc,"
                    " status, meaningful, p_anom, fault_label, confidence,"
                    " rul_raw, rul_smoothed, rul_trusted, latency_ms,"
                    " frame_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", self._buf)
            if self._ebuf:
                self.db.executemany(
                    "INSERT INTO events(session_id, frame_seq, ts_utc,"
                    " severity, channel, message, value, threshold)"
                    " VALUES(?,?,?,?,?,?,?,?)", self._ebuf)
            self.db.execute("COMMIT")
        except sqlite3.Error as exc:
            self.db.execute("ROLLBACK")
            raise StoreError(f"flush failed, {n} frames not written: {exc}") from exc
        self._buf.clear()
        self._ebuf.clear()
        self._last_flush = time.monotonic()
        self.frames_written += n
        return n

    # -- reads -----------------------------------------------------------
    def recent_frames(self, limit: int = 50, session_id: int | None = None,
                      full: bool = False) -> list[dict[str, Any]]:
        self.flush()  # read-your-writes: the buffer is not visible to SQL
        sid = session_id or self.session_id
        cols = ("*" if full else
                "id, seq, ts_utc, status, meaningful, p_anom, fault_label,"
                " confidence, rul_smoothed, rul_trusted, latency_ms")
        rows = self.db.execute(
            f"SELECT {cols} FROM frames WHERE session_id=? "
            "ORDER BY seq DESC LIMIT ?", (sid, limit)).fetchall()
        out = [dict(r) for r in rows]
        if full:
            for r in out:
                r["frame"] = json.loads(r.pop("frame_json"))
        return out

    def recent_events(self, limit: int = 50, severity: str | None = None,
                      session_id: int | None = None) -> list[dict[str, Any]]:
        self.flush()
        sid = session_id or self.session_id
        if severity:
            rows = self.db.execute(
                "SELECT * FROM events WHERE session_id=? AND severity=? "
                "ORDER BY id DESC LIMIT ?", (sid, severity.upper(), limit)).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM events WHERE session_id=? ORDER BY id DESC "
                "LIMIT ?", (sid, limit)).fetchall()
        return [dict(r) for r in rows]

    def session_summary(self, session_id: int | None = None) -> dict[str, Any]:
        self.flush()
        sid = session_id or self.session_id
        s = self.db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        if s is None:
            raise StoreError(f"no session {sid}")
        out = dict(s)
        out["frames"] = self.db.execute(
            "SELECT COUNT(*) c FROM frames WHERE session_id=?", (sid,)).fetchone()["c"]
        out["status_counts"] = {
            r["status"]: r["c"] for r in self.db.execute(
                "SELECT status, COUNT(*) c FROM frames WHERE session_id=? "
                "GROUP BY status", (sid,)).fetchall()}
        out["event_counts"] = {
            r["severity"]: r["c"] for r in self.db.execute(
                "SELECT severity, COUNT(*) c FROM events WHERE session_id=? "
                "GROUP BY severity", (sid,)).fetchall()}
        lat = self.db.execute(
            "SELECT AVG(latency_ms) a, MAX(latency_ms) m FROM frames "
            "WHERE session_id=?", (sid,)).fetchone()
        out["latency_mean_ms"] = lat["a"]
        out["latency_max_ms"] = lat["m"]
        return out

    def sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute(
            "SELECT id, started_utc, ended_utc, models_trusted, "
            "data_provenance, note FROM sessions ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()]

    def prune(self, keep_sessions: int = 10) -> int:
        rows = self.db.execute(
            "SELECT id FROM sessions ORDER BY id DESC LIMIT -1 OFFSET ?",
            (keep_sessions,)).fetchall()
        ids = [r["id"] for r in rows]
        for i in ids:
            self.db.execute("DELETE FROM sessions WHERE id=?", (i,))
        return len(ids)

    def get_frame(self, seq: int,
                  session_id: int | None = None) -> dict[str, Any] | None:
        """One frame with its full json blob, or None."""
        self.flush()
        sid = session_id or self.session_id
        row = self.db.execute(
            "SELECT * FROM frames WHERE session_id=? AND seq=?",
            (sid, seq)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["frame"] = json.loads(d.pop("frame_json"))
        return d

    def integrity_check(self) -> str:
        return self.db.execute("PRAGMA integrity_check").fetchone()[0]

    def close(self) -> None:
        try:
            self.close_session()
        finally:
            self.db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _self_test() -> None:
    import statistics as st
    import tempfile

    tmp = Path(tempfile.gettempdir()) / f"aeris_selftest_{os.getpid()}.db"
    for suffix in ("", "-wal", "-shm"):
        Path(str(tmp) + suffix).unlink(missing_ok=True)
    print(f"test db: {tmp}")
    failures: list[str] = []

    store = Store(tmp, flush_every=25, warn_on_sync_folder=False)
    print(f"schema version {SCHEMA_VERSION}  integrity: {store.integrity_check()}")

    print("\nCASE 1  session records model provenance")
    try:
        from node2_twin_core.manifest import ModelManifest
        mf = ModelManifest.load()
    except Exception as exc:
        mf = None
        print(f"  manifest unavailable ({type(exc).__name__}); untrusted session")
    sid = store.open_session(note="self-test", manifest=mf)
    summ = store.session_summary(sid)
    print(f"  session {sid}  trusted={summ['models_trusted']}  "
          f"provenance={summ['data_provenance']}")
    print(f"  manifest sha={str(summ['manifest_sha256'])[:16]}...")
    if mf is not None and summ["models_trusted"] != 0:
        failures.append("placeholder models must not be recorded as trusted")

    print("\nCASE 2  a frame with no status is refused")
    try:
        store.add_frame({"p_anom": 0.5})
        failures.append("unlabelled frame accepted")
    except StoreError as exc:
        print(f"  refused: {str(exc)[:80]}")

    print("\nCASE 3  ingest 200 frames, batched")
    def synth(i: int) -> dict[str, Any]:
        crit = (i == 120)
        return {
            "status": "CRITICAL" if crit else ("ADVISORY" if i % 40 == 0 else "OK"),
            "meaningful": True,
            "p_anom": 0.5 + 0.001 * (i % 50),
            "fault_label": "cooling_degradation" if i % 40 == 0 else None,
            "confidence": 0.7 if i % 40 == 0 else None,
            "rul_raw": 180.0 - 0.1 * i,
            "rul_smoothed": 179.0 - 0.1 * i,
            "rul_trusted": False,
            "latency_ms": 17.0 + (i % 5),
            "safety_breaches": ([{"channel": "oil_pressure_bar", "value": 0.6,
                                  "threshold": 1.0, "message": "below hard limit"}]
                                if crit else []),
            "advisories": ([{"channel": "coolant_temp_C",
                             "message": "outside healthy range"}]
                           if i % 40 == 0 else []),
            "vector": [float(i)] * 14,
        }
    ts = []
    for i in range(200):
        t0 = time.perf_counter()
        store.add_frame(synth(i))
        ts.append((time.perf_counter() - t0) * 1000.0)
    store.flush()
    ts.sort()
    print(f"  ingest cost mean={st.mean(ts):.3f} ms  p95={ts[189]:.3f} ms  "
          f"max={ts[-1]:.3f} ms")
    print(f"  frames written: {store.frames_written}")
    if st.mean(ts) > 5.0:
        failures.append("ingest too slow for 10 Hz")
    if store.frames_written != 200:
        failures.append(f"expected 200 frames, wrote {store.frames_written}")

    print("\nCASE 4  events were extracted from both shapes")
    ev = store.recent_events(limit=10)
    crit = store.recent_events(limit=10, severity="CRITICAL")
    print(f"  events total={len(store.recent_events(limit=999))}  "
          f"critical={len(crit)}")
    if crit:
        c = crit[0]
        print(f"  {c['severity']} {c['channel']} value={c['value']} "
              f"limit={c['threshold']} :: {c['message']}")
    if not crit:
        failures.append("critical event not extracted")

    print("\nCASE 5  queries the dashboard needs")
    recent = store.recent_frames(limit=3)
    print(f"  newest seq: {[r['seq'] for r in recent]}")
    s = store.session_summary(sid)
    print(f"  status counts {s['status_counts']}  events {s['event_counts']}")
    print(f"  latency mean {s['latency_mean_ms']:.1f} ms  "
          f"max {s['latency_max_ms']:.1f} ms")
    if recent and recent[0]["seq"] != 200:
        failures.append("recent_frames not newest-first")

    print("\nCASE 6  full frame survives the round-trip")
    one = store.recent_frames(limit=1, full=True)[0]
    print(f"  vector len={len(one['frame']['vector'])}  "
          f"keys={len(one['frame'])}")
    if len(one["frame"]["vector"]) != 14:
        failures.append("frame_json lost data")

    print("\nCASE 7  durability across reopen")
    store.close_session()
    store.db.close()
    again = Store(tmp, warn_on_sync_folder=False)
    n = again.db.execute("SELECT COUNT(*) c FROM frames WHERE session_id=?",
                         (sid,)).fetchone()["c"]
    print(f"  frames after reopen: {n}  integrity: {again.integrity_check()}")
    if n != 200:
        failures.append(f"durability: {n} != 200")

    print("\nCASE 8  cascade delete + prune")
    before = again.db.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
    again.db.execute("DELETE FROM sessions WHERE id=?", (sid,))
    after = again.db.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
    print(f"  events {before} -> {after} after deleting the session")
    if after != 0:
        failures.append("foreign key cascade not active")
    again.close()

    for suffix in ("", "-wal", "-shm"):
        Path(str(tmp) + suffix).unlink(missing_ok=True)

    if failures:
        print("\nSTORE SELF-CHECK FAILED")
        for f in failures:
            print(f"  - {f}")
        raise SystemExit(1)
    print("\nSTORE SELF-CHECK OK")
    print("NOTE: sessions carry models_trusted=0 while the gate is at chance")
    print("      and RUL R2 is negative. Stored numbers are not evidence.")


if __name__ == "__main__":
    _self_test()