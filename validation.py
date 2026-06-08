"""
pydisk.validation
~~~~~~~~~~~~~~~~~
Native data-validation layer — works with stdlib dataclasses out of the box,
and upgrades to Pydantic automatically if it's installed.

Public surface:
- @validated_dataclass  : drop-in for @dataclass with runtime type-checking
- CommandInput          : base class for typed slash-command argument models
- validate_input()      : parse & validate a raw options dict against a model
- field_validator()     : per-field validator decorator (Pydantic-style for plain DCs)
- ValidationError       : unified exception (wraps Pydantic's or our own)
- BotConfig             : example fully-typed bot configuration model
"""

from __future__ import annotations

import dataclasses
import inspect
import re
from typing import Any, Dict, List, Optional, Type, TypeVar, get_type_hints

__all__ = [
    "validated_dataclass",
    "CommandInput",
    "validate_input",
    "field_validator",
    "ValidationError",
    "BotConfig",
    "InteractionContext",
]

T = TypeVar("T")

# ──────────────────────────────────────────────────────────────────────────────
#  Detect Pydantic (optional upgrade)
# ──────────────────────────────────────────────────────────────────────────────

try:
    import pydantic  # type: ignore

    _PYDANTIC = True
    _PYDANTIC_V2 = int(pydantic.VERSION.split(".")[0]) >= 2

    if _PYDANTIC_V2:
        from pydantic import BaseModel, field_validator as _pyd_field_validator, model_validator
        from pydantic import ValidationError as PydanticValidationError
    else:
        from pydantic import BaseModel, validator as _pyd_field_validator  # type: ignore
        from pydantic import ValidationError as PydanticValidationError

except ImportError:
    _PYDANTIC = False
    _PYDANTIC_V2 = False


# ──────────────────────────────────────────────────────────────────────────────
#  Unified ValidationError
# ──────────────────────────────────────────────────────────────────────────────

class ValidationError(Exception):
    """
    Raised when command input or model data fails validation.

    Attributes
    ----------
    errors : list of dicts with ``field`` and ``message`` keys.
    """

    def __init__(self, errors: List[Dict[str, str]]) -> None:
        self.errors = errors
        msg = "; ".join(f"{e['field']}: {e['message']}" for e in errors)
        super().__init__(msg)

    @classmethod
    def _from_pydantic(cls, exc: Any) -> "ValidationError":
        """Convert a Pydantic ValidationError to our unified type."""
        errors = []
        for e in exc.errors():
            field = ".".join(str(loc) for loc in e["loc"]) if e.get("loc") else "?"
            errors.append({"field": field, "message": e["msg"]})
        return cls(errors)

    @classmethod
    def single(cls, field: str, message: str) -> "ValidationError":
        return cls([{"field": field, "message": message}])


# ──────────────────────────────────────────────────────────────────────────────
#  @validated_dataclass — stdlib dataclass + runtime type checks
# ──────────────────────────────────────────────────────────────────────────────

def _check_types(instance: Any) -> None:
    """Walk all dataclass fields and verify the runtime type matches the hint."""
    hints = get_type_hints(type(instance))
    errors: List[Dict[str, str]] = []

    for f in dataclasses.fields(instance):  # type: ignore[arg-type]
        value = getattr(instance, f.name)
        expected = hints.get(f.name)

        # Skip Optional / None
        if value is None:
            continue

        # Unwrap Optional[X] → X
        origin = getattr(expected, "__origin__", None)
        if origin is type(None):
            continue
        if hasattr(expected, "__args__"):
            args = [a for a in expected.__args__ if a is not type(None)]
            if args:
                expected = args[0]

        # Skip generics we can't easily check (List[X], Dict[K,V])
        if getattr(expected, "__origin__", None) is not None:
            continue

        if expected is inspect.Parameter.empty or expected is Any:
            continue

        if not isinstance(value, expected):  # type: ignore[arg-type]
            errors.append({
                "field": f.name,
                "message": f"expected {expected.__name__}, got {type(value).__name__}",
            })

    if errors:
        raise ValidationError(errors)


def validated_dataclass(cls: Type[T]) -> Type[T]:
    """
    Decorator that turns a class into a dataclass AND adds runtime type-checking
    on ``__post_init__``.

    If Pydantic is installed you can use ``CommandInput`` instead for richer
    validation. This decorator is the zero-dependency option.

    Usage::

        @validated_dataclass
        class RollInput:
            sides: int = 6

            def __post_init__(self):
                if not (2 <= self.sides <= 100):
                    raise ValidationError.single("sides", "must be between 2 and 100")
    """
    # Apply @dataclass first
    dc = dataclasses.dataclass(cls)

    original_post_init = getattr(dc, "__post_init__", None)

    def __post_init__(self):
        _check_types(self)
        if original_post_init:
            original_post_init(self)

    dc.__post_init__ = __post_init__
    return dc


# ──────────────────────────────────────────────────────────────────────────────
#  CommandInput — base class for slash-command argument models
# ──────────────────────────────────────────────────────────────────────────────

if _PYDANTIC:
    # ── Pydantic branch ───────────────────────────────────────────────────────
    class CommandInput(BaseModel):  # type: ignore[misc]
        """
        Base class for typed slash-command argument models (Pydantic backend).

        Usage::

            class EchoInput(CommandInput):
                message: str
                times: int = 1

                @field_validator("times")
                @classmethod
                def clamp_times(cls, v):
                    return max(1, min(v, 10))

            @bot.slash_command(description="Echo a message")
            async def echo(interaction, args: EchoInput):
                await interaction.respond(args.message * args.times)
        """

        model_config = {"arbitrary_types_allowed": True} if _PYDANTIC_V2 else {}

        class Config:
            arbitrary_types_allowed = True

        @classmethod
        def from_options(cls, options: Dict[str, Any]) -> "CommandInput":
            """Parse and validate raw Discord interaction options."""
            try:
                return cls(**options)
            except Exception as exc:
                if _PYDANTIC and isinstance(exc, PydanticValidationError):
                    raise ValidationError._from_pydantic(exc) from exc
                raise

        def to_dict(self) -> Dict[str, Any]:
            if _PYDANTIC_V2:
                return self.model_dump()
            return self.dict()  # type: ignore[return-value]

else:
    # ── Stdlib dataclass branch ───────────────────────────────────────────────
    @dataclasses.dataclass
    class CommandInput:  # type: ignore[no-redef]
        """
        Base class for typed slash-command argument models (stdlib dataclass backend).

        Works like a plain dataclass but adds ``from_options()`` and ``to_dict()``.
        Override ``__post_init__`` for custom validation logic.
        """

        @classmethod
        def from_options(cls, options: Dict[str, Any]) -> "CommandInput":
            """Parse raw Discord interaction options into this model."""
            try:
                return cls(**options)
            except (TypeError, ValidationError) as exc:
                raise ValidationError.single("__init__", str(exc)) from exc

        def to_dict(self) -> Dict[str, Any]:
            return dataclasses.asdict(self)  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────────────────
#  validate_input() — parse a raw options dict into a CommandInput subclass
# ──────────────────────────────────────────────────────────────────────────────

def validate_input(model: Type[T], options: Dict[str, Any]) -> T:
    """
    Parse *options* (raw Discord interaction options) into *model*.

    Raises ``ValidationError`` on failure.

    Usage::

        class BanInput(CommandInput):
            user_id: str
            reason: str = "No reason provided"
            days: int = 0

            @field_validator("days")
            @classmethod
            def valid_days(cls, v):
                if not (0 <= v <= 7):
                    raise ValueError("days must be 0–7")
                return v

        @bot.slash_command(description="Ban a user")
        async def ban(interaction, user_id: str, reason: str = "", days: int = 0):
            args = validate_input(BanInput, interaction.options)
            await bot.ban_member(interaction.guild_id, args.user_id,
                                  reason=args.reason, delete_message_days=args.days)
    """
    if hasattr(model, "from_options"):
        return model.from_options(options)  # type: ignore[return-value]
    # Fallback: try direct construction
    try:
        return model(**options)  # type: ignore[call-arg]
    except (TypeError, ValidationError) as exc:
        raise ValidationError.single("__init__", str(exc)) from exc


# ──────────────────────────────────────────────────────────────────────────────
#  field_validator() — portable decorator that works in both branches
# ──────────────────────────────────────────────────────────────────────────────

if _PYDANTIC:
    # Re-export Pydantic's decorator unchanged
    field_validator = _pyd_field_validator  # type: ignore[assignment]
else:
    # Lightweight stdlib stand-in: stores validators on the class
    def field_validator(*fields: str):  # type: ignore[misc]
        """
        Stdlib-compatible field validator decorator.

        Validators are called by ``__post_init__`` when Pydantic is absent.

        Usage::

            @validated_dataclass
            class RollInput:
                sides: int = 6

                @field_validator("sides")
                @classmethod
                def must_be_positive(cls, v):
                    if v < 2:
                        raise ValueError("sides must be >= 2")
                    return v
        """
        def decorator(func):
            func.__validates_fields__ = fields
            return func
        return decorator


# ──────────────────────────────────────────────────────────────────────────────
#  Built-in validated models
# ──────────────────────────────────────────────────────────────────────────────

if _PYDANTIC:
    from pydantic import field_validator as _fv  # noqa: F811

    class BotConfig(BaseModel):  # type: ignore[misc]
        """Fully-typed, validated bot configuration."""
        token: str
        application_id: str
        prefix: str = "!"
        owner_ids: List[str] = dataclasses.field(default_factory=list) if not _PYDANTIC else []  # type: ignore[assignment]
        log_level: str = "INFO"
        intents: int = 0

        if _PYDANTIC_V2:
            @_fv("token")
            @classmethod
            def token_not_empty(cls, v: str) -> str:
                if not v.strip():
                    raise ValueError("token must not be empty")
                return v

            @_fv("prefix")
            @classmethod
            def prefix_length(cls, v: str) -> str:
                if len(v) > 5:
                    raise ValueError("prefix must be ≤ 5 characters")
                return v
        else:
            @_fv("token")  # type: ignore[misc]
            @classmethod
            def token_not_empty(cls, v):
                if not v.strip():
                    raise ValueError("token must not be empty")
                return v

    class InteractionContext(BaseModel):  # type: ignore[misc]
        """Validated, typed wrapper around raw interaction data from Discord."""
        interaction_id: str
        token: str
        guild_id: Optional[str] = None
        channel_id: str
        user_id: str
        username: str
        command_name: str
        locale: str = "en-US"
        options: Dict[str, Any] = {}

        model_config = {"arbitrary_types_allowed": True} if _PYDANTIC_V2 else {}

        class Config:
            arbitrary_types_allowed = True

        @classmethod
        def from_interaction(cls, interaction: Any) -> "InteractionContext":
            return cls(
                interaction_id=interaction.id,
                token=interaction.token,
                guild_id=interaction.guild_id,
                channel_id=interaction.channel_id,
                user_id=interaction.user.id,
                username=interaction.user.username,
                command_name=interaction.command_name,
                locale=interaction.locale,
                options=interaction.options,
            )

else:
    @validated_dataclass
    class BotConfig:  # type: ignore[no-redef]
        """Fully-typed, validated bot configuration (stdlib dataclass backend)."""
        token: str = ""
        application_id: str = ""
        prefix: str = "!"
        log_level: str = "INFO"
        intents: int = 0

        def __post_init__(self):
            errors = []
            if not self.token.strip():
                errors.append({"field": "token", "message": "must not be empty"})
            if len(self.prefix) > 5:
                errors.append({"field": "prefix", "message": "must be ≤ 5 characters"})
            if errors:
                raise ValidationError(errors)

    @validated_dataclass
    class InteractionContext:  # type: ignore[no-redef]
        """Validated, typed wrapper around raw interaction data from Discord."""
        interaction_id: str = ""
        token: str = ""
        channel_id: str = ""
        user_id: str = ""
        username: str = ""
        command_name: str = ""
        guild_id: Optional[str] = None
        locale: str = "en-US"
        options: Dict[str, Any] = dataclasses.field(default_factory=dict)

        @classmethod
        def from_interaction(cls, interaction: Any) -> "InteractionContext":
            return cls(
                interaction_id=interaction.id,
                token=interaction.token,
                guild_id=interaction.guild_id,
                channel_id=interaction.channel_id,
                user_id=interaction.user.id,
                username=interaction.user.username,
                command_name=interaction.command_name,
                locale=interaction.locale,
                options=interaction.options,
            )
