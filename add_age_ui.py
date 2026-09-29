import io, shutil
P = r"static\index.html"
shutil.copy2(P, P + ".bak_agebtn")
src = io.open(P, encoding="utf-8").read()

old = '''      <div class="dim" id="sm-age-now">select an engine</div>'''
new = '''      <div class="dim" id="sm-age-now">select an engine</div>
      <div class="kv" style="margin-top:6px"><span>put hours on it</span>
        <input id="sm-agehrs" type="number" value="300" min="1" max="2000" step="50"></div>
      <div class="rp-actions">
        <button id="sm-age-go" class="btn">AGE</button>
        <button id="sm-age-reset" class="btn">FACTORY</button></div>
      <div class="dim" id="sm-age-log" style="margin-top:6px"></div>'''

if old in src:
    io.open(P, "w", encoding="utf-8", newline="\n").write(src.replace(old, new, 1))
    print("ok -- age controls added")
else:
    print("MISS -- paste the sm-card-age block")
