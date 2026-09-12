import pathlib
p = pathlib.Path("shared/mission_engine.py")
s = p.read_text(encoding="utf-8")
old = ("ENV_ALT_MAX_FT = 21709.34086551268\n"
       "ENV_THR_MIN_PCT = 56.5\n"
       "ENV_RPM_MIN = 3000.0\n"
       "ENV_RPM_MAX = 5800.0")
new = (
 "# The trained envelope is owned by models/configs/reconstruction_config.json\n"
 "# (written by the baseline refit from the data actually fit on). Do NOT\n"
 "# restate it here: a local copy of the 14-column feature contract disagreed\n"
 "# with residual_calc once and the gate scored permuted columns for a whole\n"
 "# retrain cycle. Read it, fall back only if the config is unreadable.\n"
 "def _load_envelope() -> dict:\n"
 "    import json\n"
 "    try:\n"
 "        cfg = json.loads((pathlib.Path(__file__).resolve().parents[1]\n"
 "               / 'models' / 'configs' / 'reconstruction_config.json')\n"
 "               .read_text(encoding='utf-8'))\n"
 "        rng = next(iter(cfg['baseline_stats'].values()))['operating_range']\n"
 "        return {k: (float(v[0]), float(v[1])) for k, v in rng.items()}\n"
 "    except Exception as exc:\n"
 "        print('[mission_engine] envelope config unreadable (%s), '\n"
 "              'using fallback' % exc)\n"
 "        return {'altitude_ft': (0.0, 22799.88),\n"
 "                'throttle_pct': (20.0, 100.0),\n"
 "                'rpm': (2592.26, 5807.61),\n"
 "                'ambient_temperature_C': (-39.5, 39.86)}\n"
 "\n"
 "ENVELOPE = _load_envelope()\n"
 "ENV_ALT_MIN_FT, ENV_ALT_MAX_FT = ENVELOPE['altitude_ft']\n"
 "ENV_THR_MIN_PCT, ENV_THR_MAX_PCT = ENVELOPE['throttle_pct']\n"
 "ENV_RPM_MIN, ENV_RPM_MAX = ENVELOPE['rpm']\n"
 "ENV_OAT_MIN_C, ENV_OAT_MAX_C = ENVELOPE['ambient_temperature_C']")
if old not in s: raise SystemExit("NOT FOUND: envelope constants")
s = s.replace(old, new, 1)

o2 = ("        scoreable = (alt <= ENV_ALT_MAX_FT and thr >= ENV_THR_MIN_PCT\n"
      "                     and ENV_RPM_MIN <= lagged[\"rpm\"] <= ENV_RPM_MAX)")
n2 = ("        # Ambient is checked too: a hot sea-level day can exceed the\n"
      "        # +39.9 C the baselines ever saw, and forests extrapolate flat.\n"
      "        scoreable = (ENV_ALT_MIN_FT <= alt <= ENV_ALT_MAX_FT\n"
      "                     and ENV_THR_MIN_PCT <= thr <= ENV_THR_MAX_PCT\n"
      "                     and ENV_RPM_MIN <= lagged[\"rpm\"] <= ENV_RPM_MAX\n"
      "                     and ENV_OAT_MIN_C <= oat <= ENV_OAT_MAX_C)")
if o2 not in s:
    print("WARNING: scoreable block not matched verbatim - patch it by hand at ~356")
else:
    s = s.replace(o2, n2, 1)
if "import pathlib" not in s.split("ENVELOPE = ")[0]:
    s = s.replace("from __future__ import annotations",
                  "from __future__ import annotations\n\nimport pathlib", 1)
compile(s.lstrip("\ufeff"), str(p), "exec")
p.write_text(s, encoding="utf-8")
print("envelope now read from config, syntax OK")
