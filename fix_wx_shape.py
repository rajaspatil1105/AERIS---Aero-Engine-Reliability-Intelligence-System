import io, re, shutil
P = r"node3_service\api.py"
shutil.copy2(P, P + ".bak_wxshape")
src = io.open(P, encoding="utf-8-sig").read()
n = 0

old = '''    wx = {"source": "isa", "notes": ["no date given"]}
    if date:
        try:
            wx = mw.apply_to_plan(plan, plan.route, date, hour=hour)
        except Exception as exc:                      # network, parse, range
            wx = {"source": "isa", "notes": ["weather unavailable: %s" % exc]}'''
new = '''    wx = {"sources": ["isa"], "clamped": [], "date": None,
          "hour_utc": hour, "note": "no date given, ISA standard day"}
    if date:
        try:
            wx = dict(mw.apply_to_plan(plan, plan.route, date, hour=hour))
        except Exception as exc:                      # network, parse, range
            wx = {"sources": ["isa"], "clamped": [], "date": date,
                  "hour_utc": hour,
                  "note": "weather unavailable (%s), fell back to ISA" % exc}
    wx.setdefault("note", "")
    wx["single_hour_caveat"] = (
        "Every point sampled at %02d:00 UTC. A 30 h sortie crosses a night; "
        "this one does not cool down." % hour)'''
if old in src.replace("\r\n", "\n"):
    src = src.replace("\r\n", "\n").replace(old, new, 1); n += 1
    print("ok    weather shape normalised")
else:
    print("MISS  wx block")

io.open(P, "w", encoding="utf-8", newline="\n").write(src)
print("%d edits" % n)
