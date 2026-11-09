"""The pass trace: the name of every pass that runs, and ``MODULE_TRACE_MARK`` before each output module.

``scripts/check_pass_pipeline.py`` records it; the wrappers of ``parse.py`` append to it.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Final

from tests.checks.installer import install

if TYPE_CHECKING:
    from collections.abc import Generator

MODULE_TRACE_MARK: Final = "-- module"
TRACE: Final[ContextVar[list[str] | None]] = ContextVar("pass_trace", default=None)


@contextmanager
def recording_pass_trace() -> Generator[list[str], None, None]:
    """Record the name of every pass that runs while the block runs, and ``MODULE_TRACE_MARK`` before each module."""
    install()
    trace: list[str] = []
    token = TRACE.set(trace)
    try:
        yield trace
    finally:
        TRACE.reset(token)
