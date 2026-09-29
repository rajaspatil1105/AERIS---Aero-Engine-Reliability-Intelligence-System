import io, shutil

h = r"static\index.html"
src = io.open(h, encoding="utf-8").read()
anchor = '''    <section class="panel placeholder tile" style="grid-area:vib">'''
new = '''    <section class="panel placeholder" style="grid-area:advisory">
      <h2>MAINTENANCE ADVISORY <i>not implemented</i></h2>
      <div class="body"><b>--</b><span class="tag">PLANNED</span></div>
    </section>
    <section class="panel placeholder" style="grid-area:effic">
      <h2>EFFICIENCY TREND <i>not implemented</i></h2>
      <div class="body"><b>--</b><span class="tag">PLANNED</span></div>
    </section>
''' + anchor
assert src.count(anchor) == 1
shutil.copyfile(h, h + ".bak_ph")
io.open(h, "w", encoding="utf-8").write(src.replace(anchor, new))
print("ok    two placeholder panels added")

c = r"static\aeris.css"
css = io.open(c, encoding="utf-8").read()
old = '''    "cht     egt     map     vib     elec    timing";}'''
newc = '''    "cht     egt     map     vib     elec    timing"
    "advisory advisory advisory effic effic effic";}'''
assert css.count(old) == 1
shutil.copyfile(c, c + ".bak_ph")
io.open(c, "w", encoding="utf-8").write(css.replace(old, newc))
print("ok    grid rows extended")
