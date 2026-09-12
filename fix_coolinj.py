import pathlib
p = pathlib.Path("generate_mvem_dataset.py")
s = p.read_text(encoding="utf-8")

A = ("                sev = str(rng.choice(list(SEV)))\n"
     "                frac = SEV[sev] * float(rng.uniform(0.7, 1.0))\n"
     "                alt = float(rng.uniform(ALT_MIN, ALT_MAX))\n"
     "                oat = sample_oat(alt, rng)\n"
     "                thr = float(rng.uniform(THR_MIN, THR_MAX))\n")
A2 = ("                sev = str(rng.choice(list(SEV)))\n"
      "                frac = SEV[sev] * float(rng.uniform(0.7, 1.0))\n"
      "                if kind == \"cooling_degradation\":\n"
      "                    # A thermostatted engine hides a weak coolant pump at\n"
      "                    # low load: the outlet sensor holds setpoint until heat\n"
      "                    # rejection exceeds what the degraded pump can carry.\n"
      "                    # Sampling this class across the full envelope produced\n"
      "                    # 2% gate detection. It is therefore defined only where\n"
      "                    # the fault is thermodynamically observable - high\n"
      "                    # throttle, warm air, lower altitude. Cooling detection\n"
      "                    # is CONDITIONAL ON THERMAL LOAD, not envelope-wide.\n"
      "                    # Proper fix is coolant dT (coolant_temp_in_c vs\n"
      "                    # _out_c), which needs a 6th sensor channel. [UNVERIFIED]\n"
      "                    alt = float(rng.uniform(ALT_MIN, 9000.0))\n"
      "                    oat = float(min(OAT_MAX, max(OAT_MIN, isa_oat(alt)\n"
      "                                    + float(rng.uniform(12.0, ISA_DEV_MAX)))))\n"
      "                    thr = float(rng.uniform(72.0, THR_MAX))\n"
      "                else:\n"
      "                    alt = float(rng.uniform(ALT_MIN, ALT_MAX))\n"
      "                    oat = sample_oat(alt, rng)\n"
      "                    thr = float(rng.uniform(THR_MIN, THR_MAX))\n")

B = "    df = pd.DataFrame(rows)\n"
B2 = ("    df = pd.DataFrame(rows)\n"
      "    BOIL_C = 125.0\n"
      "    n_boil = int((df.coolant_temp_C > BOIL_C).sum())\n"
      "    if n_boil:\n"
      "        df = df[df.coolant_temp_C <= BOIL_C].reset_index(drop=True)\n"
      "    print(f\"dropped {n_boil:,} rows with coolant > {BOIL_C:.0f} C: \"\n"
      "          \"pressurised 50/50 glycol boils near 120-125 C and MVEM models \"\n"
      "          \"no boiling or coolant loss, so those rows are out of scope\")\n")

for name, old, new in (("fault sampling", A, A2), ("boil guard", B, B2)):
    if old not in s: raise SystemExit("NOT FOUND: " + name)
    s = s.replace(old, new, 1)
compile(s.lstrip("\ufeff"), str(p), "exec")
p.write_text(s, encoding="utf-8")
print("both patches applied, syntax OK")
