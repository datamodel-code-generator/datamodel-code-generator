"""Non-abandoning file operations through native backend offload."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, TypeVar

from anyio import to_thread

from .errors import body_failure

if TYPE_CHECKING:
    from collections.abc import Callable
T = TypeVar("T")


class DiskWorker:
    """Offload blocking file work, completing it before releasing the file."""

    def __init__(self) -> None:
        self.closed = False

    def acquire(self) -> None:
        """Refuse new file use after the source closed."""
        if self.closed:
            reason = "not_replayable"
            raise body_failure(reason)

    async def run(
        self,
        function: Callable[..., T],
        *arguments: object,
        cleanup: bool = False,
    ) -> T:
        """Finish each admitted operation before cancellation can release or rewind its file."""
        if self.closed and not cleanup:
            reason = "not_replayable"
            raise body_failure(reason)
        return await to_thread.run_sync(partial(function, *arguments), abandon_on_cancel=False)

    def close(self) -> None:
        """Refuse new work; no executor or pending result is retained."""
        self.closed = True
