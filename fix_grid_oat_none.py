import io, shutil
P = r"shared\mission_engine.py"
shutil.copy2(P, P + ".bak_oatnone")
src = io.open(P, encoding="utf-8").read()

old = "            and abs(b[2] - a[2]) < 1e-6):"
new = ("            and (a[2] is b[2] if (a[2] is None or b[2] is None)\n"
       "                 else abs(b[2] - a[2]) < 1e-6)):")

if old in src:
    io.open(P, "w", encoding="utf-8", newline="\n").write(src.replace(old, new, 1))
    print("ok -- grid tolerates oat_c=None (ISA fallback)")
else:
    print("MISS -- paste lines 504-514")
