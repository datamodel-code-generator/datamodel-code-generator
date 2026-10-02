"""Bounded FIFO admission to one lazy database worker, independent of callers' asyncio loops."""

from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial
from threading import RLock
from typing import TYPE_CHECKING, Final, TypeVar, cast

from .sqlite import SQLiteConnection

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable
    from pathlib import Path

T = TypeVar("T")
_CAPACITY: Final = 64


def _notify(loop: asyncio.AbstractEventLoop, function: Callable[[], None]) -> None:
    try:
        loop.call_soon_threadsafe(function)
    except RuntimeError:
        return


def _completion(notification: asyncio.Future[T], settlement: Future[T]) -> None:
    if notification.done():
        return
    if (error := settlement.exception()) is not None:
        notification.set_exception(error)
    else:
        notification.set_result(settlement.result())


class _Job:
    """An accepted operation and its independent settlement and loop notification."""

    def __init__(self, operation: Callable[[sqlite3.Connection], object], loop: asyncio.AbstractEventLoop) -> None:
        self.operation = operation
        self.loop = loop
        self.notification: asyncio.Future[object] = loop.create_future()
        self.settlement: Future[object] = Future()
        self.cancelled = False


class SQLiteWorker:
    """One lazily started executor with 64 pending jobs and FIFO async backpressure."""

    __slots__ = ("_closed", "_connection", "_lock", "_pending", "_pool", "_shutdown", "_waiting")

    def __init__(self, path: str | Path) -> None:
        """Retain the explicit database path, allocating neither a thread nor a database."""
        self._connection = SQLiteConnection(path)
        self._lock = RLock()
        self._pool: ThreadPoolExecutor | None = None
        self._pending = 0
        self._waiting: deque[_Job] = deque()
        self._closed = False
        self._shutdown: Future[None] | None = None

    def _execute(self, job: _Job) -> object:
        with self._lock:
            self._pending -= 1
            admitted = self._admit()
            cancelled = job.cancelled
        self._callbacks(admitted)
        return None if cancelled else self._connection.run(job.operation)

    def _admit(self) -> list[_Job]:
        admitted: list[_Job] = []
        while self._waiting and self._pending < _CAPACITY:
            job = self._waiting.popleft()
            if job.cancelled or job.loop.is_closed():
                continue
            if self._pool is None:
                self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sqlite-store")
            self._pending += 1
            job.settlement = self._pool.submit(self._execute, job)
            admitted.append(job)
        return admitted

    def _callbacks(self, jobs: list[_Job]) -> None:
        for job in jobs:
            job.settlement.add_done_callback(partial(self._settled, job))

    @staticmethod
    def _settled(job: _Job, settlement: Future[object]) -> None:
        _notify(job.loop, lambda: _completion(job.notification, settlement))

    async def run(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        """Await a FIFO job; cancellation skips pending work and leaves started work to settle independently."""
        job = _Job(operation, asyncio.get_running_loop())
        with self._lock:
            if self._closed:
                msg = "SQLite store is closed"
                raise ValueError(msg)
            self._waiting.append(job)
            admitted = self._admit()
        self._callbacks(admitted)
        try:
            return cast("T", await job.notification)
        except asyncio.CancelledError:
            with self._lock:
                job.cancelled = True
            raise

    @staticmethod
    def _shutdown_done(pool: ThreadPoolExecutor, settlement: Future[None]) -> None:
        del settlement
        pool.shutdown(wait=False)

    async def aclose(self) -> None:
        """Close admission and drain accepted jobs; repeated or cancelled closes share retained shutdown."""
        loop = asyncio.get_running_loop()
        shutdown: Future[None] | None = None
        with self._lock:
            if self._shutdown is None:
                self._closed = True
                while self._waiting:
                    job = self._waiting.popleft()
                    rejected: Future[object] = Future()
                    rejected.set_exception(ValueError("SQLite store is closed"))
                    _notify(job.loop, lambda job=job, rejected=rejected: _completion(job.notification, rejected))
                if self._pool is None:
                    self._shutdown = Future()
                    self._shutdown.set_result(None)
                else:
                    shutdown = self._shutdown = self._pool.submit(self._connection.close)
            settlement = self._shutdown
        if shutdown is not None:
            shutdown.add_done_callback(partial(self._shutdown_done, cast("ThreadPoolExecutor", self._pool)))
        notification: asyncio.Future[None] = loop.create_future()
        settlement.add_done_callback(lambda done: _notify(loop, lambda: _completion(notification, done)))
        await notification
