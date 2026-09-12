import pathlib
p = pathlib.Path("node2_twin_core/twin_core.py")
s = p.read_text(encoding="utf-8")
old = "class TwinCore:"
new = ('class TwinCoreError(RuntimeError):\n'
       '    """Twin core refused to start. Was raised on line 174 without ever\n'
       '    being defined, so a real ManifestError surfaced as a NameError and\n'
       '    the actual reason was lost."""\n\n\n'
       'class TwinCore:')
if "class TwinCoreError" in s: raise SystemExit("already defined")
if old not in s: raise SystemExit("NOT FOUND")
s = s.replace(old, new, 1)
compile(s.lstrip("\ufeff"), str(p), "exec")
p.write_text(s, encoding="utf-8")
print("TwinCoreError defined, syntax OK")
