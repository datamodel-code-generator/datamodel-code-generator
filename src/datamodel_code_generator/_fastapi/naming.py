"""The file stems a FastAPI server target cannot write as router modules."""

from __future__ import annotations

from datamodel_code_generator._target_naming import WINDOWS_DEVICES


def file_stem_conflict(stem: str) -> bool:
    """Return whether a file stem is reserved by the package layout or by Windows device names."""
    return stem.casefold() in {"__init__", *WINDOWS_DEVICES}
