"""Bounded FIFO admission to one lazy database worker, independent of callers' asyncio loops."""

from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial
from threading import Condition, RLock
from typing import TYPE_CHECKING, Final, TypeVar, cast

from ..client.errors import add_secondary
from .sqlite import SQLiteConnection

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable
    from pathlib import Path

T = TypeVar("T")
_CAPACITY: Final = 64
_LOOP_SCAN: Final = 0.05


def _notify(loop: asyncio.AbstractEventLoop, function: Callable[[], None]) -> None:
    if not loop.is_closed():
        try:
            loop.call_soon_threadsafe(function)
        except RuntimeError:
            return


def _completion(notification: asyncio.Future[T], settlement: Future[T]) -> None:
    if notification.done():
        return
    if settlement.cancelled():
        notification.cancel()
    elif (error := settlement.exception()) is not None:
        notification.set_exception(error)
    else:
        notification.set_result(settlement.result())


class _Job:
    """An accepted operation and its independent settlement and loop notification."""

    def __init__(
        self,
        operation: Callable[[sqlite3.Connection], object],
        loop: asyncio.AbstractEventLoop,
        discard: Callable[[sqlite3.Connection, object], None] | None,
    ) -> None:
        self.operation = operation
        self.loop = loop
        self.notification: asyncio.Future[object] = loop.create_future()
        self.settlement: Future[object] | None = None
        self.cancelled = False
        self.discard = discard
        self.returned = False
        self.disposed = False


class SQLiteWorker:
    """One lazily started executor with 64 pending jobs and FIFO async backpressure."""

    __slots__ = (
        "_closed",
        "_condition",
        "_connection",
        "_failures",
        "_lock",
        "_monitoring",
        "_pending",
        "_pool",
        "_retained",
        "_shutdown",
        "_waiting",
    )

    def __init__(self, path: str | Path) -> None:
        """Retain the explicit database path, allocating neither a thread nor a database."""
        self._connection = SQLiteConnection(path)
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._pool: ThreadPoolExecutor | None = None
        self._pending = 0
        self._waiting: deque[_Job] = deque()
        self._closed = False
        self._shutdown: Future[None] | None = None
        self._retained: set[_Job] = set()
        self._monitoring = False
        self._failures: list[BaseException] = []

    def _execute(self, job: _Job) -> object:
        with self._lock:
            self._pending -= 1
            admitted = self._admit()
        self._callbacks(admitted)
        return self._connection.run(job.operation)

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
        self._condition.notify_all()
        return admitted

    def _callbacks(self, jobs: list[_Job]) -> None:
        for job in jobs:
            if job.settlement is not None:
                job.settlement.add_done_callback(partial(self._settled, job))

    def _settled(self, job: _Job, settlement: Future[object]) -> None:
        admitted: list[_Job] = []
        with self._lock:
            if settlement.cancelled():
                self._pending -= 1
                admitted = self._admit()
            elif settlement.exception() is None and job.discard is not None:
                self._retained.add(job)
        self._callbacks(admitted)
        _notify(job.loop, lambda: _completion(job.notification, settlement))
        self._monitor()

    async def run(
        self,
        operation: Callable[[sqlite3.Connection], T],
        *,
        discard: Callable[[sqlite3.Connection, T], None] | None = None,
    ) -> T:
        """Await a FIFO job, preserving started settlement and compensating abandoned successful results if requested.

        A discard callback runs only on this worker, once, after successful settlement whose result was never
        returned. Queue mutations omit it. Publication callbacks retain monotonic counters when releasing a ref.
        """
        job = _Job(
            operation, asyncio.get_running_loop(), cast("Callable[[sqlite3.Connection, object], None] | None", discard)
        )
        with self._lock:
            if self._closed:
                msg = "SQLite store is closed"
                raise ValueError(msg)
            self._waiting.append(job)
            admitted = self._admit()
        self._callbacks(admitted)
        try:
            result = await job.notification
        except asyncio.CancelledError:
            with self._lock:
                job.cancelled = True
                settlement = job.settlement
                self._condition.notify_all()
            if settlement is not None:
                settlement.cancel()
            self._monitor()
            raise
        with self._lock:
            if job.disposed:
                msg = "SQLite store closed before returning publication"
                raise ValueError(msg)
            job.returned = True
            self._retained.discard(job)
            self._condition.notify_all()
        return cast("T", result)

    def _monitor(self) -> None:
        future: Future[None] | None = None
        with self._lock:
            if self._retained and not self._monitoring and not self._closed and self._pool is not None:
                self._monitoring = True
                future = self._pool.submit(self._scan)
        if future is not None:
            future.add_done_callback(self._scan_done)

    def _scan(self) -> None:
        while True:
            with self._condition:
                abandoned = tuple(job for job in self._retained if job.cancelled or job.loop.is_closed())
                if not abandoned:
                    if not self._retained or self._closed or self._pending:
                        return
                    self._condition.wait(_LOOP_SCAN)
                    continue
            for job in abandoned:
                self._dispose(job)

    def _dispose(self, job: _Job) -> None:
        with self._lock:
            settlement = job.settlement
            discard = job.discard
            if job.disposed or job.returned or settlement is None or discard is None:
                return
            job.disposed = True
            self._retained.discard(job)
        self._connection.run(lambda connection: discard(connection, settlement.result()))

    def _scan_done(self, future: Future[None]) -> None:
        with self._lock:
            self._monitoring = False
            if (error := future.exception()) is not None:
                self._failures.append(error)
        self._monitor()

    def _finish(self) -> None:
        with self._lock:
            retained = tuple(self._retained)
        try:
            for job in retained:
                self._dispose(job)
        finally:
            self._connection.close()
        if self._failures:
            first, *secondary = self._failures
            add_secondary(first, *secondary)
            raise first

    def _shutdown_done(self, settlement: Future[None]) -> None:
        del settlement
        if self._pool is not None:
            self._pool.shutdown(wait=False)

    async def aclose(self) -> None:
        """Close admission and drain accepted jobs; repeated or cancelled closes share retained shutdown."""
        loop = asyncio.get_running_loop()
        shutdown: Future[None] | None = None
        with self._lock:
            if self._shutdown is None:
                self._closed = True
                self._condition.notify_all()
                while self._waiting:
                    job = self._waiting.popleft()
                    rejected: Future[object] = Future()
                    rejected.set_exception(ValueError("SQLite store is closed"))
                    _notify(job.loop, lambda job=job, rejected=rejected: _completion(job.notification, rejected))
                if self._pool is None:
                    self._shutdown = Future()
                    self._shutdown.set_result(None)
                else:
                    shutdown = self._shutdown = self._pool.submit(self._finish)
            settlement = self._shutdown
        if shutdown is not None:
            shutdown.add_done_callback(self._shutdown_done)
        notification: asyncio.Future[None] = loop.create_future()
        settlement.add_done_callback(lambda done: _notify(loop, lambda: _completion(notification, done)))
        await notification
