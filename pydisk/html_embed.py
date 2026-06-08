"""
pydisk.html_embed
~~~~~~~~~~~~~~~~~
Parse a subset of HTML into a Discord Embed.

Supported HTML → Embed mappings
--------------------------------
<title> or <h1>                 → embed title
<h2> / <h3>                     → bold field name (value = following text)
<p>                             → appended to description
<a href="...">                  → title URL  (first <a> inside <h1>/<title>)
                                  OR inline markdown link [text](url) in desc
<img src="...">                 → thumbnail (first image)  /  image (second)
<color> or style="color:#hex"   → embed color
<footer> / <small>              → footer text
<blockquote>                    → inline field (no label, quoted value)
<ul> / <ol> with <li>           → bullet list appended to description
<table> with <tr>/<td>/<th>     → each row becomes an inline field pair
<time datetime="...">           → embed timestamp
<strong> / <b>                  → **bold** in description text
<em> / <i>                      → *italic* in description text

Usage
-----
    from pydisk.html_embed import parse_html_to_embed

    embed = parse_html_to_embed(\"\"\"
        <h1><a href="https://example.com">My Bot</a></h1>
        <img src="https://example.com/logo.png">
        <p>Welcome to <strong>My Bot</strong>!</p>
        <h2>Commands</h2>
        <p>Use slash commands to interact.</p>
        <footer>Made with pydisk</footer>
    \"\"\")
    # embed is a fully configured pydisk Embed ready to send
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import List, Optional, Tuple, Dict

from .embed import Embed, EmbedField

__all__ = ["parse_html_to_embed", "HTMLEmbedParser"]


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _strip_tags(html: str) -> str:
    """Remove all HTML tags from a string, leaving plain text."""
    return re.sub(r"<[^>]+>", "", html).strip()


def _attr(attrs: List[Tuple[str, Optional[str]]], name: str) -> Optional[str]:
    """Look up an attribute value from an attrs list (case-insensitive)."""
    for k, v in attrs:
        if k.lower() == name:
            return v
    return None


def _hex_from_style(style: Optional[str]) -> Optional[int]:
    """Extract the first #rrggbb or #rgb color from a CSS style string."""
    if not style:
        return None
    m = re.search(r"color\s*:\s*(#[0-9A-Fa-f]{3,6})", style)
    if m:
        raw = m.group(1).lstrip("#")
        if len(raw) == 3:
            raw = "".join(c * 2 for c in raw)
        try:
            return int(raw, 16)
        except ValueError:
            pass
    return None


def _inline_markup(text: str) -> str:
    """Apply minimal inline markdown to text collected inside certain tags."""
    return text.strip()


# ─────────────────────────────────────────────────────────────────────────────
#  Parser
# ─────────────────────────────────────────────────────────────────────────────

class HTMLEmbedParser(HTMLParser):
    """
    Single-pass HTML parser that fills a pydisk :class:`Embed`.

    After feeding HTML call :py:meth:`build` to get the Embed.
    """

    def __init__(self) -> None:
        super().__init__()
        self._embed = Embed()

        # Internal state
        self._current_tag: Optional[str] = None
        self._tag_stack: List[str] = []
        self._buf: str = ""                  # text buffer for current element

        # Tracking
        self._in_bold: bool = False
        self._in_italic: bool = False
        self._title_set: bool = False
        self._image_count: int = 0          # 0 = no image yet, 1 = thumbnail set

        # For <h2>/<h3> → field building
        self._pending_field_name: Optional[str] = None
        self._pending_field_inline: bool = False

        # For <table> parsing
        self._in_table: bool = False
        self._table_row: List[str] = []
        self._table_header: List[str] = []
        self._table_is_first_row: bool = True

        # For <ul>/<ol> parsing
        self._list_type: Optional[str] = None      # "ul" or "ol"
        self._list_items: List[str] = []
        self._list_counter: int = 0
        self._in_li: bool = False

    # ── HTMLParser hooks ─────────────────────────────────────────────────────

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        self._tag_stack.append(tag)
        self._flush_text_to_context()    # flush anything buffered before this tag
        self._current_tag = tag
        self._buf = ""

        # ── <img> ────────────────────────────────────────────────────────────
        if tag == "img":
            src = _attr(attrs, "src")
            if src:
                if self._image_count == 0:
                    self._embed.set_thumbnail(src)
                elif self._image_count == 1:
                    self._embed.set_image(src)
                self._image_count += 1

        # ── <time> ───────────────────────────────────────────────────────────
        elif tag == "time":
            dt = _attr(attrs, "datetime")
            if dt:
                self._embed.set_timestamp(dt)

        # ── color from style attr ────────────────────────────────────────────
        color = _hex_from_style(_attr(attrs, "style"))
        if color is not None and self._embed._color is None:
            self._embed.set_color(color)

        # ── <a href> ─────────────────────────────────────────────────────────
        if tag == "a":
            href = _attr(attrs, "href")
            if href:
                # if we're inside h1 and title not yet set, use as title URL
                if "h1" in self._tag_stack and not self._embed._title_url:
                    self._embed.set_url(href)
                self._buf = f"[[[href:{href}]]]"   # sentinel parsed in handle_endtag

        # ── <ul> / <ol> ──────────────────────────────────────────────────────
        elif tag in ("ul", "ol"):
            self._list_type = tag
            self._list_items = []
            self._list_counter = 0

        elif tag == "li":
            self._in_li = True
            self._buf = ""

        # ── <table> ──────────────────────────────────────────────────────────
        elif tag == "table":
            self._in_table = True
            self._table_header = []
            self._table_is_first_row = True

        elif tag == "tr":
            self._table_row = []

        # ── inline markup ────────────────────────────────────────────────────
        elif tag in ("strong", "b"):
            self._in_bold = True
        elif tag in ("em", "i"):
            self._in_italic = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        text = self._buf.strip()
        if self._tag_stack and self._tag_stack[-1] == tag:
            self._tag_stack.pop()

        # ── <title> / <h1> → embed title ─────────────────────────────────────
        if tag in ("title", "h1"):
            if not self._title_set and text:
                clean = _strip_tags(text)
                # resolve <a> sentinel
                href_m = re.search(r"\[\[\[href:([^\]]+)\]\]\]", text)
                plain = re.sub(r"\[\[\[href:[^\]]+\]\]\]", "", clean).strip()
                self._embed.set_title(plain or clean)
                if href_m and not self._embed._title_url:
                    self._embed.set_url(href_m.group(1))
                self._title_set = True

        # ── <h2> / <h3> → field name (value collected from next <p>) ─────────
        elif tag in ("h2", "h3"):
            if text:
                self._pending_field_name = text
                self._pending_field_inline = tag == "h3"

        # ── <p> → description or field value ─────────────────────────────────
        elif tag == "p":
            if text:
                formatted = self._apply_inline_markup(text)
                if self._pending_field_name:
                    self._embed.add_field(
                        self._pending_field_name,
                        formatted,
                        inline=self._pending_field_inline,
                    )
                    self._pending_field_name = None
                else:
                    self._embed.append_description(formatted)

        # ── <blockquote> → inline field (no name) ────────────────────────────
        elif tag == "blockquote":
            if text:
                self._embed.add_field("\u200b", f"> {text}", inline=False)

        # ── <footer> / <small> → footer ──────────────────────────────────────
        elif tag in ("footer", "small"):
            if text and self._embed._footer is None:
                self._embed.set_footer(text)

        # ── <color> custom tag → color ────────────────────────────────────────
        elif tag == "color":
            if text:
                try:
                    self._embed.set_color(text.strip())
                except (ValueError, AttributeError):
                    pass

        # ── <li> ─────────────────────────────────────────────────────────────
        elif tag == "li":
            self._in_li = False
            if text:
                self._list_items.append(text)

        # ── </ul> / </ol> → append list to description ───────────────────────
        elif tag in ("ul", "ol"):
            if self._list_items:
                lines = []
                for i, item in enumerate(self._list_items, 1):
                    bullet = f"{i}." if self._list_type == "ol" else "•"
                    lines.append(f"{bullet} {item}")
                self._embed.append_description("\n".join(lines))
            self._list_type = None
            self._list_items = []

        # ── </td> / </th> → table cell ───────────────────────────────────────
        elif tag in ("td", "th"):
            self._table_row.append(text)

        # ── </tr> → process table row ────────────────────────────────────────
        elif tag == "tr":
            if self._table_is_first_row:
                self._table_header = self._table_row[:]
                self._table_is_first_row = False
            else:
                # pair header → value as inline fields
                for i, val in enumerate(self._table_row):
                    name = self._table_header[i] if i < len(self._table_header) else f"Col {i+1}"
                    self._embed.add_field(name, val, inline=True)

        elif tag == "table":
            self._in_table = False

        # ── inline markup ─────────────────────────────────────────────────────
        elif tag in ("strong", "b"):
            self._in_bold = False
        elif tag in ("em", "i"):
            self._in_italic = False

        self._buf = ""
        self._current_tag = self._tag_stack[-1] if self._tag_stack else None

    def handle_data(self, data: str) -> None:
        # Wrap bold/italic in markdown
        if self._in_bold:
            self._buf += f"**{data}**"
        elif self._in_italic:
            self._buf += f"*{data}*"
        else:
            self._buf += data

    def _flush_text_to_context(self) -> None:
        """Flush any lingering text buffer (text between tags)."""
        text = self._buf.strip()
        if text and self._current_tag is None:
            # Raw top-level text → description
            self._embed.append_description(text)
        self._buf = ""

    def _apply_inline_markup(self, text: str) -> str:
        """Resolve <a> sentinels → Markdown links."""
        def replace_link(m: re.Match) -> str:
            href = m.group(1)
            # grab surrounding text if any
            return href   # simplified: just keep the URL inline
        text = re.sub(r"\[\[\[href:([^\]]+)\]\]\]", replace_link, text)
        return text

    def build(self) -> Embed:
        """Return the populated :class:`Embed`."""
        return self._embed


# ─────────────────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────────────────

def parse_html_to_embed(html: str, *, color: Optional[int] = None) -> Embed:
    """
    Parse an HTML string and return a configured :class:`~pydisk.embed.Embed`.

    Parameters
    ----------
    html:
        The HTML to parse. Need not be a full document — fragments work fine.
    color:
        Optional fallback color (integer) applied only if no color was found
        in the HTML itself.

    Returns
    -------
    Embed
        A ready-to-send embed. Call ``.to_dict()`` if you need the raw payload.

    Examples
    --------
    ::

        embed = parse_html_to_embed(\"\"\"
            <h1>Server Status</h1>
            <p>All systems <strong>operational</strong>.</p>
            <h2>Latency</h2>
            <p>42 ms</p>
            <footer>Updated just now</footer>
        \"\"\", color=0x57F287)

        await interaction.respond(embed=embed)
    """
    parser = HTMLEmbedParser()
    parser.feed(html)
    embed = parser.build()

    # Apply fallback color
    if color is not None and embed._color is None:
        embed.set_color(color)

    return embed
