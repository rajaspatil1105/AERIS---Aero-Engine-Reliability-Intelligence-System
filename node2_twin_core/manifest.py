"""
manifest.py -- runtime model contract.

Loads models/model_manifest.json and refuses to let the pipeline start if
the manifest, the artifacts on disk, and the constants hard-coded in the
node2 modules do not all agree.

The point: when the models are retrained on real data, the only thing that
must change is the manifest.  If the new artifact has a different feature
order or label map and nobody updates the manifest, this module stops the
system instead of letting it emit confidently wrong faults.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
MANIFEST_PATH = MODELS / "model_manifest.json"
SUPPORTED_SCHEMA = "1.0"


class ManifestError(RuntimeError):
    """Manifest missing, malformed, or contradicted by disk or code."""


@dataclass(frozen=True)
class Artifact:
    filename: str
    relpath: str
    sha256: str
    size_bytes: int
    observed: dict[str, Any]
    declared: dict[str, Any]

    @property
    def role(self) -> str:
        return self.declared.get("role", "unknown")

    @property
    def trusted(self) -> bool:
        return bool(self.declared.get("trusted", False))

    @property
    def caveat(self) -> str:
        return self.declared.get("caveat", "")

    @property
    def path(self) -> Path:
        return MODELS / self.relpath

    def input_order(self) -> list[str] | None:
        return self.declared.get("input_order")

    def label_map(self) -> dict[int, str] | None:
        raw = self.declared.get("label_map")
        if raw is None:
            return None
        return {int(k): v for k, v in raw.items()}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

class ModelManifest:
    """Read-only view over model_manifest.json with three verifiers."""

    def __init__(self, data: dict[str, Any]) -> None:
        if data.get("schema_version") != SUPPORTED_SCHEMA:
            raise ManifestError(
                f"manifest schema {data.get('schema_version')!r} is not "
                f"supported (expected {SUPPORTED_SCHEMA!r})")
        self.data = data
        self.artifacts = [
            Artifact(a["filename"], a["relpath"], a["sha256"], a["size_bytes"],
                     a["observed"], a["declared"])
            for a in data["artifacts"]
        ]

    @classmethod
    def load(cls, path: Path | None = None) -> "ModelManifest":
        p = path or MANIFEST_PATH
        if not p.is_file():
            raise ManifestError(
                f"no model manifest at {p}. Run build_manifest.py. "
                "Models without a manifest are not permitted to run.")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ManifestError(f"manifest is not valid JSON: {exc}") from exc
        return cls(data)

    # -- lookups ---------------------------------------------------------
    def require(self, filename: str) -> Artifact:
        for a in self.artifacts:
            if a.filename == filename:
                return a
        raise ManifestError(
            f"'{filename}' has no manifest entry. Undeclared models may not "
            "be loaded; add it to build_manifest.py and rebuild.")

    def by_role(self, role: str) -> list[Artifact]:
        return [a for a in self.artifacts if a.role == role]

    @property
    def feature_order(self) -> list[str]:
        return list(self.data["feature_order"])

    @property
    def baseline_input_order(self) -> list[str]:
        return list(self.data["baseline_input_order"])

    @property
    def data_is_measured(self) -> bool:
        return bool(self.data["data_provenance"]["measured_engine_data"])

    # -- verifiers -------------------------------------------------------
    def verify_files(self, check_hash: bool = True) -> list[str]:
        problems: list[str] = []
        declared = {a.relpath for a in self.artifacts}
        for a in self.artifacts:
            if not a.path.is_file():
                problems.append(f"{a.relpath}: declared but missing on disk")
                continue
            if a.path.stat().st_size != a.size_bytes:
                problems.append(f"{a.relpath}: size changed since manifest build")
                continue
            if check_hash and _sha256(a.path) != a.sha256:
                problems.append(
                    f"{a.relpath}: sha256 mismatch -- artifact was replaced "
                    "without rebuilding the manifest")
        for f in sorted(MODELS.rglob("*.pkl")):
            rel = f.relative_to(MODELS).as_posix()
            if rel not in declared:
                problems.append(f"{rel}: present on disk but undeclared")
        return problems

    def verify_against_code(self) -> list[str]:
        problems: list[str] = []

        def compare(label, actual, expected):
            if actual is None:
                problems.append(f"{label}: constant not found in module")
            elif list(actual) != list(expected):
                problems.append(
                    f"{label}: code={list(actual)} manifest={list(expected)}")

        try:
            from node2_twin_core import physics_deck, residual_calc
        except Exception as exc:
            return [f"cannot import node2 modules: {exc}"]

        compare("physics_deck.BASELINE_INPUT_ORDER",
                getattr(physics_deck, "BASELINE_INPUT_ORDER", None),
                self.baseline_input_order)
        compare("residual_calc.FEATURE_ORDER",
                getattr(residual_calc, "FEATURE_ORDER", None),
                self.feature_order)

        try:
            from node2_twin_core import predictor
        except Exception as exc:
            problems.append(f"cannot import predictor: {exc}")
            return problems

        art = self.require("fault_classifier_multiclass.pkl")
        expected_map = art.label_map() or {}
        code_map = getattr(predictor, "CLASS_INDEX_MAP", None)
        if code_map is None:
            problems.append("predictor.CLASS_INDEX_MAP: constant not found")
        else:
            code_map = {int(k): v for k, v in code_map.items()}
            if code_map != expected_map:
                problems.append(
                    f"CLASS_INDEX_MAP: code={code_map} manifest={expected_map}")
        gate = self.require("fault_classifier.pkl")
        code_anom = getattr(predictor, "GATE_ANOMALY_CLASS", None)
        if code_anom != gate.declared.get("anomaly_class"):
            problems.append(
                f"GATE_ANOMALY_CLASS: code={code_anom} "
                f"manifest={gate.declared.get('anomaly_class')}")
        return problems

    def enforce(self, check_hash: bool = True) -> None:
        problems = self.verify_files(check_hash) + self.verify_against_code()
        if problems:
            raise ManifestError(
                "model contract violated:\n  - " + "\n  - ".join(problems))

    # -- UI --------------------------------------------------------------
    def trust_report(self) -> list[dict[str, Any]]:
        return [
            {"artifact": a.filename, "role": a.role,
             "trusted": a.trusted, "caveat": a.caveat}
            for a in self.artifacts if not a.trusted or a.caveat
        ]


def _self_test() -> None:
    print("MANIFEST SELF-CHECK\n")
    m = ModelManifest.load()
    print(f"  schema {m.data['schema_version']}  built {m.data['generated_at_utc']}")
    print(f"  artifacts {len(m.artifacts)}  measured data: {m.data_is_measured}")

    print("\nCASE 1  files on disk match the manifest")
    fp = m.verify_files(check_hash=True)
    print("  problems:", fp or "none")

    print("\nCASE 2  code constants match the manifest")
    cp = m.verify_against_code()
    print("  problems:", cp or "none")

    print("\nCASE 3  undeclared artifact is refused")
    try:
        m.require("nonexistent_model.pkl")
        print("  FAIL: undeclared artifact was accepted")
    except ManifestError as exc:
        print(f"  rejected: {str(exc).splitlines()[0]}")

    print("\nCASE 4  trust surface for the dashboard")
    for row in m.trust_report():
        flag = "TRUSTED" if row["trusted"] else "UNTRUSTED"
        print(f"  {row['artifact']:<38} {row['role']:<20} {flag}")

    print("\nCASE 5  open issues carried into Node 3/4")
    for issue in m.data["open_issues"]:
        print(f"  - {issue}")

    print("\nCASE 6  enforce() is the single startup gate")
    try:
        m.enforce()
        print("  enforce() passed -- pipeline may start")
    except ManifestError as exc:
        print(f"  enforce() blocked startup:\n{exc}")

    if fp or cp:
        print("\nMANIFEST SELF-CHECK FAILED")
        raise SystemExit(1)
    print("\nMANIFEST SELF-CHECK OK")
    print("NOTE: the manifest asserts a contract, not correctness. It")
    print("      guarantees the code and the artifacts agree; it does not")
    print("      make an untrusted model trustworthy.")


if __name__ == "__main__":
    _self_test()