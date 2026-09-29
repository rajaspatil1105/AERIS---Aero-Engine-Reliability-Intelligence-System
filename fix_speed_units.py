import io, shutil
P = r"shared\mission_profile.py"
src = io.open(P, encoding="utf-8-sig").read().replace("\r\n", "\n")
n = 0
if "GROUND_SPEED_KMH = 120" in src:
    shutil.copy2(P, P + ".bak_kt")
    src = src.replace("GROUND_SPEED_KMH = 120",
                      "GROUND_SPEED_KMH = 222.24   # 120 kt, matching AIRFRAMES", 1)
    n += 1; print("ok    profile speed now 120 kt, not 120 km/h")
else:
    print("MISS  paste the GROUND_SPEED_KMH line")
io.open(P, "w", encoding="utf-8", newline="\n").write(src)

A = r"node3_service\api.py"
a = io.open(A, encoding="utf-8-sig").read().replace("\r\n", "\n")
if '"A 30 h sortie crosses a night' in a:
    shutil.copy2(A, A + ".bak_cav")
    a = a.replace('"Every point sampled at %02d:00 UTC. A 30 h sortie crosses a night; "\n',
                  '"Every point sampled at %02d:00 UTC. A sortie this long crosses "\n', 1)
    a = a.replace('"this one does not cool down." % hour)',
                  '"a night; this one does not cool down." % hour)', 1)
    io.open(A, "w", encoding="utf-8", newline="\n").write(a)
    n += 1; print("ok    caveat no longer hardcodes 30 h")
else:
    print("MISS  caveat string")

J = r"static\sim.js"
j = io.open(J, encoding="utf-8-sig").read().replace("\r\n", "\n")
if 'h += "<div>clamped: "' in j:
    shutil.copy2(J, J + ".bak_clamp")
    j = j.replace('h += "<div>clamped: " + plan.clamped.join("; ") + "</div>";',
                  'h += "<div>planner notes: " + plan.clamped.join("; ") + "</div>";', 1)
    io.open(J, "w", encoding="utf-8", newline="\n").write(j)
    n += 1; print("ok    relabelled as planner notes")
else:
    print("MISS  clamped line in sim.js")

H = r"static\index.html"
h = io.open(H, encoding="utf-8-sig").read().replace("\r\n", "\n")
io.open(H, "w", encoding="utf-8", newline="\n").write(h.replace("sim.js?v=15", "sim.js?v=16", 1))
print("%d edits, v=16" % n)
