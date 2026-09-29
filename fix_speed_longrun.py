import pathlib, shutil
J = pathlib.Path("static/sim.js")
shutil.copy(J, pathlib.Path("static/sim.js.bak_speed"))
j = J.read_text(encoding="utf-8")

OLD = "    speed: smVal(\"sm-rate\", 1)\n"
NEW = ("    // Long missions ignore playback pacing. The server sleeps\n"
       "    // min(sim_dt,60)/speed per frame, so 1x over 30 h with a 180 s\n"
       "    // emit rate would sleep 60 s x 600 frames = 10 hours.\n"
       "    speed: (dur > 3600 ? 0 : smVal(\"sm-rate\", 1))\n")
if OLD not in j:
    raise SystemExit("speed anchor not found")
j = j.replace(OLD, NEW, 1)

OLD2 = '  smLog("running server-side, session " + SM.ses'
NEW2 = ('  if (dur > 3600)\n'
        '    smLog("mission is " + (dur/3600).toFixed(1) + " h -- playback "\n'
        '          + "pacing disabled, running as fast as possible");\n'
        '  smLog("running server-side, session " + SM.ses')
if OLD2 in j:
    j = j.replace(OLD2, NEW2, 1)

J.write_text(j, encoding="utf-8")
print("patched -- long missions run unthrottled")
