"""Shared single-target configuration."""

from __future__ import annotations

import keyword
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from datamodel_code_generator._api_types import APIGenerationError, Diagnostic
from datamodel_code_generator._target_unicode import NEWEST_TARGET, is_identifier

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from datamodel_code_generator._format_types import PythonVersion


def _diagnostic(code: str, option_path: str, message: str) -> Diagnostic:
    return Diagnostic(code=code, severity="error", stage="config", message=message, option_path=option_path)


def _dotted(value: object, target_python_version: PythonVersion) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and all(is_identifier(part, target_python_version) and not keyword.iskeyword(part) for part in value.split("."))
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class TargetConfig:
    """Hold the settings every single target shares; concrete targets add their own fields.

    A target that serves its source document reads no local file the document would publish.
    """

    output: Path
    package: str
    model_package: str

    _option_prefix: ClassVar[str | None] = None
    _serves_source: ClassVar[bool] = False

    def __post_init__(self) -> None:
        """Reject values and combinations the shared contract does not allow, in field order.

        Names are checked as identifiers of the newest target until `check_target` checks them against the target.
        """
        self.check_target(NEWEST_TARGET)

    def check_target(self, target_python_version: PythonVersion) -> None:
        """Reject the values the target Python version does not allow, such as names that are not its identifiers."""
        if diagnostics := tuple(self._problems(target_python_version)):
            raise APIGenerationError(diagnostics, option_prefix=self._option_prefix)

    def _problems(self, target_python_version: PythonVersion) -> Iterator[Diagnostic]:
        for name in ("package", "model_package"):
            if not _dotted(getattr(self, name), target_python_version):
                yield _diagnostic("E_CONFIG_VALUE", name, f"{name} must be a dotted Python import path")
