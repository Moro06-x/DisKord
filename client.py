"""
pydisk.client
~~~~~~~~~~~~~
The main bot client — connects everything together.
"""

import asyncio
import functools
import logging
import urllib.parse
from typing import Any, Callable, Dict, List, Optional

from .core.rest import HTTPClient
from .core.gateway import GatewayClient, Intents
from .core.async_utils import BackgroundTask
from .commands import Command, CommandRegistry, Group, Option
from .models import Interaction, Message, Embed
from .components import AppRouter
from .events import EventEmitter

log = logging.getLogger("pydisk.client")


class Client:
    """
    The main pydisk bot client.

    Usage::

        import pydisk

        bot = pydisk.Client(token="TOKEN", prefix="!")

        @bot.slash_command(description="Say hello")
        async def hello(interaction: pydisk.Interaction):
            await interaction.respond("Hello!")

        bot.run()
    """

    def __init__(
        self,
        token: str,
        *,
        prefix: str = "!",
        application_id: Optional[str] = None,
        intents: int = Intents.DEFAULT,
    ):
        self.token = token
        self.prefix = prefix
        self.application_id = application_id
        self.intents = intents

        self.http = HTTPClient(token)
        self.http._client_ref = self  # lets SmartResponder/confirm/prompt hook in

        self.registry = CommandRegistry()
        self._emitter = EventEmitter()
        self._cogs: List[Any] = []
        self._gateway: Optional[GatewayClient] = None
        self.user: Optional[dict] = None
        self._router: Optional[AppRouter] = None
        self._background_tasks: List[BackgroundTask] = []
        self._modal_handlers: Dict[str, Callable] = {}

    # ------------------------------------------------------------------ #
    #  Event system
    # ------------------------------------------------------------------ #

    def on(self, event: str, priority: int = 0):
        """Decorator to register a persistent event handler.

        Supports wildcards: ``"*"``, ``"message.*"``

            @bot.on("message")
            async def on_message(message): ...

            @bot.on("*")
            async def catch_all(event, data): ...
        """
        return self._emitter.on(event, priority=priority)

    def once(self, event: str, priority: int = 0):
        """Register a one-time event handler (auto-removed after first fire).

            @bot.once("ready")
            async def on_ready(): ...
        """
        return self._emitter.once(event, priority=priority)

    def off(self, event: str, callback: Callable) -> None:
        """Remove a specific event handler."""
        self._emitter.off(event, callback)

    async def dispatch(self, event: str, *args, **kwargs) -> None:
        """Fire all handlers registered for an event."""
        await self._emitter.emit(event, *args, **kwargs)

    # ------------------------------------------------------------------ #
    #  Command registration
    # ------------------------------------------------------------------ #

    def slash_command(
        self,
        name: Optional[str] = None,
        description: str = "No description.",
        *,
        options: Optional[List[Option]] = None,
        guild_ids: Optional[List[str]] = None,
        checks: Optional[List[Callable]] = None,
        cooldown: Optional[int] = None,
        permissions=None,
        premium_only: bool = False,
    ):
        """Decorator to register a slash command.

            @bot.slash_command(description="Ping the bot")
            async def ping(interaction):
                await interaction.respond("Pong!")
        """
        def decorator(func: Callable):
            cmd_name = name or func.__name__
            cmd = Command(
                func, cmd_name, description,
                options=options,
                guild_ids=guild_ids,
                checks=checks or [],
                cooldown_seconds=cooldown,
                permissions=permissions,
                premium_only=premium_only,
            )
            self.registry.add_command(cmd, slash=True)
            return func
        return decorator

    def prefix_command(
        self,
        name: Optional[str] = None,
        description: str = "No description.",
        *,
        aliases: Optional[List[str]] = None,
        checks: Optional[List[Callable]] = None,
    ):
        """Decorator to register a prefix (text) command.

            @bot.prefix_command(aliases=["h"])
            async def help(message):
                await bot.send_message(message.channel_id, "Help text here.")
        """
        def decorator(func: Callable):
            cmd_name = name or func.__name__
            cmd = Command(
                func, cmd_name, description,
                aliases=aliases or [],
                checks=checks or [],
            )
            self.registry.add_command(cmd, slash=False, prefix=True)
            return func
        return decorator

    def group(self, name: str, description: str = "A command group.") -> Group:
        """Create and register a slash command group.

            config = bot.group("config", "Bot configuration commands")

            @config.command(description="Set the log channel")
            async def log(interaction, channel_id: str): ...
        """
        grp = Group(name, description)
        self.registry.add_group(grp)
        return grp

    def add_cog(self, cog: Any) -> None:
        """Register a Cog (class-based command container).

            class Moderation(pydisk.Cog):
                @pydisk.command(description="Kick a user")
                async def kick(self, interaction, user_id: str): ...

            bot.add_cog(Moderation())
        """
        self._cogs.append(cog)
        for attr_name in dir(cog):
            attr = getattr(cog, attr_name, None)
            if attr is None:
                continue
            cmd = getattr(attr, "__diskord_command__", None)
            if isinstance(cmd, Command):
                cmd.callback = functools.partial(cmd.callback, cog)
                self.registry.add_command(cmd, slash=True)

    # ------------------------------------------------------------------ #
    #  Component & Modal router
    # ------------------------------------------------------------------ #

    def mount_router(self, router: AppRouter) -> None:
        """Attach an AppRouter so component/modal interactions are auto-dispatched.

            router = pydisk.AppRouter()

            @router.button("confirm:yes")
            async def on_confirm(inter): ...

            bot.mount_router(router)
        """
        self._router = router

    def modal(self, custom_id: str):
        """Decorator to register a modal submit handler by exact custom_id.

        For pattern-based routing (``"report:{type}"`` etc.) use an
        ``AppRouter`` with ``@router.modal(pattern)`` instead.

            @bot.modal("feedback_form")
            async def on_feedback(interaction: pydisk.Interaction):
                text = interaction.options.get("feedback_text", "")
                await interaction.respond(f"Got it: {text}", ephemeral=True)
        """
        def decorator(func: Callable) -> Callable:
            self._modal_handlers[custom_id] = func
            return func
        return decorator

    # ------------------------------------------------------------------ #
    #  Background tasks
    # ------------------------------------------------------------------ #

    def background_task(
        self,
        *,
        name: Optional[str] = None,
        max_retries: int = 0,
    ) -> Callable:
        """Decorator that registers a coroutine as an auto-starting background task.

        The task starts when ``run()`` is called and restarts automatically on
        failure (with exponential back-off) up to ``max_retries`` times
        (0 = infinite).

            @bot.background_task(name="status_updater")
            async def update_status():
                while True:
                    await bot.change_presence(activity="Watching the server")
                    await asyncio.sleep(60)
        """
        def decorator(func: Callable) -> Callable:
            bt = BackgroundTask(func, name=name or func.__name__, max_retries=max_retries)
            self._background_tasks.append(bt)
            return func
        return decorator

    # ------------------------------------------------------------------ #
    #  Slash command sync
    # ------------------------------------------------------------------ #

    async def sync_commands(self) -> None:
        """Push all registered slash commands to Discord.

        Called automatically by ``run(sync=True)`` (the default).
        You can also call it manually after adding commands at runtime.
        """
        if not self.registry.slash_commands and not self.registry.groups:
            return

        app_id = self.application_id or (await self._get_application_id())

        guild_specific: Dict[str, list] = {}
        global_cmds: list = []

        # Separate guild-specific from global commands
        for cmd in self.registry.slash_commands.values():
            if cmd.guild_ids:
                for gid in cmd.guild_ids:
                    guild_specific.setdefault(gid, []).append(cmd.to_application_command())
            else:
                global_cmds.append(cmd.to_application_command())

        for grp in self.registry.groups.values():
            global_cmds.append(grp.to_application_command())

        # Deduplicate by name (guards against aliases registering duplicates)
        def _dedup(payloads: list) -> list:
            seen: set = set()
            out = []
            for p in payloads:
                if p["name"] not in seen:
                    out.append(p)
                    seen.add(p["name"])
            return out

        deduped_global = _dedup(global_cmds)
        if deduped_global:
            await self.http.put(f"/applications/{app_id}/commands", json=deduped_global)
            log.info(f"Synced {len(deduped_global)} global slash command(s).")

        for guild_id, cmds in guild_specific.items():
            deduped = _dedup(cmds)
            await self.http.put(
                f"/applications/{app_id}/guilds/{guild_id}/commands",
                json=deduped,
            )
            log.info(f"Synced {len(deduped)} command(s) to guild {guild_id}.")

    async def _get_application_id(self) -> str:
        data = await self.http.get("/oauth2/applications/@me")
        self.application_id = data["id"]
        return self.application_id

    # ------------------------------------------------------------------ #
    #  Gateway event routing
    # ------------------------------------------------------------------ #

    async def _on_gateway_event(self, event: str, data: dict) -> None:
        """Called by GatewayClient for every dispatched Discord event."""
        if event == "ready":
            self.user = data.get("user", {})
            log.info(
                f"Logged in as {self.user.get('username')}#{self.user.get('discriminator')}"
            )
            await self.dispatch("ready")

        elif event == "interaction_create":
            await self._process_interaction(data)

        elif event == "message_create":
            msg = Message.from_data(data)
            await self._process_message(msg)
            await self.dispatch("message", msg)

        else:
            await self.dispatch(event, data)

    async def _process_interaction(self, data: dict) -> None:
        """Route an INTERACTION_CREATE payload to the right handler."""
        itype = data.get("type", 0)

        # ── Type 5: Modal submit ─────────────────────────────────────────
        if itype == 5:
            custom_id = data.get("data", {}).get("custom_id", "")

            # 1. Check exact-match modal handlers registered via @bot.modal()
            handler = self._modal_handlers.get(custom_id)
            if handler:
                modal_options: Dict[str, str] = {}
                for row in data.get("data", {}).get("components", []):
                    for comp in row.get("components", []):
                        modal_options[comp["custom_id"]] = comp.get("value", "")
                interaction = Interaction.from_data(data, http=self.http)
                interaction.options = modal_options
                try:
                    await handler(interaction)
                except Exception as e:
                    await self.dispatch("command_error", interaction, e)
                return

            # 2. Fall through to AppRouter (handles pattern-based modal routes)
            if self._router:
                handled = await self._router.dispatch_interaction(data, self.http)
                if handled:
                    return

        # ── Type 3: Message component (button / select) ──────────────────
        elif itype == 3:
            if self._router:
                handled = await self._router.dispatch_interaction(data, self.http)
                if handled:
                    return

        # ── Build Interaction and dispatch "interaction" event ────────────
        interaction = Interaction.from_data(data, http=self.http)
        await self.dispatch("interaction", interaction)

        # ── Slash command / sub-command routing ───────────────────────────
        cmd_name = interaction.command_name
        if not cmd_name:
            return

        # Check for command group first
        group = self.registry.get_group(cmd_name)
        if group:
            sub_options = data.get("data", {}).get("options", [])
            if sub_options and sub_options[0].get("type") == 1:
                subcommand = sub_options[0]["name"]
                sub_kwargs = {
                    o["name"]: o.get("value")
                    for o in sub_options[0].get("options", [])
                }
                try:
                    await group.invoke(interaction, subcommand, **sub_kwargs)
                except Exception as e:
                    await self.dispatch("command_error", interaction, e)
            return

        # Plain slash command
        cmd = self.registry.get_slash(cmd_name)
        if cmd:
            try:
                await cmd.invoke(interaction, **interaction.options)
            except Exception as e:
                await self.dispatch("command_error", interaction, e)

    async def _process_message(self, message: Message) -> None:
        """Handle prefix command dispatch for a message."""
        if not message.content.startswith(self.prefix):
            return
        parts = message.content[len(self.prefix):].strip().split()
        if not parts:
            return
        name, *args = parts
        cmd = self.registry.get_prefix(name)
        if cmd:
            try:
                await cmd.invoke(message, *args)
            except Exception as e:
                await self.dispatch("command_error", message, e)

    # ------------------------------------------------------------------ #
    #  Presence
    # ------------------------------------------------------------------ #

    async def change_presence(
        self,
        *,
        status: str = "online",
        activity: str = "",
        activity_type: int = 0,
    ) -> None:
        """Update the bot's status and activity in real time.

        Parameters
        ----------
        status:
            ``"online"``, ``"idle"``, ``"dnd"``, or ``"invisible"``.
        activity:
            Display name of the activity (e.g. ``"the server"``).
        activity_type:
            0 = Playing, 1 = Streaming, 2 = Listening, 3 = Watching, 5 = Competing.
        """
        if self._gateway:
            await self._gateway.update_presence(
                status=status,
                activity_name=activity,
                activity_type=activity_type,
            )

    # ------------------------------------------------------------------ #
    #  Convenience REST helpers
    # ------------------------------------------------------------------ #

    async def send_message(
        self,
        channel_id: str,
        content: str = "",
        *,
        embed: Optional[Embed] = None,
        tts: bool = False,
    ) -> dict:
        """Send a message to a channel."""
        payload: Dict[str, Any] = {"content": content, "tts": tts}
        if embed:
            payload["embeds"] = [embed.to_dict()]
        return await self.http.post(f"/channels/{channel_id}/messages", json=payload)

    async def delete_message(self, channel_id: str, message_id: str) -> None:
        """Delete a message."""
        await self.http.delete(f"/channels/{channel_id}/messages/{message_id}")

    async def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        """Add a reaction to a message."""
        encoded = urllib.parse.quote(emoji, safe="")
        await self.http.put(
            f"/channels/{channel_id}/messages/{message_id}/reactions/{encoded}/@me"
        )

    async def get_guild(self, guild_id: str) -> dict:
        """Fetch a guild by ID."""
        return await self.http.get(f"/guilds/{guild_id}")

    async def get_member(self, guild_id: str, user_id: str) -> dict:
        """Fetch a guild member by user ID."""
        return await self.http.get(f"/guilds/{guild_id}/members/{user_id}")

    async def kick_member(self, guild_id: str, user_id: str, *, reason: str = "") -> None:
        """Kick a member from a guild."""
        params = {"reason": reason} if reason else None
        await self.http.delete(f"/guilds/{guild_id}/members/{user_id}", params=params)

    async def ban_member(
        self,
        guild_id: str,
        user_id: str,
        *,
        reason: str = "",
        delete_message_days: int = 0,
    ) -> None:
        """Ban a user from a guild."""
        params = {"reason": reason} if reason else None
        await self.http.put(
            f"/guilds/{guild_id}/bans/{user_id}",
            json={"delete_message_days": delete_message_days},
            params=params,
        )

    async def create_role(
        self,
        guild_id: str,
        name: str,
        *,
        color: int = 0,
        hoist: bool = False,
    ) -> dict:
        """Create a role in a guild."""
        return await self.http.post(
            f"/guilds/{guild_id}/roles",
            json={"name": name, "color": color, "hoist": hoist},
        )

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    async def close(self) -> None:
        """Gracefully shut down the gateway, background tasks, and HTTP session."""
        if self._gateway:
            await self._gateway.close()
        for bt in self._background_tasks:
            await bt.stop()
        await self.http.close()

    def run(self, *, sync: bool = True) -> None:
        """Connect to Discord and block until Ctrl+C.

        Parameters
        ----------
        sync:
            If True (default), push slash commands to Discord before connecting.
            Set to False during development if you don't want commands re-synced
            every restart.
        """
        async def _run():
            if sync:
                await self.sync_commands()

            for bt in self._background_tasks:
                bt.start()

            self._gateway = GatewayClient(
                token=self.token,
                intents=self.intents,
                dispatch=self._on_gateway_event,
            )
            try:
                await self._gateway.connect()
            finally:
                await self.close()

        try:
            asyncio.run(_run())
        except KeyboardInterrupt:
            print("\nShutting down...")
