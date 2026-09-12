import pathlib
p = pathlib.Path("shared/mission_engine.py")
s = p.read_text(encoding="utf-8")
a = '"coolant_temp_C": ("coolant_temp_c", "coolant_temp_C", "coolant_c"),'
b = '"coolant_temp_C": ("coolant_temp_out_c", "coolant_temp_c"),'
c = '"power_kW": ("power_kw", "power_kW", "shaft_power_kw"),'
d = '"power_kW": ("brake_power_kw", "power_kw"),'
e = '"oil_temperature_C": ("oil_temp_c", "oil_temperature_c", "oil_temperature_C"),'
f = '"oil_temperature_C": ("oil_temp_c",),'
for old, new in ((a, b), (c, d), (e, f)):
    if old not in s: raise SystemExit("NOT FOUND - aborted")
    s = s.replace(old, new, 1)
compile(s.lstrip("\ufeff"), str(p), "exec")
p.write_text(s, encoding="utf-8")
print("patched, syntax OK")
