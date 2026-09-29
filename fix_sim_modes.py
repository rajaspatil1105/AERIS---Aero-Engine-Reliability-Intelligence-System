import io, shutil
P = r"static\index.html"
shutil.copy2(P, P + ".bak_modes")
src = io.open(P, encoding="utf-8").read()
n = 0

def sub(old, new, label):
    global src, n
    if old not in src:
        print("MISS  %s" % label); return
    src = src.replace(old, new, 1); n += 1
    print("ok    %s" % label)

sub('''<section id="v-sim" class="view">
  <div class="rp-head">
    <div><div class="rp-title">MISSION SIMULATION</div>
      <div class="dim">set an operating point inside the trained envelope;
        the server solves MVEM and scores every frame</div></div>
  </div>
  <div class="rep-grid">''',
'''<section id="v-sim" class="view">
  <div id="sm-choose">
    <div class="rp-head"><div><div class="rp-title">MISSION SIMULATION</div>
      <div class="dim">choose a simulator</div></div></div>
    <div class="rep-grid">
      <div class="rep-card sm-box" data-mode="fault" style="cursor:pointer">
        <h3>1 &middot; FAULT SIMULATOR</h3>
        <div class="dim">You pick the fault, the severity and when it starts.
        The engine shows how it behaves under that specific failure.
        Use this to demonstrate a known signature.</div></div>
      <div class="rep-card sm-box" data-mode="condition" style="cursor:pointer">
        <h3>2 &middot; CONDITION SIMULATOR</h3>
        <div class="dim">No fault is injected. You set only the environment
        and the engine; wear and failures emerge from the physics if the
        conditions are hard enough. Hot, low and full throttle is what hurts.</div></div>
      <div class="rep-card sm-box" data-mode="mission" style="cursor:pointer">
        <h3>3 &middot; MISSION SIMULATOR</h3>
        <div class="dim">Full MALE surveillance sortie, engine-focused:
        takeoff, climb, transit, loiter, recovery. You give the two airfields,
        the area to cover and the engine; route, timings and weather are
        worked out. Faults arise on their own. NOT BUILT YET.</div></div>
    </div>
  </div>
  <div id="sm-workspace" style="display:none">
  <div class="rp-head">
    <div><div class="rp-title"><span id="sm-back" style="cursor:pointer">&larr;</span>
      <span id="sm-mode-title">MISSION SIMULATION</span></div>
      <div class="dim" id="sm-mode-sub">set an operating point inside the trained
        envelope; the server solves MVEM and scores every frame</div></div>
  </div>
  <div class="rep-grid">''', "chooser + workspace wrapper")

sub('    <div class="rep-card"><h3>FAULT INJECTION</h3>',
    '    <div class="rep-card" id="sm-card-fault"><h3>FAULT INJECTION</h3>',
    "fault card is addressable")

sub('    <div class="rep-card"><h3>RUN</h3>',
    '''    <div class="rep-card" id="sm-card-age" style="display:none">
      <h3>ENGINE CONDITION</h3>
      <div class="dim" id="sm-age-now">select an engine</div>
      <div class="dim" style="margin-top:6px">Wear carries over between runs.
      A single hard 30 h sortie costs roughly 3% of oil pump health, so a
      fault emerges over dozens of missions, not inside one flight.</div>
    </div>
    <div class="rep-card"><h3>RUN</h3>''', "engine condition card")

sub('''    watch them arrive, or REPLAY to scrub the finished run.</div>
  </div>
</section>''',
'''    watch them arrive, or REPLAY to scrub the finished run.</div>
  </div>
  </div>
</section>''', "close workspace")

io.open(P, "w", encoding="utf-8", newline="\n").write(src)
print("\n%d edits -> %s" % (n, P))
