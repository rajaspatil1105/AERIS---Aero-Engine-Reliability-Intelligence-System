import io, shutil
H = r"static\index.html"
src = io.open(H, encoding="utf-8-sig").read().replace("\r\n", "\n")
shutil.copy2(H, H + ".bak_cssv")
src = src.replace('aeris2.css?v=9', 'aeris2.css?v=10', 1)
src = src.replace('<div id="sm-map" style="height:420px;margin:6px 0;'
                  'background:#111">',
                  '<div id="sm-map" style="min-height:420px;margin:6px 0;'
                  'background:#111">', 1)
io.open(H, "w", encoding="utf-8", newline="\n").write(src)
print("ok    aeris2.css bumped to v=10, map min-height inline")
