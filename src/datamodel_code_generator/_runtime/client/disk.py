"""Blocking file work that async file bodies and async downloads run in a thread and always wait for."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import TYPE_CHECKING, Any, TypeVar

from .errors import body_failure

if TYPE_CHECKING:
    from collections.abc import Callable

T = TypeVar("T")


async def _settled(work: asyncio.Future[Any]) -> None:
    """Wait for work running in a thread through any further cancellation, leaving its outcome on the future."""
    while not work.done():
        try:
            await asyncio.wait((work,))
        except asyncio.CancelledError:  # noqa: PERF203 - A file is never released under running work.
            continue


class DiskWorker:
    """Run each file operation in a thread, finishing it before a cancellation reaches the code that closes the file."""

    __slots__ = ("closed",)

    def __init__(self) -> None:
        """Start open, with nothing running."""
        self.closed = False

    def acquire(self) -> None:
        """Refuse new file use once the source closed."""
        if self.closed:
            raise body_failure(reason="body_not_replayable")

    async def run(
        self,
        function: Callable[..., T],
        *arguments: object,
        discard: Callable[[T], object] | None = None,
        cleanup: bool = False,
    ) -> T:
        """Run one operation; a cancelled caller still waits for it, then the cancellation propagates unchanged.

        A result the cancelled operation still returned, such as an opened file, is given to `discard` first.
        """
        if self.closed and not cleanup:
            raise body_failure(reason="body_not_replayable")
        loop = asyncio.get_running_loop()
        work = loop.run_in_executor(None, partial(function, *arguments))
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            await _settled(work)
            if discard is not None and not work.cancelled() and work.exception() is None:
                await _settled(loop.run_in_executor(None, discard, work.result()))
            raise

    def close(self) -> None:
        """Refuse new work."""
        self.closed = True
