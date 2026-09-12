import pathlib
p = pathlib.Path("tab3_probe.py")
s = p.read_text(encoding="utf-8")
old = 'print("engine %s (%s, %.0f h, %s)"'
new = ('# One session per run. POST /sessions closes the current one and opens a\n'
       '# fresh row, so replay never mixes two sim runs (or a run with the\n'
       '# smoke-test frames that preceded it).\n'
       'sid = requests.post("http://127.0.0.1:8000/sessions",\n'
       '                    json={"note": "tab3 forced %s on %s"\n'
       '                          % (fs.label, eng.serial)}, timeout=10\n'
       '                    ).json()["session_id"]\n'
       'print("session %d" % sid)\n'
       + old)
if old not in s: raise SystemExit("NOT FOUND")
s = s.replace(old, new, 1)
old2 = 'print("\\n%d frames posted, %d flagged FAULT" % (n_frames, n_hit))'
new2 = (old2 + '\n'
        'summ = sess.get("http://127.0.0.1:8000/summary",\n'
        '                params={"session_id": sid}, timeout=10).json()\n'
        'print("session %d summary: %s" % (sid, summ))\n'
        'print("replay:  http://127.0.0.1:8000/frames?session_id=%d" % sid)\n'
        'print("report:  http://127.0.0.1:8000/report/%d.csv" % sid)')
if old2 not in s: raise SystemExit("NOT FOUND: tail")
s = s.replace(old2, new2, 1)
compile(s.lstrip("\ufeff"), str(p), "exec")
p.write_text(s, encoding="utf-8")
print("probe now opens its own session, syntax OK")
