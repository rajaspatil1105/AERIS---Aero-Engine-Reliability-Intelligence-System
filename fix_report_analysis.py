import io, shutil
p = r"node3_service\report.py"
src = io.open(p, encoding="utf-8").read()

old_imp = """        from reportlab.platypus import (PageBreak, Paragraph,
                                        SimpleDocTemplate, Spacer, Table,
                                        TableStyle)"""
new_imp = old_imp + """
        from reportlab.graphics.shapes import Drawing, Line, String
        from reportlab.graphics.charts.lineplots import LinePlot
        from reportlab.graphics.charts.barcharts import VerticalBarChart
        from reportlab.graphics import renderPDF"""

start = src.index('    fl = [["SEQ", "UTC", "STATUS", "REFUSAL", "P_ANOM", "LABEL",')
end   = src.index("    # ---- appendix ---")
old_block = src[start:end]

new_block = '''    def _series(key):
        pts, n = [], max(1, len(rows) // 300)
        for i, r in enumerate(rows[::n]):
            v = r.get(key)
            if v is not None:
                pts.append((i * n, float(v)))
        return pts

    def _plot(pts, title, colr, gate=None):
        d = Drawing(460, 150)
        lp = LinePlot()
        lp.x, lp.y, lp.width, lp.height = 40, 25, 400, 105
        lp.data = [pts]
        lp.lines[0].strokeColor = colr
        lp.lines[0].strokeWidth = 0.9
        lp.joinedLines = 1
        lp.xValueAxis.labelTextFormat = '%d'
        lp.yValueAxis.labelTextFormat = '%0.2f'
        d.add(lp)
        d.add(String(40, 138, title, fontSize=8, fillColor=DIM))
        if gate is not None and pts:
            ys = [v for _, v in pts]
            lo, hi = min(ys), max(ys)
            if hi > lo and lo <= gate <= hi:
                y = 25 + (gate - lo) / (hi - lo) * 105
                d.add(Line(40, y, 440, y, strokeColor=WARN,
                           strokeDashArray=[2, 2]))
                d.add(String(442, y - 3, 'gate', fontSize=6, fillColor=WARN))
        return d

    pa = _series("p_anom")
    if pa:
        story.append(_plot(pa, "anomaly probability vs frame (gate 0.50)",
                           ACC, gate=0.5))
        story.append(Spacer(1, 3 * mm))
    ru = _series("rul_raw")
    if ru:
        story.append(_plot(ru, "rul_raw vs frame (ordering, one dominant "
                               "channel)", colors.HexColor("#7a3b8f")))
        story.append(Spacer(1, 3 * mm))

    tally = {}
    for r in rows:
        k = r.get("refusal_class") or (r.get("status") or "--")
        tally[k] = tally.get(k, 0) + 1
    if tally:
        items = sorted(tally.items(), key=lambda kv: -kv[1])
        d = Drawing(460, 140)
        bc = VerticalBarChart()
        bc.x, bc.y, bc.width, bc.height = 40, 30, 400, 95
        bc.data = [[v for _, v in items]]
        bc.categoryAxis.categoryNames = [k[:16] for k, _ in items]
        bc.categoryAxis.labels.angle = 20
        bc.categoryAxis.labels.dy = -8
        bc.categoryAxis.labels.fontSize = 6.5
        bc.bars[0].fillColor = ACC
        bc.valueAxis.valueMin = 0
        d.add(bc)
        d.add(String(40, 130, "frames by status / refusal class",
                     fontSize=8, fillColor=DIM))
        story.append(d)
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph(
            "Counts: " + ", ".join(f"{k} {v}" for k, v in items) +
            f". Total {len(rows)} frames. Per-frame values are in the CSV "
            "export; this section is interpretation, not a transcript.",
            SMALL))

'''

shutil.copyfile(p, p + ".bak_charts")
src = src.replace(old_imp, new_imp).replace(old_block, new_block)
io.open(p, "w", encoding="utf-8").write(src)
print("ok    section 4 is now charts, frame table removed")
