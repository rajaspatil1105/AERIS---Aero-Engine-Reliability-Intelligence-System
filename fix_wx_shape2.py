import io, shutil, textwrap
P = r"node3_service\api.py"
src = io.open(P, encoding="utf-8-sig").read()
lines = src.split("\n")

a = b = None
for i, ln in enumerate(lines):
    if 'wx = {"source": "isa", "notes": ["no date given"]}' in ln:
        a = i
    if 'wx = {"source": "isa", "notes": ["weather unavailable' in ln:
        b = i
        break
if a is None or b is None or b < a:
    raise SystemExit("MISS  block not found (a=%s b=%s)" % (a, b))

pad = lines[a][:len(lines[a]) - len(lines[a].lstrip())]
print("block at lines %d-%d, indent %d" % (a + 1, b + 1, len(pad)))

body = '''wx = {"sources": ["isa"], "clamped": [], "date": None,
      "hour_utc": hour, "note": "no date given, ISA standard day"}
if date:
    try:
        wx = dict(mw.apply_to_plan(plan, plan.route, date, hour=hour))
    except Exception as exc:                  # network, parse, range
        wx = {"sources": ["isa"], "clamped": [], "date": date,
              "hour_utc": hour,
              "note": "weather unavailable (%s), fell back to ISA" % exc}
wx.setdefault("note", "")
wx["single_hour_caveat"] = (
    "Every point sampled at %02d:00 UTC. A 30 h sortie crosses a night; "
    "this one does not cool down." % hour)'''

shutil.copy2(P, P + ".bak_wxshape2")
new = textwrap.indent(body, pad).split("\n")
lines[a:b + 1] = new
io.open(P, "w", encoding="utf-8", newline="\n").write("\n".join(lines))
print("ok    weather shape normalised")
