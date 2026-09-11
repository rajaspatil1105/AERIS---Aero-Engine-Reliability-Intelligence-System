import re, pathlib
p = pathlib.Path("build_manifest.py")
src = p.read_text(encoding="utf-8")
pat = r'"note": "Both artifacts are HistGradientBoostingClassifier.*?fallback\.",'
n = len(re.findall(pat, src, re.S))
print("matches", n)
if n != 1: raise SystemExit("NOT FOUND - aborted, nothing written")
new = ('"note": "Both artifacts are HistGradientBoostingClassifier as of 2026-09-11. '
       'shap.TreeExplainer verified working on the multiclass artifact under shap '
       '0.51.0 (exact, rows x 14 features x 5 classes); Permutation retained only '
       'as a fallback.",')
src = re.sub(pat, lambda _: new, src, count=1, flags=re.S)
compile(src.lstrip("\ufeff"), str(p), "exec")
p.write_text(src, encoding="utf-8")
print("repaired, syntax OK")
