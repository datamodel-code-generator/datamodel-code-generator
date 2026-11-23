"""How the generator's own warnings are shown: ignored while a block runs, once per key, and named past ``--debug``.

A retry parse, a recommendation trial and a probe of a type run with their warnings ignored (``quiet``), as the run
the user sees reports them; a parse again after one that failed shows only the warnings no parse before showed
(``shown_once``); a parse that the run may drop shows its warnings only once it is kept (``held``); a warning about a
schema is shown once per parse for each key it reports (``first_report``); and a warning names the frame its
``stacklevel`` counts without the wrapper frames that ``--debug`` puts around the functions it traces
(``is_tracer_frame``). ``shown_once`` and ``first_report`` record a warning as it is shown, past the filters
(``_showing``), so a block whose warnings the filters ignore, as inside ``quiet``, leaves its keys to a later report.
A block checks only the warnings its own thread shows, through one ``warnings.showwarning`` hook that the open blocks of
every thread share, and the last of them to close puts back the hook it replaced, so generations that overlap in
threads leave no hook of a closed block in place. The module imports only the standard library, so every layer can
import it.
"""

from __future__ import annotations

import inspect
import warnings
from contextlib import contextmanager
from contextvars import ContextVar
from functools import partial
from operator import attrgetter
from threading import Lock
from typing import TYPE_CHECKING, Final, TextIO, TypeVar, final
from warnings import catch_warnings, simplefilter

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from types import FrameType

__all__ = [
    "first_report",
    "frame_at",
    "held",
    "is_tracer_frame",
    "quiet",
    "shown_once",
    "warn_as_public",
    "warn_at_frame",
]

ShownWarning = tuple[str, type[Warning], str, int]
"""The text, category, file and line of a shown warning."""
_ShowCall = tuple[Warning | str, type[Warning], str, int, TextIO | None, str | None]
"""The arguments of a ``warnings.showwarning`` call: a warning's message, category, file, line, stream and source."""

_KeyT = TypeVar("_KeyT")
_TRACER_MODULE_PREFIX: Final = "pysnooper"
_PACKAGE_MODULE: Final = "datamodel_code_generator"
_CORE_MODULES: Final = frozenset({
    "datamodel_code_generator._debug",
    "datamodel_code_generator._facade",
    "datamodel_code_generator._generation",
    "datamodel_code_generator._option_stages",
})
"""The modules the generation core moved to out of the package root: their warnings are filtered as the root's."""


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
def held() -> Generator[Callable[[], None], None, None]:
    """Hold the warnings the block shows, and yield the function that shows them, once called after the block.

    The blocks open inside it check a warning as the block shows it, the blocks open around it as the function shows
    it, so a block whose work is dropped, its function never called, shows nothing and leaves those around it as they
    were.
    """
    calls: list[_ShowCall] = []

    def hold(call: _ShowCall) -> bool:
        calls.append(call)
        return False

    def show() -> None:
        for call in calls:
            warnings.showwarning(*call)

    with _showing(hold):
        yield show


@contextmanager
def first_report(seen: set[_KeyT], key: _KeyT) -> Generator[bool, None, None]:
    """Yield whether ``key`` is unreported in ``seen``, recording it there once the block shows a warning.

    A warning the filters ignore, as inside ``quiet``, is not shown and records nothing, so the next report of ``key``
    shows its warning (#6540).
    """
    if key in seen:
        yield False
        return

    def record(_call: _ShowCall) -> bool:
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
_CHECKS: Final[ContextVar[tuple[Callable[[_ShowCall], bool], ...]]] = ContextVar("shown_warning_checks", default=())
"""The checks of the blocks open in this thread, the outermost first."""


@contextmanager
def _showing(show: Callable[[_ShowCall], bool]) -> Generator[None, None, None]:
    """Show a warning the block emits only where ``show`` returns True for the call that shows it.

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


def _show_kept(  # noqa: PLR0917
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
    call = (message, category, filename, lineno, file, line)
    if all(show(call) for show in reversed(_CHECKS.get())):
        _HOOK.replaced(*call)


def _first_record(seen: set[ShownWarning], call: _ShowCall) -> bool:
    """Return whether the warning ``call`` shows is recorded for the first time, recording it in ``seen``."""
    message, category, filename, lineno, _, _ = call
    if (key := (str(message), category, filename, lineno)) in seen:
        return False
    seen.add(key)
    return True


def is_tracer_frame(frame: FrameType) -> bool:
    """Return whether ``frame`` runs in the tracer that ``--debug`` wraps around the functions it traces."""
    return frame.f_globals.get("__name__", "").startswith(_TRACER_MODULE_PREFIX)


def frame_at(stacklevel: int) -> FrameType:
    """Return the frame that ``warn(stacklevel=stacklevel)`` names in the function that calls the caller of this.

    A function ``warn_x`` that calls ``frame_at`` and stands in for ``warn()`` takes ``stacklevel=1`` for the function
    that called ``warn_x``. The frames of the wrappers that ``--debug`` puts around the functions it traces are not
    counted past the first.
    """
    frame: FrameType = _CALLER(_CALLER(inspect.currentframe()))
    for _ in range(stacklevel - 1):
        frame = _CALLER(frame)
        while is_tracer_frame(frame):
            frame = _CALLER(frame)
    return frame


_CALLER: Final = attrgetter("f_back")
"""The frame that called a frame (``f_back``).

The callers of `frame_at` stand in for ``warn()`` deep inside the package, so the stack holds the frames they ask for.
"""


def warn_at_frame(frame: FrameType, message: str, category: type[Warning], *, module: str | None = None) -> None:
    """Warn as ``warn()`` does for ``frame``, with ``module`` (by default the frame's own, as the package's) matched.

    The file, the line and the registry of the warnings shown once per location are those of ``frame``. A frame in a
    module of the generation core (``_CORE_MODULES``) matches filters as ``datamodel_code_generator``, the module the
    code of the core had before it moved out of the package root.
    """
    name = frame.f_globals.get("__name__", "<string>")
    warnings.warn_explicit(
        message,
        category,
        frame.f_code.co_filename,
        frame.f_lineno,
        module=module or (_PACKAGE_MODULE if name in _CORE_MODULES else name),
        registry=frame.f_globals.setdefault("__warningregistry__", {}),
        module_globals=frame.f_globals,
    )


def warn_as_public(message: str, category: type[Warning], *, stacklevel: int) -> None:
    """Warn as ``warn(message, category, stacklevel=stacklevel)`` does, with the module ``warn_at_frame`` gives."""
    warn_at_frame(frame_at(stacklevel), message, category)
