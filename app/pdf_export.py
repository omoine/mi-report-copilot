"""PDF export of the finished MI report (reportlab, no Word/Excel dependency).

Deliberately avoids any Office COM automation: PDF export via Word hangs on this
build's managed desktop, and it would disturb the user's open applications.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from . import data_access

# Light document, brand accent. See DESIGN.md: the PDF deliberately stays light
# while the web UI is dark, because this is a report meant to be printed.
INK_PRIMARY = colors.HexColor("#0b0b0b")
INK_SECONDARY = colors.HexColor("#52514e")
INK_MUTED = colors.HexColor("#898781")
ACCENT = colors.HexColor("#8A15E0")
GRIDLINE = colors.HexColor("#e1e0d9")
SURFACE_ALT = colors.HexColor("#f7f4fb")

PAGE_SIZE = landscape(A4)
MARGIN = 16 * mm


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontName="Helvetica-Bold",
                                fontSize=19, leading=23, textColor=INK_PRIMARY,
                                alignment=TA_LEFT, spaceAfter=2),
        "subtitle": ParagraphStyle("st", parent=base["Normal"], fontName="Helvetica",
                                   fontSize=9, leading=13, textColor=INK_MUTED, spaceAfter=10),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold",
                             fontSize=11.5, leading=15, textColor=INK_PRIMARY,
                             spaceBefore=12, spaceAfter=5),
        "body": ParagraphStyle("b", parent=base["Normal"], fontName="Helvetica",
                               fontSize=9.5, leading=14, textColor=INK_SECONDARY, spaceAfter=5),
        "quote": ParagraphStyle("q", parent=base["Normal"], fontName="Helvetica-Oblique",
                                fontSize=10, leading=14, textColor=INK_PRIMARY,
                                leftIndent=8, borderPadding=4, spaceAfter=6),
        "small": ParagraphStyle("sm", parent=base["Normal"], fontName="Helvetica",
                                fontSize=7.5, leading=10, textColor=INK_MUTED),
        "cell": ParagraphStyle("c", parent=base["Normal"], fontName="Helvetica",
                               fontSize=7.5, leading=9.5, textColor=INK_SECONDARY),
        "cellhead": ParagraphStyle("ch", parent=base["Normal"], fontName="Helvetica-Bold",
                                   fontSize=7.5, leading=9.5, textColor=INK_PRIMARY),
    }


def _escape(text: Any) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _bullets(items: list[str], style: ParagraphStyle) -> Any:
    if not items:
        return Paragraph("<i>None recorded.</i>", style)
    return ListFlowable(
        [ListItem(Paragraph(_escape(i), style), leftIndent=12) for i in items],
        bulletType="bullet", start="•", bulletFontSize=7,
        bulletOffsetY=0.5, leftIndent=12,
    )


def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(INK_MUTED)
    canvas.drawString(MARGIN, 10 * mm,
                      f"{data_access.get_metadata()['data_classification']} data - "
                      "figures computed deterministically from source; commentary AI-generated.")
    canvas.drawRightString(PAGE_SIZE[0] - MARGIN, 10 * mm, f"Page {canvas.getPageNumber()}")
    canvas.setStrokeColor(GRIDLINE)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, 13 * mm, PAGE_SIZE[0] - MARGIN, 13 * mm)
    canvas.restoreState()


def _result_table(table: dict[str, Any], st: dict[str, ParagraphStyle]) -> list[Any]:
    if not table or not table.get("columns"):
        return [Paragraph("<i>No rows returned.</i>", st["body"])]

    cols = table["columns"]
    data = [[Paragraph(_escape(c), st["cellhead"]) for c in cols]]
    data.extend([[Paragraph(_escape(v), st["cell"]) for v in row] for row in table["rows"]])

    avail = PAGE_SIZE[0] - 2 * MARGIN
    tbl = Table(data, colWidths=[avail / len(cols)] * len(cols), repeatRows=1, hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), SURFACE_ALT),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, ACCENT),
        ("GRID", (0, 0), (-1, -1), 0.25, GRIDLINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SURFACE_ALT]),
    ]))
    out: list[Any] = [tbl]
    if table.get("truncated"):
        out += [Spacer(1, 3),
                Paragraph(f"Showing {len(table['rows'])} of {table['total_rows']} rows. "
                          "The full result is in the accompanying Markdown file.", st["small"])]
    return out


def build_pdf(report: dict[str, Any], out_dir: Path, session_id: str,
              stamp: dt.datetime | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    # The caller passes a shared stamp so the PDF and its Markdown companion
    # carry matching filenames.
    stamp = stamp or dt.datetime.now()
    path = out_dir / f"mi_report_{session_id}_{stamp.strftime('%Y%m%d_%H%M%S')}.pdf"
    st = _styles()

    doc = SimpleDocTemplate(
        str(path), pagesize=PAGE_SIZE,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=20 * mm,
        title=report.get("title", "MI Report"), author="MI Report Copilot",
    )

    story: list[Any] = [
        Paragraph(_escape(report.get("title", "MI Report")), st["title"]),
        Paragraph(f"Generated {stamp.strftime('%d %B %Y at %H:%M')} &nbsp;·&nbsp; "
                  f"{data_access.get_metadata()['data_classification']} data", st["subtitle"]),
        Paragraph("Requested view", st["h2"]),
        Paragraph(f'"{_escape(report.get("user_query", ""))}"', st["quote"]),
        Paragraph(_escape(report.get("understood", "")), st["body"]),
    ]

    # The headline sits above the chart: a reader should be able to stop here.
    if report.get("headline"):
        cells = []
        for item in report["headline"][:3]:
            block = [
                Paragraph(_escape(item["label"]).upper(), st["small"]),
                Spacer(1, 2),
                Paragraph(f'<font size="16"><b>{_escape(item["value"])}</b></font>',
                          st["body"]),
                Paragraph(_escape(item.get("detail", "")), st["small"]),
            ]
            cells.append(block)
        avail = PAGE_SIZE[0] - 2 * MARGIN
        head_tbl = Table([cells], colWidths=[avail / len(cells)] * len(cells),
                         hAlign="LEFT")
        head_tbl.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, -1), SURFACE_ALT),
            ("BOX", (0, 0), (-1, -1), 0.5, GRIDLINE),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, GRIDLINE),
            ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ("RIGHTPADDING", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        story += [Spacer(1, 8), head_tbl]

    chart_path = report.get("chart_path")
    if chart_path and Path(chart_path).exists():
        story += [Spacer(1, 6), _fit_image(Path(chart_path))]

    if report.get("narrative"):
        story += [Paragraph("Commentary", st["h2"]),
                  Paragraph(_escape(report["narrative"]), st["body"])]

    for note in report.get("chart_notes", []) or []:
        story.append(Paragraph(_escape(note), st["small"]))

    # A short table should never be orphaned across a page break; a long one is
    # allowed to flow, since its header row repeats.
    table_block: list[Any] = [Paragraph("Result data", st["h2"]),
                              *_result_table(report.get("table", {}), st)]
    row_count = len(report.get("table", {}).get("rows", []))
    story += [KeepTogether(table_block)] if row_count <= 12 else table_block

    # Caveats start a new page so they are never visually detached from nothing.
    story += [PageBreak(), Paragraph("Limitations of this view", st["h2"]),
              _bullets(report.get("limitations", []), st["body"]),
              Paragraph("Dependencies", st["h2"]),
              _bullets(report.get("dependencies", []), st["body"]),
              Paragraph("How these figures were produced", st["h2"])]

    prov = report.get("provenance", {})
    filters = prov.get("filters") or []
    filter_text = ("; ".join(f"{f.get('column')} {f.get('operator', 'eq')} {f.get('value')}"
                             for f in filters) or "none")
    rows = [
        ("Source", f"{prov.get('sheet', '')} ({prov.get('source_file', '')})"),
        ("Aggregation", f"{prov.get('aggregation', '')} of {prov.get('measure') or 'row count'}"),
        ("Grouped by", ", ".join(prov.get("group_by") or []) or "not grouped"),
        ("Filters", filter_text),
        ("Rows", f"{prov.get('rows_after_filters', '?')} of {prov.get('rows_in_view', '?')} "
                 f"after filters; {prov.get('rows_returned', '?')} returned"),
        ("Executed", prov.get("executed_at", "")),
    ]
    meta_tbl = Table([[Paragraph(f"<b>{k}</b>", st["cell"]), Paragraph(_escape(v), st["cell"])]
                      for k, v in rows],
                     colWidths=[38 * mm, PAGE_SIZE[0] - 2 * MARGIN - 38 * mm], hAlign="LEFT")
    meta_tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, GRIDLINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [
        meta_tbl, Spacer(1, 8),
        Paragraph(
            "All figures in this report were computed directly from the source data by a "
            "deterministic query. No figure was generated, estimated or adjusted by a language "
            "model; the AI selected the query and wrote the commentary only. The accompanying "
            "Markdown file records the full basis of this report - give it to an AI assistant "
            "to ask follow-up questions.", st["small"]),
    ]

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return path


def _fit_image(chart_path: Path) -> Image:
    """Scale the chart to the content width, preserving aspect ratio."""
    img = Image(str(chart_path))
    avail_w = PAGE_SIZE[0] - 2 * MARGIN
    # Kept modest so the headline, chart, commentary and a short table all share
    # page one - a manager should not have to turn the page for the answer.
    max_h = 62 * mm
    ratio = img.imageHeight / img.imageWidth
    width = min(avail_w, img.imageWidth)
    height = width * ratio
    if height > max_h:
        height, width = max_h, max_h / ratio
    img.drawWidth, img.drawHeight = width, height
    img.hAlign = "LEFT"
    return img
