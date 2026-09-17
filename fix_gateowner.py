import ast, pathlib
p = pathlib.Path("shared/stress_sim.py")
s = p.read_text(encoding="utf-8-sig")
old = "GATE_THRESHOLD = 0.65"
new = '''def _load_gate_threshold() -> float:
    """Gate threshold has ONE owner: models/configs/reconstruction_config.json.

    Was hard-coded 0.65 here and imported by fault_injection and
    throttle_dynamics, so both suites judged against a threshold the service
    had stopped using. The service decides at confidence_threshold (0.50 as of
    the MVEM retrain, re-derived on validation engines). A suite asserting
    0.65 produced self-contradictory output: fuel_lean at p_anom 0.0845 was
    reported both "below the 0.65 gate" and "crossed the gate but
    status=HEALTHY".
    """
    import json
    cfg = (pathlib.Path(__file__).resolve().parents[1]
           / "models" / "configs" / "reconstruction_config.json")
    return float(json.loads(cfg.read_text(encoding="utf-8-sig"))["confidence_threshold"])


GATE_THRESHOLD = _load_gate_threshold()'''
if s.count(old) != 1:
    raise SystemExit("anchor %d times" % s.count(old))
s = s.replace(old, new)
if "import pathlib" not in s:
    s = s.replace("import ", "import pathlib\nimport ", 1)
ast.parse(s); p.write_text(s, encoding="utf-8")
print("GATE_THRESHOLD now config-driven")
