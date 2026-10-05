"""The single disk thread that async file bodies and async downloads run their blocking file work on.

A worker starts its thread on first use and runs one operation at a time, so work submitted later always runs after
work submitted earlier. An interrupted caller returns at once while its operation settles on a retained task.
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

from .errors import add_secondary, body_failure
from .tasks import LEFT_WORK, TaskInterruptionError, task_failure

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine
    from concurrent.futures import Future, ThreadPoolExecutor
    from typing import TypeVar

    T = TypeVar("T")


async def carried(work: Awaitable[None]) -> None:
    """Keep an interruption of deferred work as its failure, so the call it belongs to still reports it."""
    try:
        await work
    except BaseException as error:
        if isinstance(error, Exception):
            raise
        raise TaskInterruptionError(error) from None


def raise_late(failures: tuple[BaseException, ...]) -> None:
    """Raise the first late failure of interrupted disk work, carrying the rest as its secondary errors."""
    if failures:
        add_secondary(failures[0], *failures[1:])
        raise failures[0]


class DiskWorker:
    """One named thread for blocking file work, started by its first operation and stopped by closing."""

    __slots__ = ("closed", "failed", "lock", "name", "pool", "releasing", "settling", "uses")

    def __init__(self, name: str) -> None:
        """Name the thread after its owner; nothing starts before the first operation."""
        self.name = name
        self.pool: ThreadPoolExecutor | None = None
        self.lock = threading.Lock()
        self.closed = False
        self.uses = 0
        self.settling: dict[asyncio.Task[None], list[asyncio.Task[None]] | None] = {}
        self.releasing: set[asyncio.Task[None]] = set()
        self.failed: list[tuple[list[asyncio.Task[None]] | None, BaseException]] = []

    def acquire(self) -> None:
        """Keep the worker available until an admitted use finishes its cleanup."""
        with self.lock:
            if self.closed:
                raise body_failure(reason="body_not_replayable")
            self.uses += 1

    def release(self) -> None:
        """Release one use after all its disk work has settled."""
        with self.lock:
            self.uses -= 1
        self._stop()

    async def run(
        self,
        function: Callable[..., T],
        *arguments: object,
        discard: Callable[[T], None] | None = None,
        cleanup: bool = False,
    ) -> T:
        """Run one disk operation; an interrupted caller returns at once, and the work settles on a retained task.

        The retained task disposes a result the interrupted work still returned through `discard`. Cleanup, such as
        closing the file, first waits for the work interrupted callers left; a caller interrupted in that wait leaves
        the cleanup to a retained task that holds the worker. An interrupted cleanup settles before it ends, redoing a
        close its cancellation dropped.
        """
        pool = self._started(cleanup=cleanup)
        late: tuple[BaseException, ...] = ()
        if cleanup:
            try:
                late = await self.settled()
            except asyncio.CancelledError:
                with self.lock:
                    self.uses += 1
                self.defer(carried(self._finish(function, *arguments)), release=True)
                raise
        future = pool.submit(function, *arguments)
        try:
            result = await asyncio.wrap_future(future)
        except asyncio.CancelledError as error:
            if not cleanup:
                self.defer(self._settle(future, discard))
                raise
            add_secondary(error, *await self._outcome(future, lambda: pool.submit(function, *arguments)), *late)
            raise
        except BaseException as error:
            add_secondary(error, *late)
            raise
        raise_late(late)
        return result

    def submit(self, function: Callable[..., T], *arguments: object) -> Future[T]:
        """Start one disk operation without waiting for it; `abandon` leaves one nobody waits for to settle."""
        return self._started(cleanup=False).submit(function, *arguments)

    def abandon(self, future: Future[T]) -> None:
        """Let started work settle on a retained task, which reports its late failure to `settled`."""
        self.defer(self._settle(future, None))

    def _started(self, *, cleanup: bool) -> ThreadPoolExecutor:
        """Return the thread pool, starting it on first use, and refuse work other than cleanup once closed."""
        with self.lock:
            if self.closed and not cleanup:
                raise body_failure(reason="body_not_replayable")
            if (pool := self.pool) is None:
                from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415 - Only disk work starts one.

                pool = self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=self.name)
        return pool

    async def _finish(self, function: Callable[..., object], *arguments: object) -> None:
        """Run the cleanup an interrupted caller left before it began, holding the worker until it ends."""
        try:
            await self.run(function, *arguments, cleanup=True)
        finally:
            self.release()

    def defer(self, work: Coroutine[object, object, None], *, release: bool = False) -> None:
        """Finish work an interrupted caller left on a retained task, which belongs to the caller's call.

        Disk work settles before any file is released; a deferred release is awaited by the interrupted call instead.
        """
        task = asyncio.ensure_future(work)
        left = LEFT_WORK.get()
        if release:
            self.releasing.add(task)
            task.add_done_callback(self.releasing.discard)
        else:
            self.settling[task] = left
            task.add_done_callback(self._settled)
        if release and left is not None:
            left.append(task)

    def _settled(self, task: asyncio.Task[None]) -> None:
        left = self.settling.pop(task)
        if (failure := task_failure(task)) is not None:
            self.failed.append((left, failure))

    async def settled(self, *, every: bool = False) -> tuple[BaseException, ...]:
        """Wait for the disk work interrupted callers left, and return what the current call's share of it failed late.

        Settling work waits for nothing else, so two interrupted opens of one path never wait for each other. A late
        failure stays with the worker until its call asks, even when its work finished before that call's release. A
        worker that serves a single download takes every late failure with `every`.
        """
        if asyncio.current_task() in self.settling:
            return ()
        if pending := tuple(self.settling):
            await asyncio.wait(pending)
        if not self.failed:
            return ()
        owner = LEFT_WORK.get()
        failures = tuple(failure for left, failure in self.failed if every or left is owner)
        self.failed = [entry for entry in self.failed if not every and entry[0] is not owner]
        return failures

    async def _settle(self, future: Future[T], discard: Callable[[T], None] | None) -> None:
        """Wait for interrupted disk work, raise what failed late, and dispose a late file on this same worker."""
        raise_late(await self._outcome(future))
        if discard is not None and not future.cancelled():
            await self.run(discard, future.result(), cleanup=True)

    async def _outcome(
        self, future: Future[T], resubmit: Callable[[], Future[T]] | None = None
    ) -> tuple[BaseException, ...]:
        """Wait for disk work through any cancellation, redo a cancelled cleanup once, and return what failed."""
        settled = asyncio.wrap_future(future)
        while not settled.done():
            try:
                await asyncio.wait((settled,))
            except asyncio.CancelledError:  # noqa: PERF203 - Repeated cancellation must not abandon disk work.
                continue
        if future.cancelled():
            return () if resubmit is None else await self._outcome(resubmit())
        return () if (failure := settled.exception()) is None else (failure,)

    def close(self) -> None:
        """Refuse new work and stop after admitted uses finish their owned cleanup."""
        with self.lock:
            self.closed = True
        self._stop()

    def _stop(self) -> None:
        with self.lock:
            pool = None
            if self.closed and not self.uses:
                pool, self.pool = self.pool, None
        if pool is not None:
            pool.shutdown(wait=False)
