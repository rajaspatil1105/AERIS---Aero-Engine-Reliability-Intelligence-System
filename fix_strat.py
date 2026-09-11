from pathlib import Path
p = Path("train_baselines.py")
t = p.read_text(encoding="utf-8")

old = '''df["b"] = (pd.cut(df.rpm, 8, labels=False).astype(str) + "_" +
           pd.cut(df.throttle_pct, 6, labels=False).astype(str) + "_" +
           pd.cut(df.altitude_ft, 6, labels=False).astype(str))
cap = max(200, N_TARGET // df.b.nunique())
df = (df.groupby("b", observed=True, group_keys=False)
        .apply(lambda g: g.sample(min(len(g), cap), random_state=42))
        .drop(columns=["b"]).reset_index(drop=True))
print(f"stratified to {len(df):,} rows over {cap} per cell")'''

new = '''b = (pd.cut(df.rpm, 8, labels=False).astype(str) + "_" +
     pd.cut(df.throttle_pct, 6, labels=False).astype(str) + "_" +
     pd.cut(df.altitude_ft, 6, labels=False).astype(str))
ncell = b.nunique()
cap = max(200, N_TARGET // ncell)
take = []
for _, idx in b.groupby(b, observed=True).groups.items():
    arr = np.asarray(idx)
    if len(arr) > cap:
        arr = rng.choice(arr, cap, replace=False)
    take.append(arr)
sel = np.concatenate(take)
df = df.iloc[sel].reset_index(drop=True)
print(f"stratified to {len(df):,} rows over {ncell} cells, cap {cap}")'''

if old not in t:
    raise SystemExit("block not found - paste the file and I will adjust")
p.write_text(t.replace(old, new), encoding="utf-8")
print("patched")
