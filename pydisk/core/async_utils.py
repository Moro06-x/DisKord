"""
pydisk.core.async_utils
~~~~~~~~~~~~~~~~~~~~~~~
Native standard-library asynchrony helpers — zero external deps beyond asyncio.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from functools import partial
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

log = logging.getLogger("pydisk.async_utils")


# ──────────────────────────────────────────────────────────────────────────────
#  Structured concurrency: TaskGroup
# ──────────────────────────────────────────────────────────────────────────────

class TaskGroup:
    """
    Lightweight structured-concurrency group using only asyncio primitives.

    All tasks are tracked; if *any* raises, all siblings are cancelled and the
    exception is re-raised from ``__aexit__``.

    Compatible with Python 3.10 (asyncio.TaskGroup is 3.11+).
    """

    def __init__(self) -> None:
        self._tasks: Set[asyncio.Task] = set()
        self._errors: List[BaseException] = []

    async def __aenter__(self) -> "TaskGroup":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_val is not None:
            for t in self._tasks:
                t.cancel()
        if self._tasks:
            results = await asyncio.gather(*self._tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, BaseException) and not isinstance(r, asyncio.CancelledError):
                    self._errors.append(r)
        if self._errors:
            # ExceptionGroup is Python 3.11+; fall back to raising the first error
            try:
                raise ExceptionGroup("TaskGroup errors", self._errors)  # type: ignore[name-defined]
            except NameError:
                raise self._errors[0]
        return False

    def create_task(
        self,
        coro: Awaitable,
        *,
        name: Optional[str] = None,
    ) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)  # type: ignore[arg-type]
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task


# ──────────────────────────────────────────────────────────────────────────────
#  Async event bus
# ──────────────────────────────────────────────────────────────────────────────

class EventBus:
    """
    Asyncio-native publish/subscribe event bus.

    Features:
    - Multiple listeners per event
    - ``once`` listeners that auto-unsubscribe after first fire
    - Priority ordering (higher fires first)
    - Errors in listeners are caught and logged (won't crash others)
    """

    def __init__(self) -> None:
        self._listeners: Dict[str, List[tuple]] = defaultdict(list)

    def on(
        self,
        event: str,
        *,
        priority: int = 0,
        once: bool = False,
    ) -> Callable:
        """Decorator: register a coroutine as a listener for *event*."""
        def decorator(func: Callable) -> Callable:
            self._add(event, func, priority=priority, once=once)
            return func
        return decorator

    def once(self, event: str, *, priority: int = 0) -> Callable:
        """Decorator: register a one-shot listener."""
        return self.on(event, priority=priority, once=True)

    def _add(
        self,
        event: str,
        func: Callable,
        *,
        priority: int,
        once: bool,
    ) -> None:
        self._listeners[event].append((priority, once, func))
        self._listeners[event].sort(key=lambda x: x[0], reverse=True)

    def remove(self, event: str, func: Callable) -> None:
        """Remove a specific listener."""
        self._listeners[event] = [
            entry for entry in self._listeners[event] if entry[2] is not func
        ]

    def clear(self, event: Optional[str] = None) -> None:
        """Remove all listeners for *event*, or every listener if None."""
        if event:
            self._listeners.pop(event, None)
        else:
            self._listeners.clear()

    async def emit(self, event: str, *args: Any, **kwargs: Any) -> List[Any]:
        """
        Call all listeners for *event* concurrently.
        Returns list of results (exceptions are swallowed & logged).
        """
        entries = list(self._listeners.get(event, []))
        if not entries:
            return []

        once_funcs = [fn for _, is_once, fn in entries if is_once]

        coros = [self._safe_call(fn, *args, **kwargs) for _, _, fn in entries]
        results = await asyncio.gather(*coros, return_exceptions=False)

        for fn in once_funcs:
            self.remove(event, fn)

        return list(results)

    @staticmethod
    async def _safe_call(fn: Callable, *args, **kwargs) -> Any:
        try:
            result = fn(*args, **kwargs)
            if asyncio.iscoroutine(result):
                return await result
            return result
        except Exception as exc:
            log.exception(f"EventBus listener {fn.__name__!r} raised: {exc}")
            return None


# ──────────────────────────────────────────────────────────────────────────────
#  Background task wrapper
# ──────────────────────────────────────────────────────────────────────────────

class BackgroundTask:
    """
    Wraps a coroutine function as a long-running background task.

    - Auto-restarts on failure (with exponential back-off)
    - Supports graceful shutdown via ``stop()``
    - Tracks run-count and last-error for observability
    """

    def __init__(
        self,
        coro_func: Callable[[], Awaitable],
        *,
        name: str = "background",
        max_retries: int = 0,          # 0 = infinite
        backoff_base: float = 2.0,
        backoff_max: float = 60.0,
    ) -> None:
        self._func = coro_func
        self.name = name
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self.run_count: int = 0
        self.last_error: Optional[BaseException] = None

    def start(self) -> asyncio.Task:
        """Schedule the task. Returns the underlying asyncio.Task."""
        if self._task and not self._task.done():
            return self._task
        self._stop_event.clear()
        self._task = asyncio.create_task(self._runner(), name=self.name)
        return self._task

    async def stop(self, timeout: float = 5.0) -> None:
        """Signal the task to stop and wait up to *timeout* seconds."""
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

    @property
    def is_running(self) -> bool:
        return bool(self._task and not self._task.done())

    async def _runner(self) -> None:
        attempt = 0
        while not self._stop_event.is_set():
            try:
                self.run_count += 1
                await self._func()
                break  # clean exit — don't restart
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.last_error = exc
                attempt += 1
                log.error(f"[BackgroundTask:{self.name}] crashed (attempt {attempt}): {exc}")
                if self._max_retries and attempt >= self._max_retries:
                    log.error(f"[BackgroundTask:{self.name}] giving up after {attempt} attempts.")
                    break
                delay = min(self._backoff_base ** attempt, self._backoff_max)
                log.info(f"[BackgroundTask:{self.name}] restarting in {delay:.1f}s…")
                # BUG FIX: original used asyncio.wait_for(shield(create_future()), timeout=delay)
                # which never resolves on its own — the future was never set, so wait_for
                # always hit TimeoutError. Correct approach: just sleep for the delay.
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    break


# ──────────────────────────────────────────────────────────────────────────────
#  run_blocking
# ──────────────────────────────────────────────────────────────────────────────

async def run_blocking(func: Callable, /, *args, **kwargs) -> Any:
    """
    Run a synchronous (blocking) callable in the default thread-pool executor
    without blocking the asyncio event loop.
    """
    loop = asyncio.get_running_loop()
    if kwargs:
        func = partial(func, *args, **kwargs)
        return await loop.run_in_executor(None, func)
    return await loop.run_in_executor(None, func, *args)


# ──────────────────────────────────────────────────────────────────────────────
#  Timeout helper
# ──────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def async_timeout(seconds: float):
    """
    Context manager that cancels its body after *seconds*.

    BUG FIX: original Python 3.10 fallback was broken — it created a Future
    and called `future.cancel()` via call_later, but cancelling a Future object
    doesn't cancel the running coroutine inside the context manager body. The
    correct 3.10 fallback uses asyncio.wait_for wrapped around a shield of the
    actual task, or simply use asyncio.wait_for directly.
    """
    try:
        # Python 3.11+
        async with asyncio.timeout(seconds):  # type: ignore[attr-defined]
            yield
    except AttributeError:
        # Python 3.10 fallback: wrap execution in a task with wait_for
        async def _body_wrapper():
            # This is a placeholder; the actual body runs in the with-block.
            # We can't easily wrap the body here, so we use a shield approach.
            pass

        # Simplest correct fallback: raise TimeoutError after `seconds` using
        # a background cancel task watching the current task.
        current_task = asyncio.current_task()
        handle = None

        def _cancel_current():
            if current_task and not current_task.done():
                current_task.cancel()

        loop = asyncio.get_running_loop()
        handle = loop.call_later(seconds, _cancel_current)
        try:
            yield
        except asyncio.CancelledError:
            raise asyncio.TimeoutError(f"Operation timed out after {seconds}s")
        finally:
            if handle:
                handle.cancel()


# ──────────────────────────────────────────────────────────────────────────────
#  RateSemaphore
# ──────────────────────────────────────────────────────────────────────────────

class RateSemaphore:
    """
    Token-bucket rate limiter implemented purely with asyncio.

    Limits how many calls pass through per ``period`` seconds.
    """

    def __init__(self, rate: int, period: float = 1.0) -> None:
        self._rate = rate
        self._period = period
        self._tokens = rate
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Block until a token is available, then consume it."""
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                refill = int(elapsed / self._period * self._rate)
                if refill:
                    self._tokens = min(self._rate, self._tokens + refill)
                    self._last_refill = now
                if self._tokens > 0:
                    self._tokens -= 1
                    return
                wait = self._period / self._rate
            await asyncio.sleep(wait)

    async def __aenter__(self) -> "RateSemaphore":
        await self.acquire()
        return self

    async def __aexit__(self, *_) -> None:
        pass
