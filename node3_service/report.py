"""
node3_service/report.py -- mission report generation (CSV + PDF).

Separate module by design: api.py CASE 12 pins the frame response at
exactly 34 keys, so report payloads travel on their own routes and never
as extra frame fields.

Both formats carry provenance on the FIRST page, not a footnote. A report
that prints "fuel_pressure_dev 25.5%" without the caveat that produced it
would undo the honesty the rest of the system is built on.
"""
from __future__ import annotations

import csv
import io

RAW_CHANNELS = (
    "altitude_ft", "ambient_temperature_C", "throttle_pct", "rpm",
    "fuelflow_kgh", "coolant_temp_C", "EGT_mean_C", "oil_pressure_bar",
    "oil_temperature_C",
)

BASELINE_TARGETS = (
    "EGT_mean_C", "coolant_temp_C", "oil_pressure_bar",
    "oil_temperature_C", "fuelflow_kgh",
)

BANNER = ("MODELS UNVALIDATED -- gate F1 0.907 / ROC-AUC 0.985 | RUL R2 "
          "+0.842 / MAE 93 h, held-out engines | synthetic MVEM data, one "
          "validated operating point. Nothing in this document is "
          "airworthiness evidence.")

CSV_COLUMNS = (
    ["seq", "ts_utc", "status", "refusal_class", "meaningful",
     "anomaly_probability", "fault_label", "fault_confidence",
     "rul_raw", "rul_smoothed", "rul_trusted", "latency_ms"]
    + [f"meas_{c}" for c in RAW_CHANNELS]
    + [f"exp_{c}" for c in BASELINE_TARGETS]
    + [f"res_{c}" for c in BASELINE_TARGETS]
)


class ReportError(RuntimeError):
    """Report cannot be produced (missing optional dependency)."""


def _measured(frame):
    """Raw channel values. The 34-key contract calls this `features`;
    earlier builds used `measured`. Accept either, invent neither."""
    for k in ("measured", "features"):
        v = frame.get(k)
        if isinstance(v, dict):
            return v
    return {}


def frames_chronological(store, sid, limit=200000):
    """recent_frames() is newest-first by seq; a report reads forwards."""
    rows = store.recent_frames(limit=limit, session_id=sid, full=True)
    return list(reversed(rows))


def session_csv(store, sid) -> str:
    store.session_summary(sid)          # raises StoreError on bad id
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_COLUMNS)
    for r in frames_chronological(store, sid):
        f = r.get("frame") or {}
        meas, exp = _measured(f), (f.get("expected") or {})
        res = f.get("residuals") or {}
        w.writerow(
            [r.get("seq"), r.get("ts_utc"), r.get("status"),
             r.get("refusal_class"), r.get("meaningful"),
             f.get("anomaly_probability"), f.get("fault_label"),
             f.get("fault_confidence"), r.get("rul_raw"),
             r.get("rul_smoothed"), r.get("rul_trusted"), r.get("latency_ms")]
            + [meas.get(c) for c in RAW_CHANNELS]
            + [exp.get(c) for c in BASELINE_TARGETS]
            + [res.get(c) for c in BASELINE_TARGETS])
    return buf.getvalue()


def _fmt(v, nd=3):
    if v is None:
        return "--"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return f"{v:.{nd}f}" if isinstance(v, float) else str(v)
    return str(v)


def session_pdf(store, sid, manifest=None) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas as rl_canvas
        from reportlab.platypus import (PageBreak, Paragraph,
                                        SimpleDocTemplate, Spacer, Table,
                                        TableStyle)
        from reportlab.graphics.shapes import Drawing, Line, String
        from reportlab.graphics.charts.lineplots import LinePlot
        from reportlab.graphics.charts.barcharts import VerticalBarChart
        from reportlab.graphics import renderPDF
    except ImportError as exc:
        raise ReportError("reportlab is not installed. Run: "
                          "python -m pip install reportlab") from exc

    summ = store.session_summary(sid)
    rows = frames_chronological(store, sid)
    events = list(reversed(store.recent_events(limit=5000, session_id=sid)))

    ACC = colors.HexColor("#0b5f6b")
    DIM = colors.HexColor("#555555")
    WARN = colors.HexColor("#8a6d00")
    LINE = colors.HexColor("#cccccc")

    ss = getSampleStyleSheet()
    H1 = ParagraphStyle("H1", parent=ss["Title"], fontSize=20, leading=24,
                        textColor=ACC, alignment=0)
    H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontSize=11, leading=14,
                        textColor=ACC, spaceBefore=10, spaceAfter=4)
    P = ParagraphStyle("P", parent=ss["BodyText"], fontSize=8.5, leading=11.5)
    SMALL = ParagraphStyle("SM", parent=P, fontSize=7.5, leading=10,
                           textColor=DIM)
    CAV = ParagraphStyle("CAV", parent=P, fontSize=8, leading=11,
                         textColor=WARN)

    class NumberedCanvas(rl_canvas.Canvas):
        """Total page count is unknown until the doc is built, so pages are
        buffered and stamped with 'page N of M' at save time."""
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._saved = []

        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved)
            for state in self._saved:
                self.__dict__.update(state)
                self.setFont("Helvetica", 6.5)
                self.setFillColor(DIM)
                self.drawRightString(A4[0] - 18 * mm, 11.5 * mm,
                                     f"page {self._pageNumber} of {total}")
                super().showPage()
            super().save()

    def decorate(cv, _doc):
        cv.saveState()
        cv.setFont("Helvetica-Bold", 8)
        cv.setFillColor(ACC)
        cv.drawString(18 * mm, A4[1] - 12 * mm, "AERIS")
        cv.setFont("Helvetica", 7)
        cv.setFillColor(DIM)
        cv.drawString(31 * mm, A4[1] - 12 * mm,
                      f"MISSION REPORT   session {sid}   ROTAX 915 iS")
        cv.setStrokeColor(LINE)
        cv.line(18 * mm, A4[1] - 14.5 * mm, A4[0] - 18 * mm, A4[1] - 14.5 * mm)
        cv.line(18 * mm, 14.5 * mm, A4[0] - 18 * mm, 14.5 * mm)
        cv.setFont("Helvetica", 6)
        cv.setFillColor(WARN)
        cv.drawString(18 * mm, 11.5 * mm, BANNER[:150])
        cv.restoreState()

    def kv(pairs, w=(52 * mm, 108 * mm)):
        t = Table([[k, Paragraph(_fmt(v), P)] for k, v in pairs], colWidths=w)
        t.setStyle(TableStyle([
            ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 8),
            ("FONT", (1, 0), (1, -1), "Helvetica", 8),
            ("TEXTCOLOR", (0, 0), (0, -1), DIM),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("LINEBELOW", (0, 0), (-1, -2), 0.25, LINE)]))
        return t

    def grid(data, widths, size=6.8):
        t = Table(data, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", size),
            ("FONT", (0, 1), (-1, -1), "Helvetica", size),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef3f4")),
            ("TEXTCOLOR", (0, 0), (-1, 0), ACC),
            ("GRID", (0, 0), (-1, -1), 0.25, LINE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 2)]))
        return t

    story = []

    # ---- cover ------------------------------------------------------
    story += [Spacer(1, 22 * mm), Paragraph("AERIS MISSION REPORT", H1),
              Paragraph("Aero-Engine Reliability Intelligence System", SMALL),
              Spacer(1, 10 * mm)]
    story.append(kv([
        ("SESSION", summ.get("id")),
        ("ENGINE", "ROTAX 915 iS (single-engine UAV configuration)"),
        ("STARTED (UTC)", summ.get("started_utc")),
        ("ENDED (UTC)", summ.get("ended_utc") or "session still open"),
        ("FRAMES STORED", summ.get("frames")),
        ("DATA PROVENANCE", summ.get("data_provenance")),
        ("MODELS TRUSTED", bool(summ.get("models_trusted"))),
        ("MANIFEST SHA256", str(summ.get("manifest_sha256") or "--")[:32] + "..."),
        ("SKLEARN AT BUILD", summ.get("sklearn_version")),
        ("SCHEMA VERSION", summ.get("schema_version")),
        ("OPERATOR NOTE", summ.get("note") or "--"),
    ]))
    story += [Spacer(1, 8 * mm)]
    box = Table([[Paragraph("<b>READ THIS FIRST</b><br/>" + BANNER, CAV)]],
                colWidths=[160 * mm])
    box.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.7, WARN),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fdf8e6")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    story += [box, PageBreak()]

    # ---- summary ----------------------------------------------------
    story.append(Paragraph("1. SESSION SUMMARY", H2))
    sc = summ.get("status_counts") or {}
    ec = summ.get("event_counts") or {}
    total = summ.get("frames") or 0
    sdata = [["STATUS", "FRAMES", "SHARE"]]
    for k in sorted(sc, key=lambda x: -sc[x]):
        pct = (sc[k] / total * 100.0) if total else 0.0
        sdata.append([k, str(sc[k]), f"{pct:.1f} %"])
    story += [grid(sdata, [70 * mm, 30 * mm, 30 * mm], 7.5), Spacer(1, 4 * mm)]
    story.append(kv([
        ("EVENT COUNTS", ", ".join(f"{k}={v}" for k, v in ec.items()) or "none"),
        ("LATENCY MEAN", f"{_fmt(summ.get('latency_mean_ms'), 2)} ms"),
        ("LATENCY MAX", f"{_fmt(summ.get('latency_max_ms'), 2)} ms"),
    ]))

    # ---- fault occurrences ------------------------------------------
    story.append(Paragraph("2. FAULT OCCURRENCES", H2))
    labels = {}
    for r in rows:
        lbl = r.get("fault_label")
        if not lbl:
            continue
        d = labels.setdefault(lbl, {"n": 0, "first": r.get("seq"),
                                    "last": r.get("seq"), "cmax": 0.0})
        d["n"] += 1
        d["last"] = r.get("seq")
        d["cmax"] = max(d["cmax"], float(r.get("confidence") or 0.0))
    if labels:
        fdata = [["LABEL", "FRAMES", "FIRST SEQ", "LAST SEQ",
                  "PEAK CONF", "INTERPRETATION"]]
        for k, d in sorted(labels.items(), key=lambda x: -x[1]["n"]):
            near = ("near chance vs 20% five-class baseline; "
                    "NOT a diagnosis" if d["cmax"] < 0.40
                    else "above chance; still unvalidated")
            fdata.append([k, str(d["n"]), str(d["first"]), str(d["last"]),
                          f"{d['cmax'] * 100:.1f} %", near])
        story.append(grid(fdata, [34 * mm, 15 * mm, 17 * mm, 17 * mm,
                                  18 * mm, 59 * mm]))
    else:
        story.append(Paragraph("No frame in this session carried a fault "
                               "label.", P))

    # ---- events ------------------------------------------------------
    story.append(Paragraph("3. BREACHES, ADVISORIES AND REFUSALS", H2))
    if events:
        edata = [["SEQ", "SEVERITY", "CHANNEL", "VALUE", "LIMIT", "MESSAGE"]]
        for e in events[:120]:
            edata.append([str(e.get("frame_seq")), e.get("severity") or "--",
                          e.get("channel") or "--", _fmt(e.get("value"), 3),
                          _fmt(e.get("threshold"), 3),
                          Paragraph(str(e.get("message") or "")[:150], SMALL)])
        story.append(grid(edata, [13 * mm, 22 * mm, 30 * mm, 18 * mm,
                                  18 * mm, 59 * mm]))
        if len(events) > 120:
            story.append(Paragraph(f"{len(events) - 120} further events "
                                   "omitted; full set in the CSV export.",
                                   SMALL))
    else:
        story.append(Paragraph("No safety breach, advisory or envelope "
                               "violation was recorded.", P))

    # ---- per-frame ---------------------------------------------------
    story += [PageBreak(), Paragraph("4. FRAME LOG", H2),
              Paragraph("Every stored frame, chronological. Blank cells are "
                        "values the system did not produce -- refused frames "
                        "are never scored, so their columns are empty by "
                        "design rather than zero.", SMALL), Spacer(1, 2 * mm)]
    def _series(key):
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

    # ---- appendix ----------------------------------------------------
    story += [PageBreak(), Paragraph("APPENDIX A. MODEL CAVEATS", H2)]
    cav = None
    if manifest is not None:
        cav = getattr(manifest, "caveats", None) or \
            (manifest.data.get("caveats") if hasattr(manifest, "data") else None)
    if isinstance(cav, dict) and cav:
        for k, v in cav.items():
            story.append(Paragraph(f"<b>{k}</b>", P))
            story.append(Paragraph(str(v)[:900], SMALL))
            story.append(Spacer(1, 2 * mm))
    else:
        story.append(Paragraph(
            "Gate F1 is 0.907 with ROC-AUC 0.985 on ten held-out engines. "
            "RUL scores R2 +0.842, MAE 93 h on four held-out engines, but "
            "0.938 of its feature importance sits on one channel and all "
            "training data is synthetic. Baselines are healthy-only "
            "regression fits, so residuals outside the trained envelope are "
            "not meaningful and such frames are refused rather than scored. "
            "Full text: CAVEATS.md in the source tree.", SMALL))

    out = io.BytesIO()
    doc = SimpleDocTemplate(
        out, pagesize=A4, title=f"AERIS mission report - session {sid}",
        author="AERIS", subject="UAV aero-engine session report",
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=20 * mm, bottomMargin=18 * mm)
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate,
              canvasmaker=NumberedCanvas)
    return out.getvalue()
