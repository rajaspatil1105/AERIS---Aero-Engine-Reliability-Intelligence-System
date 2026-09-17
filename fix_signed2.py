import ast, pathlib
p = pathlib.Path("node2_twin_core/residual_calc.py")
s = p.read_text(encoding="utf-8")

edits = [
 ('return max(self.residuals, key=lambda k: self.residuals[k])',
  'return max(self.residuals, key=lambda k: abs(self.residuals[k]))'),
 ('for c, v in sorted(res.residuals.items(), key=lambda kv: -kv[1]):',
  'for c, v in sorted(res.residuals.items(), key=lambda kv: -abs(kv[1])):'),
 ('if max(res.residuals.values()) > 1e-9:',
  'if max(abs(v) for v in res.residuals.values()) > 1e-9:'),
]
for old, new in edits:
    if s.count(old) != 1:
        raise SystemExit("anchor %d times: %r" % (s.count(old), old[:55]))
    s = s.replace(old, new)

ast.parse(s)
p.write_text(s, encoding="utf-8")
print("magnitude consumers fixed, syntax OK")
