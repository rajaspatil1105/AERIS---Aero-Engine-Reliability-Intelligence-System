import hashlib, io, json, pathlib, shutil, datetime

MAN = pathlib.Path("models/model_manifest.json")
REL = "rul/rul_regressor.pkl"
art = pathlib.Path("models") / REL

shutil.copy2(MAN, str(MAN) + ".bak_rul")
man = json.loads(MAN.read_text(encoding="utf-8"))

arts = man["artifacts"] if isinstance(man, dict) and "artifacts" in man else man
hits = [a for a in arts if a.get("relpath") == REL]
if len(hits) != 1:
    print("expected 1 entry for %s, found %d -- stopping" % (REL, len(hits)))
    print(json.dumps(man, indent=1)[:1500])
    raise SystemExit(1)

e = hits[0]
h = hashlib.sha256()
h.update(art.read_bytes())
new_sha, new_size = h.hexdigest(), art.stat().st_size

print("entry keys: %s" % ", ".join(e.keys()))
print("  size   %d -> %d" % (e["size_bytes"], new_size))
print("  sha256 %s...\n      -> %s..." % (e["sha256"][:16], new_sha[:16]))

e["sha256"], e["size_bytes"] = new_sha, new_size
for k in ("trained_utc", "built_utc", "notes", "note", "description"):
    if k in e:
        print("  NOTE: entry also carries %r = %r -- review by hand" % (k, e[k]))

MAN.write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
print("\nupdated 1 of %d artifact entries; all others untouched" % len(arts))
