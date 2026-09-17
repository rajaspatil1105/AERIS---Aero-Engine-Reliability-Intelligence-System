import sys; sys.path.insert(0, ".")
import shared.engine_mvem as mvem
for tag, thr, alt, oat in (("climb",95,6000,10), ("cruise",80,6000,10), ("econ",70,8000,6)):
    o = mvem.solve(throttle_pct=float(thr), altitude_ft=float(alt), oat_c=float(oat))
    d = o if isinstance(o, dict) else vars(o)
    print(tag, {k: (round(v,3) if isinstance(v,(int,float)) else v)
                for k, v in d.items() if isinstance(v,(int,float))})
