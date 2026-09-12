import json, pathlib, sys
sys.path.insert(0, ".")
from node2_twin_core.contract import FEATURE_ORDER
print("FEATURE_ORDER (%d):" % len(FEATURE_ORDER))
for i, k in enumerate(FEATURE_ORDER): print("  %2d %s" % (i, k))
p = pathlib.Path("models/classifier/feature_names.json")
print("\nfeature_names.json exists:", p.exists())
if p.exists(): print(p.read_text(encoding="utf-8")[:800])
