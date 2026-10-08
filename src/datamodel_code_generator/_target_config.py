"""Shared single-target configuration."""

from __future__ import annotations

import keyword
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from datamodel_code_generator._api_types import APIGenerationError, Diagnostic

if TYPE_CHECKING:
    from collections.abc import Iterator


def _diagnostic(code: str, option_path: str, message: str) -> Diagnostic:
    return Diagnostic(code=code, severity="error", stage="config", message=message, option_path=option_path)


def _dotted(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and all(part.isidentifier() and not keyword.iskeyword(part) for part in value.split("."))
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class TargetConfig:
    """Hold the settings every single target shares; concrete targets add their own fields."""

    output: Path
    package: str
    model_package: str

    _option_prefix: ClassVar[str | None] = None

    def __post_init__(self) -> None:
        """Reject values and combinations the shared contract does not allow, in field order."""
        if diagnostics := tuple(self._problems()):
            raise APIGenerationError(diagnostics, option_prefix=self._option_prefix)

    def _problems(self) -> Iterator[Diagnostic]:
        if not isinstance(self.output, Path):
            yield _diagnostic("E_CONFIG_VALUE", "output", "output must be a Path")
        for name in ("package", "model_package"):
            if not _dotted(getattr(self, name)):
                yield _diagnostic("E_CONFIG_VALUE", name, f"{name} must be a dotted Python import path")
