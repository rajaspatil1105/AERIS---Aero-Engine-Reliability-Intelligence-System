# add_speed_picker.py -- user-selectable sim speed for FLY IT
import io, re, shutil

def read(p):
    return io.open(p, encoding="utf-8-sig").read().replace("\r\n", "\n")

def write(p, s):
    io.open(p, "w", encoding="utf-8", newline="\n").write(s)

# 1. index.html : dropdown just before the FLY IT button
H = r"static\index.html"
h = read(H)
sel = ('<select id="sm-map-speed" style="width:104px">'
       '<option value="0" selected>flat out</option>'
       '<option value="1000">1000x</option>'
       '<option value="500">500x</option>'
       '<option value="100">100x</option>'
       '<option value="10">10x</option>'
       '<option value="5">5x</option>'
       '<option value="2">2x</option>'
       '<option value="1">real time</option>'
       '</select>\n')
if "sm-map-speed" in h:
    print("skip  speed picker already present")
else:
    m = re.search(r'<button[^>]*id=["\']sm-map-fly["\'][^>]*>', h)
    if not m:
        print("MISS  index.html -- FLY IT button tag not found")
    else:
        shutil.copy2(H, H + ".bak_speed")
        h = h.replace(m.group(0), sel + m.group(0), 1)
        h = re.sub(r'sim_fly\.js\?v=\d+', "sim_fly.js?v=2", h)
        write(H, h)
        print("ok    speed picker added, sim_fly.js v=2")

# 2. sim_fly.js : send the chosen multiplier
F = r"static\sim_fly.js"
f = read(F)
if "smSpeed" in f:
    print("skip  sim_fly.js already reads the picker")
else:
    m = re.search(r'speed\s*:\s*0', f)
    if not m:
        print("MISS  sim_fly.js -- no 'speed: 0' in the POST body")
        print("      grep it with: Select-String -Path static\\sim_fly.js -Pattern speed")
    else:
        shutil.copy2(F, F + ".bak_speed")
        f = f.replace(m.group(0), "speed: smSpeed()", 1)
        f = ("function smSpeed() {\n"
             "  var s = document.getElementById('sm-map-speed');\n"
             "  return s ? Number(s.value) : 0;\n"
             "}\n") + f
        write(F, f)
        print("ok    sim_fly.js sends the chosen speed")
