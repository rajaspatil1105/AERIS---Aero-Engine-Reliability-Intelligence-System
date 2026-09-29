import io, shutil
H = r"static\index.html"
src = io.open(H, encoding="utf-8-sig").read().replace("\r\n", "\n")
shutil.copy2(H, H + ".bak_row2")
src = src.replace("flex-wrap:nowrap", "flex-wrap:wrap", 1)
src = src.replace("gap:10px;margin:6px 0;white-space:nowrap",
                  "gap:6px;margin:6px 0;font-size:11px", 1)
src = src.replace('<span class="dim">duration: auto (Heron Mk II)</span>',
                  '<span class="dim">auto duration</span>', 1)
src = src.replace('style="width:132px"', 'style="width:118px"', 1)
src = src.replace("sim.js?v=11", "sim.js?v=12", 1)
io.open(H, "w", encoding="utf-8", newline="\n").write(src)
print("ok    controls wrap instead of clipping, v=12")
