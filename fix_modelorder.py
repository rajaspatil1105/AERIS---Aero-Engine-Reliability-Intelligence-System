import pathlib, re
p = pathlib.Path("node3_service/api.py")
s = p.read_text(encoding="utf-8")

m = re.search(r"class SimRunIn\(BaseModel\):.*?(?=\nclass SetpointIn)", s, re.S)
n = re.search(r"class SetpointIn\(BaseModel\):.*?(?=\nclass SessionIn)", s, re.S)
if not (m and n): raise SystemExit("could not locate both models")
simrun, setpoint = m.group(0), n.group(0)
s = s[:m.start()] + s[n.end():]                      # cut both out
s = s.replace("class SessionIn(BaseModel):",
              setpoint.rstrip() + "\n\n\n" + simrun.rstrip()
              + "\n\n\nclass SessionIn(BaseModel):", 1)
compile(s.lstrip("\ufeff"), str(p), "exec")
p.write_text(s, encoding="utf-8")
print("SetpointIn now defined before SimRunIn")
i = s.index("class SetpointIn"); j = s.index("class SessionIn")
print(s[i:j].rstrip()[:400])
