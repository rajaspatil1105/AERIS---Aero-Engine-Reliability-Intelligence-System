import csv, pathlib
rows = list(csv.DictReader(pathlib.Path("data/rul_histories.csv").open(encoding="utf-8")))
print("rows", len(rows), "cols", list(rows[0]))
one = [r for r in rows if r["serial"] == "RTX915-0001"]
print("\nRTX915-0001, every 16th mission:")
print("  miss  hours   oil     cool    bear    rul_h")
for r in one[::16]:
    print("  %4s %6s  %-7s %-7s %-7s %s"
          % (r["mission"], r["hours"], r["oil"][:6], r["coolant"][:6],
             r["bearing"][:6], r["rul_h"]))
import statistics as s
rul = [float(r["rul_h"]) for r in rows]
oil = [float(r["oil"]) for r in rows]
print("\nrul_h  min %.0f  median %.0f  max %.0f" % (min(rul), s.median(rul), max(rul)))
print("oil    min %.3f  median %.3f  max %.3f" % (min(oil), s.median(oil), max(oil)))
