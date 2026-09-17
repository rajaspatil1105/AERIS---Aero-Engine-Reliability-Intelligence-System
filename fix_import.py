import ast, pathlib
p = pathlib.Path("shared/stress_sim.py")
s = p.read_text(encoding="utf-8-sig")
bad = "from __future__ import pathlib\nimport annotations"
if bad in s:
    s = s.replace(bad, "from __future__ import annotations")
else:
    s = s.replace("from __future__ import pathlib", "from __future__ import annotations", 1)
if "\nimport pathlib" not in s:
    s = s.replace("from __future__ import annotations",
                  "from __future__ import annotations\n\nimport pathlib", 1)
ast.parse(s); p.write_text(s, encoding="utf-8")
print("import repaired, syntax OK")
