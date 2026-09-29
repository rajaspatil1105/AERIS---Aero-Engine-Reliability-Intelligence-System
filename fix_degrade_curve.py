import pathlib, shutil

P = pathlib.Path("shared/mission_engine.py")
src = P.read_text(encoding="utf-8")
shutil.copy(P, pathlib.Path("shared/mission_engine.py.bak_curve"))

OLD = ("        over = getattr(st, counter) - 1.0\n"
       "        if over <= 0.0:\n"
       "            continue\n"
       "        if label not in st.triggered:\n"
       "            st.triggered.append(label)\n"
       "            fired.append(label)\n"
       "        frac = min(1.0, over)\n")

NEW = ("        raw = getattr(st, counter)\n"
       "        if raw <= DEGRADE_ONSET:\n"
       "            continue\n"
       "        frac = min(1.0, (raw - DEGRADE_ONSET) / (1.0 - DEGRADE_ONSET))\n"
       "        if frac >= DEGRADE_ANNOUNCE and label not in st.triggered:\n"
       "            st.triggered.append(label)\n"
       "            fired.append(label)\n")

ANCHOR = "def apply_degradation("
CONSTS = ("# Health moves from DEGRADE_ONSET onward and is fully applied at counter\n"
          "# 1.0; the fault is only named once it is DEGRADE_ANNOUNCE through that\n"
          "# walk. Previously nothing moved below 1.0, so a 40 h abusive mission\n"
          "# read HEALTHY with 0.79 cooling damage on the books.\n"
          "DEGRADE_ONSET = 0.35\n"
          "DEGRADE_ANNOUNCE = 0.60\n\n\n")

if OLD not in src:
    raise SystemExit("body anchor not found")
src = src.replace(OLD, NEW, 1)
src = src.replace(ANCHOR, CONSTS + ANCHOR, 1)

P.write_text(src, encoding="utf-8")
print("patched -- graded degradation curve")
