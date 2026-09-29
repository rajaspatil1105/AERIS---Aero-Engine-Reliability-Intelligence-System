import io, shutil
edits = [
 (r"static\index.html",
  "RUL R&sup2; &minus;0.103, ordering only",
  "RUL R&sup2; +0.842 / MAE 93 h on held-out engines, one dominant channel"),
 (r"static\index.html",
  "<h2>RUL <i>ordering only</i></h2>",
  "<h2>RUL <i>hours</i></h2>"),
 (r"static\aeris.js",
  "&middot; ordering only, not a time",
  "&middot; hours, modelled not certified"),
 (r"static\aeris.js",
  "rul_raw &middot; units unknown &middot; ordering only, ",
  "rul_raw &middot; hours &middot; 0.94 of the signal is oil pressure, "),
]
done = set()
for path, old, new in edits:
    if path not in done:
        shutil.copy2(path, path + ".bak_rultext"); done.add(path)
    src = io.open(path, encoding="utf-8").read()
    if old in src:
        io.open(path, "w", encoding="utf-8", newline="\n").write(src.replace(old, new, 1))
        print("ok    %s :: %s" % (path, old[:45]))
    else:
        print("MISS  %s :: %s" % (path, old[:45]))
