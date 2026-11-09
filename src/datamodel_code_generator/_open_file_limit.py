"""Raise the soft limit on open files of a command-line run to the hard limit, as the Go runtime does at startup.

macOS starts a process with a soft limit of 256 open files, below what writing a large client takes. The command line
raises it once before it reads its options; ``generate()`` and the rest of the Python API leave the limits of the
process that calls them as they are.
"""

from __future__ import annotations

from typing import Final

_OPEN_MAX: Final = 10240
"""``OPEN_MAX`` of macOS: the soft limit it takes where it refuses the hard limit, unlimited or past
``kern.maxfilesperproc``."""


def _targets(soft: int, hard: int, unlimited: int) -> tuple[int, ...]:
    """Return the soft limits to try, highest first: the hard limit, then one capped at ``OPEN_MAX``, above ``soft``."""
    if soft == unlimited:
        return ()
    capped = _OPEN_MAX if hard == unlimited else min(hard, _OPEN_MAX)
    return tuple(target for target in dict.fromkeys((hard, capped)) if target == unlimited or target > soft)


def raise_open_file_limit() -> None:
    """Raise the soft ``RLIMIT_NOFILE`` to the hard limit, or to ``OPEN_MAX`` where the kernel refuses that.

    Nothing changes where the ``resource`` module is unavailable (Windows) or the kernel refuses every target.
    """
    try:
        import resource  # noqa: PLC0415
    except ImportError:
        return
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    for target in _targets(soft, hard, resource.RLIM_INFINITY):
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
        except (ValueError, OSError):
            continue
        return
