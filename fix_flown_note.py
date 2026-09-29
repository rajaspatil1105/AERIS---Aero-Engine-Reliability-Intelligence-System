import io, shutil
P = r"shared\mission_engine.py"
src = io.open(P, encoding="utf-8-sig").read().replace("\r\n", "\n")
old = '            note="%.0f h flown since factory" % a.get("hours", 0.0)))'
new = ('            note=_flown_note(e.note, a.get("hours", 0.0))))')
helper = '''def _flown_note(factory_note: str, flown_h: float) -> str:
    """Keep the factory description and say what has been flown on top.

    %.0f hid anything under half an hour, so a five minute sortie
    reported "0 h flown since factory" on an engine that had moved.
    """
    if flown_h <= 0.0:
        return factory_note
    n = ("%.2f h" % flown_h) if flown_h < 1.0 else ("%.0f h" % flown_h)
    return "%s, +%s flown" % (factory_note, n)


'''
if "_flown_note" in src:
    print("ok    already patched")
elif old in src:
    shutil.copy2(P, P + ".bak_flownote")
    src = src.replace(old, new, 1)
    i = src.rfind("def ", 0, src.find("h flown since factory")
                  if "h flown since factory" in src else src.find("_flown_note"))
    i = src.rfind("\ndef ", 0, i) + 1
    src = src[:i] + helper + src[i:]
    io.open(P, "w", encoding="utf-8", newline="\n").write(src)
    print("ok    note keeps the factory text and shows small flights")
else:
    print("MISS  note line")
