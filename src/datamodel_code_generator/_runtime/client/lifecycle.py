"""The lifecycle of clients and their views: OPEN, CLOSING while their calls drain, then CLOSED.

A view counts its calls in its owner too, so closing the owner stops and waits for the calls of every view, while
closing a view touches only its own calls.
"""

from __future__ import annotations

import asyncio
import threading
from time import monotonic
from typing import Literal, TypeAlias

from .errors import ClientClosedError

State: TypeAlias = Literal["OPEN", "CLOSING", "CLOSED"]


def _closed(state: State, owner: Literal["client", "view"]) -> ClientClosedError | None:
    match state:
        case "CLOSING" | "CLOSED":
            return ClientClosedError(state=state, owner=owner)
        case _:
            pass
    return None


class Scope:
    """The calls of one client or view, counted so that closing can wait for them."""

    __slots__ = ("active", "condition", "lock", "owner", "scopes", "state", "waiters")

    def __init__(self, owner: Scope | None = None) -> None:
        """Start open, sharing the owner's lock when this is a view; closers wait on a condition of that lock."""
        self.owner = owner
        self.lock: threading.Lock = threading.Lock() if owner is None else owner.lock
        self.condition = threading.Condition(self.lock)
        self.scopes: tuple[Scope, ...] = (self,) if owner is None else (self, owner)
        self.active = 0
        self.waiters: list[asyncio.Future[None]] = []
        self.state: State = "OPEN"

    def view(self) -> Scope:
        """Return the scope of a new view of the same owner."""
        return Scope(self.owner or self)

    def closing(self) -> ClientClosedError | None:
        """Return the error a call meets when its owner or this view no longer runs calls, else None."""
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
                scope.active += 1

    def release(self) -> None:
        """Uncount a finished call and wake the closers of a closing scope once none of its calls is left."""
        with self.lock:
            for scope in self.scopes:
                scope.active -= 1
                if not scope.active and scope.state != "OPEN":
                    scope.condition.notify_all()
                    for waiter in scope.waiters:
                        waiter.get_loop().call_soon_threadsafe(waiter.set_result, None)

    def begin_close(self) -> bool:
        """Stop admitting calls; return False when already closed."""
        with self.lock:
            if self.state == "CLOSED":
                return False
            self.state = "CLOSING"
            return True

    def drain(self, timeout: float) -> int:
        """Wait up to the timeout for the active calls to finish; return how many are left."""
        deadline = monotonic() + timeout
        with self.condition:
            while self.active and (remaining := deadline - monotonic()) > 0:
                self.condition.wait(remaining)
            return self.active

    async def adrain(self, timeout: float) -> int:
        """Wait up to the timeout for the active async calls to finish; return how many are left."""
        with self.lock:
            if not self.active:
                return 0
            waiter = asyncio.get_running_loop().create_future()
            self.waiters.append(waiter)
        try:
            await asyncio.wait({waiter}, timeout=timeout)
        finally:
            with self.lock:
                self.waiters.remove(waiter)
        with self.lock:
            return self.active

    def finish(self) -> None:
        """Mark the scope closed once its calls drained."""
        with self.lock:
            self.state = "CLOSED"
