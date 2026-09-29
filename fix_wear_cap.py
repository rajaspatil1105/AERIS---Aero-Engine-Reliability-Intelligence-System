import io, shutil
P = r"shared\mission_engine.py"
shutil.copy2(P, P + ".bak_cap")
src = io.open(P, encoding="utf-8").read()

old = '''    a["coolant"] += 0.30 * min(1.0, st.thermal)
    a["oil"] += 0.25 * min(1.0, st.oil)
    a["bearing"] += 0.35 * min(1.0, st.power)'''
new = '''    # No per-call clamp on the counters. A real mission lands well under
    # 1.0 anyway, and clamping here silently threw away scaled aging:
    # a 2000 h request added exactly the same wear as a 300 h one.
    # _fleet_now() still floors health at 0.05 and caps bearing at 1.0.
    a["coolant"] += 0.30 * max(0.0, st.thermal)
    a["oil"] += 0.25 * max(0.0, st.oil)
    a["bearing"] += 0.35 * max(0.0, st.power)'''

if old in src:
    io.open(P, "w", encoding="utf-8", newline="\n").write(src.replace(old, new, 1))
    print("ok -- per-call clamp removed")
else:
    print("MISS -- paste record_wear again")
