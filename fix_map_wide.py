import io, shutil
C = r"static\aeris2.css"
css = io.open(C, encoding="utf-8-sig").read()
if "#sm-card-map" in css:
    print("ok    css rule already present")
else:
    shutil.copy2(C, C + ".bak_mapwide")
    css += ("\n/* the mission map owns the whole workspace row: the plan "
            "controls\n   do not fit in a 230px column */\n"
            "#sm-card-map{grid-column:1/-1}\n"
            "#sm-map{height:420px}\n")
    io.open(C, "w", encoding="utf-8", newline="\n").write(css)
    print("ok    map card spans the grid, 420px tall")

H = r"static\index.html"
src = io.open(H, encoding="utf-8-sig").read().replace("\r\n", "\n")
shutil.copy2(H, H + ".bak_row3")
src = src.replace("flex-wrap:wrap", "flex-wrap:nowrap", 1)
src = src.replace("gap:6px;margin:6px 0;font-size:11px",
                  "gap:12px;margin:6px 0;white-space:nowrap", 1)
src = src.replace('<span class="dim">auto duration</span>',
                  '<span class="dim">duration: auto (Heron Mk II)</span>', 1)
src = src.replace('style="height:340px;margin:6px 0;background:#111"',
                  'style="margin:6px 0;background:#111"', 1)
src = src.replace("sim.js?v=12", "sim.js?v=13", 1)
io.open(H, "w", encoding="utf-8", newline="\n").write(src)
print("ok    single control line restored, v=13")
