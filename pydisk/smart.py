"""
pydisk.smart
~~~~~~~~~~~~
Smart Context Auto-Parsing + Smart Response Engine.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .embed import Embed, EmbedBuilder

__all__ = [
    "SmartContext",
    "SmartResponder",
    "parse_context",
]


_USER_MENTION_RE    = re.compile(r"<@!?(\d+)>")
_ROLE_MENTION_RE    = re.compile(r"<@&(\d+)>")
_CHANNEL_MENTION_RE = re.compile(r"<#(\d+)>")


class SmartContext:
    """
    Unified context object that wraps either an Interaction or a Message.
    """

    def __init__(self, raw: Any, *, http: Any = None) -> None:
        self._raw = raw
        self._http = http or getattr(raw, "_http", None)
        self._is_interaction = hasattr(raw, "token")
        self._prefix_args: List[str] = []

        self._parse()

    def _parse(self) -> None:
        r = self._raw
        if self._is_interaction:
            self.user = getattr(r, "user", None)
            self.guild_id: Optional[str] = getattr(r, "guild_id", None)
            self.channel_id: str = getattr(r, "channel_id", "")
            self.locale: str = getattr(r, "locale", "en-US")
            self._options: Dict[str, Any] = getattr(r, "options", {})
            self._raw_data: Dict = getattr(r, "_raw", {})
            self._content: str = ""
        else:
            self.user = getattr(r, "author", None)
            self.guild_id = getattr(r, "guild_id", None)
            self.channel_id = getattr(r, "channel_id", "")
            self.locale = "en-US"
            self._options = {}
            self._raw_data = {}
            self._content = getattr(r, "content", "")
            parts = self._content.strip().split()
            self._prefix_args = parts[1:] if len(parts) > 1 else []

    @property
    def is_slash(self) -> bool:
        return self._is_interaction and self._raw_data.get("type", 0) not in (3, 5)

    @property
    def is_prefix(self) -> bool:
        return not self._is_interaction

    @property
    def is_component(self) -> bool:
        return self._is_interaction and self._raw_data.get("type", 0) == 3

    @property
    def is_modal(self) -> bool:
        return self._is_interaction and self._raw_data.get("type", 0) == 5

    def arg(self, name: str, default: Any = None, *, pos: int = 0) -> Any:
        if self._is_interaction:
            return self._options.get(name, default)
        if pos < len(self._prefix_args):
            return self._prefix_args[pos]
        return default

    def args(self) -> List[str]:
        return list(self._prefix_args)

    def joined_args(self, start: int = 0) -> str:
        return " ".join(self._prefix_args[start:])

    @property
    def mentioned_users(self) -> List[str]:
        content = self._content or str(self._options)
        return _USER_MENTION_RE.findall(content)

    @property
    def mentioned_roles(self) -> List[str]:
        content = self._content or str(self._options)
        return _ROLE_MENTION_RE.findall(content)

    @property
    def mentioned_channels(self) -> List[str]:
        content = self._content or str(self._options)
        return _CHANNEL_MENTION_RE.findall(content)

    @property
    def attachments(self) -> List[Dict[str, Any]]:
        raw = self._raw_data or {}
        return raw.get("attachments", [])

    @property
    def attachment_urls(self) -> List[str]:
        return [a.get("url", "") for a in self.attachments if a.get("url")]

    @property
    def interaction(self) -> Optional[Any]:
        return self._raw if self._is_interaction else None

    @property
    def message(self) -> Optional[Any]:
        return self._raw if not self._is_interaction else None

    @property
    def user_id(self) -> str:
        return getattr(self.user, "id", "")

    @property
    def username(self) -> str:
        return getattr(self.user, "username", "Unknown")

    def __repr__(self) -> str:
        kind = "slash" if self.is_slash else ("prefix" if self.is_prefix else "component")
        return f"<SmartContext type={kind} user={self.user_id} channel={self.channel_id}>"


def parse_context(raw: Any, *, http: Any = None) -> SmartContext:
    return SmartContext(raw, http=http)


class SmartResponder:
    """
    Unified response helper for both slash interactions and prefix messages.
    """

    def __init__(self, ctx: SmartContext) -> None:
        self._ctx = ctx
        self._replied = False

    async def reply(
        self,
        content: str = "",
        *,
        embed: Optional[Embed] = None,
        ephemeral: bool = False,
        embeds: Optional[List[Embed]] = None,
    ) -> None:
        ctx = self._ctx
        if ctx.is_slash or ctx.is_component:
            interaction = ctx.interaction
            flags = 64 if ephemeral else 0
            payload: Dict[str, Any] = {"content": content, "flags": flags}
            if embed:
                payload["embeds"] = [embed.to_dict()]
            elif embeds:
                payload["embeds"] = [e.to_dict() for e in embeds]

            if not self._replied:
                await ctx._http.post(
                    f"/interactions/{interaction.id}/{interaction.token}/callback",
                    json={"type": 4, "data": payload},
                )
                self._replied = True
            else:
                # BUG FIX: followup was using /webhooks/me/{token} which is invalid.
                # Correct: /webhooks/{application_id}/{token}
                app_id = getattr(interaction, "application_id", None)
                if not app_id:
                    app_id = (await ctx._http.get("/oauth2/applications/@me"))["id"]
                await ctx._http.post(
                    f"/webhooks/{app_id}/{interaction.token}",
                    json={k: v for k, v in payload.items() if k != "flags"},
                )
        else:
            msg_payload: Dict[str, Any] = {"content": content}
            if embed:
                msg_payload["embeds"] = [embed.to_dict()]
            elif embeds:
                msg_payload["embeds"] = [e.to_dict() for e in embeds]

            channel = ctx.channel_id
            if ephemeral:
                try:
                    dm = await ctx._http.post(
                        "/users/@me/channels",
                        json={"recipient_id": ctx.user_id},
                    )
                    channel = dm["id"]
                except Exception:
                    pass
            await ctx._http.post(f"/channels/{channel}/messages", json=msg_payload)
            self._replied = True

    async def success(self, title: str = "✅ Success", description: str = "", **kw) -> None:
        await self.reply(embed=EmbedBuilder.success(title, description=description), **kw)

    async def error(self, title: str = "❌ Error", description: str = "", **kw) -> None:
        await self.reply(embed=EmbedBuilder.error(title, description=description), **kw)

    async def warning(self, title: str = "⚠️ Warning", description: str = "", **kw) -> None:
        await self.reply(embed=EmbedBuilder.warning(title, description=description), **kw)

    async def info(self, title: str = "ℹ️ Info", description: str = "", **kw) -> None:
        await self.reply(embed=EmbedBuilder.info(title, description=description), **kw)

    async def ephemeral(self, content: str = "", **kw) -> None:
        await self.reply(content, ephemeral=True, **kw)

    async def typing(self) -> None:
        try:
            await self._ctx._http.post(f"/channels/{self._ctx.channel_id}/typing")
        except Exception:
            pass

    async def confirm(
        self,
        question: str,
        *,
        yes_label: str = "Yes",
        no_label: str = "No",
        timeout: float = 30.0,
    ) -> Optional[bool]:
        """
        Send a Yes/No button prompt and wait for the user to click.
        Returns True for Yes, False for No, None on timeout.

        BUG FIX: original used asyncio.get_event_loop().create_future() which is
        deprecated in Python 3.10+ (and wrong when called from a running loop).
        Use asyncio.get_running_loop() instead.
        """
        import uuid
        yes_id = f"_confirm_yes_{uuid.uuid4().hex[:8]}"
        no_id  = f"_confirm_no_{uuid.uuid4().hex[:8]}"

        from .components import ActionRow, Button, ButtonStyle
        row = ActionRow(components=[
            Button(label=yes_label, custom_id=yes_id, style=ButtonStyle.SUCCESS),
            Button(label=no_label,  custom_id=no_id,  style=ButtonStyle.DANGER),
        ])

        ctx = self._ctx
        payload: Dict[str, Any] = {
            "content": question,
            "components": [row.to_dict()],
        }

        # BUG FIX: use get_running_loop() not get_event_loop()
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()

        client = getattr(ctx._http, "_client_ref", None)

        async def _handle_interaction(data: dict) -> None:
            cid = data.get("data", {}).get("custom_id", "")
            if cid not in (yes_id, no_id):
                return
            if not future.done():
                future.set_result(cid == yes_id)

        if client:
            client._event_handlers.setdefault("interaction_create", []).append(_handle_interaction)

        if ctx.is_slash:
            await ctx._http.post(
                f"/interactions/{ctx.interaction.id}/{ctx.interaction.token}/callback",
                json={"type": 4, "data": payload},
            )
            self._replied = True
        else:
            await ctx._http.post(f"/channels/{ctx.channel_id}/messages", json=payload)

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            result = None
        finally:
            if client:
                handlers = client._event_handlers.get("interaction_create", [])
                if _handle_interaction in handlers:
                    handlers.remove(_handle_interaction)

        return result

    async def prompt(
        self,
        question: str,
        *,
        timeout: float = 60.0,
        validator: Optional[Any] = None,
    ) -> Optional[str]:
        """
        Ask the user a question and wait for their next message.

        BUG FIX: same asyncio.get_event_loop() → get_running_loop() fix.
        """
        ctx = self._ctx
        await self.reply(question)

        # BUG FIX: use get_running_loop()
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        client = getattr(ctx._http, "_client_ref", None)

        async def _handle_message(msg: Any) -> None:
            if getattr(getattr(msg, "author", None), "id", None) != ctx.user_id:
                return
            if msg.channel_id != ctx.channel_id:
                return
            content = msg.content or ""
            if validator:
                try:
                    ok = await validator(content)
                    if not ok:
                        return
                except Exception:
                    return
            if not future.done():
                future.set_result(content)

        if client:
            client._event_handlers.setdefault("message", []).append(_handle_message)

        try:
            answer = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            answer = None
        finally:
            if client:
                handlers = client._event_handlers.get("message", [])
                if _handle_message in handlers:
                    handlers.remove(_handle_message)

        return answer

    async def paginate(
        self,
        pages: List[Embed],
        *,
        timeout: float = 120.0,
    ) -> None:
        """
        Send the first page of an embed list with Prev/Next buttons.
        """
        if not pages:
            return

        import uuid
        nav_id = uuid.uuid4().hex[:8]
        prev_id = f"_page_prev_{nav_id}"
        next_id = f"_page_next_{nav_id}"

        from .components import ActionRow, Button, ButtonStyle
        current_page = [0]
        total = len(pages)

        def _build_row(page_idx: int) -> ActionRow:
            return ActionRow(components=[
                Button(
                    label="◀",
                    custom_id=prev_id,
                    style=ButtonStyle.SECONDARY,
                    disabled=page_idx <= 0,
                ),
                Button(
                    label=f"{max(page_idx + 1, 1)}/{total}",
                    custom_id=f"_page_noop_{nav_id}",
                    style=ButtonStyle.SECONDARY,
                    disabled=True,
                ),
                Button(
                    label="▶",
                    custom_id=next_id,
                    style=ButtonStyle.SECONDARY,
                    disabled=page_idx >= total - 1,
                ),
            ])

        ctx = self._ctx
        row = _build_row(0)
        msg_payload: Dict[str, Any] = {
            "embeds": [pages[0].to_dict()],
            "components": [row.to_dict()],
        }

        msg_id: Optional[str] = None

        if ctx.is_slash:
            await ctx._http.post(
                f"/interactions/{ctx.interaction.id}/{ctx.interaction.token}/callback",
                json={"type": 4, "data": msg_payload},
            )
            self._replied = True
            try:
                app_id = getattr(ctx.interaction, "application_id", None)
                if not app_id:
                    app_id = (await ctx._http.get("/oauth2/applications/@me"))["id"]
                orig = await ctx._http.get(
                    f"/webhooks/{app_id}/{ctx.interaction.token}/messages/@original"
                )
                msg_id = orig["id"]
            except Exception:
                pass
        else:
            resp = await ctx._http.post(
                f"/channels/{ctx.channel_id}/messages",
                json=msg_payload,
            )
            msg_id = resp["id"]

        client = getattr(ctx._http, "_client_ref", None)
        expiry = time.monotonic() + timeout

        async def _handle_nav(data: dict) -> None:
            if time.monotonic() > expiry:
                return
            cid = data.get("data", {}).get("custom_id", "")
            if cid not in (prev_id, next_id):
                return
            if cid == prev_id and current_page[0] > 0:
                current_page[0] -= 1
            elif cid == next_id and current_page[0] < total - 1:
                current_page[0] += 1
            else:
                # At boundary; still must ACK the interaction
                int_id = data.get("id")
                int_token = data.get("token")
                try:
                    await ctx._http.post(
                        f"/interactions/{int_id}/{int_token}/callback",
                        json={"type": 6},  # deferred update — no visible change
                    )
                except Exception:
                    pass
                return

            new_row = _build_row(current_page[0])
            edit_payload = {
                "embeds": [pages[current_page[0]].to_dict()],
                "components": [new_row.to_dict()],
            }
            try:
                int_id = data.get("id")
                int_token = data.get("token")
                await ctx._http.post(
                    f"/interactions/{int_id}/{int_token}/callback",
                    json={"type": 7, "data": edit_payload},
                )
            except Exception:
                pass

        if client:
            client._event_handlers.setdefault("interaction_create", []).append(_handle_nav)

        async def _cleanup() -> None:
            await asyncio.sleep(timeout)
            if client:
                handlers = client._event_handlers.get("interaction_create", [])
                if _handle_nav in handlers:
                    handlers.remove(_handle_nav)
            # Disable buttons on the original message
            if msg_id:
                disabled_row = ActionRow(components=[
                    Button(label="◀", custom_id=prev_id, style=ButtonStyle.SECONDARY, disabled=True),
                    Button(label=f"1/{total}", custom_id=f"_page_noop_{nav_id}", style=ButtonStyle.SECONDARY, disabled=True),
                    Button(label="▶", custom_id=next_id, style=ButtonStyle.SECONDARY, disabled=True),
                ])
                try:
                    await ctx._http.patch(
                        f"/channels/{ctx.channel_id}/messages/{msg_id}",
                        json={"components": [disabled_row.to_dict()]},
                    )
                except Exception:
                    pass

        asyncio.ensure_future(_cleanup())
