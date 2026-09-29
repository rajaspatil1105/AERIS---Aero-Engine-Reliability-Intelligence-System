import io, shutil
P = r"static\sim.js"
src = io.open(P, encoding="utf-8-sig").read().replace("\r\n", "\n")
old = '      o.textContent = e.serial + " -- " + e.hours + " h, " + e.note;'
new = ('      o.textContent = e.serial + " -- " + Number(e.hours).toFixed(0) +\n'
       '        " h" + (e.note ? ", " + e.note : "");')
if new.split("\n")[0] in src:
    print("ok    already patched")
elif old in src:
    shutil.copy2(P, P + ".bak_label")
    src = src.replace(old, new, 1)
    io.open(P, "w", encoding="utf-8", newline="\n").write(src)
    print("ok    engine label rounded, empty note dropped")
else:
    print("MISS  option label line")

H = r"static\index.html"
h = io.open(H, encoding="utf-8-sig").read().replace("\r\n", "\n")
io.open(H, "w", encoding="utf-8", newline="\n").write(
    h.replace("sim.js?v=14", "sim.js?v=15", 1))
print("ok    v=15")
