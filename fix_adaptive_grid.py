import pathlib, shutil

P = pathlib.Path("shared/mission_engine.py")
src = P.read_text(encoding="utf-8")
shutil.copy(P, pathlib.Path("shared/mission_engine.py.bak_grid"))

OLD_HEAD = "    for i in range(n):\n        t = i * dt_s\n"
NEW_HEAD = (
'    fine_dt = dt_s\n'
'    coarse_dt = 20.0 if t_end > 3600.0 else fine_dt\n'
'    _marks = [m for m in (forced_at_s, forced_clear_s) if m is not None]\n'
'\n'
'    def _grid():\n'
'        """(t, step) pairs. 1 s where it matters, coarse in settled cruise."""\n'
'        tt = 0.0\n'
'        while tt <= t_end + 1e-9:\n'
'            step = fine_dt\n'
'            if coarse_dt > fine_dt and tt > event_until:\n'
'                a = _interp(pts, tt)\n'
'                b = _interp(pts, min(t_end, tt + coarse_dt))\n'
'                if (abs(b[0] - a[0]) < 1e-9 and abs(b[1] - a[1]) < 1e-6\n'
'                        and abs(b[2] - a[2]) < 1e-6):\n'
'                    step = coarse_dt\n'
'            for m in _marks:\n'
'                if tt < m < tt + step:\n'
'                    step = m - tt\n'
'            yield tt, step\n'
'            tt += step\n'
'\n'
'    for i, (t, dt_s) in enumerate(_grid()):\n')

OLD_GUARD = "        if t - last_emit < due and 0 < i < n - 1:"
NEW_GUARD = "        if t - last_emit < due and i > 0 and t < t_end - 1e-9:"

for old, new, tag in ((OLD_HEAD, NEW_HEAD, "grid"), (OLD_GUARD, NEW_GUARD, "guard")):
    if old not in src:
        raise SystemExit("anchor not found: " + tag)
    src = src.replace(old, new, 1)

P.write_text(src, encoding="utf-8")
print("patched -- adaptive time grid")
