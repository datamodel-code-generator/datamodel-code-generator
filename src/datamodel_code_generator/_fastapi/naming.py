"""The file stems a FastAPI server target cannot write as router modules."""

from __future__ import annotations

from typing import TYPE_CHECKING

from datamodel_code_generator._target_naming import WINDOWS_DEVICES
from datamodel_code_generator._target_unicode import casefold

if TYPE_CHECKING:
    from datamodel_code_generator._format_types import PythonVersion


def file_stem_conflict(stem: str, target_python_version: PythonVersion) -> bool:
    """Return whether a file stem is reserved by the package layout or by Windows device names, folded as the target."""
    return casefold(stem, target_python_version) in {"__init__", *WINDOWS_DEVICES}
