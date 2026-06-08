"""
diskord.cog
~~~~~~~~~~~
Base class for Cogs — class-based containers for commands and listeners.
"""

from typing import Optional
from .commands import Command, command


class CogMeta(type):
    """Metaclass that collects command methods at class definition time."""
    def __new__(mcs, name, bases, namespace):
        commands = {}
        for key, value in namespace.items():
            cmd = getattr(value, "__diskord_command__", None)
            if isinstance(cmd, Command):
                commands[cmd.name] = cmd
        namespace["__diskord_commands__"] = commands
        return super().__new__(mcs, name, bases, namespace)


class Cog(metaclass=CogMeta):
    """
    Base class for cogs.

    Example::

        class Moderation(diskord.Cog):
            @diskord.command(description="Kick a user")
            async def kick(self, interaction, user_id: str):
                await self.bot.kick_member(interaction.guild_id, user_id)
                await interaction.respond(f"Kicked <@{user_id}>")

        bot.add_cog(Moderation())
    """

    @property
    def qualified_name(self) -> str:
        return type(self).__name__

    def cog_check(self, ctx) -> bool:
        """Override to add a check applied to ALL commands in this cog."""
        return True
