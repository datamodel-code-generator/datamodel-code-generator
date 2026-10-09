"""Warn about generated modules that an existing package directory hides from imports.

This module is a dependency-free leaf, so the writers and the publication journal can share it.
"""

from __future__ import annotations

import os
import warnings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


def _entries(directory: Path) -> list[str]:
    """Return the entry names of a directory, none when it cannot be listed, which is how imports read it."""
    try:
        return os.listdir(directory)  # noqa: PTH208
    except OSError:
        return []


def warn_shadowed_modules(modules: Iterable[Path]) -> None:
    """Warn for each module beside a package directory of the same name, which Python imports instead.

    A directory shadows `<name>.py` only when its entry is spelled exactly `<name>`, also on a case-insensitive
    filesystem, and holds an `__init__.py`: a directory without one loses to the module. Other files, and
    `__init__.py`, which is never imported by that name, cost no filesystem access. The warning names the absolute
    path of the module without resolving links.
    """
    for module in modules:
        if (name := module.name) == "__init__.py" or not name.endswith(".py"):
            continue
        if (
            os.path.isfile(os.path.join(os.fspath(module)[:-3], "__init__.py"))  # noqa: PTH113, PTH118
            and name[:-3] in _entries(module.parent)
        ):
            location = module.absolute()
            warnings.warn(
                f"{location.as_posix()}: The generated module is shadowed by the existing package directory "
                f"{location.with_suffix('').as_posix()}, which Python imports instead. "
                "Remove the stale directory to import the generated module.",
                stacklevel=2,
            )
