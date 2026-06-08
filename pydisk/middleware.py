"""
pydisk.middleware
~~~~~~~~~~~~~~~~~
Event Interceptors & Middleware Pipeline + Intent-Based Smart Router.

Two systems in one file:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 1. MIDDLEWARE PIPELINE  (EventPipeline)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Middleware runs BEFORE event listeners, in registration order.
Each middleware can:
  • Modify event args / kwargs
  • Block / suppress the event entirely (return STOP)
  • Log the event
  • Measure execution time
  • Add metadata to kwargs (e.g. inject a DB session)

Built-in middleware included:
  • LogMiddleware      — logs every event with level + timing
  • FilterMiddleware   — block specific events by name/pattern
  • RateLimitMiddleware— suppress events fired too frequently
  • MetaMiddleware     — inject extra kwargs into every event

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 2. SMART ROUTER  (SmartRouter)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Routes events to specific handlers based on:
  • Context  — guild_id, channel_id, user_id, role_ids
  • Content  — message content patterns (regex or plain match)
  • Combined — context AND content together

Usage
-----
    from pydisk.middleware import EventPipeline, SmartRouter, STOP
    from pydisk.middleware import LogMiddleware, FilterMiddleware

    # ── Middleware ────────────────────────────────────────────────────────
    pipeline = EventPipeline()
    pipeline.use(LogMiddleware(level="INFO"))
    pipeline.use(FilterMiddleware(block=["typing_start"]))

    # Custom middleware
    @pipeline.intercept("message")
    async def add_prefix_check(event, args, kwargs):
        message = args[0] if args else None
        if message and message.content.startswith("!ignore"):
            return STOP          # suppress this event entirely
        return args, kwargs      # pass through

    # ── Smart Router ──────────────────────────────────────────────────────
    router = SmartRouter()

    # Route by guild
    @router.on("message", guild_id="123456789")
    async def guild_only(message): ...

    # Route by content pattern
    @router.on("message", content="help")
    async def help_handler(message): ...

    # Route by content regex
    @router.on("message", content=r"^!\\w+")
    async def command_handler(message): ...

    # Route by channel + content combined
    @router.on("message", channel_id="987654321", content="report")
    async def report_handler(message): ...

    # Route by role (checks message.author roles if available)
    @router.on("message", role_id="111222333")
    async def mod_only(message): ...

    # Attach to bot
    pipeline.attach(bot)
    router.attach(bot)
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Pattern, Tuple, Union

__all__ = [
    "EventPipeline",
    "SmartRouter",
    "STOP",
    "LogMiddleware",
    "FilterMiddleware",
    "RateLimitMiddleware",
    "MetaMiddleware",
]

log = logging.getLogger("pydisk.middleware")


# ─────────────────────────────────────────────────────────────────────────────
#  Sentinel — return this from middleware to block the event
# ─────────────────────────────────────────────────────────────────────────────

class _Stop:
    """Sentinel returned from middleware to suppress an event."""
    def __repr__(self): return "STOP"

STOP = _Stop()


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _call(func: Callable, *args, **kwargs) -> Any:
    result = func(*args, **kwargs)
    if asyncio.iscoroutine(result):
        return await result
    return result


def _extract_attr(obj: Any, *attrs: str) -> Any:
    """Walk a chain of attributes safely. e.g. _extract_attr(msg, 'author', 'id')"""
    for attr in attrs:
        obj = getattr(obj, attr, None)
        if obj is None:
            return None
    return obj


# ─────────────────────────────────────────────────────────────────────────────
#  Interceptor dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Interceptor:
    """A single middleware interceptor bound to an event pattern."""
    event:    str        # supports fnmatch wildcards
    callback: Callable
    priority: int = 0

    def matches(self, event: str) -> bool:
        return fnmatch.fnmatch(event, self.event)


# ─────────────────────────────────────────────────────────────────────────────
#  EventPipeline
# ─────────────────────────────────────────────────────────────────────────────

class EventPipeline:
    """
    Middleware pipeline that wraps the bot's dispatch method.

    Middleware runs in registration order before any event listeners fire.
    Each middleware can modify, block, or annotate event data.
    """

    def __init__(self) -> None:
        self._interceptors: List[Interceptor] = []
        self._global_middleware: List[Callable] = []
        self._timing_enabled: bool = False
        self._timing_log: Dict[str, List[float]] = {}

    # ── Global middleware (runs for every event) ──────────────────────────

    def use(self, middleware: Callable) -> "EventPipeline":
        """
        Register a global middleware (runs for ALL events).

            async def my_mw(event, args, kwargs):
                print(f"event: {event}")
                return args, kwargs   # or return STOP to block

            pipeline.use(my_mw)
        """
        self._global_middleware.append(middleware)
        return self

    # ── Per-event interceptors ────────────────────────────────────────────

    def intercept(self, event: str = "*", *, priority: int = 0):
        """
        Decorator to register an interceptor for a specific event (or wildcard).

            @pipeline.intercept("message")
            async def check(event, args, kwargs):
                msg = args[0]
                if msg.author.bot:
                    return STOP      # ignore bot messages
                return args, kwargs
        """
        def decorator(func: Callable) -> Callable:
            self._interceptors.append(
                Interceptor(event=event, callback=func, priority=priority)
            )
            self._interceptors.sort(key=lambda i: i.priority, reverse=True)
            return func
        return decorator

    def enable_timing(self) -> "EventPipeline":
        """Enable per-event timing measurements (accessible via get_timings)."""
        self._timing_enabled = True
        return self

    def get_timings(self) -> Dict[str, Dict[str, float]]:
        """Return avg/min/max timing stats per event name."""
        stats = {}
        for event, times in self._timing_log.items():
            if times:
                stats[event] = {
                    "count": len(times),
                    "avg_ms": round(sum(times) / len(times), 2),
                    "min_ms": round(min(times), 2),
                    "max_ms": round(max(times), 2),
                }
        return stats

    # ── Core: run the pipeline ────────────────────────────────────────────

    async def run(
        self, event: str, args: tuple, kwargs: dict
    ) -> Optional[Tuple[tuple, dict]]:
        """
        Run all middleware for an event.
        Returns (args, kwargs) — possibly modified — or None if blocked.
        """
        start = time.perf_counter() if self._timing_enabled else 0.0

        current_args, current_kwargs = args, kwargs

        # Global middleware first
        for mw in self._global_middleware:
            try:
                result = await _call(mw, event, current_args, current_kwargs)
                if isinstance(result, _Stop):
                    log.debug(f"[pipeline] Event '{event}' blocked by global middleware.")
                    return None
                if result is not None:
                    current_args, current_kwargs = result
            except Exception:
                log.exception(f"[pipeline] Global middleware error on '{event}'")

        # Per-event interceptors
        for interceptor in self._interceptors:
            if not interceptor.matches(event):
                continue
            try:
                result = await _call(
                    interceptor.callback, event, current_args, current_kwargs
                )
                if isinstance(result, _Stop):
                    log.debug(
                        f"[pipeline] Event '{event}' blocked by "
                        f"interceptor '{interceptor.callback.__name__}'"
                    )
                    return None
                if result is not None:
                    current_args, current_kwargs = result
            except Exception:
                log.exception(
                    f"[pipeline] Interceptor '{interceptor.callback.__name__}' "
                    f"error on '{event}'"
                )

        if self._timing_enabled:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self._timing_log.setdefault(event, []).append(elapsed_ms)
            if len(self._timing_log[event]) > 1000:
                self._timing_log[event] = self._timing_log[event][-500:]

        return current_args, current_kwargs

    # ── Attach to bot ─────────────────────────────────────────────────────

    def attach(self, bot) -> None:
        """
        Wrap the bot's dispatch method so all events pass through this pipeline.

            pipeline.attach(bot)
        """
        original_dispatch = bot.dispatch

        async def patched_dispatch(event: str, *args, **kwargs):
            result = await self.run(event, args, kwargs)
            if result is None:
                return   # blocked
            new_args, new_kwargs = result
            await original_dispatch(event, *new_args, **new_kwargs)

        bot.dispatch = patched_dispatch
        log.info("[pipeline] Attached to bot dispatch.")


# ─────────────────────────────────────────────────────────────────────────────
#  Built-in middleware
# ─────────────────────────────────────────────────────────────────────────────

class LogMiddleware:
    """
    Logs every event that passes through the pipeline.

        pipeline.use(LogMiddleware(level="DEBUG", ignore=["typing_start"]))
    """

    def __init__(
        self,
        level: str = "DEBUG",
        *,
        ignore: Optional[List[str]] = None,
        show_args: bool = False,
    ) -> None:
        self._level = getattr(logging, level.upper(), logging.DEBUG)
        self._ignore = set(ignore or [])
        self._show_args = show_args

    async def __call__(
        self, event: str, args: tuple, kwargs: dict
    ) -> Tuple[tuple, dict]:
        if event not in self._ignore:
            extra = f" args={args}" if self._show_args else ""
            log.log(self._level, f"[event] {event}{extra}")
        return args, kwargs


class FilterMiddleware:
    """
    Block specific events from ever reaching listeners.

        pipeline.use(FilterMiddleware(block=["typing_start", "presence_update"]))

    Also supports fnmatch patterns:
        pipeline.use(FilterMiddleware(block=["guild_*"]))
    """

    def __init__(self, *, block: List[str]) -> None:
        self._blocked = block

    async def __call__(
        self, event: str, args: tuple, kwargs: dict
    ) -> Any:
        for pattern in self._blocked:
            if fnmatch.fnmatch(event, pattern):
                return STOP
        return args, kwargs


class RateLimitMiddleware:
    """
    Suppress events fired more than ``limit`` times per ``window`` seconds.

        pipeline.use(RateLimitMiddleware(limit=5, window=1.0))
    """

    def __init__(self, *, limit: int = 10, window: float = 1.0) -> None:
        self._limit = limit
        self._window = window
        self._buckets: Dict[str, List[float]] = {}

    async def __call__(
        self, event: str, args: tuple, kwargs: dict
    ) -> Any:
        now = time.monotonic()
        bucket = self._buckets.setdefault(event, [])
        # Prune old entries
        self._buckets[event] = [t for t in bucket if now - t < self._window]
        if len(self._buckets[event]) >= self._limit:
            log.debug(f"[ratelimit] Event '{event}' suppressed (rate limit).")
            return STOP
        self._buckets[event].append(now)
        return args, kwargs


class MetaMiddleware:
    """
    Inject extra keyword arguments into every event call.
    Useful for passing a DB session, config, or shared state.

        pipeline.use(MetaMiddleware(db=my_db, config=cfg))
    """

    def __init__(self, **meta: Any) -> None:
        self._meta = meta

    async def __call__(
        self, event: str, args: tuple, kwargs: dict
    ) -> Tuple[tuple, dict]:
        kwargs = {**kwargs, **self._meta}
        return args, kwargs


# ─────────────────────────────────────────────────────────────────────────────
#  SmartRouter
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _Route:
    """A single route rule."""
    event:      str
    callback:   Callable
    guild_id:   Optional[str]           = None
    channel_id: Optional[str]           = None
    user_id:    Optional[str]           = None
    role_id:    Optional[str]           = None
    content:    Optional[str]           = None   # plain substring or regex
    _regex:     Optional[re.Pattern]    = field(default=None, repr=False)

    def __post_init__(self):
        if self.content:
            try:
                self._regex = re.compile(self.content, re.IGNORECASE)
            except re.error:
                self._regex = None   # treat as plain substring

    def matches_context(self, obj: Any) -> bool:
        """Check guild/channel/user/role constraints against an object."""
        if self.guild_id:
            val = (
                getattr(obj, "guild_id", None)
                or _extract_attr(obj, "message", "guild_id")
            )
            if str(val) != str(self.guild_id):
                return False

        if self.channel_id:
            val = (
                getattr(obj, "channel_id", None)
                or _extract_attr(obj, "message", "channel_id")
            )
            if str(val) != str(self.channel_id):
                return False

        if self.user_id:
            val = (
                _extract_attr(obj, "author", "id")
                or _extract_attr(obj, "user", "id")
            )
            if str(val) != str(self.user_id):
                return False

        if self.role_id:
            roles = (
                getattr(obj, "roles", None)
                or _extract_attr(obj, "member", "roles")
                or []
            )
            if str(self.role_id) not in [str(r) for r in roles]:
                return False

        return True

    def matches_content(self, obj: Any) -> bool:
        """Check content constraint against message content."""
        if not self.content:
            return True
        text = (
            getattr(obj, "content", None)
            or getattr(obj, "text", None)
            or ""
        )
        if self._regex:
            return bool(self._regex.search(text))
        return self.content.lower() in text.lower()

    def matches(self, event: str, obj: Any) -> bool:
        if not fnmatch.fnmatch(event, self.event):
            return False
        return self.matches_context(obj) and self.matches_content(obj)


class SmartRouter:
    """
    Routes events to specific handlers based on context and/or content.

    Context filters: guild_id, channel_id, user_id, role_id
    Content filters: content (substring or regex pattern)

    All filters are optional and combinable.

    Examples
    --------
        router = SmartRouter()

        # Guild-specific handler
        @router.on("message", guild_id="123456789")
        async def guild_handler(message): ...

        # Content-based (substring)
        @router.on("message", content="hello")
        async def hello_handler(message): ...

        # Content-based (regex)
        @router.on("message", content=r"^!\\w+")
        async def command_handler(message): ...

        # Combined
        @router.on("message", channel_id="987654321", content="report")
        async def report_handler(message): ...

        # Any interaction in a specific guild
        @router.on("interaction", guild_id="123456789")
        async def guild_interaction(interaction): ...

        router.attach(bot)
    """

    def __init__(self) -> None:
        self._routes: List[_Route] = []

    def on(
        self,
        event: str,
        *,
        guild_id:   Optional[str] = None,
        channel_id: Optional[str] = None,
        user_id:    Optional[str] = None,
        role_id:    Optional[str] = None,
        content:    Optional[str] = None,
    ):
        """
        Register a route. All filter kwargs are optional.

            @router.on("message", guild_id="123", content=r"^!help")
            async def help_cmd(message): ...
        """
        def decorator(func: Callable) -> Callable:
            self._routes.append(_Route(
                event=event,
                callback=func,
                guild_id=guild_id,
                channel_id=channel_id,
                user_id=user_id,
                role_id=role_id,
                content=content,
            ))
            return func
        return decorator

    async def dispatch(self, event: str, *args) -> None:
        """Run all matching routes for an event."""
        obj = args[0] if args else None
        if obj is None:
            return

        for route in self._routes:
            if route.matches(event, obj):
                try:
                    await _call(route.callback, *args)
                except Exception:
                    log.exception(
                        f"[router] Error in route '{route.callback.__name__}' "
                        f"for event '{event}'"
                    )

    def attach(self, bot) -> None:
        """
        Hook the router into the bot's event system.

            router.attach(bot)

        After this, every event the bot dispatches also goes through
        the SmartRouter automatically.
        """
        router = self

        @bot.on("*")
        async def _smart_route(event: str, *args, **kwargs):
            await router.dispatch(event, *args)

        log.info("[router] SmartRouter attached to bot.")

    def route_count(self) -> int:
        """Return total number of registered routes."""
        return len(self._routes)

    def routes_for(self, event: str) -> List[_Route]:
        """Return all routes registered for an exact event name."""
        return [r for r in self._routes if r.event == event]

    def remove(self, callback: Callable) -> None:
        """Remove a route by its callback function."""
        self._routes = [r for r in self._routes if r.callback is not callback]
