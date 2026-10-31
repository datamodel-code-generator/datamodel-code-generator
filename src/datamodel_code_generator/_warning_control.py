"""How the generator's own warnings are shown: ignored while a block runs, once per key, and named past ``--debug``.

A retry parse, a recommendation trial and a probe of a type run with their warnings ignored (``quiet``), as the run
the user sees reports them; a warning about a schema is shown once per parse for each key it reports
(``first_report``); and a warning names the frame its ``stacklevel`` counts without the wrapper frames that
``--debug`` puts around the functions it traces (``is_tracer_frame``). The module imports only the standard library,
so every layer can import it.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Final, TypeVar
from warnings import catch_warnings, simplefilter

if TYPE_CHECKING:
    from collections.abc import Generator
    from types import FrameType

__all__ = ["first_report", "is_tracer_frame", "quiet"]

_KeyT = TypeVar("_KeyT")
_TRACER_MODULE_PREFIX: Final = "pysnooper"


@contextmanager
def quiet(category: type[Warning] = Warning) -> Generator[None, None, None]:
    """Ignore the warnings of ``category`` (every warning by default) while the block runs, then restore the filters."""
    with catch_warnings():
        simplefilter("ignore", category)
        yield


def first_report(seen: set[_KeyT], key: _KeyT) -> bool:
    """Return whether ``key`` is reported for the first time, recording it in ``seen``."""
    if key in seen:
        return False
    seen.add(key)
    return True


def is_tracer_frame(frame: FrameType) -> bool:
    """Return whether ``frame`` runs in the tracer that ``--debug`` wraps around the functions it traces."""
    return frame.f_globals.get("__name__", "").startswith(_TRACER_MODULE_PREFIX)
