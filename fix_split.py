from pathlib import Path
p = Path("train_classifiers.py"); t = p.read_text(encoding="utf-8")
a = t.index("print(\"reading\", SRC)")
b = t.index("print(\"\\ncomputing deltas")
t = t[:a] + '''print("reading", SRC)
KEEP = 0.15
parts, seen = [], 0
for ch in pd.read_csv(SRC, usecols=["engine_id","fault_type"]+RAW,
                      chunksize=400_000, low_memory=False):
    seen += len(ch)
    m = np.ones(len(ch), bool)
    for k,(lo,hi) in ENV.items(): m &= ch[k].between(lo,hi).to_numpy()
    ch = ch[m]
    parts.append(ch[rng.random(len(ch)) < KEEP])
    print(f"\\rread {seen:,}  pool {sum(len(x) for x in parts):,}", end="")

pool = pd.concat(parts, ignore_index=True); del parts
df = (pool.groupby("fault_type", observed=True, group_keys=False)
          .sample(n=PER_CLASS, random_state=42)
          .reset_index(drop=True))
del pool
print(f"\\nsampled {len(df):,}")
print(df.fault_type.value_counts().to_string())
nn = eng(df.engine_id)
print("healthy engines", nn[df.fault_type=="healthy"].min(), "-", nn[df.fault_type=="healthy"].max())
print("faulted engines", nn[df.fault_type!="healthy"].min(), "-", nn[df.fault_type!="healthy"].max())

''' + t[b:]

t = t.replace('print(f"\\ntrain {tr.sum():,}   val {val.sum():,}   test {test.sum():,}")',
'''print(f"\\ntrain {tr.sum():,}   val {val.sum():,}   test {test.sum():,}")
for lbl, s in (("train",tr),("val",val),("test",test)):
    h = (s & ~faulted).sum(); f = (s & faulted).sum()
    print(f"  {lbl:<6} healthy {h:>8,}   faulted {f:>8,}")
assert (test & ~faulted).sum() > 1000, "test set has no healthy rows - split broken"
assert (test & faulted).sum() > 1000, "test set has no faulted rows - split broken"''')
p.write_text(t, encoding="utf-8"); print("patched")
