import json, pathlib
p = pathlib.Path("models/configs/reconstruction_config.json")
d = json.loads(p.read_text(encoding="utf-8"))
print("top-level:", sorted(d.keys()))
bs = d.get("baseline_stats", {})
print("baseline_stats channels:", sorted(bs.keys()))
for k, v in bs.items():
    print(" ", k, "->", sorted(v.keys()) if isinstance(v, dict) else type(v).__name__)
print("\nprovenance:", d.get("baseline_provenance", "(none)"))
print("\nBASELINE_INPUT_ORDER is what operating_range must cover:")
import sys; sys.path.insert(0, ".")
from node2_twin_core.physics_deck import BASELINE_INPUT_ORDER
print(" ", list(BASELINE_INPUT_ORDER))
