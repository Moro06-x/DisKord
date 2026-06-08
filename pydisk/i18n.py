"""
pydisk.i18n
~~~~~~~~~~~
Automatic internationalisation (i18n) for slash commands.

Discord sends a `locale` field with every interaction telling you
exactly what language the user has set. pydisk uses this to
automatically pick the right translation.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional


# ── Discord locale codes ──────────────────────────────────────────────────────
DISCORD_LOCALES = {
    "id":    "Indonesian",
    "da":    "Danish",
    "de":    "German",
    "en-GB": "English (UK)",
    "en-US": "English (US)",
    "es-ES": "Spanish (Spain)",
    "es-419":"Spanish (LATAM)",
    "fr":    "French",
    "hr":    "Croatian",
    "it":    "Italian",
    "lt":    "Lithuanian",
    "hu":    "Hungarian",
    "nl":    "Dutch",
    "no":    "Norwegian",
    "pl":    "Polish",
    "pt-BR": "Portuguese (Brazil)",
    "ro":    "Romanian",
    "fi":    "Finnish",
    "sv-SE": "Swedish",
    "vi":    "Vietnamese",
    "tr":    "Turkish",
    "cs":    "Czech",
    "el":    "Greek",
    "bg":    "Bulgarian",
    "ru":    "Russian",
    "uk":    "Ukrainian",
    "hi":    "Hindi",
    "th":    "Thai",
    "zh-CN": "Chinese (Simplified)",
    "ja":    "Japanese",
    "zh-TW": "Chinese (Traditional)",
    "ko":    "Korean",
    "ar":    "Arabic",
}


class Translations:
    """
    Holds all your translation strings and resolves them at runtime.

    Dict format::

        {
            "key.name": {
                "en-US": "Hello!",
                "fr": "Bonjour!",
                "de": "Hallo!",
            },
        }
    """

    def __init__(
        self,
        data: Optional[Dict[str, Dict[str, str]]] = None,
        *,
        fallback_locale: str = "en-US",
    ):
        self._strings: Dict[str, Dict[str, str]] = data or {}
        self.fallback_locale = fallback_locale

    @classmethod
    def from_json(cls, path: str, *, fallback_locale: str = "en-US") -> "Translations":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls(data, fallback_locale=fallback_locale)

    @classmethod
    def from_directory(cls, directory: str, *, fallback_locale: str = "en-US") -> "Translations":
        data: Dict[str, Dict[str, str]] = {}
        for filename in os.listdir(directory):
            if not filename.endswith(".json"):
                continue
            locale = filename[:-5]
            filepath = os.path.join(directory, filename)
            with open(filepath, encoding="utf-8") as f:
                locale_strings = json.load(f)
            for key, value in locale_strings.items():
                data.setdefault(key, {})[locale] = value
        return cls(data, fallback_locale=fallback_locale)

    def add(self, key: str, translations: Dict[str, str]) -> "Translations":
        self._strings.setdefault(key, {}).update(translations)
        return self

    def merge(self, other: "Translations") -> "Translations":
        for key, locales in other._strings.items():
            self._strings.setdefault(key, {}).update(locales)
        return self

    def get(self, key: str, locale: str, **kwargs: Any) -> str:
        bucket = self._strings.get(key, {})

        text = bucket.get(locale)

        if text is None and "-" in locale:
            lang = locale.split("-")[0]
            text = bucket.get(lang)

        if text is None:
            text = bucket.get(self.fallback_locale)

        if text is None:
            return key

        if kwargs:
            try:
                text = text.format(**kwargs)
            except KeyError:
                pass

        return text

    def get_all_for_key(self, key: str) -> Dict[str, str]:
        return dict(self._strings.get(key, {}))


# ── Global default instance ───────────────────────────────────────────────────

_global: Optional[Translations] = None


def set_translations(translations: Translations) -> None:
    """Set the global Translations instance used by t()."""
    global _global
    _global = translations


def t(interaction_or_locale: Any, key: str, **kwargs: Any) -> str:
    """
    Translate `key` for the locale of the given interaction (or a raw locale string).

    BUG FIX: Original had a broken _patch_interaction() call at module level that
    tried to dynamically add a field to a frozen dataclass using object.__setattr__,
    which fails at runtime. The Interaction model now correctly includes the locale
    field as a proper dataclass field, so no patching is needed.
    """
    global _global

    if isinstance(interaction_or_locale, str):
        locale = interaction_or_locale
    else:
        locale = getattr(interaction_or_locale, "locale", "en-US")

    if _global is None:
        raise RuntimeError(
            "No global Translations set. Call pydisk.i18n.set_translations(your_translations) "
            "before using t(), or use Translations.get() directly."
        )

    return _global.get(key, locale, **kwargs)
