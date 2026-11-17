"""Build the submission design document as a PDF with embedded diagrams.

Run inside a Python 3.12 container (ReportLab 4 requires Python 3.10+):

    docker run --rm -v "$PWD":/app -v /System/Library/Fonts:/fonts:ro \
      -w /app python:3.12-slim sh -c \
      "pip install -q reportlab pillow pypdf && python docs/build_design_pdf.py"

The script draws four labelled diagrams with Pillow, then lays out a Letter-size
PDF with ReportLab.  The generated PNGs are temporary; only the PDF is kept.
"""
from __future__ import annotations

import math
import os
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "docs" / "_design_assets"
PDF_PATH = ROOT / "docs" / "Nimbus-Cold-Chain-Design.pdf"

# ---------------------------------------------------------------------------
# Fonts and drawing helpers
# ---------------------------------------------------------------------------
FONT_CANDIDATES = [
    Path("/fonts/Arial.ttf"),
    Path("/fonts/Supplemental/Arial.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
]
BOLD_CANDIDATES = [
    Path("/fonts/Arial Bold.ttf"),
    Path("/fonts/Supplemental/Arial Bold.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
]


def _first(paths: list[Path]) -> Path:
    for path in paths:
        if path.exists():
            return path
    raise FileNotFoundError("No usable TrueType font found")


REGULAR = _first(FONT_CANDIDATES)
BOLD = _first(BOLD_CANDIDATES)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(BOLD if bold else REGULAR), size)


NAVY = (23, 43, 77)
TEAL = (0, 121, 140)
MINT = (232, 246, 245)
AMBER = (255, 244, 224)
RED = (178, 58, 72)
GREY = (92, 104, 117)
LIGHT = (218, 226, 233)
WHITE = (255, 255, 255)
INK = (33, 41, 54)
GREEN = (22, 120, 96)
PURPLE = (94, 74, 158)


def _text_size(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont):
    box = draw.textbbox((0, 0), text, font=fnt)
    return box[2] - box[0], box[3] - box[1]


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        line = words[0]
        for word in words[1:]:
            candidate = f"{line} {word}"
            if _text_size(draw, candidate, fnt)[0] <= width:
                line = candidate
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def box(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    title: str,
    body: str = "",
    fill: tuple[int, int, int] = WHITE,
    outline: tuple[int, int, int] = LIGHT,
    title_color: tuple[int, int, int] = NAVY,
    body_color: tuple[int, int, int] = GREY,
    radius: int = 18,
    title_size: int = 31,
    body_size: int = 23,
) -> None:
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=3)
    draw.rounded_rectangle((x1, y1, x2, y1 + 8), radius=radius, fill=outline)
    title_font = font(title_size, bold=True)
    body_font = font(body_size)
    lines = wrap(draw, title, title_font, x2 - x1 - 36)
    cursor = y1 + 26
    for line in lines:
        w, h = _text_size(draw, line, title_font)
        draw.text(((x1 + x2 - w) / 2, cursor), line, font=title_font, fill=title_color)
        cursor += h + 8
    if body:
        body_lines = wrap(draw, body, body_font, x2 - x1 - 44)
        cursor += 4
        for line in body_lines:
            w, h = _text_size(draw, line, body_font)
            draw.text(((x1 + x2 - w) / 2, cursor), line, font=body_font, fill=body_color)
            cursor += h + 7


def arrow(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[int, int]],
    label: str = "",
    color: tuple[int, int, int] = TEAL,
    width: int = 4,
    label_pos: tuple[int, int] | None = None,
    label_size: int = 21,
    label_color: tuple[int, int, int] = INK,
) -> None:
    draw.line(points, fill=color, width=width, joint="curve")
    (x1, y1), (x2, y2) = points[-2], points[-1]
    angle = math.atan2(y2 - y1, x2 - x1)
    length = 18
    spread = 0.55
    left = (x2 - length * math.cos(angle - spread), y2 - length * math.sin(angle - spread))
    right = (x2 - length * math.cos(angle + spread), y2 - length * math.sin(angle + spread))
    draw.polygon([(x2, y2), left, right], fill=color)
    if label:
        fnt = font(label_size, bold=True)
        lines = label.split("\n")
        widths = [_text_size(draw, line, fnt)[0] for line in lines]
        heights = [_text_size(draw, line, fnt)[1] for line in lines]
        tw = max(widths) + 22
        th = sum(heights) + 10 * (len(lines) - 1) + 14
        if label_pos is None:
            mx = (points[0][0] + points[-1][0]) // 2
            my = (points[0][1] + points[-1][1]) // 2
        else:
            mx, my = label_pos
        draw.rounded_rectangle(
            (mx - tw // 2, my - th // 2, mx + tw // 2, my + th // 2),
            radius=8,
            fill=WHITE,
            outline=LIGHT,
            width=2,
        )
        cy = my - th // 2 + 7
        for line, w, h in zip(lines, widths, heights):
            draw.text((mx - w / 2, cy), line, font=fnt, fill=label_color)
            cy += h + 10


def badge(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, color: tuple[int, int, int] = TEAL) -> None:
    fnt = font(20, bold=True)
    w, h = _text_size(draw, text, fnt)
    draw.rounded_rectangle((x, y, x + w + 24, y + h + 14), radius=12, fill=color)
    draw.text((x + 12, y + 5), text, font=fnt, fill=WHITE)


# ---------------------------------------------------------------------------
# Diagram 1: system architecture
# ---------------------------------------------------------------------------
def diagram_architecture(path: Path) -> None:
    img = Image.new("RGB", (1800, 1120), WHITE)
    d = ImageDraw.Draw(img)
    d.text((50, 36), "Figure 1. System architecture and data path", font=font(38, bold=True), fill=NAVY)

    box(d, (50, 110, 370, 270), "Truck sensors", "T-01 to T-05\none reading every ~5 min", fill=MINT, outline=TEAL)
    box(d, (470, 110, 830, 270), "Ingest API / CLI", "POST /api/v1/readings\ncoldchain replay", fill=WHITE, outline=TEAL)
    box(d, (930, 110, 1280, 270), "Validate & normalise", "required fields, numeric range,\nISO-8601 UTC timestamps", fill=WHITE, outline=TEAL)
    box(d, (1380, 110, 1750, 270), "SQLite readings", "PRIMARY KEY\n(truck_id, timestamp)\nUPSERT = retries are safe", fill=AMBER, outline=(201, 143, 44))

    box(d, (1380, 400, 1750, 580), "Pipeline", "load the truck's full\nordered history", fill=WHITE, outline=PURPLE)
    box(d, (930, 400, 1280, 580), "Detection engine", "event-time sort, dedupe,\nPENDING / ACTIVE / RESOLVED", fill=WHITE, outline=PURPLE)
    box(d, (470, 400, 830, 580), "Incident + event store", "incidents + alert_events\nUNIQUE(incident_id, event_type)", fill=AMBER, outline=(201, 143, 44))
    box(d, (50, 400, 370, 580), "FastAPI + dashboard", "GET /alerts, /incidents,\n/trucks, /stats, /events", fill=MINT, outline=TEAL)

    box(d, (930, 720, 1280, 870), "Data-quality report", "gaps, duplicates,\nout-of-order counts", fill=WHITE, outline=GREY)
    box(d, (470, 720, 830, 870), "Webhook / optional LLM", "OPENED / RESOLVED\nsummary text only", fill=WHITE, outline=GREY)
    box(d, (50, 720, 370, 870), "Ops user", "review evidence\nand act", fill=MINT, outline=TEAL)
    box(d, (1380, 720, 1750, 870), "Policy book", "defaults + per-truck\noverrides + env vars", fill=WHITE, outline=GREY)

    arrow(d, [(370, 190), (470, 190)], "readings arrive", label_pos=(420, 165))
    arrow(d, [(830, 190), (930, 190)], "validate", label_pos=(880, 165))
    arrow(d, [(1280, 190), (1380, 190)], "UPSERT", label_pos=(1330, 165))
    arrow(d, [(1565, 270), (1565, 400)], "load history", label_pos=(1620, 335))
    arrow(d, [(1380, 490), (1280, 490)], "analyse", label_pos=(1330, 465))
    arrow(d, [(930, 490), (830, 490)], "save incidents\n+ events", label_pos=(880, 465))
    arrow(d, [(470, 490), (370, 490)], "query", label_pos=(420, 465))
    arrow(d, [(1105, 580), (1105, 720)], "quality report", label_pos=(1170, 650))
    arrow(d, [(1280, 795), (830, 795)], "event payload", label_pos=(1055, 770))
    arrow(d, [(470, 795), (370, 795)], "summaries", label_pos=(420, 770))
    arrow(d, [(1565, 720), (1565, 580)], "policy", label_pos=(1620, 650))
    arrow(d, [(1565, 870), (1280, 870)], "thresholds,\nwindows", label_pos=(1420, 910))

    img.save(path)


# ---------------------------------------------------------------------------
# Diagram 2: detection state machine
# ---------------------------------------------------------------------------
def diagram_state_machine(path: Path) -> None:
    img = Image.new("RGB", (1800, 1080), WHITE)
    d = ImageDraw.Draw(img)
    d.text((50, 36), "Figure 2. Per-truck detection state machine", font=font(38, bold=True), fill=NAVY)

    box(d, (60, 400, 310, 550), "IDLE", "no open run", fill=WHITE, outline=GREY)
    box(d, (430, 400, 760, 550), "PENDING", "breach seen;\nevidence not yet\nsufficient", fill=AMBER, outline=(201, 143, 44))
    box(d, (880, 400, 1210, 550), "ACTIVE", "alert already emitted;\nincident is open", fill=(252, 232, 234), outline=RED)
    box(d, (1330, 400, 1700, 550), "RESOLVED", "recovery evidence\ncomplete", fill=MINT, outline=GREEN)
    box(d, (430, 780, 760, 940), "SUPPRESSED", "brief excursion or\ndata gap before alert", fill=(242, 242, 242), outline=GREY)
    box(d, (880, 780, 1210, 940), "OPEN (end of data)", "still warm when\nthe feed ends", fill=(252, 232, 234), outline=RED)

    arrow(d, [(310, 475), (430, 475)], "reading > 8.0 C", label_pos=(370, 445))
    arrow(d, [(595, 400), (595, 330), (790, 330), (790, 400)], "breach reading;\ngates not met", label_pos=(692, 300))
    arrow(d, [(760, 475), (880, 475)], "20 min + 3 breaches\n+ 50% coverage\n+ gap <= 15 min", label_pos=(820, 420))
    arrow(d, [(1210, 475), (1330, 475)], "2 consecutive\nreadings <= 7.5 C", label_pos=(1270, 420))
    arrow(d, [(1045, 400), (1045, 330), (1110, 330), (1110, 400)], "breach / hysteresis band:\nupdate evidence, no new alert", label_pos=(1078, 285))
    arrow(d, [(880, 525), (760, 525), (760, 780)], "<= 7.5 C before gates\n(brief excursion)", label_pos=(800, 690))
    arrow(d, [(880, 550), (820, 550), (820, 780)], "gap > 15 min\n(data_gap)", label_pos=(870, 690))
    arrow(d, [(1045, 550), (1045, 780)], "end of data;\nstill warm", label_pos=(1110, 670))
    arrow(d, [(1330, 525), (1240, 525), (1240, 640), (595, 640), (595, 550)], "new breach starts\na new incident", label_pos=(920, 615))
    arrow(d, [(595, 940), (595, 980), (170, 980), (170, 550)], "new breach after\ndata resumes", label_pos=(380, 1005))
    arrow(d, [(880, 860), (820, 860), (820, 940)], "gap > 15 min while\nactive: stay open", label_pos=(760, 1015))

    img.save(path)


# ---------------------------------------------------------------------------
# Diagram 3: out-of-order / late data sequence
# ---------------------------------------------------------------------------
def diagram_sequence(path: Path) -> None:
    img = Image.new("RGB", (1800, 1050), WHITE)
    d = ImageDraw.Draw(img)
    d.text((50, 36), "Figure 3. Out-of-order data: event-time recomputation", font=font(38, bold=True), fill=NAVY)

    lanes = [
        (50, "Sensor T-05"),
        (480, "Ingest API / CLI"),
        (910, "SQLite readings"),
        (1340, "Pipeline + engine"),
    ]
    for x, name in lanes:
        box(d, (x, 110, x + 360, 190), name, "", fill=MINT, outline=TEAL, title_size=27)
        d.line([(x + 180, 190), (x + 180, 960)], fill=LIGHT, width=3)

    steps = [
        (300, 0, 1, "08:30, 08:10, 08:20 ...\n(shuffled arrival)"),
        (390, 1, 2, "validate; UPSERT by\n(truck_id, timestamp)"),
        (490, 2, 1, "SELECT all T-05\nreadings"),
        (590, 1, 3, "sort by event time;\ncollapse duplicates"),
        (700, 3, 2, "recompute; incident_id =\nhash(T-05 + 08:10)"),
        (810, 2, 0, "INSERT OR IGNORE\n(incident_id, event_type)"),
        (900, 0, 1, "same alert; no duplicate\nOPENED event"),
    ]
    for y, src, dst, label in steps:
        x1 = lanes[src][0] + 180
        x2 = lanes[dst][0] + 180
        arrow(d, [(x1, y), (x2, y)], label, label_pos=((x1 + x2) // 2, y - 34), label_size=20)

    d.text((50, 1000), "The same recomputation path handles replay, retries, and late readings.", font=font(21), fill=GREY)
    img.save(path)


# ---------------------------------------------------------------------------
# Diagram 4: golden dataset timeline
# ---------------------------------------------------------------------------
def diagram_timeline(path: Path) -> None:
    img = Image.new("RGB", (1800, 880), WHITE)
    d = ImageDraw.Draw(img)
    d.text((50, 36), "Figure 4. Golden dataset: three incidents, two blips, one quiet truck", font=font(34, bold=True), fill=NAVY)

    left, right, y = 90, 1710, 330
    d.line([(left, y), (right, y)], fill=NAVY, width=5)
    for i, minute in enumerate(range(0, 51, 5)):
        x = left + (right - left) * i / 10
        d.line([(x, y - 12), (x, y + 12)], fill=NAVY, width=3)
        d.text((x - 28, y + 22), f"08:{minute:02d}", font=font(20), fill=GREY)

    # T-01 above the line
    d.text((90, 95), "T-01 / T-05: sustained", font=font(25, bold=True), fill=RED)
    box(d, (200, 140, 520, 235), "08:10 first breach", "PENDING starts", fill=AMBER, outline=(201, 143, 44), title_size=24, body_size=20)
    box(d, (660, 140, 1040, 235), "08:30 gates met", "OPENED (one alert)", fill=(252, 232, 234), outline=RED, title_size=24, body_size=20)
    box(d, (1080, 140, 1450, 235), "08:35-08:40 warm", "update only; no re-alert", fill=WHITE, outline=GREY, title_size=24, body_size=20)
    box(d, (1480, 140, 1750, 235), "08:50 resolved", "RESOLVED (one event)", fill=MINT, outline=GREEN, title_size=24, body_size=20)
    for x, label in [(360, "start"), (850, "alert"), (1265, "update"), (1615, "clear")]:
        arrow(d, [(x, 235), (x, y - 20)], label, label_pos=(x, 275), label_size=18)

    # T-02 below
    d.text((90, 520), "T-02: blip", font=font(25, bold=True), fill=GREY)
    box(d, (200, 560, 520, 650), "08:10 8.9 C", "one spike", fill=WHITE, outline=GREY, title_size=24, body_size=20)
    box(d, (660, 560, 1040, 650), "08:15 6.5 C", "SUPPRESSED: brief_excursion", fill=(242, 242, 242), outline=GREY, title_size=24, body_size=20)
    arrow(d, [(360, 650), (360, y + 20)], "spike", label_pos=(360, 690), label_size=18)
    arrow(d, [(850, 650), (850, y + 20)], "cool", label_pos=(850, 690), label_size=18)

    # T-04 below
    d.text((90, 740), "T-04: intermittent + gap", font=font(25, bold=True), fill=PURPLE)
    box(d, (200, 780, 620, 860), "08:05-08:20 blips", "suppressed as brief", fill=WHITE, outline=GREY, title_size=23, body_size=19)
    box(d, (700, 780, 1200, 860), "08:25-08:45 sustained", "20 min; 10-min gap; still open", fill=AMBER, outline=(201, 143, 44), title_size=23, body_size=19)
    arrow(d, [(410, 780), (410, y + 20)], "two blips", label_pos=(410, 715), label_size=18)
    arrow(d, [(950, 780), (950, y + 20)], "alert", label_pos=(950, 715), label_size=18)

    img.save(path)


# ---------------------------------------------------------------------------
# PDF layout
# ---------------------------------------------------------------------------
def build_pdf() -> int:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        BaseDocTemplate,
        Frame,
        HRFlowable,
        Image as RLImage,
        KeepTogether,
        NextPageTemplate,
        PageBreak,
        PageTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )

    PAGE_W, PAGE_H = LETTER
    MARGIN_X = 0.72 * inch
    MARGIN_TOP = 0.64 * inch
    MARGIN_BOTTOM = 0.62 * inch
    CONTENT_W = PAGE_W - 2 * MARGIN_X

    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "Body",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9.4,
        leading=13.6,
        textColor=colors.HexColor("#212936"),
        spaceAfter=6,
    )
    small = ParagraphStyle(
        "Small",
        parent=body,
        fontSize=8.2,
        leading=11,
        textColor=colors.HexColor("#5c6875"),
    )
    h1 = ParagraphStyle(
        "H1",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=17,
        leading=21,
        textColor=colors.HexColor("#172b4d"),
        spaceBefore=18,
        spaceAfter=4,
        keepWithNext=1,
    )
    h2 = ParagraphStyle(
        "H2",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=11.5,
        leading=15,
        textColor=colors.HexColor("#00798c"),
        spaceBefore=10,
        spaceAfter=4,
        keepWithNext=1,
    )
    bullet = ParagraphStyle(
        "Bullet",
        parent=body,
        leftIndent=14,
        bulletIndent=4,
        spaceAfter=3,
    )
    code = ParagraphStyle(
        "Code",
        parent=body,
        fontName="Courier",
        fontSize=7.6,
        leading=10,
        textColor=colors.HexColor("#172b4d"),
    )
    caption = ParagraphStyle(
        "Caption",
        parent=small,
        alignment=TA_CENTER,
        fontName="Helvetica-Oblique",
        spaceBefore=3,
        spaceAfter=10,
    )
    table_cell = ParagraphStyle(
        "TableCell",
        parent=body,
        fontSize=7.9,
        leading=10.0,
        spaceAfter=0,
    )
    table_head = ParagraphStyle(
        "TableHead",
        parent=table_cell,
        fontName="Helvetica-Bold",
        textColor=colors.white,
    )
    cover_title = ParagraphStyle(
        "CoverTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=34,
        leading=39,
        textColor=colors.HexColor("#172b4d"),
        alignment=TA_LEFT,
        spaceAfter=8,
    )
    cover_sub = ParagraphStyle(
        "CoverSub",
        parent=body,
        fontSize=13,
        leading=18,
        textColor=colors.HexColor("#00798c"),
        spaceAfter=16,
    )
    cover_meta = ParagraphStyle(
        "CoverMeta",
        parent=small,
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#5c6875"),
    )

    def P(text: str, style=body) -> Paragraph:
        return Paragraph(text, style)

    def B(text: str) -> Paragraph:
        return Paragraph(text, bullet, bulletText="•")

    def H(text: str, level: int = 1) -> Paragraph:
        return Paragraph(text, h1 if level == 1 else h2)

    def rule() -> HRFlowable:
        return HRFlowable(width="100%", thickness=1.2, color=colors.HexColor("#dae2e9"), spaceBefore=2, spaceAfter=8)

    def callout(title: str, text: str, fill="#e8f6f5", border="#00798c") -> Table:
        inner = [
            [P(f"<b>{title}</b>", ParagraphStyle("CalloutTitle", parent=body, textColor=colors.HexColor(border), fontSize=9.5))],
            [P(text, ParagraphStyle("CalloutBody", parent=body, fontSize=8.7, leading=12.2))],
        ]
        table = Table(inner, colWidths=[CONTENT_W - 0.3 * inch])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(fill)),
                    ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor(border)),
                    ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(border)),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )
        return table

    def table(data: list[list[str]], widths: list[float], header: bool = True) -> Table:
        rows = []
        for r_index, row in enumerate(data):
            cells = []
            for cell in row:
                style = table_head if (header and r_index == 0) else table_cell
                cells.append(P(cell, style))
            rows.append(cells)
        t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
        style_commands = [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#dae2e9")),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]
        if header:
            style_commands += [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#172b4d")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ]
            for i in range(1, len(data)):
                if i % 2 == 0:
                    style_commands.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f5f8fa")))
        t.setStyle(TableStyle(style_commands))
        return t

    def figure(path: Path, width: float = CONTENT_W, caption_text: str = "") -> list:
        with Image.open(path) as im:
            w, h = im.size
        height = width * h / w
        items = [RLImage(str(path), width=width, height=height)]
        if caption_text:
            items.append(P(caption_text, caption))
        return [KeepTogether(items)]

    # Page templates --------------------------------------------------------
    def cover_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#172b4d"))
        canvas.rect(0, PAGE_H - 0.55 * inch, PAGE_W, 0.55 * inch, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#00798c"))
        canvas.rect(0, PAGE_H - 0.62 * inch, PAGE_W, 0.07 * inch, fill=1, stroke=0)
        canvas.restoreState()

    def later_pages(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#dae2e9"))
        canvas.setLineWidth(0.7)
        canvas.line(MARGIN_X, 0.58 * inch, PAGE_W - MARGIN_X, 0.58 * inch)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#5c6875"))
        canvas.drawString(MARGIN_X, 0.40 * inch, "Nimbus Freight  |  Cold-Chain Alerting  |  Design document")
        canvas.drawRightString(PAGE_W - MARGIN_X, 0.40 * inch, f"Page {doc.page}")
        canvas.restoreState()

    doc = BaseDocTemplate(
        str(PDF_PATH),
        pagesize=LETTER,
        leftMargin=MARGIN_X,
        rightMargin=MARGIN_X,
        topMargin=MARGIN_TOP,
        bottomMargin=MARGIN_BOTTOM,
        title="Nimbus Freight Cold-Chain Alerting - Design Document",
        author="Bharathamani",
        subject="Sustained-excursion detection, data quality, and idempotent alerting",
    )
    frame = Frame(MARGIN_X, MARGIN_BOTTOM, CONTENT_W, PAGE_H - MARGIN_TOP - MARGIN_BOTTOM, id="body")
    cover_frame = Frame(MARGIN_X, MARGIN_BOTTOM, CONTENT_W, PAGE_H - MARGIN_TOP - MARGIN_BOTTOM, id="cover")
    doc.addPageTemplates(
        [
            PageTemplate(id="Cover", frames=[cover_frame], onPage=cover_page),
            PageTemplate(id="Body", frames=[frame], onPage=later_pages),
        ]
    )

    story = []

    # ------------------------------------------------------------------ cover
    story.append(Spacer(1, 0.55 * inch))
    story.append(P("NIMBUS FREIGHT  /  CASE STUDY", ParagraphStyle("Kicker", parent=small, fontName="Helvetica-Bold", textColor=colors.HexColor("#00798c"), fontSize=9, tracking=1.2)))
    story.append(Spacer(1, 0.25 * inch))
    story.append(P("Cold-Chain Alerting", cover_title))
    story.append(P("Sustained-excursion detection, data quality, and idempotent alerting", cover_sub))
    story.append(rule())
    story.append(Spacer(1, 0.15 * inch))
    story.append(P("Design document", ParagraphStyle("CoverLabel", parent=body, fontName="Helvetica-Bold", fontSize=11, textColor=colors.HexColor("#172b4d"))))
    story.append(P("Prepared for the Nimbus Freight FDE case study. This document explains the problem, the delivered design, the decisions behind the alert rule, and the trade-offs and gaps that remain.", body))
    story.append(Spacer(1, 0.3 * inch))
    story.append(
        callout(
            "Result on the provided dataset",
            "<b>3 sustained incidents</b> (T-01, T-04, T-05), <b>2 suppressed blips</b> (T-02 and the first T-04 run), and <b>1 quiet truck</b> (T-03). A second replay produces <b>0 new events</b>.",
        )
    )
    story.append(Spacer(1, 0.55 * inch))
    story.append(
        table(
            [
                ["Submission", "https://github.com/Bharathamani/FDE"],
                ["Author", "Bharathamani"],
                ["Date", "November 2026"],
                ["Companion documents", "README.md, DECISIONS.md, SOLUTION_DESIGN.md"],
            ],
            [1.55 * inch, CONTENT_W - 1.55 * inch],
            header=False,
        )
    )
    story.append(NextPageTemplate("Body"))
    story.append(PageBreak())

    # ------------------------------------------------------ executive summary
    story.append(H("1. Executive summary"))
    story.append(P("Nimbus Freight receives a temperature reading from each reefer truck every few minutes. The current alerting fires on individual threshold crossings, so a single probe spike creates noise while a real sustained excursion can be lost in the same stream. The ops team has started ignoring alerts. The required output is narrower and more useful: <b>one incident per genuine episode, with enough evidence to act on, and no re-alerting while that episode continues.</b>"))
    story.append(P("The delivered solution is a deterministic, event-time alerting service. Readings are validated and upserted into SQLite, each truck's history is sorted and deduplicated, and a small state machine promotes a run from <b>PENDING</b> to <b>ACTIVE</b> only when the evidence is sustained. The incident and its lifecycle events are stored with deterministic identity and a unique event key, so replay, retries, and late readings cannot create duplicate alerts. A FastAPI service, an ops dashboard, a CLI, Docker packaging, and a test suite make the result runnable end to end."))
    story.append(Spacer(1, 4))
    story.append(
        table(
            [
                ["Truck", "Outcome", "Evidence"],
                ["T-01", "Alert, resolved", "30 min above 8.0 C, peak 9.6 C, 7/7 expected samples, cleared at 08:50."],
                ["T-02", "Suppressed", "One 8.9 C spike; back to 6.5 C five minutes later."],
                ["T-03", "No breach", "Peak 7.0 C."],
                ["T-04", "Alert, still open", "20 min sustained run, 10-minute gap inside it; two earlier blips suppressed."],
                ["T-05", "Alert, resolved", "Same shape as T-01, but readings arrive shuffled; event-time sort restores the result."],
            ],
            [0.65 * inch, 1.25 * inch, CONTENT_W - 1.90 * inch],
        )
    )
    story.append(Spacer(1, 6))
    story.append(H("1.1 What is included", 2))
    for text in [
        "<b>Detection engine</b> - pure, deterministic, event-time state machine with explicit evidence gates.",
        "<b>Idempotent ingest</b> - SQLite upsert on <font name='Courier'>(truck_id, timestamp)</font>; recomputation after late data.",
        "<b>Incident-level alerting</b> - deterministic incident ID and <font name='Courier'>UNIQUE(incident_id, event_type)</font>.",
        "<b>Data-quality reporting</b> - out-of-order counts, duplicates, missing samples, and every gap with its tolerance verdict.",
        "<b>Ops surface</b> - FastAPI endpoints, a single-page dashboard, CLI replay/alerts/stats/what-if, and an optional webhook.",
        "<b>Deployment and tests</b> - Dockerfile, Compose, health check, 23 passing tests, and a golden-dataset replay.",
    ]:
        story.append(B(text))

    story.append(Spacer(1, 0.16 * inch))

    # ---------------------------------------------------------- requirements
    story.append(H("2. Requirements and where they live"))
    story.append(P("The brief deliberately leaves the policy open. The implementation makes each decision explicit and testable. This table is the map a reviewer can use to jump from a requirement to the file that owns it."))
    story.append(Spacer(1, 2))
    story.append(
        table(
            [
                ["Requirement", "How it is met", "Primary file(s)"],
                ["Detect genuinely sustained excursions per truck", "Event-time state machine with duration, minimum breach readings, coverage, and gap gates.", "src/coldchain/engine.py"],
                ["Suppress brief blips", "A run that returns to <= 7.5 C before the gates is recorded as SUPPRESSED with a reason code, not alerted.", "src/coldchain/engine.py"],
                ["Do not re-alert an ongoing issue", "One deterministic incident ID; one OPENED event; later readings update evidence only.", "src/coldchain/store.py"],
                ["Handle missing readings and gaps", "Gaps are measured; a gap > 15 min breaks PENDING but never silently resolves ACTIVE; missing data is not recovery.", "src/coldchain/engine.py, src/coldchain/quality.py"],
                ["Handle out-of-order readings", "Sort by event time, collapse duplicate timestamps, recompute the affected truck, keep incident identity stable.", "src/coldchain/engine.py, src/coldchain/pipeline.py"],
                ["Make policy configurable", "Typed Policy dataclass, environment overrides, JSON file, per-truck overrides.", "src/coldchain/config.py"],
                ["Expose an ops surface", "FastAPI ingest/query/dashboard endpoints plus a single-page dashboard and CLI.", "src/coldchain/api.py, src/coldchain/cli.py, src/coldchain/static/index.html"],
                ["Be runnable and deployable", "Package install, Dockerfile, Compose, health check, seeded demo.", "pyproject.toml, Dockerfile, docker-compose.yml"],
                ["Document assumptions and trade-offs", "Decision register plus this design document.", "DECISIONS.md, docs/Nimbus-Cold-Chain-Design.pdf"],
                ["Optional LLM summary (bonus)", "Template summary always works; optional OpenAI-compatible rewrite; LLM never changes alert decisions.", "src/coldchain/summarizer.py"],
            ],
            [1.55 * inch, 3.45 * inch, CONTENT_W - 5.0 * inch],
        )
    )
    story.append(Spacer(1, 8))
    story.append(H("2.1 Key files (15)", 2))
    story.append(P("The reviewer reads a limited number of files. These are the ones that carry the design; generated data, the dashboard image, and the starter kit are intentionally not in this list."))
    key_files = [
        ("src/coldchain/engine.py", "Sort, dedupe, state machine, gates, severity."),
        ("src/coldchain/quality.py", "Gaps, duplicates, out-of-order, missing samples."),
        ("src/coldchain/store.py", "Schema, upsert, incidents, event dedupe."),
        ("src/coldchain/pipeline.py", "Persist, recompute affected trucks, save events."),
        ("src/coldchain/models.py", "Reading, Incident, BreachSample, DataGap, result."),
        ("src/coldchain/config.py", "Policy defaults, validation, per-truck overrides."),
        ("src/coldchain/api.py", "FastAPI ingest/query endpoints and dashboard data."),
        ("src/coldchain/cli.py", "replay, alerts, stats, what-if, serve."),
        ("src/coldchain/summarizer.py", "Deterministic summary, optional LLM rewrite."),
        ("src/coldchain/notify.py", "Best-effort Slack-compatible webhook."),
        ("src/coldchain/dataset.py", "Load and validate readings from the seed file."),
        ("src/coldchain/timeutil.py", "UTC parsing, formatting, elapsed minutes."),
        ("tests/test_engine.py", "Golden dataset, gaps, hysteresis, recovery, replay."),
        ("tests/test_quality.py", "Out-of-order, duplicate, missing-sample, gap checks."),
        ("tests/test_api.py", "Ingest, alerts, dashboard, replay, health."),
    ]
    half = (CONTENT_W - 0.12 * inch) / 2
    key_rows = [["File", "File"]]
    for i in range(0, len(key_files), 2):
        left_name, left_desc = key_files[i]
        left = [
            P(f"<font name='Courier' size='7.3'>{left_name}</font>", table_cell),
            P(left_desc, ParagraphStyle("KeyDesc", parent=table_cell, fontSize=7.3, leading=9.2, textColor=colors.HexColor("#5c6875"))),
        ]
        if i + 1 < len(key_files):
            right_name, right_desc = key_files[i + 1]
            right = [
                P(f"<font name='Courier' size='7.3'>{right_name}</font>", table_cell),
                P(right_desc, ParagraphStyle("KeyDesc2", parent=table_cell, fontSize=7.3, leading=9.2, textColor=colors.HexColor("#5c6875"))),
            ]
        else:
            right = [P(""), P("")]
        key_rows.append([left, right])
    key_table = Table(key_rows, colWidths=[half, half], repeatRows=1)
    key_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#dae2e9")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#172b4d")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("BACKGROUND", (0, 1), (0, -1), colors.white),
                ("BACKGROUND", (1, 1), (1, -1), colors.HexColor("#f5f8fa")),
            ]
        )
    )
    story.append(key_table)

    story.append(Spacer(1, 0.16 * inch))

    # ---------------------------------------------------------- architecture
    story.append(H("3. Architecture"))
    story.append(P("The system is deliberately split into a pure detection core, a durable store, and thin surfaces. The engine has no database or wall-clock dependency, so the same logic runs in replay, tests, and late-arriving-data recomputation. The store owns durability and deduplication; the pipeline owns orchestration; the API and CLI are adapters."))
    story.extend(figure(ASSET_DIR / "architecture.png", caption_text="Figure 1. End-to-end architecture. Every box and arrow is labelled. Readings move left to right across the top; processing and serving move right to left on the lower rows."))
    story.append(H("3.1 Component responsibilities", 2))
    story.append(
        table(
            [
                ["Component", "Responsibility", "Why it is separate"],
                ["Ingest API / CLI", "Accept one reading, a batch, or a dataset replay.", "Keeps transport concerns out of the engine."],
                ["Validation + normalisation", "Required fields, finite numeric range, humidity range, UTC timestamps.", "Bad data fails at the edge, not inside detection."],
                ["SQLite readings", "Durable upsert keyed by (truck_id, timestamp).", "Retries and replay are naturally idempotent."],
                ["Pipeline", "Load the affected truck's full history and recompute it.", "Late data is handled by recomputation, not by mutable in-memory state."],
                ["Detection engine", "Sort, dedupe, walk PENDING/ACTIVE/RESOLVED, apply policy gates.", "Pure and line-by-line testable."],
                ["Incident + event store", "Persist incidents and lifecycle events with a unique event key.", "Deduplication and audit live in one place."],
                ["FastAPI + dashboard", "Ingest, query, stats, policy, events, dashboard data.", "Thin read/write adapter over the store."],
                ["Webhook / LLM", "Best-effort notification and optional summary rewrite.", "Optional side effects never block ingestion."],
            ],
            [1.45 * inch, 3.25 * inch, CONTENT_W - 4.70 * inch],
        )
    )
    story.append(Spacer(1, 4))
    story.append(callout("Why event time, not arrival time", "T-05 in the supplied dataset is T-01's shape with shuffled arrival order. Arrival-time evaluation would produce a different, wrong answer. The engine sorts each truck's readings before evaluating, so T-05 produces the same incident as T-01."))

    story.append(Spacer(1, 0.16 * inch))

    # ------------------------------------------------------ detection logic
    story.append(H("4. Detection logic"))
    story.append(P("A truck starts a candidate run when a reading is strictly above the configured ceiling. The run is only promoted to an alert when four independent gates are satisfied. The state machine is shown in Figure 2; the exact rule is below."))
    story.extend(figure(ASSET_DIR / "state_machine.png", caption_text="Figure 2. Per-truck state machine. Every transition and guard is labelled. PENDING is evidence gathering; ACTIVE means the alert has already been emitted; RESOLVED and SUPPRESSED are terminal for that episode."))
    story.append(H("4.1 The alert rule", 2))
    for text in [
        "<b>Breach:</b> <font name='Courier'>temperature_c &gt; 8.0 C</font>. Exactly 8.0 C is not a breach.",
        "<b>Sustained:</b> elapsed event time from the first to the latest breach is at least <b>20 minutes</b>.",
        "<b>Evidence count:</b> at least <b>3</b> actual breach readings.",
        "<b>Coverage:</b> breach readings cover at least <b>50%</b> of the samples expected at the 5-minute nominal cadence.",
        "<b>Continuity:</b> no gap inside the run exceeds <b>15 minutes</b>.",
        "<b>Recovery:</b> an active incident resolves after <b>2 consecutive readings at or below 7.5 C</b>. The 7.5-8.0 C band is hysteresis: not a new breach, not recovery.",
        "<b>Severity:</b> CRITICAL when peak exceeds the threshold by 4 C or exposure reaches 60 min; HIGH at 2 C or 30 min; otherwise MEDIUM.",
    ]:
        story.append(B(text))
    story.append(Spacer(1, 3))
    story.append(callout("Why duration alone is not enough", "Two spike readings 30 minutes apart would satisfy a naive elapsed-time rule. Requiring three real breach readings, 50% coverage, and a bounded gap forces the data to support the claim that the excursion was continuous. The engine is conservative on purpose: a false alert is worse than waiting for one more reading."))

    story.append(Spacer(1, 0.16 * inch))

    # ------------------------------------------------------- data quality
    story.append(H("5. Data quality and out-of-order handling"))
    story.append(P("The starter kit assumes sorted input and computes duration from reading counts. The supplied data violates both assumptions: T-05 arrives shuffled, and T-04 is missing a sample. The solution repairs only what it can defend and makes every repair visible."))
    story.extend(figure(ASSET_DIR / "sequence.png", caption_text="Figure 3. Late or out-of-order readings. Every lane and arrow is labelled. The API upserts by key, the pipeline reloads the truck's history, the engine recomputes in event time, and the store ignores a duplicate lifecycle event."))
    story.append(H("5.1 Three repairs, all reported", 2))
    story.append(
        table(
            [
                ["Repair", "Behaviour", "What is not done"],
                ["Order", "Group by truck and sort by timestamp before evaluation.", "No arrival-time decisions; no silent reordering of the output."],
                ["Duplicates", "Collapse equal timestamps; last value seen wins (upsert semantics).", "No duplicate incidents; no hidden averaging."],
                ["Gaps", "Measure every interval above the 5-minute cadence; classify against the 15-minute tolerance.", "No interpolation; no invented readings."],
            ],
            [0.95 * inch, 3.55 * inch, CONTENT_W - 4.50 * inch],
        )
    )
    story.append(Spacer(1, 4))
    story.append(P("The report is computed from the raw input before the engine's canonical sort. It includes raw vs. unique readings, duplicate timestamps, out-of-order readings, expected vs. missing samples for the observed span, and every gap with its tolerance verdict. <font name='Courier'>coldchain replay</font> prints it as a DATA QUALITY section; the JSON payload exposes it as <font name='Courier'>data_quality</font>."))
    story.append(H("5.2 What the supplied dataset shows", 2))
    story.append(P("The raw file holds <b>54 readings</b> across five trucks with <b>54 unique timestamps</b>. There are <b>5 out-of-order readings, all on T-05</b>, and <b>1 missing expected sample</b> (T-04 at 08:35, leaving a 10-minute interval). <b>No gap exceeds the 15-minute tolerance</b>; the T-04 gap is inside policy and is still reported."))
    story.append(Spacer(1, 4))
    story.append(callout("Gap policy: pending vs. active", "A gap > 15 min breaks a PENDING run because continuity cannot be proven. A gap > 15 min never silently resolves an ACTIVE incident: the incident stays open, the gap is recorded, and fresh recovery evidence is required after data returns. Missing data is not cold cargo."))

    story.append(Spacer(1, 0.16 * inch))

    # ---------------------------------------------------- alert lifecycle
    story.append(H("6. Alert lifecycle and idempotency"))
    story.append(P("The original problem was alert fatigue. The unit of output is therefore an incident, not a reading. An incident is identified by <font name='Courier'>INC-&lt;sha1(truck_id + episode start)[:12]&gt;</font>, so the same episode always has the same identity even when it is recomputed after late data."))
    story.extend(figure(ASSET_DIR / "timeline.png", caption_text="Figure 4. Golden dataset timeline. Every event and arrow is labelled. T-01/T-05 open once and resolve once; T-02 is suppressed; T-04's intermittent run remains open."))
    story.append(H("6.1 Event contract", 2))
    story.append(
        table(
            [
                ["Event", "When", "Cardinality"],
                ["OPENED", "The evidence gates are first met.", "Exactly one per incident; replay cannot insert a second."],
                ["RESOLVED", "Two consecutive readings at or below 7.5 C.", "At most one per incident."],
                ["SUPERSEDED", "A later recomputation invalidates an episode (for example, a late reading shows a spike was not real).", "At most one; the audit row is kept."],
            ],
            [0.95 * inch, 3.55 * inch, CONTENT_W - 4.50 * inch],
        )
    )
    story.append(Spacer(1, 4))
    story.append(P("The <font name='Courier'>alert_events</font> table has a unique constraint on <font name='Courier'>(incident_id, event_type)</font>. Inserts use <font name='Courier'>INSERT OR IGNORE</font>, so retries, replay, and recomputation are safe. The store updates evidence on an existing incident but only inserts a lifecycle event once. Running the same replay twice produces <b>0 new events</b>."))
    story.append(callout("Late data changes evidence, not identity", "A late reading triggers recomputation of that truck from persisted history. If the recomputation produces the same episode, the incident is updated in place. If it invalidates the episode, the incident is marked SUPERSEDED rather than deleted, preserving the audit trail."))

    story.append(Spacer(1, 0.16 * inch))

    # ------------------------------------------------------ API / deployment
    story.append(H("7. API, dashboard, and deployment"))
    story.append(P("The service is intentionally thin: it validates input, delegates to the pipeline, and reads the store. The same package exposes a CLI for replay and investigation. The dashboard is a single static page served by FastAPI, so there is no separate front-end build."))
    story.append(H("7.1 HTTP surface", 2))
    story.append(
        table(
            [
                ["Endpoint", "Purpose"],
                ["POST /api/v1/readings", "Ingest one reading; returns accepted reading and any new events."],
                ["POST /api/v1/readings/batch", "Ingest a batch; useful for sensor gateways."],
                ["GET /api/v1/alerts?status=open", "Alert feed with incident evidence and summaries."],
                ["GET /api/v1/incidents?status=all", "Incident list, including resolved and superseded."],
                ["GET /api/v1/incidents/{id}", "One incident with breach samples and gaps."],
                ["GET /api/v1/trucks, /stats, /policy, /events", "Fleet status, counters, active policy, lifecycle feed."],
                ["GET /api/v1/dashboard", "Aggregated payload for the single-page dashboard."],
                ["POST /api/v1/demo/replay", "Demo-only replay; disabled unless COLDCHAIN_DEMO_MODE=true."],
                ["GET /healthz", "Liveness probe used by Docker."],
            ],
            [2.45 * inch, CONTENT_W - 2.45 * inch],
        )
    )
    story.append(H("7.2 Run and test", 2))
    story.append(P("Local, Python 3.10+:", small))
    story.append(P("python -m venv .venv &amp;&amp; source .venv/bin/activate<br/>pip install -r requirements-dev.txt &amp;&amp; pip install -e .<br/>coldchain replay --reset --data data/sensor_readings.json<br/>coldchain serve --demo --port 8000<br/>pytest -q", code))
    story.append(P("Docker:", small))
    story.append(P("docker compose up --build -d<br/>docker compose logs -f coldchain<br/># dashboard: http://localhost:8000", code))
    story.append(P("The image runs as a non-root user, persists SQLite in the <font name='Courier'>coldchain-state</font> volume, seeds the demo dataset on first boot, and has a health check against <font name='Courier'>/healthz</font>. Configuration is environment-driven; the policy file supports per-truck overrides. The optional webhook is best effort and the optional LLM summary always falls back to a deterministic template."))

    story.append(Spacer(1, 0.16 * inch))

    # --------------------------------------------------------------- tests
    story.append(H("8. Testing and verification"))
    story.append(P("The repository contains 23 tests. They run in a Python 3.12 container with <font name='Courier'>pytest -q</font>; the same suite also runs through <font name='Courier'>python run_tests.py</font> when pytest is unavailable. The tests are designed around the failure modes in the brief, not just the happy path."))
    story.append(
        table(
            [
                ["Test area", "What it proves"],
                ["Golden dataset", "T-01, T-04, T-05 alert; T-02 suppressed; T-03 quiet; expected counts and evidence."],
                ["Out-of-order and duplicates", "T-05 is restored to the T-01 result; duplicate timestamps collapse to one reading."],
                ["Missing readings and gaps", "A tolerated gap does not erase a real run; a large gap breaks PENDING; an active incident is not silently resolved."],
                ["Hysteresis and recovery", "The 7.5-8.0 C band keeps an active incident open; two clear readings resolve it."],
                ["Policy overrides", "Per-truck thresholds and durations change the outcome without code changes."],
                ["Idempotency", "Replaying the same dataset produces zero new events; late ingest updates the same incident."],
                ["Data quality", "Out-of-order, duplicate, missing-sample, and gap counts match the supplied data."],
                ["API and demo", "Ingest, alert feed, dashboard payload, demo replay, and health endpoints work."],
            ],
            [1.85 * inch, CONTENT_W - 1.85 * inch],
        )
    )
    story.append(Spacer(1, 6))
    story.append(callout("Verified result", "In the Python 3.12 test container: <b>23 passed</b>. The golden replay reports 3 incidents, 2 suppressed episodes, and 1 truck with no breach. A second replay produces 0 new events."))

    story.append(Spacer(1, 0.16 * inch))

    # ---------------------------------------------------------- trade-offs
    story.append(H("9. Trade-offs"))
    story.append(P("The design optimises for explainability, correctness on messy data, and a small surface that an FDE can defend line by line. That choice has costs; they are listed here rather than hidden."))
    story.append(
        table(
            [
                ["Choice", "Benefit", "Cost / when to change"],
                ["Rule-based detection", "Explainable to ops, testable, auditable; no labelled training data required.", "Cannot catch novel patterns. Add ML only after enough labelled incidents exist, and keep the rule as a baseline."],
                ["Event-time recomputation per truck", "Late and out-of-order data are correct; the implementation is small.", "Recomputes a truck's history on ingest. At high throughput, use event-time windows, watermarks, and incremental state."],
                ["Elapsed-time + evidence gates", "Rejects sparse spikes and single-reading blips; transparent to verify.", "Not a time-weighted exposure integral. A v2 could add degree-minutes and cargo tolerance."],
                ["SQLite", "One-command reproducible demo; durable deduplication; no external service.", "Single node; not a production fleet store. Move to a managed event/time-series store with retention and replication."],
                ["15-minute gap tolerance", "Covers one missed 5-minute sample plus jitter; does not call a blackout continuous.", "A silent truck stays open rather than raising a separate stale-sensor alert. Add sensor-health alerting."],
                ["Best-effort webhook", "Notification failures do not fail ingestion.", "No durable outbox or retry. Add an outbox and delivery worker for production."],
                ["Optional LLM summary", "Better operator prose when available; deterministic fallback.", "External dependency is optional; the model never changes alert decisions."],
            ],
            [1.35 * inch, 2.65 * inch, CONTENT_W - 4.0 * inch],
        )
    )

    story.append(Spacer(1, 0.16 * inch))

    # ---------------------------------------------------------- gaps / next
    story.append(H("10. Gaps and what I would do next"))
    story.append(P("The delivered system is complete for the case study, but it is not a production fleet platform. The most important gaps are operational rather than algorithmic."))
    for text in [
        "<b>No stale-sensor alert.</b> A truck that goes silent keeps an active incident open and records gaps, but does not raise a separate 'sensor offline' incident. This is the next reliability feature; silence is not safety.",
        "<b>No cargo metadata.</b> The seed data has no cargo type, customer, or setpoint. The default 8.0 C policy is a defensible starting assumption; production needs per-cargo and per-customer policies.",
        "<b>No acknowledgement or escalation workflow.</b> The system produces trustworthy incidents; claiming, assigning, escalating, and closing them is a separate product surface.",
        "<b>Single-node storage and recompute.</b> SQLite and per-truck recomputation are appropriate for this fleet and demo. A high-throughput deployment needs Kafka/Kinesis, event-time windows, a time-series store, and a durable notification outbox.",
        "<b>No authentication.</b> The API is intended to sit behind the customer's gateway; tenant isolation, auth, audit logs, and retention controls are production work.",
        "<b>No time-weighted exposure or ML.</b> The rule is deliberately conservative and explainable. Degree-minutes, door-open events, and compressor telemetry are natural v2 inputs; ML should wait for labelled data.",
    ]:
        story.append(B(text))
    story.append(Spacer(1, 4))
    story.append(H("10.1 What I would do with more time", 2))
    story.append(P("First, add sensor-health and staleness alerting, because a silent truck is the most dangerous failure mode the current design only records. Second, add per-cargo policy and a backtesting harness against months of labelled incidents, so the 8.0 C / 20 min defaults become evidence-based rather than assumed. Third, replace SQLite and per-truck recomputation with an event-time stream architecture and a durable notification outbox. Fourth, add acknowledgement, escalation, and on-call routing so the alert feed becomes a workflow rather than a list."))
    story.append(Spacer(1, 6))
    story.append(callout("Bottom line", "The delivered system turns a noisy per-reading stream into a small number of evidence-backed incidents. It is deterministic, replay-safe, explicit about data quality, and honest about the policy choices it makes. That is the right foundation for a production alerting path; the remaining work is operational depth, not a rewrite."))

    story.append(Spacer(1, 0.16 * inch))

    # --------------------------------------------------------------- appendix
    story.append(PageBreak())
    story.append(H("Appendix A. Decision summary"))
    story.append(P("The full register, including alternatives and sign-off fields, is in <font name='Courier'>DECISIONS.md</font>. This is the one-page version."))
    story.append(
        table(
            [
                ["#", "Question", "Decision", "Default"],
                ["D1", "What counts as too warm?", "Strictly greater than the ceiling.", "> 8.0 C"],
                ["D2", "What counts as too long?", "Elapsed time plus minimum breach readings, coverage, and gap gates.", ">= 20 min, >= 3 readings, >= 50% coverage"],
                ["D3", "What is the alert unit?", "One incident per truck episode; one OPENED, at most one RESOLVED.", "Deterministic incident ID"],
                ["D4", "How are gaps handled?", "Small gaps tolerated; large gaps break PENDING but never silently resolve ACTIVE.", "<= 15 min"],
                ["D5", "How is out-of-order data handled?", "Event-time sort, dedupe, recompute, stable identity.", "Last write wins per timestamp"],
                ["D6", "When is an incident over?", "Consecutive clear readings below the recovery threshold.", "2 readings <= 7.5 C"],
                ["D7", "How is bad data visible?", "Report what was repaired; do not hide it.", "DATA QUALITY section"],
                ["D8", "What clock is assumed?", "ISO-8601; naive timestamps are UTC; output is UTC.", "UTC"],
            ],
            [0.35 * inch, 1.65 * inch, 3.3 * inch, CONTENT_W - 5.3 * inch],
        )
    )
    story.append(Spacer(1, 6))
    story.append(P("Companion docs: <b>README.md</b> (run/test map), <b>DECISIONS.md</b> (decision register), <b>SOLUTION_DESIGN.md</b> (design notes).", small))

    doc.build(story)

    # Count pages without an external PDF tool.
    from pypdf import PdfReader

    return len(PdfReader(str(PDF_PATH)).pages)


def main() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    try:
        diagram_architecture(ASSET_DIR / "architecture.png")
        diagram_state_machine(ASSET_DIR / "state_machine.png")
        diagram_sequence(ASSET_DIR / "sequence.png")
        diagram_timeline(ASSET_DIR / "timeline.png")
        pages = build_pdf()
        print(f"Wrote {PDF_PATH} ({pages} pages)")
    finally:
        shutil.rmtree(ASSET_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
