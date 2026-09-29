# fix_fly_wire.py -- wire sim_fly.js in
import io, re, shutil

def read(p):
    return io.open(p, encoding="utf-8-sig").read().replace("\r\n", "\n")

def write(p, s):
    io.open(p, "w", encoding="utf-8", newline="\n").write(s)

# 1. sim.js : publish the plan so sim_fly.js can see it
P = r"static\sim.js"
s = read(P)
old = "plan = o.j; redraw();"
new = "plan = o.j; window.smLastPlan = plan; redraw();"
if new in s:
    print("skip  sim.js already publishes the plan")
elif old in s:
    shutil.copy2(P, P + ".bak_fly")
    write(P, s.replace(old, new, 1))
    print("ok    sim.js publishes window.smLastPlan")
else:
    print("MISS  sim.js -- anchor line not found")

# 2. sim_fly.js : grey the button until a plan exists
F = r"static\sim_fly.js"
f = read(F)
if "SM_FLY_ENABLER" in f:
    print("skip  enabler already there")
else:
    shutil.copy2(F, F + ".bak_fly")
    f += ("\n// SM_FLY_ENABLER\n"
          "setInterval(function () {\n"
          "  var b = document.getElementById('sm-map-fly');\n"
          "  if (b) b.disabled = !window.smLastPlan;\n"
          "}, 500);\n")
    write(F, f)
    print("ok    enabler appended to sim_fly.js")

# 3. index.html : load sim_fly.js first, bump the buster
H = r"static\index.html"
h = read(H)
m = re.search(r'<script src=["\']sim\.js\?v=\d+["\']></script>', h)
if not m:
    print("MISS  index.html -- sim.js tag not found")
else:
    shutil.copy2(H, H + ".bak_fly")
    if "sim_fly.js" not in h:
        h = h.replace(m.group(0),
                      '<script src="sim_fly.js?v=1"></script>\n' + m.group(0), 1)
    h = re.sub(r'sim\.js\?v=\d+', "sim.js?v=22", h)
    write(H, h)
    print("ok    sim_fly.js before sim.js, v=22")
