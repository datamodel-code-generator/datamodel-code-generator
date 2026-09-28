"""The lifecycle of clients and their views: OPEN, CLOSING while their calls and handles drain, then CLOSED.

A view counts its calls and response handles in its owner too, so closing the owner stops and waits for those of
every view, while closing a view touches only its own.
"""

from __future__ import annotations

import threading
from contextvars import ContextVar
from functools import partial
from time import monotonic
from typing import TYPE_CHECKING, Generic, Literal, Protocol, TypeAlias, TypeVar

from .errors import CleanupError, ClientClosedError, add_secondary

if TYPE_CHECKING:
    import asyncio

State: TypeAlias = Literal["OPEN", "CLOSING", "CLOSED"]
HandleT = TypeVar("HandleT")
TaskT = TypeVar("TaskT")
LEFT_WORK: ContextVar[list[asyncio.Task[None]] | None] = ContextVar("left_work", default=None)


class TaskInterruptionError(Exception):
    """Carry the exact native interruption out of a cleanup or deferred task as that task's failure."""

    __slots__ = ("cause",)

    def __init__(self, cause: BaseException) -> None:
        """Retain the original exception without publishing the internal carrier."""
        super().__init__()
        self.cause = cause


def task_result(task: asyncio.Task[TaskT]) -> TaskT:
    """Retrieve an owned task's result, restoring any original native interruption."""
    try:
        return task.result()
    except TaskInterruptionError as error:
        raise error.cause from None


def task_failure(task: asyncio.Task[object]) -> BaseException | None:
    """Observe a settled owned task without losing an interruption's identity or safe secondary information."""
    if task.cancelled():
        return None
    error = task.exception()
    return error.cause if isinstance(error, TaskInterruptionError) else error


class CleanupOwner(Protocol):
    """The logical owner whose released call or stream still has unfinished asynchronous cleanup."""

    call_id: str
    finished: bool
    streaming: bool


def _closed(state: State, owner: Literal["client", "view"]) -> ClientClosedError | None:
    match state:
        case "CLOSING" | "CLOSED":
            return ClientClosedError(state=state, owner=owner)
        case _:
            pass
    return None


def _signal(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)


def cleanup_secondary(error: BaseException, failure: BaseException) -> None:
    """Preserve the direct cleanup cause beside a primary error, removing one cleanup wrapper."""
    if error is failure:
        return
    if isinstance(failure, CleanupError) and failure.cause is not None:
        add_secondary(error, failure.cause)
        for secondary in failure.secondary_errors:
            add_secondary(error, secondary)
        return
    add_secondary(error, failure)


class Scope(Generic[HandleT]):
    """The calls and response handles of one client or view, counted so that closing can wait for them."""

    __slots__ = (
        "calls",
        "cleanups",
        "closing_signal",
        "condition",
        "handles",
        "lock",
        "owner",
        "scopes",
        "state",
        "waiters",
    )

    def __init__(self, owner: Scope[HandleT] | None = None) -> None:
        """Start open, sharing the owner's lock when this is a view; closers wait on a condition of that lock."""
        self.owner = owner
        self.lock: threading.Lock = threading.Lock() if owner is None else owner.lock
        self.condition = threading.Condition(self.lock)
        self.scopes: tuple[Scope[HandleT], ...] = (self,) if owner is None else (self, owner)
        self.calls = 0
        self.handles: dict[HandleT, None] = {}
        self.waiters: list[asyncio.Future[None]] = []
        self.closing_signal: asyncio.Future[None] | None = None
        self.cleanups: dict[asyncio.Task[object], CleanupOwner | None] = {}
        self.state: State = "OPEN"

    def view(self) -> Scope[HandleT]:
        """Return the scope of a new view of the same owner."""
        return Scope(self.owner or self)

    def closing(self) -> ClientClosedError | None:
        """Return the error a call or handle meets when its owner or this view is closing, else None."""
        owner = self.owner
        if self.state == "OPEN" and (owner is None or owner.state == "OPEN"):
            return None
        if owner is not None and (error := _closed(owner.state, "client")) is not None:
            return error
        return _closed(self.state, "client" if owner is None else "view")

    def closing_signals(self) -> tuple[asyncio.Future[None], ...]:
        """Return one lazy closing signal per scope, shared by all of its asynchronous calls."""
        import asyncio  # noqa: PLC0415

        signals: list[asyncio.Future[None]] = []
        with self.lock:
            for scope in self.scopes:
                signal = scope.closing_signal
                if signal is None:
                    signal = scope.closing_signal = asyncio.get_running_loop().create_future()
                signals.append(signal)
        return tuple(signals)

    def retain_cleanup(
        self, task: asyncio.Task[object], error: BaseException | None = None, *, owner: CleanupOwner | None = None
    ) -> None:
        """Retain owned cleanup beyond one caller's cap, observing its result and waking later closers."""
        with self.lock:
            for scope in self.scopes:
                scope.cleanups[task] = owner
        task.add_done_callback(partial(self._cleaned, error))

    def _cleaned(self, error: BaseException | None, task: asyncio.Task[object]) -> None:
        if (failure := task_failure(task)) is not None and error is not None:
            cleanup_secondary(error, failure)
        with self.lock:
            for scope in self.scopes:
                del scope.cleanups[task]
                scope.settle()

    def admit(self) -> None:
        """Count a new call, or raise ClientClosedError when closing."""
        with self.lock:
            if (error := self.closing()) is not None:
                raise error
            for scope in self.scopes:
                scope.calls += 1

    def release(self) -> None:
        """Uncount a finished call."""
        with self.lock:
            for scope in self.scopes:
                scope.calls -= 1
                scope.settle()

    def handoff(self, handle: HandleT) -> None:
        """Turn an active call into the response handle it returns, which holds its lease until released."""
        with self.lock:
            for scope in self.scopes:
                scope.calls -= 1
                scope.handles[handle] = None

    def release_handle(self, handle: HandleT) -> None:
        """Uncount a handle whose response was released."""
        with self.lock:
            for scope in self.scopes:
                del scope.handles[handle]
                scope.settle()

    def settle(self) -> None:
        """Wake the closers of a closing scope once none of its calls or handles is left; the lock is held."""
        if not (self.calls or self.handles or self.cleanups) and self.state != "OPEN":
            self.condition.notify_all()
            for waiter in self.waiters:
                waiter.get_loop().call_soon_threadsafe(_signal, waiter)

    def begin_close(self) -> bool:
        """Stop admitting calls; return False when already closed."""
        with self.lock:
            if self.state == "CLOSED":
                return False
            self.state = "CLOSING"
            if (signal := self.closing_signal) is not None:
                signal.get_loop().call_soon_threadsafe(_signal, signal)
            return True

    def drain(self, timeout: float) -> tuple[HandleT, ...]:
        """Wait up to the timeout for the calls and handles to finish; return the handles still open."""
        deadline = monotonic() + timeout
        with self.condition:
            while (self.calls or self.handles or self.cleanups) and (remaining := deadline - monotonic()) > 0:
                self.condition.wait(min(remaining, threading.TIMEOUT_MAX))
            return tuple(self.handles)

    async def adrain(self, timeout: float) -> tuple[HandleT, ...]:
        """Wait up to the timeout for the async calls and handles to finish; return the handles still open."""
        import asyncio  # noqa: PLC0415

        with self.lock:
            if not (self.calls or self.handles or self.cleanups):
                return ()
            waiter = asyncio.get_running_loop().create_future()
            self.waiters.append(waiter)
        try:
            await asyncio.wait({waiter}, timeout=timeout)
        finally:
            with self.lock:
                self.waiters.remove(waiter)
        with self.lock:
            return tuple(self.handles)

    def pending(self) -> tuple[int, int]:
        """Return how many calls and handles are still unfinished."""
        with self.lock:
            owners = {owner.call_id: owner for owner in self.cleanups.values() if owner is not None and owner.finished}
            pending_calls = sum(owner is None for owner in self.cleanups.values())
            pending_calls += sum(not owner.streaming for owner in owners.values())
            pending_leases = sum(owner.streaming for owner in owners.values())
            return self.calls + pending_calls, len(self.handles) + pending_leases

    def finish(self) -> None:
        """Mark the scope closed once its calls and handles drained."""
        with self.lock:
            self.state = "CLOSED"
