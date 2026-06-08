"""
diskord.automod
~~~~~~~~~~~~~~
Built-in Anti-Spam & Auto-Moderation engine.

Features
--------
- Per-user message rate tracking (spam detection)
- Duplicate / repeated-message detection
- Mention spam (mass-ping) detection
- Caps-lock flood detection
- Link / invite filter
- Bad-word / pattern filter
- Auto-actions: warn, mute, kick, ban, delete
- Configurable per-guild rules via fluent builder
- Event hooks: on_spam_detected, on_automod_action
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional, Pattern, Set, Tuple

__all__ = [
    "AutoMod",
    "AutoModConfig",
    "AutoModRule",
    "AutoModAction",
    "SpamConfig",
    "FilterConfig",
    "Violation",
    "ViolationType",
]


# ─────────────────────────────────────────────────────────────────────────────
#  Enums / constants
# ─────────────────────────────────────────────────────────────────────────────

class ViolationType(IntEnum):
    SPAM            = 1   # too many messages per window
    DUPLICATE       = 2   # same content repeated
    MENTION_SPAM    = 3   # too many @mentions
    CAPS_FLOOD      = 4   # excessive uppercase
    LINK_FILTER     = 5   # blocked URL / invite
    WORD_FILTER     = 6   # banned word / pattern
    EMOJI_SPAM      = 7   # too many emojis


class AutoModAction(IntEnum):
    DELETE          = 1   # silently delete the message
    WARN            = 2   # reply with a warning, then delete
    MUTE            = 3   # timeout the user (requires manage_members)
    KICK            = 4   # kick the user
    BAN             = 5   # ban the user


# ─────────────────────────────────────────────────────────────────────────────
#  Config dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SpamConfig:
    """Rate-based spam settings."""
    # Maximum messages allowed within `window` seconds
    max_messages: int = 5
    window: float = 5.0
    # Duplicate content: how many identical messages trigger a violation
    max_duplicates: int = 3
    duplicate_window: float = 10.0
    # Mention spam
    max_mentions: int = 5
    # Caps flood: minimum message length and % uppercase to trigger
    caps_min_length: int = 10
    caps_ratio: float = 0.7
    # Emoji spam
    max_emojis: int = 10


@dataclass
class FilterConfig:
    """Content-filter settings."""
    block_invites: bool = False          # discord.gg/... links
    block_links: bool = False            # any http(s) link
    link_whitelist: List[str] = field(default_factory=list)
    banned_words: List[str] = field(default_factory=list)
    banned_patterns: List[str] = field(default_factory=list)   # raw regex strings
    # Compiled at runtime
    _word_re: Optional[Pattern] = field(default=None, init=False, repr=False)
    _pattern_re: Optional[Pattern] = field(default=None, init=False, repr=False)

    def compile(self) -> None:
        if self.banned_words:
            words = [re.escape(w) for w in self.banned_words]
            self._word_re = re.compile(
                r"\b(" + "|".join(words) + r")\b", re.IGNORECASE
            )
        if self.banned_patterns:
            self._pattern_re = re.compile(
                "(" + "|".join(self.banned_patterns) + ")", re.IGNORECASE
            )


@dataclass
class AutoModRule:
    """A single auto-mod rule: violation type → action."""
    violation: ViolationType
    action: AutoModAction
    # For MUTE: timeout duration in seconds (default 60 s)
    mute_duration: int = 60
    # Optional custom message sent on WARN
    warn_message: Optional[str] = None
    # Number of strikes before escalating (0 = always)
    strikes_before_action: int = 0


@dataclass
class AutoModConfig:
    """Full configuration for one guild (or global default)."""
    enabled: bool = True
    spam: SpamConfig = field(default_factory=SpamConfig)
    filters: FilterConfig = field(default_factory=FilterConfig)
    rules: List[AutoModRule] = field(default_factory=list)
    # Channels / roles exempt from auto-mod
    ignored_channels: Set[str] = field(default_factory=set)
    ignored_roles: Set[str] = field(default_factory=set)
    # Role ID to assign as "muted" (instead of timeout API)
    mute_role_id: Optional[str] = None
    # Log channel for auto-mod actions
    log_channel_id: Optional[str] = None

    def add_rule(
        self,
        violation: ViolationType,
        action: AutoModAction,
        *,
        mute_duration: int = 60,
        warn_message: Optional[str] = None,
        strikes: int = 0,
    ) -> "AutoModConfig":
        """Fluent rule builder."""
        self.rules.append(AutoModRule(
            violation=violation,
            action=action,
            mute_duration=mute_duration,
            warn_message=warn_message,
            strikes_before_action=strikes,
        ))
        return self

    def compile_filters(self) -> None:
        self.filters.compile()


# ─────────────────────────────────────────────────────────────────────────────
#  Violation result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Violation:
    type: ViolationType
    user_id: str
    guild_id: str
    channel_id: str
    message_id: str
    content: str
    matched: str = ""       # what triggered it (word, link, etc.)
    strike: int = 1         # current strike count for this user+type


# ─────────────────────────────────────────────────────────────────────────────
#  Per-user state
# ─────────────────────────────────────────────────────────────────────────────

class _UserState:
    def __init__(self) -> None:
        # timestamps of recent messages
        self.message_times: deque = deque()
        # recent message contents for duplicate detection
        self.recent_contents: deque = deque()
        # strike counters per ViolationType
        self.strikes: Dict[ViolationType, int] = defaultdict(int)

    def record_message(self, content: str, now: float, cfg: SpamConfig) -> None:
        # Prune old timestamps
        cutoff = now - cfg.window
        while self.message_times and self.message_times[0] < cutoff:
            self.message_times.popleft()
        self.message_times.append(now)

        # Prune old content records
        dup_cutoff = now - cfg.duplicate_window
        while self.recent_contents and self.recent_contents[0][0] < dup_cutoff:
            self.recent_contents.popleft()
        self.recent_contents.append((now, content))

    def message_count(self) -> int:
        return len(self.message_times)

    def duplicate_count(self, content: str) -> int:
        return sum(1 for _, c in self.recent_contents if c == content)

    def add_strike(self, vtype: ViolationType) -> int:
        self.strikes[vtype] += 1
        return self.strikes[vtype]


# ─────────────────────────────────────────────────────────────────────────────
#  AutoMod engine
# ─────────────────────────────────────────────────────────────────────────────

_INVITE_RE = re.compile(
    r"(discord\.gg|discord\.com/invite|discordapp\.com/invite)/[a-zA-Z0-9\-]+",
    re.IGNORECASE,
)
_LINK_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001F9FF]|<a?:[a-zA-Z0-9_]+:\d+>",
)


class AutoMod:
    """
    Pluggable auto-moderation engine.

    Usage::

        automod = AutoMod(bot)

        config = AutoModConfig()
        config.spam.max_messages = 6
        config.filters.block_invites = True
        config.filters.banned_words = ["badword"]
        config.add_rule(ViolationType.SPAM, AutoModAction.WARN)
        config.add_rule(ViolationType.WORD_FILTER, AutoModAction.DELETE)
        config.compile_filters()

        automod.set_config(config)           # global default
        automod.set_config(config, guild_id="123456")  # per-guild

    The engine hooks into ``bot.on("message")`` automatically when you
    call ``automod.setup()``.
    """

    def __init__(self, client: Any) -> None:
        self._client = client
        self._configs: Dict[Optional[str], AutoModConfig] = {}
        # Per-guild → per-user state
        self._state: Dict[str, Dict[str, _UserState]] = defaultdict(lambda: defaultdict(_UserState))
        self._action_handlers: List[Callable] = []
        self._violation_handlers: List[Callable] = []

    # ── Config ──────────────────────────────────────────────────────────────

    def set_config(self, config: AutoModConfig, *, guild_id: Optional[str] = None) -> "AutoMod":
        config.compile_filters()
        self._configs[guild_id] = config
        return self

    def get_config(self, guild_id: Optional[str]) -> Optional[AutoModConfig]:
        return self._configs.get(guild_id) or self._configs.get(None)

    # ── Hooks ────────────────────────────────────────────────────────────────

    def on_violation(self, func: Callable) -> Callable:
        """Decorator: called with ``(violation: Violation)`` before action."""
        self._violation_handlers.append(func)
        return func

    def on_action(self, func: Callable) -> Callable:
        """Decorator: called with ``(violation: Violation, action: AutoModAction)``."""
        self._action_handlers.append(func)
        return func

    def setup(self) -> "AutoMod":
        """Register the message listener on the bot."""
        @self._client.on("message")
        async def _automod_listener(message: Any) -> None:
            await self.process_message(message)
        return self

    # ── Core processing ──────────────────────────────────────────────────────

    async def process_message(self, message: Any) -> Optional[Violation]:
        """
        Inspect a message and take action if it violates any rule.
        Returns the first Violation found, or None.
        """
        guild_id = getattr(message, "guild_id", None)
        channel_id = message.channel_id
        user_id = message.author.id
        content = message.content or ""
        message_id = message.id

        cfg = self.get_config(guild_id)
        if cfg is None or not cfg.enabled:
            return None

        # Check exemptions
        if channel_id in cfg.ignored_channels:
            return None
        member_roles: List[str] = getattr(message, "member_roles", [])
        if cfg.ignored_roles.intersection(member_roles):
            return None

        now = time.monotonic()
        state = self._state[guild_id or "__global__"][user_id]
        state.record_message(content, now, cfg.spam)

        # Run all detectors in priority order
        violations_to_check: List[Tuple[ViolationType, str]] = []

        # 1. Word filter
        if cfg.filters._word_re and cfg.filters._word_re.search(content):
            m = cfg.filters._word_re.search(content)
            violations_to_check.append((ViolationType.WORD_FILTER, m.group()))

        # 2. Pattern filter
        if cfg.filters._pattern_re and cfg.filters._pattern_re.search(content):
            m = cfg.filters._pattern_re.search(content)
            violations_to_check.append((ViolationType.WORD_FILTER, m.group()))

        # 3. Invite filter
        if cfg.filters.block_invites and _INVITE_RE.search(content):
            url = _INVITE_RE.search(content).group()
            violations_to_check.append((ViolationType.LINK_FILTER, url))

        # 4. Link filter
        if cfg.filters.block_links and _LINK_RE.search(content):
            url = _LINK_RE.search(content).group()
            # Check whitelist
            if not any(w in url for w in cfg.filters.link_whitelist):
                violations_to_check.append((ViolationType.LINK_FILTER, url))

        # 5. Mention spam
        mention_count = content.count("<@") + content.count("<@!")
        if mention_count > cfg.spam.max_mentions:
            violations_to_check.append((ViolationType.MENTION_SPAM, f"{mention_count} mentions"))

        # 6. Emoji spam
        emojis = _EMOJI_RE.findall(content)
        if len(emojis) > cfg.spam.max_emojis:
            violations_to_check.append((ViolationType.EMOJI_SPAM, f"{len(emojis)} emojis"))

        # 7. Caps flood
        if len(content) >= cfg.spam.caps_min_length:
            alpha = [c for c in content if c.isalpha()]
            if alpha and sum(1 for c in alpha if c.isupper()) / len(alpha) >= cfg.spam.caps_ratio:
                violations_to_check.append((ViolationType.CAPS_FLOOD, content[:30]))

        # 8. Duplicate messages
        dup_count = state.duplicate_count(content)
        if dup_count >= cfg.spam.max_duplicates:
            violations_to_check.append((ViolationType.DUPLICATE, content[:30]))

        # 9. Spam (rate)
        if state.message_count() > cfg.spam.max_messages:
            violations_to_check.append((ViolationType.SPAM, f"{state.message_count()} msgs"))

        if not violations_to_check:
            return None

        vtype, matched = violations_to_check[0]
        strike = state.add_strike(vtype)

        violation = Violation(
            type=vtype,
            user_id=user_id,
            guild_id=guild_id or "",
            channel_id=channel_id,
            message_id=message_id,
            content=content,
            matched=matched,
            strike=strike,
        )

        # Fire violation hooks
        for handler in self._violation_handlers:
            try:
                await handler(violation)
            except Exception:
                pass

        # Find matching rule
        rule = self._find_rule(cfg, vtype, strike)
        if rule:
            await self._execute_action(violation, rule, message)
        
        return violation

    def _find_rule(self, cfg: AutoModConfig, vtype: ViolationType, strike: int) -> Optional[AutoModRule]:
        for rule in cfg.rules:
            if rule.violation == vtype:
                if strike > rule.strikes_before_action:
                    return rule
        return None

    async def _execute_action(self, violation: Violation, rule: AutoModRule, message: Any) -> None:
        http = self._client.http
        action = rule.action

        # Always delete on WARN, MUTE, KICK, BAN
        if action in (AutoModAction.DELETE, AutoModAction.WARN,
                      AutoModAction.MUTE, AutoModAction.KICK, AutoModAction.BAN):
            try:
                await http.delete(f"/channels/{violation.channel_id}/messages/{violation.message_id}")
            except Exception:
                pass

        if action == AutoModAction.WARN:
            warn_msg = rule.warn_message or self._default_warn(violation)
            try:
                await http.post(
                    f"/channels/{violation.channel_id}/messages",
                    json={"content": warn_msg},
                )
            except Exception:
                pass

        elif action == AutoModAction.MUTE:
            try:
                import datetime
                until = (
                    datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                    + datetime.timedelta(seconds=rule.mute_duration)
                ).isoformat() + "Z"
                await http.patch(
                    f"/guilds/{violation.guild_id}/members/{violation.user_id}",
                    json={"communication_disabled_until": until},
                )
            except Exception:
                pass

        elif action == AutoModAction.KICK:
            try:
                await http.delete(f"/guilds/{violation.guild_id}/members/{violation.user_id}")
            except Exception:
                pass

        elif action == AutoModAction.BAN:
            try:
                await http.put(
                    f"/guilds/{violation.guild_id}/bans/{violation.user_id}",
                    json={"delete_message_days": 1},
                )
            except Exception:
                pass

        # Log to log channel
        cfg = self.get_config(violation.guild_id or None)
        if cfg and cfg.log_channel_id:
            log_msg = (
                f"🛡️ **AutoMod** | `{violation.type.name}` "
                f"— <@{violation.user_id}> in <#{violation.channel_id}> "
                f"| Action: `{action.name}` | Strike #{violation.strike} "
                f"| Matched: `{violation.matched[:50]}`"
            )
            try:
                await http.post(
                    f"/channels/{cfg.log_channel_id}/messages",
                    json={"content": log_msg},
                )
            except Exception:
                pass

        # Fire action hooks
        for handler in self._action_handlers:
            try:
                await handler(violation, action)
            except Exception:
                pass

    @staticmethod
    def _default_warn(v: Violation) -> str:
        reasons = {
            ViolationType.SPAM:         "sending messages too fast",
            ViolationType.DUPLICATE:    "repeating the same message",
            ViolationType.MENTION_SPAM: "mass-mentioning users",
            ViolationType.CAPS_FLOOD:   "excessive caps",
            ViolationType.LINK_FILTER:  "posting blocked links",
            ViolationType.WORD_FILTER:  "using prohibited language",
            ViolationType.EMOJI_SPAM:   "spamming emojis",
        }
        return (
            f"⚠️ <@{v.user_id}>, your message was removed for "
            f"{reasons.get(v.type, 'violating server rules')}."
        )
