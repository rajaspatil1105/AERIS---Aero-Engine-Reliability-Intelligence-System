import pathlib
p = pathlib.Path("generate_mvem_dataset.py")
s = p.read_text(encoding="utf-8")
old = "        fs.coolant_pump_health = max(0.05, base.coolant_pump_health - 0.28 * frac)"
new = ("        # 0.45 puts severe near 0.54 pump health. Deeper was tested and\n"
       "        # rejected: at 0.35 only 31% of envelope-sampled rows clear 98.7 C\n"
       "        # and peak coolant reaches 210 C, which is fiction - pressurised\n"
       "        # 50/50 glycol boils near 120-125 C and MVEM models no boiling or\n"
       "        # coolant loss. Detectability comes from injecting under thermal\n"
       "        # load instead, see the fault loop in main(). [UNVERIFIED]\n"
       "        fs.coolant_pump_health = max(0.05, base.coolant_pump_health - 0.45 * frac)")
if old not in s: raise SystemExit("NOT FOUND - knob line changed")
s = s.replace(old, new, 1)
compile(s.lstrip("\ufeff"), str(p), "exec")
p.write_text(s, encoding="utf-8")
print("knob patched 0.28 -> 0.45, syntax OK\n")
