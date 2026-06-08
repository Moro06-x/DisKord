"""
diskord.statemachine
~~~~~~~~~~~~~~~~~~~
Generic state-machine for Discord mini-games, multi-step forms,
surveys, and any interaction flow that needs to track "where the
user is" across multiple messages / interactions.

Features
--------
- Decorator-based state definitions
- Per-user, per-channel, or per-guild sessions
- Entry / exit hooks per state
- Timeout per state (auto-expires inactive sessions)
- Transition guards (predicate functions)
- Built-in data bag carried across the whole session
- Works with both slash interactions and prefix messages
- Fire-and-forget: sessions stored in memory (pluggable backends)

Quick example — a mini trivia game::

    from diskord.statemachine import StateMachine, State

    trivia = StateMachine("trivia", session_key="user")

    @trivia.state("start")
    async def start_state(session, interaction):
        session.data["score"] = 0
        await interaction.respond("🎮 Trivia started! Type your answer.")
        session.go("question_1")

    @trivia.state("question_1")
    async def q1(session, interaction):
        answer = interaction.options.get("answer", "")
        if answer.lower() == "paris":
            session.data["score"] += 1
            await interaction.respond("✅ Correct!")
        else:
            await interaction.respond("❌ Wrong! It was Paris.")
        session.go("end")

    @trivia.state("end")
    async def end_state(session, interaction):
        await interaction.respond(f"🏁 Game over! Score: {session.data['score']}")
        session.finish()

    bot.mount_statemachine(trivia)
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

__all__ = [
    "StateMachine",
    "Session",
    "State",
    "TransitionError",
    "SessionExpired",
    "SessionNotFound",
]


# ─────────────────────────────────────────────────────────────────────────────
#  Exceptions
# ─────────────────────────────────────────────────────────────────────────────

class TransitionError(Exception):
    """Raised when a transition to a state is not allowed."""

class SessionExpired(Exception):
    """Raised when a session has timed out."""

class SessionNotFound(Exception):
    """No active session for the given key."""


# ─────────────────────────────────────────────────────────────────────────────
#  Session
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Session:
    """Represents one user's (or channel's) journey through a state machine."""
    id: str                                  # unique session ID
    machine_name: str
    session_key: str                         # e.g. user_id
    current_state: str
    data: Dict[str, Any] = field(default_factory=dict)
    history: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.monotonic)
    updated_at: float = field(default_factory=time.monotonic)
    finished: bool = False
    _machine: Any = field(default=None, repr=False)

    # ── Navigation helpers ───────────────────────────────────────────────────

    def go(self, state_name: str) -> None:
        """Transition to a new state."""
        if self._machine:
            self._machine._transition(self, state_name)

    def finish(self) -> None:
        """Mark the session as complete and clean it up."""
        self.finished = True
        if self._machine:
            self._machine._end_session(self)

    def restart(self, state: Optional[str] = None) -> None:
        """Restart from the initial state (or a specific one)."""
        target = state or (self._machine.initial_state if self._machine else self.current_state)
        self.history.clear()
        self.data.clear()
        self.go(target)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.created_at


# ─────────────────────────────────────────────────────────────────────────────
#  State descriptor
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class State:
    name: str
    handler: Callable
    on_enter: Optional[Callable] = None
    on_exit: Optional[Callable] = None
    timeout: Optional[float] = None          # seconds before auto-expire
    allowed_from: Optional[Set[str]] = None  # restrict which states can transition here
    guard: Optional[Callable] = None         # async predicate(session, ctx) → bool


# ─────────────────────────────────────────────────────────────────────────────
#  StateMachine
# ─────────────────────────────────────────────────────────────────────────────

class StateMachine:
    """
    Decorator-driven state machine for multi-step Discord flows.

    Parameters
    ----------
    name : str
        Identifier for this machine (used in routing).
    session_key : str
        One of ``"user"``, ``"channel"``, ``"guild"``.
        Determines what scope a session is scoped to.
    initial_state : str
        The name of the first state. The machine expects a state with
        this name to be registered.
    timeout : float, optional
        Global default timeout in seconds. Per-state ``timeout`` overrides this.
    """

    def __init__(
        self,
        name: str,
        *,
        session_key: str = "user",
        initial_state: str = "start",
        timeout: Optional[float] = 300.0,
    ) -> None:
        self.name = name
        self.session_key = session_key  # "user" | "channel" | "guild"
        self.initial_state = initial_state
        self.default_timeout = timeout
        self._states: Dict[str, State] = {}
        self._sessions: Dict[str, Session] = {}   # key → session
        self._timeout_tasks: Dict[str, asyncio.Task] = {}
        self._on_expire_handlers: List[Callable] = []
        self._on_error_handlers: List[Callable] = []

    # ── State registration ───────────────────────────────────────────────────

    def state(
        self,
        name: str,
        *,
        on_enter: Optional[Callable] = None,
        on_exit: Optional[Callable] = None,
        timeout: Optional[float] = None,
        allowed_from: Optional[List[str]] = None,
        guard: Optional[Callable] = None,
    ):
        """Decorator to register a state handler.

        Usage::

            @machine.state("waiting_for_answer")
            async def handle_answer(session: Session, ctx):
                ...
        """
        def decorator(func: Callable) -> Callable:
            self._states[name] = State(
                name=name,
                handler=func,
                on_enter=on_enter,
                on_exit=on_exit,
                timeout=timeout,
                allowed_from=set(allowed_from) if allowed_from else None,
                guard=guard,
            )
            return func
        return decorator

    def on_expire(self, func: Callable) -> Callable:
        """Decorator: called with ``(session)`` when a session times out."""
        self._on_expire_handlers.append(func)
        return func

    def on_error(self, func: Callable) -> Callable:
        """Decorator: called with ``(session, error)`` on unhandled exceptions."""
        self._on_error_handlers.append(func)
        return func

    # ── Session management ───────────────────────────────────────────────────

    def _resolve_key(self, ctx: Any) -> str:
        if self.session_key == "user":
            return str(getattr(getattr(ctx, "user", None) or getattr(ctx, "author", None), "id", "unknown"))
        elif self.session_key == "channel":
            return str(getattr(ctx, "channel_id", "unknown"))
        elif self.session_key == "guild":
            return str(getattr(ctx, "guild_id", "unknown"))
        return "global"

    def start_session(self, ctx: Any, *, data: Optional[Dict[str, Any]] = None) -> Session:
        """Start a new session for the context."""
        key = self._resolve_key(ctx)
        session = Session(
            id=str(uuid.uuid4()),
            machine_name=self.name,
            session_key=key,
            current_state=self.initial_state,
            data=data or {},
            _machine=self,
        )
        self._sessions[key] = session
        self._schedule_timeout(session)
        return session

    def get_session(self, ctx: Any) -> Optional[Session]:
        """Return the active session for this context, or None."""
        key = self._resolve_key(ctx)
        sess = self._sessions.get(key)
        if sess and sess.finished:
            return None
        return sess

    def has_session(self, ctx: Any) -> bool:
        return self.get_session(ctx) is not None

    def _end_session(self, session: Session) -> None:
        key = session.session_key
        self._sessions.pop(key, None)
        task = self._timeout_tasks.pop(key, None)
        if task and not task.done():
            task.cancel()

    # ── Transition logic ─────────────────────────────────────────────────────

    def _transition(self, session: Session, target: str) -> None:
        state_def = self._states.get(target)
        if not state_def:
            raise TransitionError(f"State '{target}' is not registered in machine '{self.name}'.")
        if state_def.allowed_from and session.current_state not in state_def.allowed_from:
            raise TransitionError(
                f"Cannot transition from '{session.current_state}' to '{target}'."
            )
        session.history.append(session.current_state)
        session.current_state = target
        session.updated_at = time.monotonic()
        # Reset timeout
        self._schedule_timeout(session)

    # ── Dispatch ─────────────────────────────────────────────────────────────

    async def dispatch(self, ctx: Any) -> bool:
        """
        Process a context (Interaction or Message) through the current state.
        Returns True if a session handled it, False otherwise.
        """
        session = self.get_session(ctx)
        if session is None:
            return False

        state_def = self._states.get(session.current_state)
        if not state_def:
            return False

        # Guard check
        if state_def.guard:
            allowed = await state_def.guard(session, ctx)
            if not allowed:
                return False

        # on_exit previous state
        if session.history:
            prev_state = self._states.get(session.history[-1])
            if prev_state and prev_state.on_exit:
                try:
                    await prev_state.on_exit(session, ctx)
                except Exception:
                    pass

        # on_enter new state (first time entering this specific state_def)
        if state_def.on_enter:
            try:
                await state_def.on_enter(session, ctx)
            except Exception:
                pass

        try:
            await state_def.handler(session, ctx)
        except Exception as e:
            for h in self._on_error_handlers:
                try:
                    await h(session, e)
                except Exception:
                    pass

        return True

    async def begin(self, ctx: Any, *, data: Optional[Dict[str, Any]] = None) -> Session:
        """Start a session AND immediately dispatch the initial state."""
        session = self.start_session(ctx, data=data)
        await self.dispatch(ctx)
        return session

    # ── Timeout management ───────────────────────────────────────────────────

    def _schedule_timeout(self, session: Session) -> None:
        key = session.session_key
        # Cancel existing
        existing = self._timeout_tasks.pop(key, None)
        if existing and not existing.done():
            existing.cancel()

        # Determine timeout duration
        state_def = self._states.get(session.current_state)
        timeout = (state_def.timeout if state_def and state_def.timeout is not None
                   else self.default_timeout)
        if timeout is None:
            return

        async def _expire() -> None:
            await asyncio.sleep(timeout)
            # If session still active
            if key in self._sessions and not self._sessions[key].finished:
                expired = self._sessions.pop(key)
                for h in self._on_expire_handlers:
                    try:
                        await h(expired)
                    except Exception:
                        pass

        task = asyncio.ensure_future(_expire())
        self._timeout_tasks[key] = task

    # ── Introspection ────────────────────────────────────────────────────────

    @property
    def active_sessions(self) -> List[Session]:
        return [s for s in self._sessions.values() if not s.finished]

    def state_names(self) -> List[str]:
        return list(self._states.keys())
