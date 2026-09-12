import pathlib
p = pathlib.Path("train_classifiers_mvem.py")
s = p.read_text(encoding="utf-8")
old = "FEATURES = ENV + RAW + DELTA                      # 14, order must not change"
new = ("# Do NOT restate the order here. node2_twin_core.residual_calc owns the\n"
       "# 14-column contract and feature_names.json mirrors it; a local list\n"
       "# silently disagreed once and the gate scored permuted columns (0.513 on\n"
       "# an exact-ground-truth frame). Import it so that cannot recur.\n"
       "import sys as _sys; _sys.path.insert(0, str(ROOT))\n"
       "from node2_twin_core.residual_calc import FEATURE_ORDER as FEATURES\n"
       "FEATURES = list(FEATURES)\n"
       "assert sorted(FEATURES) == sorted(ENV + RAW + DELTA), \\\n"
       "    'contract columns differ from the 14 this script can build'")
if old not in s: raise SystemExit("NOT FOUND")
s = s.replace(old, new, 1)
compile(s.lstrip("\ufeff"), str(p), "exec")
p.write_text(s, encoding="utf-8")
print("retrain now imports the contract order, syntax OK")
