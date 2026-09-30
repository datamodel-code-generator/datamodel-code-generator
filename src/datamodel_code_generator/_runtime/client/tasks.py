"""The owned asyncio tasks of a call: the work interrupted callers leave, and the failures those tasks carry."""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    import asyncio

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
