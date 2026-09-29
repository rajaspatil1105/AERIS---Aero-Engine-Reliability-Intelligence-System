import sys, pathlib, csv, dataclasses
sys.path.insert(0, str(pathlib.Path(".").resolve()))
from shared import mission_engine as me
from node2_twin_core.residual_calc import ResidualCalculator, FEATURE_ORDER

calc = ResidualCalculator()
print("FEATURE_ORDER n =", len(FEATURE_ORDER))

base = me.FLEET_BY_SERIAL["RTX915-0001"]
# Sample the same engine at several points along its life.
for oil, cool, bear, tag in ((1.000, 1.000, 0.000, "factory"),
                             (0.940, 0.989, 0.050, "early"),
                             (0.868, 0.976, 0.094, "mid"),
                             (0.773, 0.957, 0.141, "late"),
                             (0.700, 0.940, 0.180, "EOL")):
    e = dataclasses.replace(base, oil_pump_health=oil,
                            coolant_pump_health=cool, bearing_wear=bear)
    prof = [me.Setpoint(t_s=0.0, throttle_pct=80.0, altitude_ft=6000.0, oat_c=15.0),
            me.Setpoint(t_s=600.0, throttle_pct=80.0, altitude_ft=6000.0, oat_c=15.0)]
    last = None
    for rec in me.run_mission(prof, e, dt_s=1.0, stress_enabled=False,
                              emit_cruise_s=300.0):
        last = rec
    f = last["frame"]
    r = calc.residuals(f) if hasattr(calc, "residuals") else None
    print("\n%-8s oil %.3f" % (tag, oil))
    for k in ("oil_pressure_bar", "coolant_temp_C", "EGT_mean_C",
              "oil_temperature_C", "fuelflow_kgh"):
        print("   %-20s meas %10.4f" % (k, f.get(k, float("nan"))))
