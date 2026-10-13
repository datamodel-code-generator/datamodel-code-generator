"""Find the Python files of an output directory, and warn about those a generation left in place.

This module imports nothing but the warning category, so the output comparison of `--check` and the target writers
can share it.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from datamodel_code_generator import LeftoverFilesWarning

if TYPE_CHECKING:
    from collections.abc import Set as AbstractSet
    from pathlib import Path


def python_files(directory: Path) -> set[Path]:
    """Return every `*.py` file under a directory outside `__pycache__`, relative to it; none without the directory."""
    if not directory.exists():
        return set()
    return {path.relative_to(directory) for path in directory.rglob("*.py") if "__pycache__" not in path.parts}


def warn_leftover_files(directory: Path, generated: AbstractSet[Path], shown: Path) -> None:
    """Warn once about the Python files under an output directory that are not among its generated files.

    They are the files `--check` lists as extra, named in its order as paths under the `shown` output path.
    """
    if not (leftover := sorted(python_files(directory) - generated)):
        return
    names = ", ".join((shown / path).as_posix() for path in leftover)
    warnings.warn(
        f"{shown.as_posix()}: These Python files are no longer generated and are left in place: {names}. "
        "Delete the ones you no longer need.",
        LeftoverFilesWarning,
        stacklevel=2,
    )
