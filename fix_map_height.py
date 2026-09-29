import io, shutil
H = r"static\index.html"
src = io.open(H, encoding="utf-8-sig").read().replace("\r\n", "\n")
shutil.copy2(H, H + ".bak_h")
src = src.replace('<div id="sm-map" style="margin:6px 0;background:#111">',
                  '<div id="sm-map" style="height:420px;margin:6px 0;'
                  'background:#111">', 1)
src = src.replace("sim.js?v=13", "sim.js?v=14", 1)
io.open(H, "w", encoding="utf-8", newline="\n").write(src)
print("ok    inline height back, v=14")
