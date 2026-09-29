# fix_rul_paperwork.py -- RUL was retrained; three places still say otherwise
import io, re, shutil

def read(p):
    return io.open(p, encoding="utf-8-sig").read().replace("\r\n", "\n")

def write(p, s):
    io.open(p, "w", encoding="utf-8", newline="\n").write(s)

# ---- 1. fault_injection.py : the guard now has it backwards ----------------
P = r"shared\fault_injection.py"
s = read(P)
i = s.find("check(inj.rul_trusted is False")
if i < 0:
    print("MISS  fault_injection -- assertion not found (already fixed?)")
else:
    print("--- current block ---")
    print(s[i:i + 400])
    print("---------------------")
    shutil.copy2(P, P + ".bak_rultrust")
    s = s.replace("check(inj.rul_trusted is False",
                  "check(inj.rul_trusted is True", 1)
    write(P, s)
    print("ok    condition flipped to expect True")
    print("NOTE  the failure message above still reads 'RUL is not")
    print("      validated' -- edit that string by hand, the wrapping")
    print("      is unknown to me and I will not guess at it.")

# ---- 2. verify_all.py : locate the XFAIL marker, do not guess --------------
V = "verify_all.py"
v = read(V)
hits = [(n + 1, ln) for n, ln in enumerate(v.split("\n"))
        if "XFAIL" in ln or "Stage 8" in ln or "xfail" in ln]
if not hits:
    print("MISS  verify_all -- no XFAIL marker found")
else:
    print("\nverify_all.py XFAIL marker, remove by hand:")
    for n, ln in hits:
        print("  %d: %s" % (n, ln.rstrip()))

# ---- 3. README.md : show the stale RUL text --------------------------------
R = "README.md"
r = read(R)
for m in re.finditer(r"[^\n]*(?:R2 |R\u00b2 |rul_trusted|rul_units|-0\.10|MAE 107)[^\n]*", r):
    print("\nREADME stale line:\n  " + m.group(0).strip())
