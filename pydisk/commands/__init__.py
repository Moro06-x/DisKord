"""
pydisk.commands
~~~~~~~~~~~~~~~~
Command framework supporting both slash commands and prefix commands.
"""

import asyncio
import inspect
import time
from typing import Any, Callable, Dict, List, Optional, Union
from functools import wraps

from ..models import Interaction, Message


# ─────────────────────────────────────────────
#  Exceptions
# ─────────────────────────────────────────────

class CommandError(Exception):
    """Base for all command-related errors."""

class CheckFailure(CommandError):
    """A check on a command returned False."""
    def __init__(self, message: str = "Check failed.", *, reason: str = "unknown"):
        self.reason = reason   # "cooldown" | "permission" | "premium" | "guild_only" | "owner" | "custom"
        super().__init__(message)

class CooldownError(CommandError):
    """Command is on cooldown."""
    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"Command on cooldown. Try again in {retry_after:.1f}s")

class MissingPermission(CheckFailure):
    """Invoker is missing a required permission or role."""
    def __init__(self, missing: List[str]):
        self.missing = missing
        super().__init__(
            f"Missing permission(s): {', '.join(missing)}",
            reason="permission",
        )

class PremiumRequired(CheckFailure):
    """Command requires a premium subscription."""
    def __init__(self):
        super().__init__("This command requires premium.", reason="premium")

class MissingArgument(CommandError):
    """A required argument was not provided."""

class BadArgument(CommandError):
    """An argument failed conversion."""


# ─────────────────────────────────────────────
#  Discord permission flags
# ─────────────────────────────────────────────

PERMISSION_FLAGS: Dict[str, int] = {
    "administrator":            1 << 3,
    "manage_guild":             1 << 5,
    "manage_roles":             1 << 28,
    "manage_channels":          1 << 4,
    "manage_messages":          1 << 13,
    "manage_nicknames":         1 << 27,
    "manage_webhooks":          1 << 29,
    "manage_emojis":            1 << 30,
    "kick_members":             1 << 1,
    "ban_members":              1 << 2,
    "moderate_members":         1 << 40,
    "mention_everyone":         1 << 17,
    "view_audit_log":           1 << 7,
    "send_messages":            1 << 11,
    "embed_links":              1 << 14,
    "attach_files":             1 << 15,
    "read_message_history":     1 << 16,
    "use_slash_commands":       1 << 31,
    "connect":                  1 << 20,
    "speak":                    1 << 21,
    "move_members":             1 << 24,
    "mute_members":             1 << 22,
    "deafen_members":           1 << 23,
}


# ─────────────────────────────────────────────
#  Option types
# ─────────────────────────────────────────────

class OptionType:
    SUB_COMMAND       = 1
    SUB_COMMAND_GROUP = 2
    STRING            = 3
    INTEGER           = 4
    BOOLEAN           = 5
    USER              = 6
    CHANNEL           = 7
    ROLE              = 8
    MENTIONABLE       = 9
    NUMBER            = 10
    ATTACHMENT        = 11


PYTHON_TYPE_TO_OPTION = {
    str:   OptionType.STRING,
    int:   OptionType.INTEGER,
    float: OptionType.NUMBER,
    bool:  OptionType.BOOLEAN,
}


# ─────────────────────────────────────────────
#  Option descriptor
# ─────────────────────────────────────────────

class Option:
    """Describes a slash command option."""
    def __init__(
        self,
        name: str,
        description: str,
        type: int = OptionType.STRING,
        required: bool = True,
        choices: Optional[List[Dict[str, Any]]] = None,
        autocomplete: bool = False,
    ):
        self.name = name
        self.description = description
        self.type = type
        self.required = required
        self.choices = choices or []
        self.autocomplete = autocomplete

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "type": self.type,
            "required": self.required,
        }
        if self.choices:
            d["choices"] = self.choices
        if self.autocomplete:
            d["autocomplete"] = True
        return d


# ─────────────────────────────────────────────
#  Cooldown bucket
# ─────────────────────────────────────────────

class Cooldown:
    def __init__(self, rate: int, per: float):
        self.rate = rate
        self.per = per
        self._cache: Dict[str, List[float]] = {}

    def check(self, key: str) -> Optional[float]:
        """Returns seconds to wait, or None if allowed."""
        now = time.monotonic()
        calls = [t for t in self._cache.get(key, []) if now - t < self.per]
        if len(calls) >= self.rate:
            retry_after = self.per - (now - calls[0])
            self._cache[key] = calls
            return retry_after
        calls.append(now)
        self._cache[key] = calls
        return None


# ─────────────────────────────────────────────
#  Permission checker
# ─────────────────────────────────────────────

class PermissionChecker:
    """
    Holds the required permissions/roles for a command and checks them
    against the interaction's member data.

    Accepts a mix of:
    - Permission name strings: "administrator", "manage_guild", …
    - Role IDs as strings:     "123456789012345678"
    - A single integer bitfield
    """

    def __init__(self, permissions: Union[List[Union[str, int]], int, str]):
        if isinstance(permissions, int):
            permissions = [permissions]
        elif isinstance(permissions, str):
            permissions = [permissions]

        self._perm_flags: int = 0    # combined bitfield of named perms
        self._role_ids: List[str] = []  # role IDs to check membership

        for p in permissions:
            if isinstance(p, int):
                self._perm_flags |= p
            elif isinstance(p, str):
                name = p.lower().replace(" ", "_").replace("-", "_")
                if name in PERMISSION_FLAGS:
                    self._perm_flags |= PERMISSION_FLAGS[name]
                else:
                    # Treat as a role ID
                    self._role_ids.append(p)

    def check(self, interaction: Any) -> bool:
        """Returns True if the member passes all permission requirements."""
        raw = interaction._raw if hasattr(interaction, "_raw") else {}
        member = raw.get("member", {})
        member_perms = int(member.get("permissions", "0") or "0")
        member_roles = member.get("roles", [])

        # administrator bypasses all checks
        if member_perms & PERMISSION_FLAGS["administrator"]:
            return True

        # Check named permission flags
        if self._perm_flags:
            if (member_perms & self._perm_flags) != self._perm_flags:
                return False

        # Check role IDs
        for role_id in self._role_ids:
            if role_id not in member_roles:
                return False

        return True

    def missing(self, interaction: Any) -> List[str]:
        """Return list of missing permission/role names for error messages."""
        raw = interaction._raw if hasattr(interaction, "_raw") else {}
        member = raw.get("member", {})
        member_perms = int(member.get("permissions", "0") or "0")
        member_roles = member.get("roles", [])

        if member_perms & PERMISSION_FLAGS["administrator"]:
            return []

        missing = []
        for name, flag in PERMISSION_FLAGS.items():
            if self._perm_flags & flag and not (member_perms & flag):
                missing.append(name)
        for role_id in self._role_ids:
            if role_id not in member_roles:
                missing.append(f"role:{role_id}")
        return missing


# ─────────────────────────────────────────────
#  Premium checker (pluggable)
# ─────────────────────────────────────────────

async def _default_premium_check(interaction: Any) -> bool:
    return False

PREMIUM_CHECK: Callable = _default_premium_check


# ─────────────────────────────────────────────
#  Base Command
# ─────────────────────────────────────────────

class Command:
    """Represents a single command (prefix or slash)."""

    def __init__(
        self,
        callback: Callable,
        name: str,
        description: str = "No description provided.",
        *,
        aliases: Optional[List[str]] = None,
        checks: Optional[List[Callable]] = None,
        cooldown: Optional[Cooldown] = None,
        options: Optional[List[Option]] = None,
        guild_ids: Optional[List[str]] = None,
        # ── New shorthand params ──────────────────
        cooldown_seconds: Optional[float] = None,
        permissions: Optional[Union[List[Union[str, int]], int, str]] = None,
        premium_only: bool = False,
    ):
        self.callback = callback
        self.name = name
        self.description = description
        self.aliases = aliases or []
        self.checks = checks or []
        self.options = options or []
        self.guild_ids = guild_ids
        self._auto_options: List[Option] = []
        self.premium_only = premium_only
        self._perm_checker: Optional[PermissionChecker] = None

        # cooldown_seconds=5  →  Cooldown(rate=1, per=5)
        if cooldown_seconds is not None:
            self.cooldown = Cooldown(rate=1, per=float(cooldown_seconds))
        else:
            self.cooldown = cooldown

        if permissions is not None:
            self._perm_checker = PermissionChecker(permissions)

        self._infer_options()

    def _infer_options(self):
        sig = inspect.signature(self.callback)
        params = list(sig.parameters.values())
        # Skip 'self'/'cls' and the first context param (interaction/message)
        skip = 1
        if params and params[0].name in ("self", "cls"):
            skip = 2
        for param in params[skip:]:
            ann = param.annotation
            opt_type = PYTHON_TYPE_TO_OPTION.get(ann, OptionType.STRING)
            required = param.default is inspect.Parameter.empty
            self._auto_options.append(Option(
                name=param.name,
                description=f"The {param.name} argument.",
                type=opt_type,
                required=required,
            ))

    @property
    def effective_options(self) -> List[Option]:
        return self.options if self.options else self._auto_options

    def to_application_command(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "options": [o.to_dict() for o in self.effective_options],
            "type": 1,
        }
        if self._perm_checker and self._perm_checker._perm_flags:
            payload["default_member_permissions"] = str(self._perm_checker._perm_flags)
        return payload

    async def invoke(self, ctx: Any, **kwargs) -> Any:
        """Run checks, cooldowns, permissions, premium — then execute."""

        # Custom checks
        for chk in self.checks:
            result = chk(ctx)
            if asyncio.iscoroutine(result):
                result = await result
            if not result:
                raise CheckFailure(
                    f"Check failed for '{self.name}'",
                    reason="custom",
                )

        # Guild-only (implicit when permissions are set)
        if self._perm_checker and not getattr(ctx, "guild_id", None):
            raise CheckFailure("This command can only be used in a server.", reason="guild_only")

        # Permission check
        if self._perm_checker:
            if not self._perm_checker.check(ctx):
                missing = self._perm_checker.missing(ctx)
                raise MissingPermission(missing)

        # Premium check
        if self.premium_only:
            has_premium = await PREMIUM_CHECK(ctx)
            if not has_premium:
                raise PremiumRequired()

        # Cooldown — per user
        if self.cooldown:
            key = getattr(getattr(ctx, "user", None), "id", "global")
            wait = self.cooldown.check(key)
            if wait is not None:
                raise CooldownError(wait)

        return await self.callback(ctx, **kwargs)


# ─────────────────────────────────────────────
#  Command Group
# ─────────────────────────────────────────────

class Group:
    """A group of slash sub-commands (e.g. /config set, /config get)."""

    def __init__(self, name: str, description: str = "A command group."):
        self.name = name
        self.description = description
        self.children: Dict[str, Command] = {}

    def command(
        self,
        name: Optional[str] = None,
        description: str = "No description.",
        **kwargs,
    ):
        def decorator(func: Callable):
            cmd_name = name or func.__name__
            cmd = Command(func, cmd_name, description, **kwargs)
            self.children[cmd_name] = cmd
            return cmd
        return decorator

    def to_application_command(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "type": 1,
            "options": [
                {
                    "type": OptionType.SUB_COMMAND,
                    "name": child.name,
                    "description": child.description,
                    "options": [o.to_dict() for o in child.effective_options],
                }
                for child in self.children.values()
            ],
        }

    async def invoke(self, ctx: Any, subcommand: str, **kwargs) -> Any:
        if subcommand not in self.children:
            raise CommandError(f"Unknown subcommand '{subcommand}'")
        return await self.children[subcommand].invoke(ctx, **kwargs)


# ─────────────────────────────────────────────
#  Decorators
# ─────────────────────────────────────────────

def command(
    name: Optional[str] = None,
    description: str = "No description.",
    **kwargs,
):
    """Mark a coroutine as a slash/prefix command."""
    def decorator(func: Callable):
        cmd_name = name or func.__name__
        cmd = Command(func, cmd_name, description, **kwargs)
        func.__diskord_command__ = cmd
        return func
    return decorator


def check(predicate: Callable):
    """Add a check to a command."""
    def decorator(func: Callable):
        cmd: Command = getattr(func, "__diskord_command__", None)
        if cmd:
            cmd.checks.append(predicate)
        else:
            if not hasattr(func, "__diskord_checks__"):
                func.__diskord_checks__ = []
            func.__diskord_checks__.append(predicate)
        return func
    return decorator


def cooldown(rate: int, per: float):
    """Apply a cooldown to a command (rate calls per `per` seconds)."""
    def decorator(func: Callable):
        cmd: Command = getattr(func, "__diskord_command__", None)
        if cmd:
            cmd.cooldown = Cooldown(rate, per)
        return func
    return decorator


# ─────────────────────────────────────────────
#  Built-in checks
# ─────────────────────────────────────────────

def is_owner(owner_id: str):
    async def predicate(ctx) -> bool:
        uid = getattr(getattr(ctx, "user", None), "id", None)
        return uid == owner_id
    return predicate


def guild_only():
    """
    BUG FIX: original returned bool(getattr(ctx, "guild_id", None)) directly,
    which correctly works as a predicate — but the check() decorator expects a
    *function* not the result. The predicate itself was fine; keeping as-is but
    ensuring it's a proper sync predicate.
    """
    def predicate(ctx) -> bool:
        return bool(getattr(ctx, "guild_id", None))
    return predicate


def has_role(role_id_or_name: str):
    """
    BUG FIX: original compared role string against member.roles which is a list
    of ROLE IDs, not role names. Discord doesn't send role names in interactions;
    only IDs are available. This now accepts a role ID and compares correctly.
    If you need name-based checks, fetch the guild roles separately.
    """
    async def predicate(ctx) -> bool:
        raw = getattr(ctx, "_raw", {})
        member = raw.get("member", {})
        member_roles: List[str] = member.get("roles", [])
        return role_id_or_name in member_roles
    return predicate


# ─────────────────────────────────────────────
#  CommandRegistry
# ─────────────────────────────────────────────

class CommandRegistry:
    def __init__(self):
        self.slash_commands: Dict[str, Command] = {}
        self.prefix_commands: Dict[str, Command] = {}
        self.groups: Dict[str, Group] = {}

    def add_command(self, cmd: Command, *, slash: bool = True, prefix: bool = False):
        if slash:
            self.slash_commands[cmd.name] = cmd
            for alias in cmd.aliases:
                self.slash_commands[alias] = cmd
        if prefix:
            self.prefix_commands[cmd.name] = cmd
            for alias in cmd.aliases:
                self.prefix_commands[alias] = cmd

    def add_group(self, group: Group):
        self.groups[group.name] = group

    def get_slash(self, name: str) -> Optional[Command]:
        return self.slash_commands.get(name)

    def get_group(self, name: str) -> Optional[Group]:
        return self.groups.get(name)

    def get_prefix(self, name: str) -> Optional[Command]:
        return self.prefix_commands.get(name)

    def all_slash_payloads(self) -> List[Dict[str, Any]]:
        seen = set()
        payloads = []
        for cmd in self.slash_commands.values():
            if id(cmd) not in seen:
                payloads.append(cmd.to_application_command())
                seen.add(id(cmd))
        for group in self.groups.values():
            payloads.append(group.to_application_command())
        return payloads
