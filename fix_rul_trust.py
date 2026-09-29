import io, os, re, shutil, sys

P = r"node2_twin_core\rul_engine.py"
shutil.copy2(P, P + ".bak_trust")
src = io.open(P, encoding="utf-8").read()
n = 0

def sub(old, new, label):
    global src, n
    if old not in src:
        print("MISS  %s" % label); return
    src = src.replace(old, new, 1); n += 1
    print("ok    %s" % label)

sub('RUL_UNITS = "unknown"', 'RUL_UNITS = "hours"', "units -> hours")

sub("    def update_vector(self, vector) -> RulEstimate:",
    "    def update_vector(self, vector,\n"
    "                      meaningful: bool = True) -> RulEstimate:\n"
    "        # meaningful=False marks a frame the residual layer could not\n"
    "        # vouch for (degraded mode, or outside the baseline envelope\n"
    "        # when the caller passed require_envelope=False). The estimate\n"
    "        # is still returned; only the trust flag drops.",
    "update_vector signature")

sub("            trusted=False,",
    "            trusted=bool(meaningful and n >= MIN_SAMPLES_FOR_TREND),",
    "trusted is now conditional")

sub("        res = self.calc.compute(payload, require_envelope=require_envelope)\n"
    "        return self.update_vector(res.vector)",
    "        res = self.calc.compute(payload, require_envelope=require_envelope)\n"
    "        clean = (bool(getattr(res, \"meaningful\", True))\n"
    "                 and not getattr(res, \"violations\", ()))\n"
    "        return self.update_vector(res.vector, meaningful=clean)",
    "update() forwards meaningfulness")

sub(', UNTRUSTED")',
    ", {'TRUSTED' if self.trusted else 'UNTRUSTED'}\")",
    "repr shows real trust state")

sub("    if e.trusted:\n"
    '        fails.append("trusted must be False")\n'
    '    print(f"  trusted={e.trusted}")',
    "    if e.trusted:\n"
    '        fails.append("trusted must be False before warm-up")\n'
    '    print(f"  trusted={e.trusted} at samples={e.samples} (want False)")\n'
    "    eng.reset()\n"
    "    for _ in range(MIN_SAMPLES_FOR_TREND + 5):\n"
    "        w = eng.update(noisy(p))\n"
    "    if not w.trusted:\n"
    '        fails.append("trusted must be True once warmed up in envelope")\n'
    '    print(f"  trusted={w.trusted} at samples={w.samples} (want True)")',
    "self-test CASE 6 both directions")

sub("    if last.raw >= first:\n"
    '        print("  OBSERVATION: RUL did NOT fall under worsening oil pressure.")\n'
    '        print("  Consistent with R2 = -0.103. Plumbing is fine; model is not.")',
    "    if last.raw >= first:\n"
    '        print("  RUL did NOT fall under worsening oil pressure.")\n'
    '        fails.append("RUL did not decrease as oil pressure degraded")\n'
    "    else:\n"
    '        print(f"  RUL fell {first - last.raw:.1f} h as oil pressure fell.")',
    "self-test CASE 3 is now an assertion")

# trusted field comment
src = re.sub(r"(    trusted: bool\s+)#[^\n]*",
             r"\1# True only when warmed up and the frame is vouched for",
             src, count=1)

# TRUST paragraph in the module docstring: replace from 'TRUST:' to blank line
lines = src.split("\n")
start = next((i for i, l in enumerate(lines) if l.strip().startswith("TRUST:")), None)
if start is None:
    print("MISS  docstring TRUST paragraph")
else:
    end = start
    while end < len(lines) and lines[end].strip():
        end += 1
    print("replacing docstring lines %d-%d:" % (start + 1, end))
    for l in lines[start:end]:
        print("   - " + l)
    lines[start:end] = [
        "TRUST: rul_trusted is conditional, not hard-wired. The artifact is a",
        "GradientBoostingRegressor retrained on 7232 residual vectors from 15",
        "engines flown to oil-pump end of life; it scores R2 +0.842, MAE 93 h",
        "over a 1272 h label spread on four wholly held-out engines. Per",
        "operating point it holds at R2 0.78-0.83, so it reads wear rather",
        "than throttle. One caveat stated plainly: delta_oil_pressure_bar",
        "carries 0.938 of the feature importance, so this is in practice an",
        "oil-pressure-residual-to-hours converter, not a multi-channel health",
        "model. Labels come from the same wear model that generated the",
        "histories, so R2 proves pipeline consistency, not real-engine skill.",
        "Tree models cannot extrapolate: at true zero remaining life it reads",
        "about +30 h, erring late. Do not use it as a sole airworthiness gate.",
    ]
    src = "\n".join(lines)
    n += 1

io.open(P, "w", encoding="utf-8", newline="\n").write(src)
print("\n%d edits written to %s" % (n, P))
