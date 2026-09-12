import json, pathlib
rep = json.loads(pathlib.Path("models/classifier/retrain_metrics_mvem.json").read_text(encoding="utf-8"))
g = rep["gate"]
p = pathlib.Path("models/configs/reconstruction_config.json")
pathlib.Path(str(p) + ".thrbak").write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
d = json.loads(p.read_text(encoding="utf-8"))
print("confidence_threshold %s -> %s" % (d.get("confidence_threshold"), g["threshold"]))
d["confidence_threshold"] = g["threshold"]
d["anomaly_gate_performance"] = {k: g[k] for k in g}
d["anomaly_gate_performance"]["source"] = "train_classifiers_mvem.py, mvem_v3.parquet, threshold chosen on validation engines"
p.write_text(json.dumps(d, indent=2), encoding="utf-8")
print("config synced to the retrain")
