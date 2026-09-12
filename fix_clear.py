import pathlib
p = pathlib.Path("shared/mission_engine.py")
lines = p.read_text(encoding="utf-8").splitlines(keepends=True)

i_act = next(i for i, l in enumerate(lines)
             if l.strip().startswith("active = t >= forced_at_s"))
i_end = next(i for i in range(i_act, len(lines))
             if "engine.fault_state()" in lines[i] and "recovery" in lines[i])
pad = lines[i_act][:len(lines[i_act]) - len(lines[i_act].lstrip())]

block = [
 pad + "active = t >= forced_at_s and (forced_clear_s is None\n",
 pad + "                                or t <= forced_clear_s)\n",
 pad + "# Do NOT infer forced state from fs.label: apply_degradation()\n",
 pad + "# rewrites it when stress fires, the label comparison then misses\n",
 pad + "# and the forced fault never clears (measured: injected at 300 s,\n",
 pad + "# still applied at 1770 s with forced_clear_s=900).\n",
 pad + "if active and not _forced_on:\n",
 pad + "    fs = replace(forced_fault)\n",
 pad + "    _forced_on = True\n",
 pad + "elif not active and _forced_on:\n",
 pad + "    fs = engine.fault_state()          # recovery: back to baseline\n",
 pad + "    _forced_on = False\n",
]
lines[i_act:i_end + 1] = block

i_fs = next(i for i, l in enumerate(lines) if l.strip() == "fs = engine.fault_state()")
ind = lines[i_fs][:len(lines[i_fs]) - len(lines[i_fs].lstrip())]
lines.insert(i_fs + 1, ind + "_forced_on = False\n")

s = "".join(lines)
compile(s.lstrip("\ufeff"), str(p), "exec")
p.write_text(s, encoding="utf-8")
print("forced-fault state now tracked explicitly, syntax OK")
