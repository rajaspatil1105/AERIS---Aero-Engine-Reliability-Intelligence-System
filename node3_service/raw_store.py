"""
AERIS Phase 1 -- Node 3 raw telemetry persistence.

node3_service/store.py persists the twin's OUTPUT frame (30 keys: status,
residuals, p_anom, rul, ...). The 68-column INPUT telemetry is discarded once
processed. That blocks three things:

  1. Mission replay. You cannot re-fly what you did not record.
  2. Re-analysis. When the gate is retrained, old sessions cannot be re-scored
     because the inputs are gone.
  3. Vibration. node1_ingestion/dsp_fft.py computes 1x/2x/3x, crest factor and
     band energy. None of it is in the 14-feature vector, so without a raw
     table those features have nowhere to live at all.

This module stores the canonical contract, schema-driven: columns are generated
from shared.schema.COLUMN_NAMES, so adding a telemetry field changes the table
automatically instead of requiring 68 hand-written DDL lines.

DESIGN NOTES
------------
* Companion, not a rewrite. store.py is untouched. This opens the same DB file
  and discovers the existing sessions table by introspection, so the FK and
  ON DELETE CASCADE keep working with whatever store.py actually created.
* Name collisions are explicit. The schema owns `session_id` (TEXT mission id)
  and `timestamp` (source clock). The store's integer session key is therefore
  stored as `db_session_id`, and insert time as `recorded_utc`. Nothing is
  silently overloaded.
* NULL means "not computed". The DSP fields default to None in the schema; they
  round-trip as SQL NULL and must never be read back as 0.0.
* vib_samples is NOT persisted (excluded in the schema, ~thousands of floats
  per frame at 10 Hz). Replay therefore reproduces DSP *outputs*, not the raw
  accelerometer window. Re-running DSP with different settings on an old
  mission is not possible. Stated here rather than discovered later.
"""

from __future__ import annotations

import threading

import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple, get_args, get_origin

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.schema import (  # noqa: E402
    COLUMN_NAMES,
    EngineState,
    TelemetryPayload,
    to_column_row,
)

RAW_SCHEMA_VERSION = 1
TABLE = "raw_telemetry"

# Columns this module adds around the 68 canonical ones.
FK_COL = "db_session_id"
SEQ_COL = "seq"
RECORDED_COL = "recorded_utc"

DEFAULT_FLUSH_EVERY = 20
DEFAULT_FLUSH_SECONDS = 2.0


class RawStoreError(Exception):
    """Raised when raw telemetry cannot be persisted or read back."""


def _sql_type(name: str) -> str:
    """Map a pydantic field to a SQLite column type."""
    fld = TelemetryPayload.model_fields[name]
    ann = fld.annotation
    if get_origin(ann) is not None:
        args = [a for a in get_args(ann) if a is not type(None)]
        ann = args[0] if args else str
    try:
        if isinstance(ann, type) and issubclass(ann, EngineState):
            return "TEXT"
    except TypeError:
        pass
    if ann is bool:
        return "INTEGER"
    if ann is int:
        return "INTEGER"
    if ann is float:
        return "REAL"
    return "TEXT"


COLUMN_TYPES: Tuple[Tuple[str, str], ...] = tuple(
    (n, _sql_type(n)) for n in COLUMN_NAMES
)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class DiscoveredSchema:
    """What introspection found in an existing AERIS database."""

    tables: List[str]
    sessions_table: Optional[str]
    sessions_pk: Optional[str]
    frames_table: Optional[str]

    def describe(self) -> str:
        return (
            f"tables={self.tables} sessions={self.sessions_table}"
            f"({self.sessions_pk}) frames={self.frames_table}"
        )


def discover(conn: sqlite3.Connection) -> DiscoveredSchema:
    """Find store.py's tables without importing or assuming its internals."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    tables = sorted(r[0] for r in rows)

    sessions_table = sessions_pk = frames_table = None
    for t in tables:
        cols = [c[1] for c in conn.execute(f'PRAGMA table_info("{t}")').fetchall()]
        pks = [c[1] for c in conn.execute(f'PRAGMA table_info("{t}")').fetchall() if c[5]]
        low = {c.lower() for c in cols}
        if sessions_table is None and (
            t.lower() == "sessions" or {"models_trusted", "manifest_sha256"} & low
        ):
            sessions_table = t
            sessions_pk = pks[0] if pks else "id"
        if frames_table is None and t.lower() in ("frames", "twin_frames") :
            frames_table = t
    return DiscoveredSchema(tables, sessions_table, sessions_pk, frames_table)


class RawStore:
    """Batched writer and reader for 68-column canonical telemetry."""

    def __init__(
        self,
        db_path: Optional[os.PathLike | str] = None,
        flush_every: int = DEFAULT_FLUSH_EVERY,
        flush_seconds: float = DEFAULT_FLUSH_SECONDS,
        create_sessions_if_absent: bool = False,
    ) -> None:
        self.db_path = Path(
            db_path or os.environ.get("AERIS_DB") or (ROOT / "data" / "aeris.db")
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.flush_every = int(flush_every)
        self.flush_seconds = float(flush_seconds)

        self.conn = sqlite3.connect(str(self.db_path), isolation_level=None, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

        self.discovered = discover(self.conn)
        if self.discovered.sessions_table is None and create_sessions_if_absent:
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS sessions ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " started_utc TEXT, note TEXT)"
            )
            self.discovered = discover(self.conn)

        self._buf: List[Tuple[Any, ...]] = []
        self._last_flush = time.monotonic()
        self._written = 0
        self._ensure_schema()

    # ----------------------------------------------------------------- #
    # schema
    # ----------------------------------------------------------------- #
    def _ensure_schema(self) -> None:
        cols = [
            f'"{FK_COL}" INTEGER NOT NULL',
            f'"{SEQ_COL}" INTEGER NOT NULL',
            f'"{RECORDED_COL}" TEXT NOT NULL',
        ]
        cols += [f'"{n}" {t}' for n, t in COLUMN_TYPES]

        fk = ""
        st, pk = self.discovered.sessions_table, self.discovered.sessions_pk
        if st and pk:
            fk = (
                f', FOREIGN KEY("{FK_COL}") REFERENCES "{st}"("{pk}") '
                f"ON DELETE CASCADE"
            )
        ddl = (
            f'CREATE TABLE IF NOT EXISTS "{TABLE}" ('
            f" id INTEGER PRIMARY KEY AUTOINCREMENT, "
            + ", ".join(cols)
            + fk
            + ")"
        )
        self.conn.execute(ddl)
        self.conn.execute(
            f'CREATE INDEX IF NOT EXISTS idx_{TABLE}_session_seq '
            f'ON "{TABLE}"("{FK_COL}", "{SEQ_COL}")'
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS raw_meta ("
            " key TEXT PRIMARY KEY, value TEXT)"
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO raw_meta(key, value) VALUES(?, ?)",
            ("raw_schema_version", str(RAW_SCHEMA_VERSION)),
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO raw_meta(key, value) VALUES(?, ?)",
            ("raw_columns", json.dumps(list(COLUMN_NAMES))),
        )

    @property
    def column_count(self) -> int:
        return len(COLUMN_NAMES)

    def table_columns(self) -> List[str]:
        return [c[1] for c in self.conn.execute(f'PRAGMA table_info("{TABLE}")')]

    # ----------------------------------------------------------------- #
    # write
    # ----------------------------------------------------------------- #
    # --- thread safety (patch_raw_thread): the FastAPI worker thread is not
    # the thread that opened the connection, so serialise all writes here.
    @property
    def _lock(self):
        lk = getattr(self, "_lock_obj", None)
        if lk is None:
            lk = threading.RLock()
            self._lock_obj = lk
        return lk

    def add_frame(self, *a, **kw):
        with self._lock:
            return self._add_frame_locked(*a, **kw)

    def flush(self, *a, **kw):
        with self._lock:
            return self._flush_locked(*a, **kw)

    def _add_frame_locked(
        self, payload: TelemetryPayload, db_session_id: int, seq: int
    ) -> None:
        if not isinstance(payload, TelemetryPayload):
            raise RawStoreError(
                f"expected TelemetryPayload, got {type(payload).__name__}. "
                "Convert at the boundary; do not persist loose dicts."
            )
        row = (int(db_session_id), int(seq), _now_utc()) + to_column_row(payload)
        self._buf.append(row)
        due = (
            len(self._buf) >= self.flush_every
            or (time.monotonic() - self._last_flush) >= self.flush_seconds
        )
        if due:
            self.flush()

    def _flush_locked(self) -> int:
        if not self._buf:
            return 0
        names = [FK_COL, SEQ_COL, RECORDED_COL] + list(COLUMN_NAMES)
        ph = ",".join("?" * len(names))
        q = ",".join(f'"{n}"' for n in names)
        sql = f'INSERT INTO "{TABLE}"({q}) VALUES({ph})'
        n = len(self._buf)
        try:
            self.conn.executemany(sql, self._buf)
        except sqlite3.IntegrityError as exc:
            self._buf.clear()
            raise RawStoreError(
                f"raw telemetry insert rejected: {exc}. Most likely "
                f"{FK_COL} does not exist in "
                f"{self.discovered.sessions_table or '<no sessions table>'}."
            ) from exc
        self._buf.clear()
        self._last_flush = time.monotonic()
        self._written += n
        return n

    # ----------------------------------------------------------------- #
    # read
    # ----------------------------------------------------------------- #
    def _row_to_payload(self, row: Sequence[Any]) -> TelemetryPayload:
        data = dict(zip(COLUMN_NAMES, row))
        return TelemetryPayload(**data)

    def count(self, db_session_id: Optional[int] = None) -> int:
        self.flush()
        if db_session_id is None:
            return int(self.conn.execute(f'SELECT COUNT(*) FROM "{TABLE}"').fetchone()[0])
        return int(
            self.conn.execute(
                f'SELECT COUNT(*) FROM "{TABLE}" WHERE "{FK_COL}"=?',
                (db_session_id,),
            ).fetchone()[0]
        )

    def get_frame(self, db_session_id: int, seq: int) -> Optional[TelemetryPayload]:
        self.flush()
        q = ",".join(f'"{n}"' for n in COLUMN_NAMES)
        r = self.conn.execute(
            f'SELECT {q} FROM "{TABLE}" WHERE "{FK_COL}"=? AND "{SEQ_COL}"=?',
            (db_session_id, seq),
        ).fetchone()
        return None if r is None else self._row_to_payload(r)

    def replay(
        self,
        db_session_id: int,
        start_seq: int = 0,
        limit: Optional[int] = None,
    ) -> Iterator[Tuple[int, TelemetryPayload]]:
        """Yield (seq, payload) in flight order. The mission replay primitive."""
        self.flush()
        q = ",".join(f'"{n}"' for n in COLUMN_NAMES)
        sql = (
            f'SELECT "{SEQ_COL}", {q} FROM "{TABLE}" '
            f'WHERE "{FK_COL}"=? AND "{SEQ_COL}">=? ORDER BY "{SEQ_COL}" ASC'
        )
        args: List[Any] = [db_session_id, start_seq]
        if limit is not None:
            sql += " LIMIT ?"
            args.append(int(limit))
        for r in self.conn.execute(sql, args):
            yield int(r[0]), self._row_to_payload(r[1:])

    def session_span(self, db_session_id: int) -> Dict[str, Any]:
        self.flush()
        r = self.conn.execute(
            f'SELECT COUNT(*), MIN("{SEQ_COL}"), MAX("{SEQ_COL}"), '
            f'MIN("timestamp"), MAX("timestamp") '
            f'FROM "{TABLE}" WHERE "{FK_COL}"=?',
            (db_session_id,),
        ).fetchone()
        n, lo, hi, t0, t1 = r
        dur = (t1 - t0) if (t0 is not None and t1 is not None) else None
        return {
            "frames": int(n or 0),
            "seq_first": lo,
            "seq_last": hi,
            "t_first": t0,
            "t_last": t1,
            "duration_s": dur,
            "rate_hz": (n / dur) if (dur and dur > 0) else None,
        }

    def null_columns(self, db_session_id: int) -> List[str]:
        """Columns that are NULL for every row -- i.e. never computed."""
        self.flush()
        out = []
        for n in COLUMN_NAMES:
            r = self.conn.execute(
                f'SELECT COUNT("{n}") FROM "{TABLE}" WHERE "{FK_COL}"=?',
                (db_session_id,),
            ).fetchone()[0]
            if int(r) == 0:
                out.append(n)
        return out

    def integrity_check(self) -> str:
        self.flush()
        return str(self.conn.execute("PRAGMA integrity_check").fetchone()[0])

    def close(self) -> None:
        try:
            self.flush()
        finally:
            self.conn.close()

# ==================================================================== #
# Self-check
# ==================================================================== #

def _mk_frame(i: int, sid: str = "selftest") -> TelemetryPayload:
    """A synthetic frame with a slow oil-pressure decay, 10 Hz timestamps."""
    return TelemetryPayload(
        timestamp=1_760_000_000.0 + i * 0.1,
        frame_id=i,
        session_id=sid,
        flight_time_hr=i * 0.1 / 3600.0,
        engine_state=EngineState.RUNNING,
        rpm=5000.0 + (i % 7),
        throttle_pct=80.0,
        altitude_m=1828.8,
        oat_c=10.0,
        fuel_flow_lph=30.0,
        coolant_temp_in_c=71.0,
        coolant_temp_out_c=78.0,
        egt_1_c=450.0, egt_2_c=455.0, egt_3_c=460.0, egt_4_c=465.0,
        egt_spread_c=15.0,
        oil_pressure_kpa=250.0 - i * 0.5,
        oil_temp_c=92.0,
    )


def _self_test() -> None:
    fails: List[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            fails.append(msg)

    print("RAW STORE SELF-CHECK")
    print(f"  raw schema version {RAW_SCHEMA_VERSION}")
    print(f"  canonical columns  {len(COLUMN_NAMES)}")

    # -- CASE 0: type mapping ----------------------------------------- #
    print("\nCASE 0  column type mapping")
    kinds: Dict[str, int] = {}
    for _, t in COLUMN_TYPES:
        kinds[t] = kinds.get(t, 0) + 1
    print(f"  {kinds}")
    check(len(COLUMN_TYPES) == 68, f"expected 68 typed columns, got {len(COLUMN_TYPES)}")
    check(dict(COLUMN_TYPES)["engine_state"] == "TEXT", "engine_state must map to TEXT")
    check(dict(COLUMN_TYPES)["frame_id"] == "INTEGER", "frame_id must map to INTEGER")
    check(dict(COLUMN_TYPES)["rpm"] == "REAL", "rpm must map to REAL")

    # -- CASE 1: introspect the live DB, read-only -------------------- #
    print("\nCASE 1  discovery against the live database")
    live = Path(os.environ.get("AERIS_DB") or (ROOT / "data" / "aeris.db"))
    if live.is_file():
        c = sqlite3.connect(f"file:{live}?mode=ro", uri=True, check_same_thread=False)
        d = discover(c)
        print(f"  {live}")
        print(f"  {d.describe()}")
        if d.sessions_table is None:
            print("  WARNING no sessions table found; FK would be omitted")
        c.close()
    else:
        print(f"  {live} absent -- skipping live discovery")

    # -- CASE 2: schema creation in a temp DB ------------------------- #
    print("\nCASE 2  table creation")
    tmp = Path(os.environ.get("TEMP", ".")) / f"aeris_rawstore_{os.getpid()}.db"
    for suf in ("", "-wal", "-shm"):
        Path(str(tmp) + suf).unlink(missing_ok=True)
    rs = RawStore(tmp, flush_every=50, flush_seconds=5.0,
                  create_sessions_if_absent=True)
    cur = rs.conn.execute(
        "INSERT INTO sessions(started_utc, note) VALUES(?, ?)",
        (_now_utc(), "raw store self-test"),
    )
    sid = int(cur.lastrowid)
    cols = rs.table_columns()
    print(f"  db          : {tmp.name}")
    print(f"  sessions row: {sid}")
    print(f"  table cols  : {len(cols)} (68 canonical + id/fk/seq/recorded)")
    print(f"  fk wired to : {rs.discovered.sessions_table}."
          f"{rs.discovered.sessions_pk}")
    check(len(cols) == 68 + 4, f"unexpected column count {len(cols)}")
    for n in COLUMN_NAMES:
        if n not in cols:
            fails.append(f"canonical column missing from table: {n}")
            break

    # -- CASE 3: ingest cost ------------------------------------------ #
    print("\nCASE 3  ingest 300 frames")
    costs = []
    for i in range(300):
        t0 = time.perf_counter()
        rs.add_frame(_mk_frame(i), sid, i)
        costs.append((time.perf_counter() - t0) * 1000.0)
    rs.flush()
    costs.sort()
    mean = sum(costs) / len(costs)
    print(f"  mean={mean:.3f} ms  p95={costs[int(0.95*len(costs))]:.3f} ms  "
          f"max={costs[-1]:.3f} ms")
    print(f"  rows written: {rs.count(sid)}")
    check(rs.count(sid) == 300, "not all frames persisted")
    check(mean < 1.0, f"ingest too slow: {mean:.3f} ms/frame")

    # -- CASE 4: 68-column round trip --------------------------------- #
    print("\nCASE 4  full round trip")
    orig = _mk_frame(7)
    back = rs.get_frame(sid, 7)
    check(back is not None, "frame 7 not readable")
    if back is not None:
        diffs = []
        for n in COLUMN_NAMES:
            a, b = getattr(orig, n), getattr(back, n)
            if isinstance(a, float) and isinstance(b, float):
                if abs(a - b) > 1e-9:
                    diffs.append(f"{n}: {a!r} -> {b!r}")
            elif a != b:
                diffs.append(f"{n}: {a!r} -> {b!r}")
        print(f"  columns compared: {len(COLUMN_NAMES)}  differences: {len(diffs)}")
        for d in diffs[:5]:
            print(f"    {d}")
        check(not diffs, f"round trip altered {len(diffs)} columns")
        print(f"  engine_state survived as enum: "
              f"{back.engine_state is EngineState.RUNNING}")
        check(back.engine_state is EngineState.RUNNING, "enum did not round-trip")

    # -- CASE 5: NULL preservation for uncomputed DSP fields ---------- #
    print("\nCASE 5  None stays NULL, never 0.0")
    nulls = rs.null_columns(sid)
    print(f"  all-NULL columns ({len(nulls)}): {nulls}")
    if back is not None:
        vib_none = [n for n in nulls if n.startswith("vib_")]
        print(f"  vib_* still NULL: {vib_none}")
        for n in nulls:
            if getattr(back, n) == 0.0 and getattr(back, n) is not None:
                fails.append(f"{n} came back 0.0 instead of None")
                break
        check(bool(nulls), "expected the DSP fields to be NULL by default")

    # -- CASE 6: replay ordering -------------------------------------- #
    print("\nCASE 6  replay in flight order")
    seqs = [s for s, _ in rs.replay(sid, limit=5)]
    print(f"  first 5 seq   : {seqs}")
    check(seqs == [0, 1, 2, 3, 4], f"replay out of order: {seqs}")
    mid = list(rs.replay(sid, start_seq=298))
    print(f"  from seq 298  : {[s for s, _ in mid]}")
    check(len(mid) == 2, "start_seq offset wrong")
    p0 = dict(rs.replay(sid, limit=1))[0]
    print(f"  frame 0 oil   : {p0.oil_pressure_kpa:.1f} kPa")
    plast = mid[-1][1]
    print(f"  frame 299 oil : {plast.oil_pressure_kpa:.1f} kPa "
          f"(decay preserved: {plast.oil_pressure_kpa < p0.oil_pressure_kpa})")
    check(plast.oil_pressure_kpa < p0.oil_pressure_kpa, "decay not preserved")

    # -- CASE 7: span / rate ------------------------------------------ #
    print("\nCASE 7  session span")
    span = rs.session_span(sid)
    print(f"  frames={span['frames']} seq={span['seq_first']}..{span['seq_last']}")
    print(f"  duration={span['duration_s']:.2f} s  rate={span['rate_hz']:.2f} Hz")
    check(span["frames"] == 300, "span frame count wrong")
    check(abs((span["rate_hz"] or 0) - 10.0) < 0.5, "rate not ~10 Hz")

    # -- CASE 8: durability across reopen ----------------------------- #
    print("\nCASE 8  reopen")
    rs.close()
    rs2 = RawStore(tmp, create_sessions_if_absent=False)
    print(f"  frames after reopen: {rs2.count(sid)}")
    print(f"  integrity          : {rs2.integrity_check()}")
    check(rs2.count(sid) == 300, "rows lost across reopen")
    check(rs2.integrity_check() == "ok", "integrity check failed")

    # -- CASE 9: FK cascade ------------------------------------------- #
    print("\nCASE 9  cascade delete")
    before = rs2.count(sid)
    rs2.conn.execute("PRAGMA foreign_keys=ON")
    rs2.conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
    after = rs2.count(sid)
    print(f"  raw rows {before} -> {after} after deleting session {sid}")
    check(after == 0, f"cascade did not remove raw rows ({after} left)")

    # -- CASE 10: loose dict is refused ------------------------------- #
    print("\nCASE 10  type discipline")
    try:
        rs2.add_frame({"rpm": 5000.0}, 1, 0)  # type: ignore[arg-type]
        fails.append("a plain dict was accepted")
    except RawStoreError as exc:
        print(f"  dict refused: {str(exc)[:70]}")

    rs2.close()
    for suf in ("", "-wal", "-shm"):
        Path(str(tmp) + suf).unlink(missing_ok=True)

    print()
    if fails:
        print("RAW STORE SELF-CHECK FAILED:")
        for m in fails:
            print(f"  - {m}")
        raise SystemExit(1)
    print("RAW STORE SELF-CHECK OK")
    print("  note: vib_samples is not persisted; replay reproduces DSP outputs, "
          "not the raw accelerometer window")


if __name__ == "__main__":
    _self_test()