import io, shutil
P = r"node3_service\api.py"
src = io.open(P, encoding="utf-8-sig").read().replace("\r\n", "\n")
if "target_h=t_h," in src:
    print("ok    already using derived hours")
elif "target_h=target_h," in src:
    shutil.copy2(P, P + ".bak_th")
    src = src.replace("target_h=target_h,", "target_h=t_h,", 1)
    io.open(P, "w", encoding="utf-8", newline="\n").write(src)
    print("ok    build uses derived hours")
else:
    print("MISS  paste the mp.build( call")
