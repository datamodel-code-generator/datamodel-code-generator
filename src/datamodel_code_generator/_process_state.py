"""Serialize operations that temporarily modify process-wide state, and put back the entries they replaced.

An operation that replaces an entry of a process-wide mapping (``sys.modules``, ``sys.path_importer_cache``) saves the
entry first as ``mapping.get(key, ABSENT)`` and gives it to ``restore_entry`` when it is done.
"""

from __future__ import annotations

from threading import RLock
from typing import TYPE_CHECKING, Final, TypeVar, cast

from typing_extensions import Sentinel

if TYPE_CHECKING:
    from collections.abc import MutableMapping

PROCESS_STATE_LOCK = RLock()

ABSENT: Final = Sentinel("ABSENT")
"""The saved entry of a key that a mapping did not hold."""

_K = TypeVar("_K")
_V = TypeVar("_V")


def restore_entry(mapping: MutableMapping[_K, _V], key: _K, saved: object) -> None:
    """Put back the entry ``mapping`` held for ``key`` when it was saved: the saved value, or no entry (``ABSENT``)."""
    if saved is ABSENT:
        mapping.pop(key, None)
    else:
        mapping[key] = cast("_V", saved)
