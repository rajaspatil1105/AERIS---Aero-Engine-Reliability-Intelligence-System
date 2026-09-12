import pathlib
p = pathlib.Path("build_manifest.py")
s = p.read_text(encoding="utf-8")
anchor = "ENVELOPE = {"
i = s.index(anchor); j = i + len(anchor); depth = 1
while depth:
    if s[j] == "{": depth += 1
    elif s[j] == "}": depth -= 1
    j += 1
print("replacing %d chars of literal:" % (j - i))
print("  " + s[i:j].replace("\n", " ")[:160])

new = (
 '# FOURTH place this envelope was written down by hand (after mission_engine.py,\n'
 '# the physics_deck baseline_stats, and the feature contract). Every copy drifted\n'
 '# and this one still published 3000-5800 rpm / 56.5%% throttle / 21709 ft after\n'
 '# the MVEM refit moved it to 2592-5808 / 20%% / 22800 ft. One owner now:\n'
 '# models/configs/reconstruction_config.json, written by the baseline refit from\n'
 '# the rows actually fit on. Read it, never restate it.\n'
 'def _load_envelope() -> dict:\n'
 '    import json\n'
 '    cfg = json.loads((pathlib.Path(__file__).resolve().parent / "models"\n'
 '           / "configs" / "reconstruction_config.json").read_text(encoding="utf-8"))\n'
 '    rng = next(iter(cfg["baseline_stats"].values()))["operating_range"]\n'
 '    return {k: [round(float(v[0]), 2), round(float(v[1]), 2)]\n'
 '            for k, v in rng.items()}\n'
 '\n'
 'ENVELOPE = _load_envelope()')
s = s[:i] + new + s[j:]
if "import pathlib" not in s.split("def _load_envelope")[0]:
    s = s.replace("import sklearn", "import pathlib\nimport sklearn", 1)
compile(s.lstrip("\ufeff"), str(p), "exec")
p.write_text(s, encoding="utf-8")
print("\nbuild_manifest.py envelope now read from config, syntax OK")
