"""
pydisk
~~~~~~
A feature-rich Discord library built directly on the REST API.
"""

from .client import Client
from .cog import Cog
from .models import User, Member, Message, Embed, Interaction
from .commands import (
    Command, Group, Option, OptionType, Cooldown,
    CommandError, CheckFailure, CooldownError, MissingArgument, BadArgument,
    MissingPermission, PremiumRequired, PermissionChecker, PERMISSION_FLAGS,
    command, check, cooldown, is_owner, guild_only, has_role,
)
from .core.rest import HTTPClient, HTTPError, RateLimitError
from .core.gateway import Intents
from .core.async_utils import (
    TaskGroup,
    EventBus,
    BackgroundTask,
    run_blocking,
    async_timeout,
    RateSemaphore,
)
from .validation import (
    validated_dataclass,
    CommandInput,
    validate_input,
    field_validator,
    ValidationError,
    BotConfig,
    InteractionContext,
)
from .components import (
    AppRouter,
    ComponentRouter,
    ModalRouter,
    ComponentBuilder,
    ModalBuilder,
    ActionRow,
    Button,
    SelectMenu,
    SelectOption,
    TextInput,
    ButtonStyle,
    TextInputStyle,
    ComponentInteraction,
    ModalSubmitInteraction,
)
from .i18n import Translations, set_translations, t, DISCORD_LOCALES

from .automod import (
    AutoMod,
    AutoModConfig,
    AutoModRule,
    AutoModAction,
    SpamConfig,
    FilterConfig,
    Violation,
    ViolationType,
)
from .statemachine import (
    StateMachine,
    Session,
    State,
    TransitionError,
    SessionExpired,
    SessionNotFound,
)
from .events import EventEmitter, EventListener
from .middleware import (
    EventPipeline, SmartRouter, STOP,
    LogMiddleware, FilterMiddleware, RateLimitMiddleware, MetaMiddleware,
)


from .embed import (
    Embed as RichEmbed,
    EmbedField,
    EmbedBuilder,
    EmbedPaginator,
)
from .smart import (
    SmartContext,
    SmartResponder,
    parse_context,
)
from .api import APIClient


__version__ = "0.4.0"
__author__ = "Moro & Claude"

__all__ = [
    # Core
    "Client", "Cog",
    # Models
    "User", "Member", "Message", "Embed", "Interaction",
    # Commands
    "Command", "Group", "Option", "OptionType", "Cooldown",
    "CommandError", "CheckFailure", "CooldownError", "MissingArgument", "BadArgument",
    "MissingPermission", "PremiumRequired", "PermissionChecker", "PERMISSION_FLAGS",
    "command", "check", "cooldown", "is_owner", "guild_only", "has_role",
    # HTTP / Gateway
    "HTTPClient", "HTTPError", "RateLimitError", "Intents",
    # Async utils
    "TaskGroup", "EventBus", "BackgroundTask", "run_blocking",
    "async_timeout", "RateSemaphore",
    # Validation
    "validated_dataclass", "CommandInput", "validate_input",
    "field_validator", "ValidationError", "BotConfig", "InteractionContext",
    # Components & Modals
    "AppRouter", "ComponentRouter", "ModalRouter",
    "ComponentBuilder", "ModalBuilder",
    "ActionRow", "Button", "SelectMenu", "SelectOption", "TextInput",
    "ButtonStyle", "TextInputStyle",
    "ComponentInteraction", "ModalSubmitInteraction",
    # i18n
    "Translations", "set_translations", "t", "DISCORD_LOCALES",
    # Auto-Mod
    "AutoMod", "AutoModConfig", "AutoModRule", "AutoModAction",
    "SpamConfig", "FilterConfig", "Violation", "ViolationType",
    # State Machine
    "StateMachine", "Session", "State",
    "TransitionError", "SessionExpired", "SessionNotFound",
    # Enhanced Embed Builder
    "RichEmbed", "EmbedField", "EmbedBuilder", "EmbedPaginator",
    # Smart Context & Response Engine
    "SmartContext", "SmartResponder", "parse_context",
    # Direct API Client
    "APIClient",
]
