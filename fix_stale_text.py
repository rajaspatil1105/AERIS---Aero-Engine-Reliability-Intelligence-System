import io, shutil

EDITS = [
 (r"node2_twin_core\predictor.py", [
  ("RUL          R2 -0.103, worse than predicting the training mean.",
   "RUL          R2 +0.842, MAE 93 h on four held-out engines."),
  ('"reason": "R2 -0.103, worse than the mean predictor. The number "\n                  "carries no demonstrated accuracy.",',
   '"reason": "R2 +0.842, MAE 93 h over a 1272 h label spread on "\n                  "four held-out engines. delta_oil_pressure_bar carries "\n                  "0.938 of feature importance: effectively one channel.",'),
  ('"metrics": {"r2": -0.1033, "mae": 107.0},',
   '"metrics": {"r2": 0.842, "mae": 93.0},'),
 ]),
 (r"node3_service\api.py", [
  ("RUL R2 is negative; the dashboard is expected to render this, and the",
   "models are synthetic-only; the dashboard is expected to render this, and the"),
 ]),
 (r"node3_service\canonical.py", [
  ('"verdicts produced by the models currently loaded; gate F1 is at "\n            "chance and RUL R2 is negative, so these counts are not a "\n            "trustworthy diagnosis"',
   '"verdicts produced by the models currently loaded; trained on "\n            "synthetic MVEM data with one validated operating point, so "\n            "these counts are not a trustworthy diagnosis"'),
 ]),
 (r"node3_service\ingest.py", [
  ('"caveat": "gate is at chance and RUL R2 is negative; "\n                      "statuses are pipeline output, not validated diagnosis",',
   '"caveat": "models trained on synthetic MVEM data, one validated "\n                      "operating point; statuses are pipeline output, not "\n                      "validated diagnosis",'),
 ]),
 (r"node3_service\report.py", [
  ('BANNER = ("MODELS UNTRUSTED -- gate F1 0.676 = trivial baseline | RUL R2 "\n          "-0.103 | placeholder models, plumbing verified. Nothing in this "\n          "document is airworthiness evidence.")',
   'BANNER = ("MODELS UNVALIDATED -- gate F1 0.907 / ROC-AUC 0.985 | RUL R2 "\n          "+0.842 / MAE 93 h, held-out engines | synthetic MVEM data, one "\n          "validated operating point. Nothing in this document is "\n          "airworthiness evidence.")'),
  ('"Gate F1 0.676 is indistinguishable from a trivial always-fault "\n            "baseline. RUL R2 is -0.103, i.e. worse than predicting the mean, "\n            "so rul_trusted is false on every frame and RUL is exposed for "\n            "ordering only, never as a time. Baselines are healthy-only "',
   '"Gate F1 is 0.907 with ROC-AUC 0.985 on ten held-out engines. "\n            "RUL scores R2 +0.842, MAE 93 h on four held-out engines, but "\n            "0.938 of its feature importance sits on one channel and all "\n            "training data is synthetic. Baselines are healthy-only "'),
 ]),
 (r"node3_service\store.py", [
  ('print("      and RUL R2 is negative. Stored numbers are not evidence.")',
   'print("      Models are synthetic-only. Stored numbers are not evidence.")'),
  ('print("NOTE: sessions carry models_trusted=0 while the gate is at chance")',
   'print("NOTE: sessions carry models_trusted=0 while training data is")'),
 ]),
]

for path, subs in EDITS:
    src = io.open(path, encoding="utf-8").read()
    for old, new in subs:
        n = src.count(old)
        if n != 1:
            print(f"SKIP  {path}: pattern found {n}x -- {old[:50]}...")
            continue
        src = src.replace(old, new)
    shutil.copyfile(path, path + ".bak_stale")
    io.open(path, "w", encoding="utf-8").write(src)
    print(f"ok    {path}")
