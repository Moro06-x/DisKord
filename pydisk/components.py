"""
pydisk.components
~~~~~~~~~~~~~~~~~
First-class Component & Modal routers for Discord interactions.

Eliminates the ``if custom_id == "..."`` soup by providing a decorator-driven
router that dispatches to the right handler based on custom_id patterns.

Public surface
--------------
ComponentRouter   : routes button / select-menu interactions
ModalRouter       : routes modal submit interactions
ComponentBuilder  : fluent builder for action rows, buttons, select menus
ModalBuilder      : fluent builder for modal text inputs
ActionRow         : serialisable action-row container
Button            : button component model
SelectMenu        : select-menu component model
TextInput         : text-input (modal) component model
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("pydisk.components")

__all__ = [
    "ComponentRouter",
    "ModalRouter",
    "ComponentBuilder",
    "ModalBuilder",
    "ActionRow",
    "Button",
    "SelectMenu",
    "TextInput",
    "ButtonStyle",
    "TextInputStyle",
]


# ──────────────────────────────────────────────────────────────────────────────
#  Discord enums
# ──────────────────────────────────────────────────────────────────────────────

class ComponentType:
    ACTION_ROW   = 1
    BUTTON       = 2
    SELECT_MENU  = 3
    TEXT_INPUT   = 4
    USER_SELECT  = 5
    ROLE_SELECT  = 6
    MENTIONABLE_SELECT = 7
    CHANNEL_SELECT     = 8


class ButtonStyle:
    PRIMARY   = 1   # Blurple
    SECONDARY = 2   # Grey
    SUCCESS   = 3   # Green
    DANGER    = 4   # Red
    LINK      = 5   # URL (no custom_id)


class TextInputStyle:
    SHORT     = 1
    PARAGRAPH = 2


# ──────────────────────────────────────────────────────────────────────────────
#  Component models
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Button:
    """A Discord button component."""
    label: str
    custom_id: str = ""
    style: int = ButtonStyle.PRIMARY
    emoji: Optional[str] = None
    url: Optional[str] = None
    disabled: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "type": ComponentType.BUTTON,
            "label": self.label,
            "style": self.style,
            "disabled": self.disabled,
        }
        if self.style == ButtonStyle.LINK:
            d["url"] = self.url or ""
        else:
            d["custom_id"] = self.custom_id
        if self.emoji:
            d["emoji"] = {"name": self.emoji}
        return d


@dataclass
class SelectOption:
    """One option inside a SelectMenu."""
    label: str
    value: str
    description: Optional[str] = None
    emoji: Optional[str] = None
    default: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "label": self.label,
            "value": self.value,
            "default": self.default,
        }
        if self.description:
            d["description"] = self.description
        if self.emoji:
            d["emoji"] = {"name": self.emoji}
        return d


@dataclass
class SelectMenu:
    """A Discord string select-menu component."""
    custom_id: str
    options: List[SelectOption] = field(default_factory=list)
    placeholder: Optional[str] = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False

    def add_option(
        self,
        label: str,
        value: str,
        *,
        description: Optional[str] = None,
        emoji: Optional[str] = None,
        default: bool = False,
    ) -> "SelectMenu":
        self.options.append(SelectOption(label, value, description, emoji, default))
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": ComponentType.SELECT_MENU,
            "custom_id": self.custom_id,
            "options": [o.to_dict() for o in self.options],
            "placeholder": self.placeholder or "",
            "min_values": self.min_values,
            "max_values": self.max_values,
            "disabled": self.disabled,
        }


@dataclass
class TextInput:
    """A text-input field inside a modal."""
    label: str
    custom_id: str
    style: int = TextInputStyle.SHORT
    placeholder: Optional[str] = None
    value: Optional[str] = None       # pre-filled value
    min_length: int = 0
    max_length: int = 4000
    required: bool = True

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "type": ComponentType.TEXT_INPUT,
            "label": self.label,
            "custom_id": self.custom_id,
            "style": self.style,
            "min_length": self.min_length,
            "max_length": self.max_length,
            "required": self.required,
        }
        if self.placeholder:
            d["placeholder"] = self.placeholder
        if self.value:
            d["value"] = self.value
        return d


@dataclass
class ActionRow:
    """A single action-row containing up to 5 components."""
    components: List[Any] = field(default_factory=list)

    def add(self, component) -> "ActionRow":
        self.components.append(component)
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": ComponentType.ACTION_ROW,
            "components": [c.to_dict() for c in self.components],
        }


# ──────────────────────────────────────────────────────────────────────────────
#  Fluent builders
# ──────────────────────────────────────────────────────────────────────────────

class ComponentBuilder:
    """
    Fluent builder that assembles action rows with buttons / select menus.

    Usage::

        rows = (
            ComponentBuilder()
            .button("✅ Confirm", custom_id="confirm:yes", style=ButtonStyle.SUCCESS)
            .button("❌ Cancel",  custom_id="confirm:no",  style=ButtonStyle.DANGER)
            .build()
        )
        await interaction.respond("Are you sure?", components=rows)
    """

    def __init__(self) -> None:
        self._rows: List[ActionRow] = []
        self._current: ActionRow = ActionRow()

    def button(
        self,
        label: str,
        *,
        custom_id: str = "",
        style: int = ButtonStyle.PRIMARY,
        emoji: Optional[str] = None,
        url: Optional[str] = None,
        disabled: bool = False,
    ) -> "ComponentBuilder":
        if len(self._current.components) >= 5:
            self._rows.append(self._current)
            self._current = ActionRow()
        self._current.add(Button(label, custom_id, style, emoji, url, disabled))
        return self

    def select(self, menu: SelectMenu) -> "ComponentBuilder":
        """Add a select menu (takes its own row)."""
        if self._current.components:
            self._rows.append(self._current)
            self._current = ActionRow()
        self._current.add(menu)
        self._rows.append(self._current)
        self._current = ActionRow()
        return self

    def new_row(self) -> "ComponentBuilder":
        """Force-start a new action row."""
        if self._current.components:
            self._rows.append(self._current)
            self._current = ActionRow()
        return self

    def build(self) -> List[Dict[str, Any]]:
        """Serialise all rows to Discord-ready dicts."""
        rows = list(self._rows)
        if self._current.components:
            rows.append(self._current)
        return [r.to_dict() for r in rows]


class ModalBuilder:
    """
    Fluent builder for Discord modals.

    Usage::

        modal = (
            ModalBuilder("Submit Feedback", custom_id="feedback:submit")
            .text_input("Your feedback", custom_id="feedback_text",
                        style=TextInputStyle.PARAGRAPH, max_length=500)
            .text_input("Email (optional)", custom_id="email", required=False)
            .build()
        )
        await interaction.respond_modal(modal)
    """

    def __init__(self, title: str, *, custom_id: str) -> None:
        self.title = title
        self.custom_id = custom_id
        self._inputs: List[TextInput] = []

    def text_input(
        self,
        label: str,
        *,
        custom_id: str,
        style: int = TextInputStyle.SHORT,
        placeholder: Optional[str] = None,
        value: Optional[str] = None,
        min_length: int = 0,
        max_length: int = 4000,
        required: bool = True,
    ) -> "ModalBuilder":
        self._inputs.append(TextInput(
            label, custom_id, style, placeholder, value,
            min_length, max_length, required,
        ))
        return self

    def build(self) -> Dict[str, Any]:
        """Return the Discord modal payload dict."""
        return {
            "title": self.title,
            "custom_id": self.custom_id,
            "components": [
                ActionRow([inp]).to_dict() for inp in self._inputs
            ],
        }


# ──────────────────────────────────────────────────────────────────────────────
#  ComponentInteraction helper (wraps raw interaction data)
# ──────────────────────────────────────────────────────────────────────────────

class ComponentInteraction:
    """
    A thin wrapper around raw component interaction data, giving convenient
    accessors for ``custom_id``, ``values`` (select menus), and responding.
    """

    def __init__(self, data: dict, http: Any) -> None:
        self._raw = data
        self._http = http
        self.id: str = data["id"]
        self.token: str = data["token"]
        self.guild_id: Optional[str] = data.get("guild_id")
        self.channel_id: str = data["channel_id"]
        cdata = data.get("data", {})
        self.custom_id: str = cdata.get("custom_id", "")
        self.component_type: int = cdata.get("component_type", 0)
        self.values: List[str] = cdata.get("values", [])
        user_data = data.get("member", {}).get("user") or data.get("user", {})
        self.user_id: str = user_data.get("id", "")
        self.username: str = user_data.get("username", "")
        # Named capture groups from pattern matching (set by router)
        self.params: Dict[str, str] = {}

    async def respond(
        self,
        content: str = "",
        *,
        ephemeral: bool = False,
        embed: Any = None,
        components: Optional[List[Dict[str, Any]]] = None,
        update: bool = False,
    ) -> None:
        """
        Respond to the component interaction.

        ``update=True`` → silently update the original message (type 7).
        ``update=False`` → send a new reply (type 4).
        """
        itype = 7 if update else 4
        flags = 64 if ephemeral else 0
        msg: Dict[str, Any] = {"content": content, "flags": flags}
        if embed:
            msg["embeds"] = [embed.to_dict() if hasattr(embed, "to_dict") else embed]
        if components:
            msg["components"] = components
        await self._http.post(
            f"/interactions/{self.id}/{self.token}/callback",
            json={"type": itype, "data": msg},
        )

    async def defer(self, *, ephemeral: bool = False, update: bool = False) -> None:
        """Defer the response (type 5 = loading indicator, type 6 = silent defer)."""
        itype = 6 if update else 5
        flags = 64 if ephemeral else 0
        await self._http.post(
            f"/interactions/{self.id}/{self.token}/callback",
            json={"type": itype, "data": {"flags": flags}},
        )

    async def followup(self, content: str = "", *, embed: Any = None) -> None:
        msg: Dict[str, Any] = {"content": content}
        if embed:
            msg["embeds"] = [embed.to_dict() if hasattr(embed, "to_dict") else embed]
        await self._http.post(f"/webhooks/me/{self.token}", json=msg)


class ModalSubmitInteraction:
    """
    Wrapper around modal submit interaction data.
    Provides easy access to submitted field values by ``custom_id``.
    """

    def __init__(self, data: dict, http: Any) -> None:
        self._raw = data
        self._http = http
        self.id: str = data["id"]
        self.token: str = data["token"]
        self.guild_id: Optional[str] = data.get("guild_id")
        self.channel_id: str = data["channel_id"]
        self.custom_id: str = data.get("data", {}).get("custom_id", "")
        user_data = data.get("member", {}).get("user") or data.get("user", {})
        self.user_id: str = user_data.get("id", "")
        self.username: str = user_data.get("username", "")
        self.params: Dict[str, str] = {}

        # Parse submitted field values from nested components
        self.fields: Dict[str, str] = {}
        for row in data.get("data", {}).get("components", []):
            for comp in row.get("components", []):
                if comp.get("type") == ComponentType.TEXT_INPUT:
                    self.fields[comp["custom_id"]] = comp.get("value", "")

    def get(self, field_id: str, default: str = "") -> str:
        """Get the submitted value of a text input by its custom_id."""
        return self.fields.get(field_id, default)

    async def respond(
        self,
        content: str = "",
        *,
        ephemeral: bool = False,
        embed: Any = None,
        components: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        flags = 64 if ephemeral else 0
        msg: Dict[str, Any] = {"content": content, "flags": flags}
        if embed:
            msg["embeds"] = [embed.to_dict() if hasattr(embed, "to_dict") else embed]
        if components:
            msg["components"] = components
        await self._http.post(
            f"/interactions/{self.id}/{self.token}/callback",
            json={"type": 4, "data": msg},
        )

    async def defer(self, *, ephemeral: bool = False) -> None:
        flags = 64 if ephemeral else 0
        await self._http.post(
            f"/interactions/{self.id}/{self.token}/callback",
            json={"type": 5, "data": {"flags": flags}},
        )


# ──────────────────────────────────────────────────────────────────────────────
#  ComponentRouter
# ──────────────────────────────────────────────────────────────────────────────

class ComponentRouter:
    """
    Routes button and select-menu interactions to handlers by ``custom_id``
    pattern.

    Patterns support:
    - Exact strings:  ``"confirm:yes"``
    - Glob prefix:    ``"page:*"``
    - Named params:   ``"item:{item_id}:buy"`` → ``interaction.params["item_id"]``
    - Regex:          ``re.compile(r"vote:(?P<choice>\\w+)")``

    Usage::

        router = ComponentRouter()

        @router.button("confirm:yes")
        async def on_confirm(interaction: ComponentInteraction):
            await interaction.respond("✅ Confirmed!", ephemeral=True)

        @router.button("page:{page_num}")
        async def on_page(interaction: ComponentInteraction):
            page = int(interaction.params["page_num"])
            await interaction.respond(f"Page {page}")

        @router.select("color_picker")
        async def on_color(interaction: ComponentInteraction):
            color = interaction.values[0]
            await interaction.respond(f"You picked {color}!")

        # In your bot's interaction handler:
        # await router.dispatch(raw_data, http)
    """

    def __init__(self) -> None:
        # list of (compiled_pattern, handler, is_select)
        self._routes: List[Tuple[Any, Callable, bool]] = []

    # ── Registration decorators ───────────────────────────────────────────────

    def button(self, pattern: Any) -> Callable:
        """Register a handler for button interactions matching *pattern*."""
        def decorator(func: Callable) -> Callable:
            self._routes.append((self._compile(pattern), func, False))
            return func
        return decorator

    def select(self, pattern: Any) -> Callable:
        """Register a handler for select-menu interactions matching *pattern*."""
        def decorator(func: Callable) -> Callable:
            self._routes.append((self._compile(pattern), func, True))
            return func
        return decorator

    def route(self, pattern: Any) -> Callable:
        """Register a handler for ANY component type matching *pattern*."""
        def decorator(func: Callable) -> Callable:
            self._routes.append((self._compile(pattern), func, None))
            return func
        return decorator

    # ── Dispatch ─────────────────────────────────────────────────────────────

    async def dispatch(self, data: dict, http: Any) -> bool:
        """
        Try to route *data* to a matching handler.

        Returns True if a handler was found and called, False otherwise.
        """
        interaction = ComponentInteraction(data, http)
        ctype = interaction.component_type

        for pattern, handler, expected_select in self._routes:
            # Type filter: None = any, True = select, False = button
            if expected_select is True and ctype not in (
                ComponentType.SELECT_MENU,
                ComponentType.USER_SELECT,
                ComponentType.ROLE_SELECT,
                ComponentType.MENTIONABLE_SELECT,
                ComponentType.CHANNEL_SELECT,
            ):
                continue
            if expected_select is False and ctype != ComponentType.BUTTON:
                continue

            params = self._match(pattern, interaction.custom_id)
            if params is not None:
                interaction.params = params
                try:
                    await handler(interaction)
                except Exception as exc:
                    log.exception(f"ComponentRouter handler error for {interaction.custom_id!r}: {exc}")
                return True

        return False

    # ── Pattern helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _compile(pattern: Any):
        """Turn a string/glob/regex into a compiled internal representation."""
        if hasattr(pattern, "match"):
            return pattern  # already a compiled regex
        if isinstance(pattern, str):
            if "{" in pattern:
                # Named param style: "item:{item_id}:buy" → named groups regex
                regex = re.escape(pattern)
                regex = re.sub(r"\\{(\w+)\\}", r"(?P<\1>[^:]+)", regex)
                return re.compile(f"^{regex}$")
            if pattern.endswith("*"):
                prefix = re.escape(pattern[:-1])
                return re.compile(f"^{prefix}.*$")
            # Exact match
            return pattern
        raise TypeError(f"Unsupported pattern type: {type(pattern)}")

    @staticmethod
    def _match(pattern: Any, custom_id: str) -> Optional[Dict[str, str]]:
        """Return captured params dict if pattern matches, else None."""
        if isinstance(pattern, str):
            return {} if pattern == custom_id else None
        m = pattern.match(custom_id)
        if m:
            return m.groupdict()
        return None


# ──────────────────────────────────────────────────────────────────────────────
#  ModalRouter
# ──────────────────────────────────────────────────────────────────────────────

class ModalRouter:
    """
    Routes modal submit interactions to handlers by ``custom_id`` pattern.

    Same pattern syntax as ``ComponentRouter``.

    Usage::

        modal_router = ModalRouter()

        @modal_router.modal("feedback:submit")
        async def on_feedback(interaction: ModalSubmitInteraction):
            text = interaction.get("feedback_text")
            email = interaction.get("email", "not provided")
            await interaction.respond(f"Thanks! We'll reply to {email}.", ephemeral=True)

        @modal_router.modal("report:{type}")
        async def on_report(interaction: ModalSubmitInteraction):
            report_type = interaction.params["type"]
            reason = interaction.get("reason")
            await interaction.respond(f"Report ({report_type}) received.", ephemeral=True)
    """

    def __init__(self) -> None:
        self._routes: List[Tuple[Any, Callable]] = []

    def modal(self, pattern: Any) -> Callable:
        """Register a handler for modal submits matching *pattern*."""
        def decorator(func: Callable) -> Callable:
            self._routes.append((ComponentRouter._compile(pattern), func))
            return func
        return decorator

    async def dispatch(self, data: dict, http: Any) -> bool:
        """
        Try to route *data* to a matching modal handler.

        Returns True if dispatched, False if no route matched.
        """
        interaction = ModalSubmitInteraction(data, http)

        for pattern, handler in self._routes:
            params = ComponentRouter._match(pattern, interaction.custom_id)
            if params is not None:
                interaction.params = params
                try:
                    await handler(interaction)
                except Exception as exc:
                    log.exception(f"ModalRouter handler error for {interaction.custom_id!r}: {exc}")
                return True

        return False


# ──────────────────────────────────────────────────────────────────────────────
#  Combined AppRouter — mounts both routers + extends Interaction.respond_modal
# ──────────────────────────────────────────────────────────────────────────────

class AppRouter:
    """
    Convenience wrapper that holds one ``ComponentRouter`` and one ``ModalRouter``
    and integrates with the ``Client`` in a single call.

    Usage::

        router = AppRouter()

        @router.button("ping")
        async def on_ping(inter):
            await inter.respond("Pong!")

        @router.modal("form:submit")
        async def on_form(inter):
            name = inter.get("name")
            await inter.respond(f"Hi {name}!")

        bot.mount_router(router)   # wires into Client.dispatch
    """

    def __init__(self) -> None:
        self.components = ComponentRouter()
        self.modals = ModalRouter()

    # Proxy decorators
    def button(self, pattern: Any) -> Callable:
        return self.components.button(pattern)

    def select(self, pattern: Any) -> Callable:
        return self.components.select(pattern)

    def route(self, pattern: Any) -> Callable:
        return self.components.route(pattern)

    def modal(self, pattern: Any) -> Callable:
        return self.modals.modal(pattern)

    async def dispatch_interaction(self, data: dict, http: Any) -> bool:
        """
        Route an INTERACTION_CREATE payload to the appropriate sub-router.

        Returns True if handled.
        """
        itype = data.get("type", 0)

        if itype == 3:  # MESSAGE_COMPONENT
            return await self.components.dispatch(data, http)
        elif itype == 5:  # MODAL_SUBMIT
            return await self.modals.dispatch(data, http)

        return False
