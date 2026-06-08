"""
pydisk.events
~~~~~~~~~~~~~
JS-style event listener system for pydisk.

Features
--------
- on(event, fn)       — register a persistent listener
- off(event, fn)      — remove a listener
- once(event, fn)     — listener that fires exactly once then removes itself
- emit(event, *args)  — fire all listeners for an event
- Wildcard support    — listen to "message.*" or "*" to catch all events
- Priority support    — listeners run in priority order (higher = first)
- Middleware support  — transform/filter event data before listeners run
- Async + sync        — both coroutine and regular functions work

Usage (standalone)
------------------
    from pydisk.events import EventEmitter

    emitter = EventEmitter()

    @emitter.on("message")
    async def on_message(data):
        print("got message:", data)

    @emitter.once("ready")
    async def on_ready():
        print("fired once only")

    # Wildcard — catches message.create, message.delete, etc.
    @emitter.on("message.*")
    async def on_any_message(data):
        print("wildcard caught:", data)

    # Catch absolutely everything
    @emitter.on("*")
    async def on_everything(event, data):
        print(f"event={event}", data)

    await emitter.emit("message.create", {"content": "hello"})

Usage (with pydisk Client)
--------------------------
    import pydisk as diskord

    bot = diskord.Client(token="TOKEN")

    # These all work exactly like before, but now support off/once/wildcards:
    @bot.on("message")
    async def on_msg(message):
        print(message.content)

    @bot.once("ready")
    async def on_ready():
        print("Ready! (fires once)")

    # Remove a listener
    bot.off("message", on_msg)

    # Wildcard — catch all interaction events
    @bot.on("interaction.*")
    async def debug(interaction):
        print("interaction event caught")
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

__all__ = [
    "EventEmitter",
    "EventListener",
    "ListenerMiddleware",
]

log = logging.getLogger("pydisk.events")


# ─────────────────────────────────────────────────────────────────────────────
#  Listener dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EventListener:
    """A single registered event listener."""
    event:    str
    callback: Callable
    once:     bool  = False
    priority: int   = 0        # higher = runs first
    _fired:   bool  = field(default=False, repr=False)

    def matches(self, event: str) -> bool:
        """
        Check if this listener's pattern matches the emitted event name.
        Supports fnmatch wildcards: ``*``, ``message.*``, ``?``.
        """
        return fnmatch.fnmatch(event, self.event)


# ─────────────────────────────────────────────────────────────────────────────
#  Middleware type alias
# ─────────────────────────────────────────────────────────────────────────────

# A middleware is an async callable that receives (event_name, args, kwargs)
# and returns (args, kwargs) — possibly modified.
# Return None to suppress / cancel the event entirely.
ListenerMiddleware = Callable[
    [str, Tuple, Dict],
    Coroutine[Any, Any, Optional[Tuple[Tuple, Dict]]]
]


# ─────────────────────────────────────────────────────────────────────────────
#  EventEmitter
# ─────────────────────────────────────────────────────────────────────────────

class EventEmitter:
    """
    JS-style async event emitter with wildcard support.

    All public methods return ``self`` for chaining where applicable.
    """

    def __init__(self) -> None:
        self._listeners: List[EventListener] = []
        self._middlewares: List[ListenerMiddleware] = []
        self._max_listeners: int = 100

    # ── Registration ─────────────────────────────────────────────────────────

    def on(
        self,
        event: str,
        callback: Optional[Callable] = None,
        *,
        priority: int = 0,
    ):
        """
        Register a persistent listener. Can be used as a decorator or called
        directly.

            # Decorator style
            @emitter.on("message")
            async def handler(data): ...

            # Direct style
            emitter.on("message", handler)

        Supports wildcards: ``"*"``, ``"message.*"``, ``"interaction.?"``.
        """
        def decorator(func: Callable) -> Callable:
            if len(self._listeners) >= self._max_listeners:
                log.warning(
                    f"EventEmitter: max listeners ({self._max_listeners}) "
                    f"reached for event '{event}'."
                )
            self._listeners.append(
                EventListener(event=event, callback=func, once=False, priority=priority)
            )
            self._listeners.sort(key=lambda l: l.priority, reverse=True)
            return func

        if callback is not None:
            decorator(callback)
            return self
        return decorator

    def once(
        self,
        event: str,
        callback: Optional[Callable] = None,
        *,
        priority: int = 0,
    ):
        """
        Register a one-time listener — auto-removed after first fire.

            @emitter.once("ready")
            async def on_ready(): ...
        """
        def decorator(func: Callable) -> Callable:
            self._listeners.append(
                EventListener(event=event, callback=func, once=True, priority=priority)
            )
            self._listeners.sort(key=lambda l: l.priority, reverse=True)
            return func

        if callback is not None:
            decorator(callback)
            return self
        return decorator

    def off(self, event: str, callback: Callable) -> "EventEmitter":
        """
        Remove a specific listener.

            emitter.off("message", my_handler)
        """
        self._listeners = [
            l for l in self._listeners
            if not (l.event == event and l.callback is callback)
        ]
        return self

    def off_all(self, event: Optional[str] = None) -> "EventEmitter":
        """
        Remove all listeners for an event, or ALL listeners if event is None.

            emitter.off_all("message")   # clear just "message"
            emitter.off_all()            # nuclear option
        """
        if event is None:
            self._listeners.clear()
        else:
            self._listeners = [l for l in self._listeners if l.event != event]
        return self

    # ── Middleware ────────────────────────────────────────────────────────────

    def use(self, middleware: ListenerMiddleware) -> "EventEmitter":
        """
        Register a middleware function that runs before listeners.

        The middleware receives ``(event_name, args, kwargs)`` and should
        return ``(args, kwargs)`` — optionally modified.
        Return ``None`` to cancel/suppress the event entirely.

            async def logger_middleware(event, args, kwargs):
                print(f"[EVENT] {event} args={args}")
                return args, kwargs   # pass through unchanged

            emitter.use(logger_middleware)
        """
        self._middlewares.append(middleware)
        return self

    # ── Emit ─────────────────────────────────────────────────────────────────

    async def emit(self, event: str, *args, **kwargs) -> bool:
        """
        Fire all listeners matching ``event``.

        Wildcard listeners (``"*"`` or ``"message.*"``) receive the event
        name as their first argument, followed by the normal args.

        Returns ``True`` if at least one listener was called, ``False`` if
        the event was suppressed by middleware or had no listeners.
        """
        # ── Run middleware chain ──────────────────────────────────────────
        current_args, current_kwargs = args, kwargs
        for mw in self._middlewares:
            try:
                result = await _maybe_await(mw, event, current_args, current_kwargs)
                if result is None:
                    log.debug(f"Event '{event}' suppressed by middleware.")
                    return False
                current_args, current_kwargs = result
            except Exception:
                log.exception(f"Middleware error on event '{event}'")

        # ── Find matching listeners ───────────────────────────────────────
        matched = [l for l in self._listeners if l.matches(event)]
        if not matched:
            return False

        # ── Identify once-listeners to remove after firing ───────────────
        to_remove = []

        for listener in matched:
            try:
                # Wildcard listeners get the event name prepended
                if "*" in listener.event or "?" in listener.event:
                    await _maybe_await(listener.callback, event, *current_args)
                else:
                    await _maybe_await(listener.callback, *current_args)
            except Exception:
                log.exception(
                    f"Error in listener '{listener.callback.__name__}' "
                    f"for event '{event}'"
                )

            if listener.once:
                to_remove.append(listener)

        # Remove spent once-listeners
        for l in to_remove:
            try:
                self._listeners.remove(l)
            except ValueError:
                pass

        return True

    def emit_sync(self, event: str, *args, **kwargs) -> None:
        """
        Fire an event from synchronous code.
        Schedules ``emit`` on the running event loop if available,
        otherwise creates a new one.
        """
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.emit(event, *args, **kwargs))
        except RuntimeError:
            asyncio.run(self.emit(event, *args, **kwargs))

    # ── Introspection ─────────────────────────────────────────────────────────

    def listener_count(self, event: Optional[str] = None) -> int:
        """Return the number of listeners (for a specific event, or total)."""
        if event is None:
            return len(self._listeners)
        return sum(1 for l in self._listeners if l.event == event)

    def event_names(self) -> List[str]:
        """Return a deduplicated list of all registered event names."""
        return list(dict.fromkeys(l.event for l in self._listeners))

    def set_max_listeners(self, n: int) -> "EventEmitter":
        """Change the max-listener warning threshold (default: 100)."""
        self._max_listeners = n
        return self

    def listeners(self, event: str) -> List[Callable]:
        """Return all callbacks registered for an exact event name."""
        return [l.callback for l in self._listeners if l.event == event]

    # ── Repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<EventEmitter listeners={len(self._listeners)} "
            f"events={self.event_names()}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _maybe_await(func: Callable, *args, **kwargs) -> Any:
    """Call func — awaiting it if it's a coroutine function."""
    result = func(*args, **kwargs)
    if asyncio.iscoroutine(result):
        return await result
    return result
