"""
AERIS -- one-off artifact inspection tool. READ-ONLY. Delete when finished.

Reports what each uploaded model artifact actually declares about its own
inputs and outputs, so that the Node 2 interfaces can be written against
ground truth instead of assumption.
"""

from __future__ import annotations

import json
import pickle
import warnings
from pathlib import Path
from typing import Any

MODELS = Path(__file__).resolve().parent / "models"


def load(path: Path) -> Any:
    """Try joblib first (sklearn's usual format), then plain pickle."""
    try:
        import joblib

        return joblib.load(path)
    except Exception as exc_joblib:
        try:
            with path.open("rb") as fh:
                return pickle.load(fh)
        except Exception as exc_pickle:
            raise RuntimeError(
                f"joblib failed ({type(exc_joblib).__name__}: {exc_joblib}); "
                f"pickle failed ({type(exc_pickle).__name__}: {exc_pickle})"
            ) from exc_pickle


def describe(obj: Any, indent: str = "    ") -> None:
    print(f"{indent}python type      : {type(obj).__module__}.{type(obj).__name__}")

    if isinstance(obj, dict):
        print(f"{indent}dict keys        : {list(obj.keys())}")
        for k, v in obj.items():
            print(f"{indent}  [{k}] -> {type(v).__module__}.{type(v).__name__}")
            if hasattr(v, "n_features_in_") or hasattr(v, "predict"):
                describe(v, indent + "    ")
        return

    if hasattr(obj, "steps"):  # sklearn Pipeline
        print(f"{indent}pipeline steps   : "
              f"{[(n, type(s).__name__) for n, s in obj.steps]}")

    for attr in ("n_features_in_", "n_outputs_", "n_estimators", "max_depth",
                 "learning_rate", "loss", "criterion"):
        if hasattr(obj, attr):
            print(f"{indent}{attr:<17}: {getattr(obj, attr)}")

    if hasattr(obj, "feature_names_in_"):
        names = list(obj.feature_names_in_)
        print(f"{indent}feature_names_in_: {len(names)}")
        for i, n in enumerate(names):
            print(f"{indent}   [{i:>2}] {n}")
    else:
        print(f"{indent}feature_names_in_: ABSENT "
              f"(fitted on a bare array -- order is undocumented in the artifact)")

    if hasattr(obj, "classes_"):
        print(f"{indent}classes_         : {list(obj.classes_)} "
              f"(dtype {getattr(obj.classes_, 'dtype', '?')})")

    if hasattr(obj, "__getstate__"):
        try:
            state = obj.__getstate__()
            if isinstance(state, dict) and "_sklearn_version" in state:
                print(f"{indent}pickled with     : sklearn "
                      f"{state['_sklearn_version']}")
        except Exception:
            pass


def main() -> None:
    warnings.simplefilter("always", UserWarning)

    if not MODELS.exists():
        print(f"MISSING: {MODELS}")
        raise SystemExit(1)

    print("=" * 70)
    print("DIRECTORY TREE")
    print("=" * 70)
    for p in sorted(MODELS.rglob("*")):
        rel = str(p.relative_to(MODELS))
        if p.is_dir():
            print(f"  {rel}/")
        else:
            print(f"  {rel:<48} {p.stat().st_size:>10,} bytes")


    print("\n" + "=" * 70)
    print("JSON / CONFIG FILES (verbatim)")
    print("=" * 70)
    for p in sorted(MODELS.rglob("*.json")):
        print(f"\n--- {p.relative_to(MODELS)} ---")
        try:
            print(json.dumps(json.loads(p.read_text(encoding="utf-8")), indent=2))
        except Exception as exc:
            print(f"  UNREADABLE: {exc}")
            print(f"  raw head: {p.read_text(encoding='utf-8', errors='replace')[:500]}")

    print("\n" + "=" * 70)
    print("PICKLED MODELS")
    print("=" * 70)
    for p in sorted(MODELS.rglob("*.pkl")):
        print(f"\n--- {p.relative_to(MODELS)} ---")
        try:
            obj = load(p)
        except Exception as exc:
            print(f"    LOAD FAILED: {exc}")
            continue
        describe(obj)

    print("\n" + "=" * 70)
    print("INSPECTION COMPLETE -- nothing was modified")
    print("=" * 70)


if __name__ == "__main__":
    main()
