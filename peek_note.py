import io, re, shutil
P = r"shared\mission_engine.py"
src = io.open(P, encoding="utf-8-sig").read().replace("\r\n", "\n")
m = re.search(r'^([ \t]*).*%\.0f h flown since factory.*$', src, re.M)
if not m:
    print("MISS  paste the note line from _fleet_now")
else:
    print("found: [" + m.group(0).strip() + "]")
    shutil.copy2(P, P + ".bak_note3")
