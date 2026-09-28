"""The lifecycle of clients and their views: OPEN, CLOSING while their calls and handles drain, then CLOSED.

A view counts its calls and response handles in its owner too, so closing the owner stops and waits for those of
every view, while closing a view touches only its own.
"""

from __future__ import annotations

import asyncio
import threading
from time import monotonic
from typing import Generic, Literal, TypeAlias, TypeVar

from .errors import ClientClosedError

State: TypeAlias = Literal["OPEN", "CLOSING", "CLOSED"]
HandleT = TypeVar("HandleT")


def _closed(state: State, owner: Literal["client", "view"]) -> ClientClosedError | None:
    match state:
        case "CLOSING" | "CLOSED":
            return ClientClosedError(state=state, owner=owner)
        case _:
            pass
    return None


class Scope(Generic[HandleT]):
    """The calls and response handles of one client or view, counted so that closing can wait for them."""

    __slots__ = ("calls", "condition", "handles", "lock", "owner", "scopes", "state", "waiters")

    def __init__(self, owner: Scope[HandleT] | None = None) -> None:
        """Start open, sharing the owner's lock when this is a view; closers wait on a condition of that lock."""
        self.owner = owner
        self.lock: threading.Lock = threading.Lock() if owner is None else owner.lock
        self.condition = threading.Condition(self.lock)
        self.scopes: tuple[Scope[HandleT], ...] = (self,) if owner is None else (self, owner)
        self.calls = 0
        self.handles: dict[HandleT, None] = {}
        self.waiters: list[asyncio.Future[None]] = []
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
        if not (self.calls or self.handles) and self.state != "OPEN":
            self.condition.notify_all()
            for waiter in self.waiters:
                waiter.get_loop().call_soon_threadsafe(waiter.set_result, None)

    def begin_close(self) -> bool:
        """Stop admitting calls; return False when already closed."""
        with self.lock:
            if self.state == "CLOSED":
                return False
            self.state = "CLOSING"
            return True

    def drain(self, timeout: float) -> tuple[HandleT, ...]:
        """Wait up to the timeout for the calls and handles to finish; return the handles still open."""
        deadline = monotonic() + timeout
        with self.condition:
            while (self.calls or self.handles) and (remaining := deadline - monotonic()) > 0:
                self.condition.wait(min(remaining, threading.TIMEOUT_MAX))
            return tuple(self.handles)

    async def adrain(self, timeout: float) -> tuple[HandleT, ...]:
        """Wait up to the timeout for the async calls and handles to finish; return the handles still open."""
        with self.lock:
            if not (self.calls or self.handles):
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
            return self.calls, len(self.handles)

    def finish(self) -> None:
        """Mark the scope closed once its calls and handles drained."""
        with self.lock:
            self.state = "CLOSED"
