"""
build_manifest.py -- one-shot generator for models/model_manifest.json

For every .pkl under models/ this records three separate things:

  observed   -- what the artifact actually is, read from the object itself
                (class, n_features_in_, classes_, presence of
                feature_names_in_).  Cannot be wrong.
  declared   -- what we believe about it (input order, label map, metrics,
                training scope, envelope).
  provenance -- WHERE each declared fact came from.  "training_pipeline"
                means a human read it off the training script.
                "recovered_by_experiment" means we inferred it by probing
                the artifact and it must be re-confirmed after retraining.

Re-run this after every retrain.  The runtime loader
(node2_twin_core/manifest.py) refuses to start if the manifest and the
code constants disagree.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import sklearn

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"
OUT = MODELS / "model_manifest.json"
SCHEMA_VERSION = "1.0"

BASELINE_INPUT_ORDER = ["rpm", "throttle_pct", "altitude_ft", "ambient_temperature_C"]

FEATURE_ORDER = [
    "altitude_ft", "ambient_temperature_C", "throttle_pct", "rpm",
    "fuelflow_kgh", "coolant_temp_C", "EGT_mean_C", "oil_pressure_bar",
    "oil_temperature_C", "delta_EGT_mean_C", "delta_coolant_temp_C",
    "delta_oil_pressure_bar", "delta_oil_temperature_C", "delta_fuelflow_kgh",
]

ENVELOPE = {
    "rpm": [3000.0, 5800.0],
    "throttle_pct": [56.5, 100.0],
    "altitude_ft": [0.0, 21709.3],
    "ambient_temperature_C": [-27.9845, 30.0],
}

BASELINE_RANGE = {
    "EGT_mean_C": [363.541, 664.077],
    "coolant_temp_C": [62.257, 87.435],
    "oil_pressure_bar": [2.198, 4.255],
    "oil_temperature_C": [66.605, 82.562],
    "fuelflow_kgh": [4.332, 34.018],
}

CLASS_INDEX_MAP = {
    "0": "cooling_degradation",
    "1": "fuel_pressure_dev",
    "2": "lubrication_degradation",
    "3": "misfire",
    "4": "sensor_drift",
}

SAFETY_LIMITS = {
    "oil_pressure_bar": {"min": 1.0},
    "coolant_temp_C": {"max": 120.0},
    "EGT_mean_C": {"max": 950.0},
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def introspect(obj) -> dict:
    out = {
        "python_class": type(obj).__name__,
        "module": type(obj).__module__,
        "has_feature_names_in_": hasattr(obj, "feature_names_in_"),
    }
    for attr in ("n_features_in_", "n_estimators", "max_depth", "n_classes_"):
        v = getattr(obj, attr, None)
        if v is not None:
            try:
                out[attr] = int(v)
            except (TypeError, ValueError):
                out[attr] = str(v)
    classes = getattr(obj, "classes_", None)
    if classes is not None:
        out["classes_"] = [c.item() if hasattr(c, "item") else c for c in classes]
        out["classes_dtype"] = str(getattr(classes, "dtype", type(classes[0]).__name__))
    return out


def declared_for(name: str) -> dict:
    if name.endswith("_baseline.pkl"):
        target = name[: -len("_baseline.pkl")]
        return {
            "role": "baseline_regressor",
            "target": target,
            "input_order": BASELINE_INPUT_ORDER,
            "input_order_provenance": "recovered_by_experiment (identify_feature_order.py + throttle sweep)",
            "training_scope": "healthy-only rows (fault_type == 'healthy'), 50000 samples",
            "training_scope_provenance": "training_pipeline",
            "output_range_observed_healthy": BASELINE_RANGE.get(target),
            "trusted": True,
            "caveat": "healthy-only fit; residuals are meaningless outside the training envelope",
        }
    if name == "fault_classifier.pkl":
        return {
            "role": "anomaly_gate",
            "input_order": FEATURE_ORDER,
            "input_order_provenance": "config file + feature_names.json (14/14 validated)",
            "anomaly_class": 1,
            "decision_threshold": 0.65,
            "metrics": {"precision": 0.511, "recall": 0.998, "f1": 0.676,
                        "accuracy": 0.5286, "positive_class_prior": 0.511},
            "metrics_provenance": "training_pipeline",
            "trusted": False,
            "caveat": "F1 equals the trivial always-positive baseline; output is near chance. "
                      "Disabled as a sole decision source; retrain on the multiclass matrix "
                      "with labels collapsed to healthy / not-healthy.",
        }
    if name == "fault_classifier_multiclass.pkl":
        return {
            "role": "fault_classifier",
            "input_order": FEATURE_ORDER,
            "input_order_provenance": "config file + feature_names.json (14/14 validated)",
            "label_map": CLASS_INDEX_MAP,
            "label_map_provenance": "recovered_by_experiment (resolve_labels2.py directional sweep)",
            "dead_classes": ["fuel_pressure_dev"],
            "trusted": False,
            "caveat": "label map inferred, not read from training code; class "
                      "'fuel_pressure_dev' is never argmax for any fuel-flow excursion; "
                      "responses saturate at the first offset step so severity is not resolved.",
        }
    if name == "rul_regressor.pkl":
        return {
            "role": "rul_regressor",
            "input_order": FEATURE_ORDER,
            "input_order_provenance": "config file + feature_names.json (14/14 validated)",
            "units": "unknown",
            "units_provenance": "MISSING -- must be read from the training script",
            "metrics": {"r2": -0.103, "mae": 107.0},
            "metrics_provenance": "training_pipeline",
            "trusted": False,
            "caveat": "negative R2: worse than predicting the training mean. "
                      "Display as a trend indicator only, never as a time-to-failure.",
        }
    return {"role": "unknown", "trusted": False,
            "caveat": "artifact not declared in build_manifest.py DECLARED table"}


def main() -> int:
    if not MODELS.is_dir():
        print(f"ABORT: {MODELS} not found")
        return 1

    files = sorted(MODELS.rglob("*.pkl"))
    if not files:
        print(f"ABORT: no .pkl files under {MODELS}")
        return 1

    artifacts, warnings = [], []
    for f in files:
        print(f"  reading {f.relative_to(MODELS)} ...", flush=True)
        obj = joblib.load(f)
        observed = introspect(obj)
        declared = declared_for(f.name)

        n_in = observed.get("n_features_in_")
        order = declared.get("input_order")
        if order is not None and n_in is not None and n_in != len(order):
            warnings.append(f"{f.name}: n_features_in_={n_in} but declared order has {len(order)} names")
        lmap = declared.get("label_map")
        cls = observed.get("classes_")
        if lmap is not None and cls is not None and len(lmap) != len(cls):
            warnings.append(f"{f.name}: {len(cls)} model classes but {len(lmap)} declared labels")
        if declared.get("role") == "unknown":
            warnings.append(f"{f.name}: undeclared artifact")

        artifacts.append({
            "filename": f.name,
            "relpath": f.relative_to(MODELS).as_posix(),
            "size_bytes": f.stat().st_size,
            "sha256": sha256(f),
            "observed": observed,
            "declared": declared,
        })

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sklearn_version_at_build": sklearn.__version__,
        "data_provenance": {
            "source": "Cantera simulation, synthetic",
            "measured_engine_data": False,
            "note": "no artifact trained on this data may be presented as a "
                    "validated engine model; all outputs are pipeline demonstrations.",
        },
        "baseline_input_order": BASELINE_INPUT_ORDER,
        "feature_order": FEATURE_ORDER,
        "training_envelope": ENVELOPE,
        "safety_limits": SAFETY_LIMITS,
        "explainability": {
            "gate": "shap.TreeExplainer (exact)",
            "fault_classifier": "shap.explainers.Permutation",
            "note": "TreeExplainer rejects multiclass GradientBoostingClassifier. "
                    "Retrain as HistGradientBoosting / RandomForest / XGBoost to "
                    "enable exact multiclass tree attribution.",
        },
        "open_issues": [
            "gate performance is at chance (F1 0.676 == trivial baseline)",
            "multiclass label map recovered by experiment, not read from training code",
            "training script advertises a 6-label encoder; the artifact has 5 classes",
            "class 'fuel_pressure_dev' is unreachable",
            "RUL R2 is negative and its units are unknown",
        ],
        "required_from_next_training_run": [
            "feature_order: exact list of column names in matrix order",
            "label_map: {int: name} taken from LabelEncoder.classes_",
            "target_units: units of the RUL target",
            "training_envelope: min/max of every operating coordinate",
            "training_scope: which rows each model saw",
            "metrics: held-out precision/recall/f1 per class, r2/mae for RUL",
            "data_provenance: simulated or measured, dataset hash",
        ],
        "artifacts": artifacts,
    }

    OUT.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nwritten: {OUT}")
    print(f"artifacts recorded: {len(artifacts)}")
    if warnings:
        print("\nCONSISTENCY WARNINGS (recorded, not fatal):")
        for w in warnings:
            print(f"  - {w}")
    else:
        print("no consistency warnings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())