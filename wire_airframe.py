import io, shutil
P = r"node3_service\api.py"
src = io.open(P, encoding="utf-8-sig").read().replace("\r\n", "\n")
lines = src.split("\n"); n = 0

def find(pred, start=0):
    for i in range(start, len(lines)):
        if pred(lines[i]):
            return i
    return -1

i = find(lambda l: l.strip() == "target_h: float = 30.0,")
if i >= 0:
    pad = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
    lines[i:i + 1] = [pad + "target_h: float = 0.0,   # 0 -> from the airframe",
                      pad + 'airframe: str = "heron_mk2",']
    n += 1; print("ok    target_h now optional, airframe added")
else:
    print("MISS  target_h signature line")

b = find(lambda l: "plan = mp.build(" in l)
if b >= 0:
    t = -1
    for k in range(b, max(0, b - 8), -1):
        if lines[k].strip() == "try:":
            t = k; break
    if t >= 0:
        pad = lines[b][:len(lines[b]) - len(lines[b].lstrip())]
        lines[t + 1:t + 1] = [
            pad + "t_h, endur = mp.auto_target_h(",
            pad + "    (tk_lat, tk_lon), (ld_lat, ld_lon),",
            pad + "    (ac_lat, ac_lon), airframe, target_h)"]
        n += 1; print("ok    duration derived before build")
    else:
        print("MISS  try: before build")
else:
    print("MISS  mp.build call")

j = find(lambda l: l.strip() == "target_h=target_h,")
if j >= 0:
    lines[j] = lines[j].replace("target_h=target_h,", "target_h=t_h,")
    n += 1; print("ok    build uses derived hours")
else:
    print("MISS  target_h=target_h argument")

k = find(lambda l: l.strip() == '"date": date,')
if k >= 0:
    pad = lines[k][:len(lines[k]) - len(lines[k].lstrip())]
    lines[k + 1:k + 1] = [pad + '"endurance": endur,']
    n += 1; print("ok    endurance in the response")
else:
    print("MISS  date key in the return dict")

if n:
    shutil.copy2(P, P + ".bak_airframe")
    io.open(P, "w", encoding="utf-8", newline="\n").write("\n".join(lines))
print("%d edits" % n)
