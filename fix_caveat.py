import io, shutil
P = r"node3_service\api.py"
src = io.open(P, encoding="utf-8-sig").read()
old = "A 30 h sortie crosses a night; "
new = "A sortie this long crosses a night; "
if new in src:
    print("ok    already patched")
elif old in src:
    shutil.copy2(P, P + ".bak_cav")
    io.open(P, "w", encoding="utf-8", newline="\n").write(src.replace(old, new, 1))
    print("ok    caveat no longer hardcodes 30 h")
else:
    print("MISS  string still not matching")
