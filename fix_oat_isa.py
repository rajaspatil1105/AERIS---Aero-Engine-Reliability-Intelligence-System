import io, re, shutil
P = r"shared\mission_engine.py"
shutil.copy2(P, P + ".bak_isa")
src = io.open(P, encoding="utf-8").read()
n = 0

# 1) import the ISA helper
if "isa_temperature_c" not in src.split("def ")[0] and "from shared.atmosphere import" not in src:
    src = src.replace("from node1_ingestion.simulator_bridge import Setpoint",
                      "from node1_ingestion.simulator_bridge import Setpoint\n"
                      "from shared.atmosphere import isa_temperature_c", 1)
    n += 1
    print("ok    imported isa_temperature_c")
else:
    print("note  atmosphere import already present -- check by hand")

# 2) resolve None right after _interp is called inside run_mission
m = re.search(r"\n(\s*)(thr, alt, oat) = _interp\(([^\n]*)\)\n", src)
if m:
    ind = m.group(1)
    ins = (m.group(0).rstrip("\n") + "\n"
           + ind + "# Setpoint.oat_c=None documents 'ISA at this altitude'.\n"
           + ind + "# Resolve it here so the envelope test and the physics\n"
           + ind + "# both see the same number instead of a None.\n"
           + ind + "if oat is None:\n"
           + ind + "    oat = isa_temperature_c(alt * 0.3048)\n")
    src = src.replace(m.group(0), ins, 1)
    n += 1
    print("ok    oat resolved to ISA at altitude")
else:
    print("MISS  could not find the '_interp(' call site -- grep it")

io.open(P, "w", encoding="utf-8", newline="\n").write(src)
print("\n%d edits" % n)
