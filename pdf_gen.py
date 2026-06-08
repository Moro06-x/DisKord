"""
pydisk.pdf_gen
~~~~~~~~~~~~~~
Generate styled PDFs from a simple markup language and send them as
Discord file attachments.

Markup syntax (used inside the /makepdf modal)
----------------------------------------------
[report]            → "REPORT" badge header (blue)
[invoice]           → "INVOICE" badge header (green)
[note]              → "NOTE" badge header (grey)

## Section Title     → H2 heading (dark bar)
### Sub Title        → H3 heading (lighter)

| Col A | Col B |    → table rows  (first row = header)
| ----- | ----- |    → separator row (ignored / auto-detected)

- item               → bullet list item
1. item              → numbered list item

**bold** *italic*    → inline formatting in paragraphs
---                  → horizontal rule / divider
                     → blank line = paragraph break

Anything else        → normal paragraph text

Usage (inside a cog or client)
-------------------------------
    from pydisk.pdf_gen import generate_pdf, PDFStyle

    # Returns a BytesIO ready to send as a Discord attachment
    pdf_bytes = generate_pdf(title="My Doc", content=modal_content)

    await http.post(
        f"/channels/{channel_id}/messages",
        data={"content": "Here is your PDF!"},
        files={"file": ("document.pdf", pdf_bytes, "application/pdf")},
    )

    # Or with the helper that wraps the above:
    from pydisk.pdf_gen import send_pdf
    await send_pdf(http, channel_id, title="My Doc", content=modal_content)
"""

from __future__ import annotations

import io
import re
import textwrap
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

__all__ = [
    "generate_pdf",
    "send_pdf",
    "PDFStyle",
    "TEMPLATE_REPORT",
    "TEMPLATE_INVOICE",
    "TEMPLATE_NOTE",
]


# ─────────────────────────────────────────────────────────────────────────────
#  Theme / colour palette
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PDFStyle:
    """Colour & font settings for the generated PDF."""
    primary:     str = "#5865F2"   # Discord blurple
    secondary:   str = "#23272A"   # almost-black
    accent:      str = "#57F287"   # green
    muted:       str = "#99AAB5"   # grey
    page_bg:     str = "#FFFFFF"
    font_body:   str = "Helvetica"
    font_bold:   str = "Helvetica-Bold"
    font_mono:   str = "Courier"
    font_size:   int = 11
    margin:      float = 20 * mm

    def hex_to_color(self, hex_str: str) -> colors.HexColor:
        return colors.HexColor(hex_str)


TEMPLATE_REPORT  = "[report]"
TEMPLATE_INVOICE = "[invoice]"
TEMPLATE_NOTE    = "[note]"

_TEMPLATE_META = {
    "[report]":  ("REPORT",  "#5865F2"),
    "[invoice]": ("INVOICE", "#57F287"),
    "[note]":    ("NOTE",    "#99AAB5"),
}


# ─────────────────────────────────────────────────────────────────────────────
#  Inline markup parser  (**bold**, *italic*)
# ─────────────────────────────────────────────────────────────────────────────

def _to_rl_markup(text: str) -> str:
    """Convert **bold** and *italic* to ReportLab XML tags."""
    # bold first (so **x** doesn't leave stray *)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*(.+?)\*",     r"<i>\1</i>", text)
    return text


# ─────────────────────────────────────────────────────────────────────────────
#  Content parser  → list of "blocks"
# ─────────────────────────────────────────────────────────────────────────────

def _parse(raw: str) -> List[dict]:
    """
    Turn the raw markup string into a list of block dicts.
    Each dict has at minimum a "type" key.
    """
    blocks: List[dict] = []
    lines = raw.splitlines()
    i = 0

    # Collect consecutive table rows
    table_buf: List[List[str]] = []

    def flush_table():
        if table_buf:
            blocks.append({"type": "table", "rows": list(table_buf)})
            table_buf.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # ── template badges ──────────────────────────────────────────────────
        if stripped.lower() in _TEMPLATE_META:
            flush_table()
            label, color = _TEMPLATE_META[stripped.lower()]
            blocks.append({"type": "badge", "label": label, "color": color})
            i += 1
            continue

        # ── H2 heading ───────────────────────────────────────────────────────
        if stripped.startswith("## "):
            flush_table()
            blocks.append({"type": "h2", "text": stripped[3:].strip()})
            i += 1
            continue

        # ── H3 heading ───────────────────────────────────────────────────────
        if stripped.startswith("### "):
            flush_table()
            blocks.append({"type": "h3", "text": stripped[4:].strip()})
            i += 1
            continue

        # ── horizontal rule ──────────────────────────────────────────────────
        if stripped == "---":
            flush_table()
            blocks.append({"type": "hr"})
            i += 1
            continue

        # ── table row ────────────────────────────────────────────────────────
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped[1:-1].split("|")]
            # Skip separator rows like | --- | --- |
            if not all(re.fullmatch(r"-+", c) for c in cells):
                table_buf.append(cells)
            i += 1
            continue

        # Non-table line → flush pending table
        flush_table()

        # ── bullet list item ─────────────────────────────────────────────────
        if stripped.startswith("- "):
            # Collect consecutive bullets
            items = []
            while i < len(lines) and lines[i].strip().startswith("- "):
                items.append(lines[i].strip()[2:])
                i += 1
            blocks.append({"type": "ul", "items": items})
            continue

        # ── numbered list item ───────────────────────────────────────────────
        if re.match(r"^\d+\. ", stripped):
            items = []
            while i < len(lines) and re.match(r"^\d+\. ", lines[i].strip()):
                items.append(re.sub(r"^\d+\. ", "", lines[i].strip()))
                i += 1
            blocks.append({"type": "ol", "items": items})
            continue

        # ── blank line (spacing) ─────────────────────────────────────────────
        if stripped == "":
            blocks.append({"type": "space"})
            i += 1
            continue

        # ── normal paragraph ─────────────────────────────────────────────────
        blocks.append({"type": "para", "text": stripped})
        i += 1

    flush_table()
    return blocks


# ─────────────────────────────────────────────────────────────────────────────
#  PDF builder
# ─────────────────────────────────────────────────────────────────────────────

def generate_pdf(
    title: str,
    content: str,
    *,
    author: str = "",
    style: Optional[PDFStyle] = None,
) -> io.BytesIO:
    """
    Generate a styled PDF and return it as a :class:`io.BytesIO` object.

    Parameters
    ----------
    title:
        The document title (shown at the top of the PDF and in metadata).
    content:
        The markup string (from the Discord modal).
    author:
        Optional author name embedded in PDF metadata.
    style:
        Optional :class:`PDFStyle` for custom colours/fonts.

    Returns
    -------
    io.BytesIO
        Seek-reset buffer ready to be read / sent as a file attachment.
    """
    s = style or PDFStyle()
    buf = io.BytesIO()

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=s.margin,
        rightMargin=s.margin,
        topMargin=s.margin,
        bottomMargin=s.margin,
        title=title,
        author=author,
    )

    W = A4[0] - 2 * s.margin   # usable page width

    # ── ReportLab styles ─────────────────────────────────────────────────────
    base = getSampleStyleSheet()

    def ps(name, **kw) -> ParagraphStyle:
        return ParagraphStyle(name, **kw)

    ST_TITLE = ps("DocTitle",
        fontName=s.font_bold, fontSize=22,
        textColor=s.hex_to_color(s.secondary),
        spaceAfter=4,
    )
    ST_AUTHOR = ps("DocAuthor",
        fontName=s.font_body, fontSize=9,
        textColor=s.hex_to_color(s.muted),
        spaceAfter=10,
    )
    ST_H2 = ps("H2",
        fontName=s.font_bold, fontSize=14,
        textColor=colors.white,
        backColor=s.hex_to_color(s.secondary),
        leftIndent=6, rightIndent=6,
        spaceBefore=10, spaceAfter=4,
        leading=20,
    )
    ST_H3 = ps("H3",
        fontName=s.font_bold, fontSize=12,
        textColor=s.hex_to_color(s.primary),
        spaceBefore=8, spaceAfter=3,
    )
    ST_BODY = ps("Body",
        fontName=s.font_body, fontSize=s.font_size,
        textColor=s.hex_to_color(s.secondary),
        leading=15, spaceAfter=4,
    )
    ST_BADGE = ps("Badge",
        fontName=s.font_bold, fontSize=10,
        textColor=colors.white,
        leading=14,
    )

    # ── Title block ───────────────────────────────────────────────────────────
    story = []
    story.append(Paragraph(title, ST_TITLE))
    if author:
        story.append(Paragraph(f"Prepared by {author}", ST_AUTHOR))
    story.append(HRFlowable(width="100%", thickness=2,
                             color=s.hex_to_color(s.primary),
                             spaceAfter=10))

    # ── Content blocks ────────────────────────────────────────────────────────
    blocks = _parse(content)

    for block in blocks:
        t = block["type"]

        if t == "space":
            story.append(Spacer(1, 5))

        elif t == "hr":
            story.append(HRFlowable(width="100%", thickness=1,
                                     color=s.hex_to_color(s.muted),
                                     spaceBefore=6, spaceAfter=6))

        elif t == "badge":
            badge_color = s.hex_to_color(block["color"])
            badge_tbl = Table(
                [[Paragraph(block["label"], ST_BADGE)]],
                colWidths=[40 * mm],
            )
            badge_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), badge_color),
                ("ROUNDEDCORNERS", [4]),
                ("TOPPADDING",    (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ]))
            story.append(badge_tbl)
            story.append(Spacer(1, 8))

        elif t == "h2":
            story.append(Paragraph(_to_rl_markup(block["text"]), ST_H2))

        elif t == "h3":
            story.append(Paragraph(_to_rl_markup(block["text"]), ST_H3))

        elif t == "para":
            story.append(Paragraph(_to_rl_markup(block["text"]), ST_BODY))

        elif t in ("ul", "ol"):
            bullet_type = "bullet" if t == "ul" else "1"
            items = [
                ListItem(
                    Paragraph(_to_rl_markup(item), ST_BODY),
                    bulletColor=s.hex_to_color(s.primary),
                )
                for item in block["items"]
            ]
            story.append(ListFlowable(items,
                bulletType=bullet_type,
                leftIndent=15,
                spaceBefore=2, spaceAfter=4,
            ))

        elif t == "table":
            rows = block["rows"]
            if not rows:
                continue

            # Equal column widths
            col_w = W / max(len(r) for r in rows)
            col_widths = [col_w] * len(rows[0])

            tbl_data = [
                [Paragraph(_to_rl_markup(cell), ST_BODY) for cell in row]
                for row in rows
            ]

            tbl = Table(tbl_data, colWidths=col_widths, repeatRows=1)
            tbl_style = TableStyle([
                # Header row
                ("BACKGROUND",   (0, 0), (-1, 0),  s.hex_to_color(s.primary)),
                ("TEXTCOLOR",    (0, 0), (-1, 0),  colors.white),
                ("FONTNAME",     (0, 0), (-1, 0),  s.font_bold),
                ("FONTSIZE",     (0, 0), (-1, 0),  10),
                # Body rows — alternating
                ("BACKGROUND",   (0, 1), (-1, -1), colors.white),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                    [colors.white, s.hex_to_color("#F2F3F5")]),
                # Grid
                ("GRID",         (0, 0), (-1, -1), 0.5, s.hex_to_color(s.muted)),
                ("TOPPADDING",   (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
                ("LEFTPADDING",  (0, 0), (-1, -1), 6),
                ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
            ])
            tbl.setStyle(tbl_style)
            story.append(tbl)
            story.append(Spacer(1, 6))

    # ── Footer line ───────────────────────────────────────────────────────────
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=1,
                             color=s.hex_to_color(s.muted)))
    story.append(Paragraph(
        f"Generated by pydisk PDF Generator",
        ps("Footer",
            fontName=s.font_body, fontSize=8,
            textColor=s.hex_to_color(s.muted),
            alignment=1,   # centre
        ),
    ))

    doc.build(story)
    buf.seek(0)
    return buf


# ─────────────────────────────────────────────────────────────────────────────
#  Discord send helper
# ─────────────────────────────────────────────────────────────────────────────

async def send_pdf(
    http,
    channel_id: str,
    *,
    title: str,
    content: str,
    author: str = "",
    filename: Optional[str] = None,
    message: str = "",
    style: Optional[PDFStyle] = None,
) -> None:
    """
    Generate a PDF and send it to a Discord channel as a file attachment.

    Parameters
    ----------
    http:
        A pydisk ``HTTPClient`` instance.
    channel_id:
        The target Discord channel ID.
    title:
        Document title.
    content:
        Markup content string.
    author:
        Optional author name.
    filename:
        Override the attachment filename (default: ``<sanitised_title>.pdf``).
    message:
        Optional text message to accompany the file.
    style:
        Optional :class:`PDFStyle`.
    """
    pdf = generate_pdf(title, content, author=author, style=style)
    safe_name = re.sub(r"[^\w\s-]", "", title).strip().replace(" ", "_") or "document"
    fname = filename or f"{safe_name}.pdf"

    await http.post(
        f"/channels/{channel_id}/messages",
        data={"content": message} if message else {},
        files={"file": (fname, pdf.read(), "application/pdf")},
    )
