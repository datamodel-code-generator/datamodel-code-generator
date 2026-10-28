"""Lightweight formatting option types.

This module intentionally stays dependency-light so CLI argument setup and
reference helpers can use formatting enums without importing formatter runtime
dependencies such as subprocess, shutil, black, or isort.
"""

from __future__ import annotations

from enum import Enum
from functools import cached_property
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datamodel_code_generator._requirements import PythonConstruct


class DatetimeClassType(Enum):
    """Output datetime class type options."""

    Datetime = "datetime"
    Awaredatetime = "AwareDatetime"
    Naivedatetime = "NaiveDatetime"
    Pastdatetime = "PastDatetime"
    Futuredatetime = "FutureDatetime"


class DateClassType(Enum):
    """Output date class type options."""

    Date = "date"
    Pastdate = "PastDate"
    Futuredate = "FutureDate"


class PythonVersion(Enum):
    """Supported Python version targets for code generation."""

    PY_310 = "3.10"
    PY_311 = "3.11"
    PY_312 = "3.12"
    PY_313 = "3.13"
    PY_314 = "3.14"
    PY_315 = "3.15"

    @cached_property
    def version_key(self) -> tuple[int, int]:
        """Return (major, minor) tuple for version comparison."""
        major, minor = self.value.split(".")
        return int(major), int(minor)

    def _meets(self, construct: PythonConstruct) -> bool:
        """Return whether this target has a construct of the requirement table."""
        from datamodel_code_generator._requirements import python_target_meets  # noqa: PLC0415

        return python_target_meets(construct, self.version_key)

    @property
    def has_union_operator(self) -> bool:  # pragma: no cover
        """Check if Python version supports the union operator (|)."""
        return self._meets("python.union_operator")

    @property
    def has_type_alias(self) -> bool:  # pragma: no cover
        """Check if Python version supports TypeAlias.

        .. deprecated::
            This property is unused and will be removed in a future version.
        """
        from datamodel_code_generator.deprecations import warn_deprecated  # noqa: PLC0415

        warn_deprecated("python-api.python-version-has-type-alias", stacklevel=2)
        return self._meets("python.type_alias_annotation")

    @property
    def has_typed_dict_non_required(self) -> bool:
        """Check if Python version supports TypedDict NotRequired."""
        return self._meets("python.typed_dict_not_required")

    @property
    def has_typed_dict_read_only(self) -> bool:
        """Check if Python version supports TypedDict ReadOnly (PEP 705)."""
        return self._meets("python.typed_dict_read_only")

    @property
    def has_typed_dict_closed(self) -> bool:
        """Check if Python version supports TypedDict closed/extra_items (PEP 728).

        PEP 728 is part of Python 3.15. Earlier targets import TypedDict from typing_extensions.
        """
        return self._meets("python.typed_dict_closed")

    @property
    def has_kw_only_dataclass(self) -> bool:
        """Check if Python version supports kw_only in dataclasses."""
        return self._meets("python.kw_only_dataclass")

    @property
    def has_type_statement(self) -> bool:
        """Check if Python version supports type statements."""
        return self._meets("python.type_statement")

    @property
    def has_string_arguments_in_builtin_generics(self) -> bool:
        """Check if ``typing`` resolves a quoted name inside ``list[...]`` and other builtin generics (3.11+)."""
        return self._meets("python.builtin_generic_strings")

    @property
    def has_native_deferred_annotations(self) -> bool:
        """Check if Python version has native deferred annotations (Python 3.14+)."""
        return self._meets("python.deferred_annotations")

    @property
    def has_strenum(self) -> bool:
        """Check if Python version supports StrEnum."""
        return self._meets("python.strenum")


PythonVersionMin = PythonVersion.PY_310


class Formatter(Enum):
    """Available code formatters for generated output."""

    BUILTIN = "builtin"
    BLACK = "black"
    ISORT = "isort"
    RUFF_CHECK = "ruff-check"
    RUFF_FORMAT = "ruff-format"


DEFAULT_FORMATTERS: tuple[Formatter, ...] = (Formatter.BLACK, Formatter.ISORT)
EXTERNAL_FORMATTERS = frozenset({
    Formatter.BLACK,
    Formatter.ISORT,
    Formatter.RUFF_CHECK,
    Formatter.RUFF_FORMAT,
})

# Keep the historical public pickle/repr path after moving these enums out of format.py.
for _public_type in (DateClassType, DatetimeClassType, Formatter, PythonVersion):
    _public_type.__module__ = "datamodel_code_generator.format"
del _public_type


__all__ = [
    "DEFAULT_FORMATTERS",
    "EXTERNAL_FORMATTERS",
    "DateClassType",
    "DatetimeClassType",
    "Formatter",
    "PythonVersion",
    "PythonVersionMin",
]
