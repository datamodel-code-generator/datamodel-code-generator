"""How the generator's own warnings are shown: ignored while a block runs, once per key, and named past ``--debug``.

A retry parse, a recommendation trial and a probe of a type run with their warnings ignored (``quiet``), as the run
the user sees reports them; a parse again after one that failed shows only the warnings no parse before showed
(``shown_once``); a warning about a schema is shown once per parse for each key it reports (``first_report``); and a
warning names the frame its ``stacklevel`` counts without the wrapper frames that ``--debug`` puts around the functions
it traces (``is_tracer_frame``). ``shown_once`` and ``first_report`` record a warning as it is shown, past the filters
(``_showing``), so a block whose warnings the filters ignore, as inside ``quiet``, leaves its keys to a later report.
A block checks only the warnings its own thread shows, through one ``warnings.showwarning`` hook that the open blocks of
every thread share, and the last of them to close puts back the hook it replaced, so generations that overlap in
threads leave no hook of a closed block in place. The module imports only the standard library, so every layer can
import it.
"""

from __future__ import annotations

import warnings
from contextlib import contextmanager
from contextvars import ContextVar
from functools import partial
from threading import Lock
from typing import TYPE_CHECKING, Final, TextIO, TypeVar, final
from warnings import catch_warnings, simplefilter

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from types import FrameType

__all__ = ["first_report", "is_tracer_frame", "quiet", "shown_once"]

ShownWarning = tuple[str, type[Warning], str, int]
"""The text, category, file and line of a shown warning."""

_KeyT = TypeVar("_KeyT")
_TRACER_MODULE_PREFIX: Final = "pysnooper"


@contextmanager
def quiet(category: type[Warning] = Warning) -> Generator[None, None, None]:
    """Ignore the warnings of ``category`` (every warning by default) while the block runs, then restore the filters."""
    with catch_warnings():
        simplefilter("ignore", category)
        yield


@contextmanager
def shown_once(seen: set[ShownWarning]) -> Generator[None, None, None]:
    """Show a warning the block emits only where ``seen`` lacks its text, category and location, recording it there.

    The filters apply first, as to any warning; a warning they leave is shown as before.
    """
    with _showing(partial(_first_record, seen)):
        yield


@contextmanager
def first_report(seen: set[_KeyT], key: _KeyT) -> Generator[bool, None, None]:
    """Yield whether ``key`` is unreported in ``seen``, recording it there once the block shows a warning.

    A warning the filters ignore, as inside ``quiet``, is not shown and records nothing, so the next report of ``key``
    shows its warning (#6540).
    """
    if key in seen:
        yield False
        return

    def record(_shown: ShownWarning) -> bool:
        seen.add(key)
        return True

    with _showing(record):
        yield True


@final
class _SharedHook:
    """The ``warnings.showwarning`` hook that the open blocks of every thread share.

    It counts the open blocks (``users``) and keeps the hook it replaced, which shows the warnings the checks keep.
    """

    def __init__(self) -> None:
        self.lock = Lock()
        self.users = 0
        self.replaced: Callable[..., None] = warnings.showwarning


_HOOK: Final = _SharedHook()
_CHECKS: Final[ContextVar[tuple[Callable[[ShownWarning], bool], ...]]] = ContextVar("shown_warning_checks", default=())
"""The checks of the blocks open in this thread, the outermost first."""


@contextmanager
def _showing(show: Callable[[ShownWarning], bool]) -> Generator[None, None, None]:
    """Show a warning the block emits only where ``show`` returns True for its text, category and location.

    The filters apply first, as to any warning: ``show`` sees only the warnings they leave, the ones shown, and of
    those only the ones its own thread shows (``_show_kept``).
    """
    token = _CHECKS.set((*_CHECKS.get(), show))
    with _HOOK.lock:
        if warnings.showwarning is not _show_kept:
            _HOOK.replaced = warnings.showwarning
            warnings.showwarning = _show_kept  # ty: ignore[invalid-assignment]
        _HOOK.users += 1
    try:
        yield
    finally:
        with _HOOK.lock:
            _HOOK.users -= 1
            if not _HOOK.users and warnings.showwarning is _show_kept:
                warnings.showwarning = _HOOK.replaced
        _CHECKS.reset(token)


def _show_kept(  # noqa: PLR0913, PLR0917
    message: Warning | str,
    category: type[Warning],
    filename: str,
    lineno: int,
    file: TextIO | None = None,
    line: str | None = None,
) -> None:
    """Show a warning that every block open in this thread keeps, the innermost asked first, through the hook replaced.

    A thread with no block open shows each warning as the hook replaced does.
    """
    if all(show((str(message), category, filename, lineno)) for show in reversed(_CHECKS.get())):
        _HOOK.replaced(message, category, filename, lineno, file, line)


def _first_record(seen: set[_KeyT], key: _KeyT) -> bool:
    """Return whether ``key`` is recorded for the first time, recording it in ``seen``."""
    if key in seen:
        return False
    seen.add(key)
    return True


def is_tracer_frame(frame: FrameType) -> bool:
    """Return whether ``frame`` runs in the tracer that ``--debug`` wraps around the functions it traces."""
    return frame.f_globals.get("__name__", "").startswith(_TRACER_MODULE_PREFIX)
