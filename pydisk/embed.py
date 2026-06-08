"""
diskord.embed
~~~~~~~~~~~~
Enhanced Discord Embed Builder with full method-chaining.

Adds over the original Embed model:
- ``set_title_url`` — clickable title
- ``set_author`` — with icon_url and url
- ``set_color_hex`` — accept ``"#5865F2"`` strings
- ``add_field`` overhaul — positional insert, remove, edit by index/name
- ``clear_fields`` — wipe all fields
- ``set_timestamp`` — ISO or datetime
- ``set_url`` — embed URL
- ``truncate`` — auto-truncate values to Discord limits
- ``validate`` — check limits before sending
- ``clone`` — deep copy
- ``to_dict`` / ``from_dict`` — round-trip serialisation
- Size helpers: ``char_count``, ``field_count``
- ``EmbedBuilder`` class with preset themes (info, success, warning, error)
- ``EmbedPaginator`` — split a long list across multiple embeds
"""

from __future__ import annotations

import copy
import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

__all__ = [
    "Embed",
    "EmbedField",
    "EmbedBuilder",
    "EmbedPaginator",
]


# ─────────────────────────────────────────────────────────────────────────────
#  Discord limits
# ─────────────────────────────────────────────────────────────────────────────

_LIMITS = {
    "title":        256,
    "description":  4096,
    "field_name":   256,
    "field_value":  1024,
    "footer_text":  2048,
    "author_name":  256,
    "fields":       25,
    "total_chars":  6000,
}


# ─────────────────────────────────────────────────────────────────────────────
#  EmbedField
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EmbedField:
    name: str
    value: str
    inline: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "value": self.value, "inline": self.inline}

    @classmethod
    def from_dict(cls, d: dict) -> "EmbedField":
        return cls(name=d["name"], value=d["value"], inline=d.get("inline", False))


# ─────────────────────────────────────────────────────────────────────────────
#  Embed
# ─────────────────────────────────────────────────────────────────────────────

class Embed:
    """
    Full-featured Discord embed with fluent method chaining.

    All setter methods return ``self``, so you can chain them::

        embed = (
            Embed(title="Stats", color=0x5865F2)
            .set_description("Here are your stats.")
            .set_author("Server Bot", icon_url="https://cdn.example.com/icon.png")
            .add_field("Messages", "1,234", inline=True)
            .add_field("Reputation", "42", inline=True)
            .set_footer("Updated just now")
            .set_thumbnail("https://cdn.example.com/thumb.png")
            .set_timestamp()
        )
    """

    def __init__(
        self,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        color: Optional[int] = None,
        url: Optional[str] = None,
    ) -> None:
        self._title: Optional[str] = title
        self._description: Optional[str] = description
        self._color: Optional[int] = color
        self._url: Optional[str] = url
        self._title_url: Optional[str] = None
        self._timestamp: Optional[str] = None
        self._fields: List[EmbedField] = []
        self._footer: Optional[Dict[str, str]] = None
        self._thumbnail: Optional[str] = None
        self._image: Optional[str] = None
        self._author: Optional[Dict[str, str]] = None
        self._video: Optional[str] = None   # read-only in Discord, but stored for completeness

    # ── Title & URL ──────────────────────────────────────────────────────────

    def set_title(self, title: str, *, url: Optional[str] = None) -> "Embed":
        """Set the embed title. Optionally make it a hyperlink."""
        self._title = title[:_LIMITS["title"]]
        if url:
            self._title_url = url
        return self

    def set_url(self, url: str) -> "Embed":
        """Set the URL that the title links to."""
        self._title_url = url
        return self

    # ── Description ─────────────────────────────────────────────────────────

    def set_description(self, text: str) -> "Embed":
        self._description = text[:_LIMITS["description"]]
        return self

    def append_description(self, text: str) -> "Embed":
        """Append text to the current description (with a newline)."""
        current = self._description or ""
        combined = (current + "\n" + text).strip()
        self._description = combined[:_LIMITS["description"]]
        return self

    # ── Color ────────────────────────────────────────────────────────────────

    def set_color(self, color: Union[int, str]) -> "Embed":
        """
        Accept int (``0x5865F2``) or hex string (``"#5865F2"`` / ``"5865F2"``).
        """
        if isinstance(color, str):
            color = int(color.lstrip("#"), 16)
        self._color = color
        return self

    # Convenience presets
    def color_blurple(self) -> "Embed":  return self.set_color(0x5865F2)
    def color_green(self)   -> "Embed":  return self.set_color(0x57F287)
    def color_yellow(self)  -> "Embed":  return self.set_color(0xFEE75C)
    def color_red(self)     -> "Embed":  return self.set_color(0xED4245)
    def color_white(self)   -> "Embed":  return self.set_color(0xFFFFFF)
    def color_black(self)   -> "Embed":  return self.set_color(0x23272A)

    # ── Timestamp ────────────────────────────────────────────────────────────

    def set_timestamp(
        self, dt: Optional[Union[datetime.datetime, str]] = None
    ) -> "Embed":
        """
        Set the embed timestamp.
        - Pass nothing → current UTC time.
        - Pass a ``datetime.datetime`` (naive or aware).
        - Pass an ISO-8601 string.
        """
        if dt is None:
            dt = datetime.datetime.utcnow()
        if isinstance(dt, datetime.datetime):
            self._timestamp = dt.isoformat()
        else:
            self._timestamp = str(dt)
        return self

    # ── Author ───────────────────────────────────────────────────────────────

    def set_author(
        self,
        name: str,
        *,
        url: Optional[str] = None,
        icon_url: Optional[str] = None,
    ) -> "Embed":
        self._author = {"name": name[:_LIMITS["author_name"]]}
        if url:
            self._author["url"] = url
        if icon_url:
            self._author["icon_url"] = icon_url
        return self

    def remove_author(self) -> "Embed":
        self._author = None
        return self

    # ── Footer ───────────────────────────────────────────────────────────────

    def set_footer(
        self,
        text: str,
        *,
        icon_url: Optional[str] = None,
    ) -> "Embed":
        self._footer = {"text": text[:_LIMITS["footer_text"]]}
        if icon_url:
            self._footer["icon_url"] = icon_url
        return self

    def remove_footer(self) -> "Embed":
        self._footer = None
        return self

    # ── Images ───────────────────────────────────────────────────────────────

    def set_thumbnail(self, url: str) -> "Embed":
        self._thumbnail = url
        return self

    def set_image(self, url: str) -> "Embed":
        self._image = url
        return self

    def remove_thumbnail(self) -> "Embed":
        self._thumbnail = None
        return self

    def remove_image(self) -> "Embed":
        self._image = None
        return self

    # ── Fields ───────────────────────────────────────────────────────────────

    def add_field(
        self,
        name: str,
        value: str,
        *,
        inline: bool = False,
        index: Optional[int] = None,
    ) -> "Embed":
        """
        Add a field. If ``index`` is given, insert at that position.
        Respects Discord's 25-field limit.
        """
        if len(self._fields) >= _LIMITS["fields"]:
            raise ValueError(f"Embed cannot have more than {_LIMITS['fields']} fields.")
        f = EmbedField(
            name=name[:_LIMITS["field_name"]],
            value=value[:_LIMITS["field_value"]],
            inline=inline,
        )
        if index is None:
            self._fields.append(f)
        else:
            self._fields.insert(index, f)
        return self

    def add_blank_field(self, *, inline: bool = False) -> "Embed":
        """Add an invisible separator field."""
        return self.add_field("\u200b", "\u200b", inline=inline)

    def edit_field(
        self,
        index: int,
        *,
        name: Optional[str] = None,
        value: Optional[str] = None,
        inline: Optional[bool] = None,
    ) -> "Embed":
        """Edit a field by index."""
        f = self._fields[index]
        if name is not None:
            f.name = name[:_LIMITS["field_name"]]
        if value is not None:
            f.value = value[:_LIMITS["field_value"]]
        if inline is not None:
            f.inline = inline
        return self

    def edit_field_by_name(self, name: str, value: str, *, inline: Optional[bool] = None) -> "Embed":
        """Edit the first field matching ``name``."""
        for f in self._fields:
            if f.name == name:
                f.value = value[:_LIMITS["field_value"]]
                if inline is not None:
                    f.inline = inline
                return self
        raise KeyError(f"No field named '{name}'.")

    def remove_field(self, index: int) -> "Embed":
        """Remove a field by index."""
        del self._fields[index]
        return self

    def remove_field_by_name(self, name: str) -> "Embed":
        """Remove the first field matching ``name``."""
        for i, f in enumerate(self._fields):
            if f.name == name:
                del self._fields[i]
                return self
        raise KeyError(f"No field named '{name}'.")

    def clear_fields(self) -> "Embed":
        self._fields.clear()
        return self

    def reorder_fields(self, indices: List[int]) -> "Embed":
        """
        Reorder fields. ``indices`` must be a permutation of ``range(len(fields))``.

        Example: swap first and second field → ``embed.reorder_fields([1, 0, 2, 3])``
        """
        if sorted(indices) != list(range(len(self._fields))):
            raise ValueError("indices must be a permutation of field positions.")
        self._fields = [self._fields[i] for i in indices]
        return self

    # ── Limits / validation ──────────────────────────────────────────────────

    @property
    def field_count(self) -> int:
        return len(self._fields)

    @property
    def char_count(self) -> int:
        total = 0
        if self._title:
            total += len(self._title)
        if self._description:
            total += len(self._description)
        if self._footer:
            total += len(self._footer.get("text", ""))
        if self._author:
            total += len(self._author.get("name", ""))
        for f in self._fields:
            total += len(f.name) + len(f.value)
        return total

    def validate(self) -> List[str]:
        """
        Check all Discord embed limits.
        Returns a list of violation strings (empty if valid).
        """
        errors: List[str] = []
        if self._title and len(self._title) > _LIMITS["title"]:
            errors.append(f"Title too long ({len(self._title)} > {_LIMITS['title']})")
        if self._description and len(self._description) > _LIMITS["description"]:
            errors.append(f"Description too long")
        if len(self._fields) > _LIMITS["fields"]:
            errors.append(f"Too many fields ({len(self._fields)} > {_LIMITS['fields']})")
        for i, f in enumerate(self._fields):
            if len(f.name) > _LIMITS["field_name"]:
                errors.append(f"Field {i} name too long")
            if len(f.value) > _LIMITS["field_value"]:
                errors.append(f"Field {i} value too long")
        if self.char_count > _LIMITS["total_chars"]:
            errors.append(f"Total characters exceed limit ({self.char_count} > {_LIMITS['total_chars']})")
        return errors

    def truncate(self) -> "Embed":
        """
        Auto-truncate all text to Discord limits (in-place).
        Useful for dynamic content.
        """
        if self._title:
            self._title = self._title[:_LIMITS["title"]]
        if self._description:
            self._description = self._description[:_LIMITS["description"]]
        if self._footer:
            self._footer["text"] = self._footer["text"][:_LIMITS["footer_text"]]
        if self._author:
            self._author["name"] = self._author["name"][:_LIMITS["author_name"]]
        for f in self._fields:
            f.name = f.name[:_LIMITS["field_name"]]
            f.value = f.value[:_LIMITS["field_value"]]
        return self

    # ── Clone & serialization ────────────────────────────────────────────────

    def clone(self) -> "Embed":
        """Return a deep copy of this embed."""
        return copy.deepcopy(self)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if self._title:
            payload["title"] = self._title
        if self._description:
            payload["description"] = self._description
        if self._color is not None:
            payload["color"] = self._color
        if self._title_url or self._url:
            payload["url"] = self._title_url or self._url
        if self._timestamp:
            payload["timestamp"] = self._timestamp
        if self._author:
            payload["author"] = self._author
        if self._footer:
            payload["footer"] = self._footer
        if self._thumbnail:
            payload["thumbnail"] = {"url": self._thumbnail}
        if self._image:
            payload["image"] = {"url": self._image}
        if self._fields:
            payload["fields"] = [f.to_dict() for f in self._fields]
        return payload

    @classmethod
    def from_dict(cls, d: dict) -> "Embed":
        e = cls()
        e._title = d.get("title")
        e._description = d.get("description")
        e._color = d.get("color")
        e._title_url = d.get("url")
        e._timestamp = d.get("timestamp")
        if d.get("author"):
            e._author = d["author"]
        if d.get("footer"):
            e._footer = d["footer"]
        if d.get("thumbnail"):
            e._thumbnail = d["thumbnail"]["url"]
        if d.get("image"):
            e._image = d["image"]["url"]
        e._fields = [EmbedField.from_dict(f) for f in d.get("fields", [])]
        return e

    def __repr__(self) -> str:
        return (
            f"<Embed title={self._title!r} fields={len(self._fields)} "
            f"chars={self.char_count}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
#  EmbedBuilder — factory / presets
# ─────────────────────────────────────────────────────────────────────────────

class EmbedBuilder:
    """
    Static factory with themed presets and utilities.

    Usage::

        embed = EmbedBuilder.success("Done!", description="Operation completed.")
        embed = EmbedBuilder.error("Oops!", description="Something went wrong.")
        embed = EmbedBuilder.info("FYI", description="Here's some info.")
        embed = EmbedBuilder.warning("Watch out!", description="Be careful.")
    """

    # Colour palette
    BLURPLE  = 0x5865F2
    GREEN    = 0x57F287
    YELLOW   = 0xFEE75C
    RED      = 0xED4245
    GREY     = 0x99AAB5
    DARK     = 0x2F3136

    @staticmethod
    def _base(title: str, description: str, color: int) -> Embed:
        return Embed(title=title, description=description, color=color)

    @classmethod
    def success(cls, title: str = "✅ Success", *, description: str = "") -> Embed:
        return cls._base(title, description, cls.GREEN)

    @classmethod
    def error(cls, title: str = "❌ Error", *, description: str = "") -> Embed:
        return cls._base(title, description, cls.RED)

    @classmethod
    def warning(cls, title: str = "⚠️ Warning", *, description: str = "") -> Embed:
        return cls._base(title, description, cls.YELLOW)

    @classmethod
    def info(cls, title: str = "ℹ️ Info", *, description: str = "") -> Embed:
        return cls._base(title, description, cls.BLURPLE)

    @classmethod
    def loading(cls, title: str = "⏳ Loading…", *, description: str = "") -> Embed:
        return cls._base(title, description, cls.GREY)

    @classmethod
    def from_template(
        cls,
        template: Embed,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        **overrides: Any,
    ) -> Embed:
        """Clone a template embed and override specific fields."""
        e = template.clone()
        if title:
            e.set_title(title)
        if description:
            e.set_description(description)
        return e


# ─────────────────────────────────────────────────────────────────────────────
#  EmbedPaginator
# ─────────────────────────────────────────────────────────────────────────────

class EmbedPaginator:
    """
    Split a long list of fields (or lines) across multiple embeds.

    Usage::

        paginator = EmbedPaginator(
            base_embed=Embed(title="Leaderboard", color=0x5865F2),
            max_fields=10,
        )
        for rank, (user, score) in enumerate(scores, 1):
            paginator.add_field(f"#{rank} {user}", str(score), inline=False)

        pages = paginator.pages
        # → list of Embed, each with ≤ 10 fields
    """

    def __init__(
        self,
        *,
        base_embed: Optional[Embed] = None,
        max_fields: int = 10,
        max_chars: int = 4000,
    ) -> None:
        self._base = base_embed or Embed()
        self._max_fields = min(max_fields, _LIMITS["fields"])
        self._max_chars = max_chars
        self._fields: List[EmbedField] = []

    def add_field(self, name: str, value: str, *, inline: bool = False) -> "EmbedPaginator":
        self._fields.append(EmbedField(
            name=name[:_LIMITS["field_name"]],
            value=value[:_LIMITS["field_value"]],
            inline=inline,
        ))
        return self

    def add_line(self, text: str) -> "EmbedPaginator":
        """Shortcut: add a single unnamed line as a zero-width-name field."""
        return self.add_field("\u200b", text[:_LIMITS["field_value"]])

    @property
    def pages(self) -> List[Embed]:
        pages: List[Embed] = []
        chunk: List[EmbedField] = []
        char_count = 0

        for f in self._fields:
            f_chars = len(f.name) + len(f.value)
            if len(chunk) >= self._max_fields or (chunk and char_count + f_chars > self._max_chars):
                pages.append(self._make_page(chunk, len(pages) + 1))
                chunk = []
                char_count = 0
            chunk.append(f)
            char_count += f_chars

        if chunk:
            pages.append(self._make_page(chunk, len(pages) + 1))

        # Add page indicators to footers
        total = len(pages)
        for i, page in enumerate(pages, 1):
            existing_footer = page._footer or {}
            footer_text = existing_footer.get("text", "")
            suffix = f"Page {i}/{total}"
            page.set_footer(
                (footer_text + " • " + suffix).strip(" • "),
                icon_url=existing_footer.get("icon_url"),
            )

        return pages

    def _make_page(self, fields: List[EmbedField], page_num: int) -> Embed:
        e = self._base.clone()
        e.clear_fields()
        for f in fields:
            e._fields.append(f)
        return e

    @property
    def page_count(self) -> int:
        return len(self.pages)
