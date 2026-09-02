"""AERIS -- run every module self-test in one command.

WHY THIS EXISTS
Verification lived in 19 separate module-level _self_test() functions invoked
by hand from PowerShell. That is why the same breakages kept being
rediscovered from new angles: nobody ran all of them at once. This runner
executes each as a subprocess, so a crash or a SystemExit in one module cannot
hide the others, and reports one table.

WHAT A PASS MEANS
Each self-test raises SystemExit(1) on failure, so exit code 0 is the pass
signal. The runner additionally reports which modules pin the regression
invariant 0.5443998040908319, because that number moving is the single event
that should stop all work.

  python verify_all.py              full run, every module
  python verify_all.py --fast       skip the three slow sweep modules
  python verify_all.py --only twin  substring filter
  python verify_all.py --list       show the module list and exit
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import List, Optional, Tuple

INVARIANT = "0.5443998040908319"
DEFAULT_DB = r"C:\aeris_data\verify.db"

# (module, group, heavy). Order matters: node2 is the foundation, so a
# failure there explains failures everywhere above it.
MODULES: List[Tuple[str, str, bool]] = [
    ("node2_twin_core.manifest",        "node2",  False),
    ("node2_twin_core.physics_deck",    "node2",  False),
    ("node2_twin_core.plausibility",    "node2",  False),
    ("node2_twin_core.residual_calc",   "node2",  False),
    ("node2_twin_core.predictor",       "node2",  False),
    ("node2_twin_core.rul_engine",      "node2",  False),
    ("node2_twin_core.safety_limits",   "node2",  False),
    ("node2_twin_core.shap_explainer",  "node2",  True),
    ("node2_twin_core.twin_core",       "node2",  False),
    ("node1_ingestion.adapter",         "node1",  False),
    ("shared.atmosphere",               "shared", False),
    ("node3_service.store",             "node3",  False),
    ("node3_service.raw_store",         "node3",  False),
    ("node3_service.canonical",         "node3",  False),
    ("node3_service.ingest",            "node3",  False),
    ("node3_service.api",               "node3",  False),
    ("shared.stress_sim",               "shared", True),
    ("shared.fault_injection",          "shared", True),
    ("shared.throttle_dynamics",        "shared", True),
]

PASS, FAIL, TIMEOUT, SKIP = "PASS", "FAIL", "TIMEOUT", "SKIP"


class Result:
    def __init__(self, module: str, group: str) -> None:
        self.module = module
        self.group = group
        self.state = SKIP
        self.seconds = 0.0
        self.rc: Optional[int] = None
        self.headline = ""
        self.first_fail = ""
        self.pins_invariant = False
        self.output = ""


def _headline(text: str) -> str:
    """The module's own verdict line, whatever it called itself."""
    for line in reversed(text.splitlines()):
        s = line.strip()
        if "SELF-CHECK" in s or "SELF CHECK" in s:
            return s[:78]
    return ""


def _first_fail(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("FAIL:") or s.startswith("- "):
            return s[:110]
    return ""


def run_one(module: str, group: str, timeout_s: float) -> Result:
    r = Result(module, group)
    t0 = time.perf_counter()
    try:
        cp = subprocess.run(
            [sys.executable, "-u", "-m", module],
            capture_output=True, text=True, timeout=timeout_s,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        r.rc = cp.returncode
        r.output = (cp.stdout or "") + (cp.stderr or "")
        r.state = PASS if cp.returncode == 0 else FAIL
    except subprocess.TimeoutExpired as exc:
        r.state = TIMEOUT
        r.output = ((exc.stdout or "") if isinstance(exc.stdout, str)
                    else "") + f"\n[runner] exceeded {timeout_s:.0f} s"
    except Exception as exc:                       # runner-level problem
        r.state = FAIL
        r.output = f"[runner] {type(exc).__name__}: {exc}"
    r.seconds = time.perf_counter() - t0
    r.headline = _headline(r.output)
    r.first_fail = _first_fail(r.output)
    r.pins_invariant = INVARIANT in r.output
    return r


def main(argv: List[str]) -> int:
    fast = "--fast" in argv
    only: Optional[str] = None
    if "--only" in argv:
        i = argv.index("--only")
        if i + 1 < len(argv):
            only = argv[i + 1]

    selected = [(m, g, h) for m, g, h in MODULES
                if (only is None or only in m)]
    if "--list" in argv:
        for m, g, h in MODULES:
            print(f"  {g:<7} {m:<34} {'heavy' if h else ''}")
        return 0
    if not selected:
        print(f"  no module matches --only {only!r}")
        return 1

    os.environ.setdefault("AERIS_DB", DEFAULT_DB)
    timeout_s = 150.0 if fast else 480.0

    print(f"AERIS verify_all   {len(selected)} module(s)"
          f"{'  [--fast]' if fast else ''}")
    print(f"  python  {sys.executable}")
    print(f"  AERIS_DB={os.environ['AERIS_DB']}")
    print(f"  invariant {INVARIANT}, per-module timeout {timeout_s:.0f} s\n")

    results: List[Result] = []
    log_parts: List[str] = []
    for module, group, heavy in selected:
        if fast and heavy:
            r = Result(module, group)
            r.state = SKIP
            r.headline = "skipped by --fast"
            results.append(r)
            print(f"  {SKIP:<7} {module:<34}      -- heavy, skipped")
            continue
        print(f"  ...     {module:<34}", end="", flush=True)
        r = run_one(module, group, timeout_s)
        results.append(r)
        log_parts.append(f"{'=' * 74}\n=== {module}  [{r.state}] "
                         f"{r.seconds:.1f}s rc={r.rc}\n{'=' * 74}\n{r.output}")
        print(f"\r  {r.state:<7} {module:<34} {r.seconds:6.1f}s  "
              f"{r.headline or r.first_fail}")

    with open("verify_all.log", "w", encoding="utf-8") as fh:
        fh.write("\n".join(log_parts))

    print("\n  " + "-" * 72)
    counts = {s: sum(1 for r in results if r.state == s)
              for s in (PASS, FAIL, TIMEOUT, SKIP)}
    total = sum(r.seconds for r in results)
    print(f"  {counts[PASS]} passed, {counts[FAIL]} failed, "
          f"{counts[TIMEOUT]} timed out, {counts[SKIP]} skipped, "
          f"in {total:.1f}s")

    pinned = [r.module for r in results if r.pins_invariant]
    print(f"  regression invariant {INVARIANT} appears in "
          f"{len(pinned)} module(s):")
    for m in pinned:
        print(f"    {m}")
    if not pinned and counts[SKIP] == 0:
        print("    NONE -- no module pinned the invariant, which means nothing "
              "is guarding it")

    bad = [r for r in results if r.state in (FAIL, TIMEOUT)]
    if bad:
        print(f"\n  failures, in dependency order (fix node2 first):")
        for r in bad:
            print(f"    [{r.group}] {r.module}  rc={r.rc}")
            if r.first_fail:
                print(f"        {r.first_fail}")
            if r.headline:
                print(f"        {r.headline}")
        print("\n  full output in verify_all.log")
        print("  NOTE a module never run in this session may be failing for "
              "pre-existing reasons")
        print("  unrelated to recent work -- read the log before changing "
              "anything.")
        return 1

    print("\n  ALL SELF-TESTS GREEN")
    if counts[SKIP]:
        print(f"  ({counts[SKIP]} heavy module(s) skipped -- run without "
              f"--fast before committing)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
