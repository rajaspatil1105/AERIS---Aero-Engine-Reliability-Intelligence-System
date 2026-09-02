#!/usr/bin/env python
"""
identify_feature_order.py -- READ ONLY. Writes nothing, modifies nothing.

Recovers the fitted column order of models whose feature_names_in_ is absent,
by matching decision-tree split thresholds (which lie inside each column's
training range) against the ranges documented in reconstruction_config.json.
A second, independent functional check feeds the inferred order back through
the baseline forests and asks whether the answers are physically sane.
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"
CFG = MODELS / "configs" / "reconstruction_config.json"

PICKLED_WITH = "1.9.0"   # as recorded in reconstruction_config.json


def _preflight() -> None:
    """Refuse to run under an interpreter that would silently corrupt the
    evidence this script depends on."""
    print(f"interpreter  : {sys.executable}", flush=True)
    print(f"in venv      : {sys.prefix != getattr(sys, 'base_prefix', sys.prefix)}")
    if not CFG.is_file():
        sys.exit(f"ABORT: config not found at {CFG}")
    print("importing scikit-learn (first run may take ~60 s) ...", flush=True)
    try:
        import sklearn
    except ImportError as exc:
        sys.exit(f"ABORT: scikit-learn not installed here -- {exc}")
    print(f"scikit-learn : {sklearn.__version__}  "
          f"(artifacts pickled with {PICKLED_WITH})", flush=True)
    if sklearn.__version__ != PICKLED_WITH:
        sys.exit(
            "\nABORT: sklearn version mismatch.\n"
            "  Unpickling tree ensembles across versions can succeed WITHOUT\n"
            "  raising, while producing unreliable tree_.threshold arrays.\n"
            "  This script reads exactly those arrays, so every verdict it\n"
            f"  printed would be worthless. Install scikit-learn=={PICKLED_WITH}."
        )
    print()


_preflight()

import joblib                                     # noqa: E402
import numpy as np                                # noqa: E402
from scipy.optimize import linear_sum_assignment  # noqa: E402

cfg = json.loads(CFG.read_text(encoding="utf-8"))
BSTATS = cfg["baseline_stats"]
SCHEMA14 = cfg["feature_schema"]
COORDS_DOC = cfg["operating_coordinates"]


def build_candidates() -> dict:
    """Documented numeric envelope per named variable. Nothing invented
    except the delta ranges, which are assumed to be +/- 3 sigma."""
    cand: dict = {}
    ranges = [s["operating_range"] for s in BSTATS.values()]
    for k in ranges[0]:
        if any(r[k] != ranges[0][k] for r in ranges):
            print(f"WARNING operating_range disagrees across baselines for {k}")
        cand[k] = (float(ranges[0][k][0]), float(ranges[0][k][1]))
    for name, st in BSTATS.items():
        cand[name] = (float(st["min"]), float(st["max"]))
        s = float(st["std"])
        cand["delta_" + name] = (-3.0 * s, 3.0 * s)  # ASSUMPTION, not documented
    return cand


CANDIDATES = build_candidates()

def iter_trees(model):
    ests = getattr(model, "estimators_", None)
    if ests is None:
        yield model
        return
    for e in np.asarray(ests, dtype=object).ravel():
        yield e


def threshold_stats(model) -> dict:
    per: dict = {}
    for est in iter_trees(model):
        t = est.tree_
        f, thr = t.feature, t.threshold
        for fi, tv in zip(f[f >= 0], thr[f >= 0]):
            per.setdefault(int(fi), []).append(float(tv))
    return {k: (min(v), max(v), len(v)) for k, v in sorted(per.items())}


def cost(obs, cand) -> float:
    olo, ohi = obs
    clo, chi = cand
    ow = max(ohi - olo, 1e-9)
    cw = max(chi - clo, 1e-9)
    width = abs(np.log10(ow / cw))
    centre = abs((olo + ohi) / 2.0 - (clo + chi) / 2.0) / cw
    return float(width + centre)


def identify(label: str, model, declared: list):
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}", flush=True)
    stats = threshold_stats(model)
    n = int(model.n_features_in_)
    missing = [i for i in range(n) if i not in stats]
    if missing:
        print(f"  slots never used in any split (unidentifiable): {missing}")
    idx = [i for i in range(n) if i in stats]

    C = np.array([[cost((stats[i][0], stats[i][1]), CANDIDATES[nm])
                   for nm in declared] for i in idx])
    rows, cols = linear_sum_assignment(C)
    assign = {idx[r]: declared[c] for r, c in zip(rows, cols)}

    print(f"\n  {'slot':>4} {'observed threshold range':>32} {'splits':>7}"
          f"  {'declared':<24} {'evidence':<24} verdict")
    order: list = []
    for i in range(n):
        if i not in stats:
            order.append(f"<UNKNOWN_{i}>")
            continue
        lo, hi, k = stats[i]
        row = C[idx.index(i)]
        srt = np.sort(row)
        margin = float(srt[1] - srt[0]) if len(srt) > 1 else 9.9
        won = assign[i]
        dec = declared[i] if i < len(declared) else "-"
        if margin <= 0.5:
            verdict = f"AMBIGUOUS (2nd={declared[int(np.argsort(row)[1])]})"
        elif won == dec:
            verdict = "CONFIRMED"
        else:
            verdict = "CONTRADICTS declared"
        print(f"  {i:>4} [{lo:>14.4f},{hi:>14.4f}] {k:>7}"
              f"  {dec:<24} {won:<24} {verdict}")
        order.append(won)
    print(f"\n  evidence-based order: {order}", flush=True)
    return order

def functional_check(baselines: dict, order: list) -> None:
    print(f"\n{'=' * 70}\nFUNCTIONAL CHECK of order {order}\n{'=' * 70}")
    base = {"rpm": 5000.0, "throttle_pct": 80.0,
            "altitude_ft": 6000.0, "ambient_temperature_C": 10.0}
    if any(o not in base for o in order):
        print("  order contains unresolved slots -- skipping")
        return
    x = np.array([[base[n] for n in order]])
    for name, m in baselines.items():
        y = float(m.predict(x)[0])
        st = BSTATS[name]
        ok = "INSIDE" if st["min"] <= y <= st["max"] else "OUT OF RANGE"
        print(f"  {name:<20} pred={y:>10.3f}  documented "
              f"[{st['min']:.3f}, {st['max']:.3f}]  {ok}")
    print("\n  throttle sweep 60 -> 100 % (others fixed):")
    for name in ("EGT_mean_C", "fuelflow_kgh"):
        if name not in baselines:
            continue
        ys = []
        for thr in (60.0, 70.0, 80.0, 90.0, 100.0):
            q = dict(base, throttle_pct=thr)
            ys.append(float(baselines[name].predict(
                np.array([[q[n] for n in order]]))[0]))
        mono = all(b >= a - 1e-9 for a, b in zip(ys, ys[1:]))
        print(f"    {name:<16} {[round(v, 2) for v in ys]}  "
              f"monotonic_increase={mono}")


def main() -> None:
    baselines = {}
    for n in BSTATS:
        f = MODELS / "baseline" / f"{n}_baseline.pkl"
        print(f"loading {f.name} ...", flush=True)
        baselines[n] = joblib.load(f)

    orders = []
    for name, m in baselines.items():
        orders.append(identify(f"BASELINE  {name}  (4 inputs)", m, COORDS_DOC))
    if all(o == orders[0] for o in orders):
        print(f"\nAll five baselines agree on: {orders[0]}")
    else:
        print("\nBASELINES DISAGREE -- artifacts are not mutually consistent:")
        for n, o in zip(BSTATS, orders):
            print(f"  {n:<20} {o}")

    for sub, fn, lbl in (
        ("classifier", "fault_classifier.pkl", "BINARY GATE (14 inputs)"),
        ("classifier", "fault_classifier_multiclass.pkl", "MULTICLASS (14 inputs)"),
        ("rul", "rul_regressor.pkl", "RUL REGRESSOR (14 inputs)"),
    ):
        f = MODELS / sub / fn
        print(f"\nloading {f.name} ...", flush=True)
        identify(lbl, joblib.load(f), SCHEMA14)

    seen = []
    for cand in (orders[0], COORDS_DOC, SCHEMA14[:4]):
        if list(cand) in seen:
            continue
        seen.append(list(cand))
        functional_check(baselines, list(cand))

    print("\nIDENTIFICATION COMPLETE -- nothing was modified")


if __name__ == "__main__":
    main()