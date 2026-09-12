import shared.engine_mvem as mvem
print("pump  coolant  oiltemp   EGT   oilpress   label")
for h in (1.0, 0.9, 0.8, 0.6, 0.4):
    fs = mvem.FaultState(coolant_pump_health=h); fs.validate()
    o = mvem.solve(throttle_pct=90.0, altitude_ft=8000.0, oat_c=35.0, fault=fs)
    print(f"{h:4.1f} {o.coolant_temp_out_c:8.1f} {o.oil_temp_c:8.1f} {o.egt_mean_c:7.1f} {o.oil_pressure_bar:8.2f}")
print()
print("oilpump  coolant  oiltemp   oilpress")
for h in (1.0, 0.9, 0.8, 0.6):
    fs = mvem.FaultState(oil_pump_health=h); fs.validate()
    o = mvem.solve(throttle_pct=90.0, altitude_ft=8000.0, oat_c=35.0, fault=fs)
    print(f"{h:6.1f} {o.coolant_temp_out_c:9.1f} {o.oil_temp_c:8.1f} {o.oil_pressure_bar:9.2f}")
