"""Small runtime types shared by configuration, API, and parser modules.

The errors and warnings of the public API live here under the package's name (``datamodel_code_generator.Error``), and
the exit reasons of the command line under ``datamodel_code_generator.__main__``, so that a module raises, catches or
returns them without importing the package root or the command line.
"""

from __future__ import annotations

import sys
from collections import UserDict
from collections.abc import Callable, Sequence
from enum import Enum, IntEnum
from typing import TYPE_CHECKING, TypeAlias, TypeVar

from datamodel_code_generator._url_redaction import redact_path, redact_url
from datamodel_code_generator.enums import _ExitCode

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path
    from typing import Any
    from urllib.parse import ParseResult

    from datamodel_code_generator.enums import InputFileType

    GenerationInput: TypeAlias = Path | str | ParseResult | Mapping[str, Any] | list[Any]
    ParserSource: TypeAlias = str | Path | list[Path] | ParseResult | dict[str, Any]

TK = TypeVar("TK")
TV = TypeVar("TV")

ResponseObserver: TypeAlias = Callable[
    [str, Sequence[tuple[str, str]] | None, Sequence[tuple[str, str]] | None, bytes], None
]
"""Take a remote fetch's URL, headers and query parameters as requested, and the body that answered them."""

# Pydantic 2.5 cannot build schemas from stdlib TypeAliasType on Python 3.12.
if sys.version_info >= (3, 14):
    from typing import TypeAliasType
else:
    from typing_extensions import TypeAliasType

YamlScalar: TypeAlias = str | int | float | bool | None
YamlValue = TypeAliasType("YamlValue", "dict[str, YamlValue] | list[YamlValue] | YamlScalar")

GeneratedModules: TypeAlias = dict[tuple[str, ...], str]
"""Type alias for multiple generated modules.

Maps module path tuples (e.g., ("models", "user.py")) to generated code strings.
Returned by generate() when output=None and multiple modules are generated, or a server or client package.
"""

_LEGACY_MODULE = "datamodel_code_generator.parser"
_ROOT_MODULE = "datamodel_code_generator"


class _CollapseRootModelsRecursionError(RecursionError):
    """Signal that collapsing root models needs its circular-reference fallback."""


class LiteralType(Enum):
    """Options for handling enum fields as literals."""

    All = "all"
    One = "one"
    Off = "none"


class DefaultPutDict(UserDict[TK, TV]):
    """Dict that can lazily compute and cache missing values."""

    def get_or_put(
        self,
        key: TK,
        default: TV | None = None,
        default_factory: Callable[[TK], TV] | None = None,
    ) -> TV:
        """Get value for key, or compute and store it if missing."""
        if key in self:
            return self[key]
        if default is not None:
            value = self[key] = default
            return value
        if default_factory is not None:
            value = self[key] = default_factory(key)
            return value
        msg = "Not found default and default_factory"
        raise ValueError(msg)


class Error(Exception):
    """Base exception for datamodel-code-generator errors."""

    def __init__(self, message: str) -> None:
        """Initialize with message."""
        self.message: str = message

    def __str__(self) -> str:
        """Return string representation."""
        return self.message


class _StructuredOutputUnavailableError(Error):
    """Raised when the installed Pydantic is too old for the payload models of the JSON output of the CLI."""


class _InvalidConfigTomlError(Error, ValueError):
    """Raised for a configuration file that is not valid TOML: a configuration error, and the ``ValueError`` it was."""


class DanglingRefWarning(UserWarning):
    """Warn that a local JSON pointer target was not found."""


class SchemaResourceRefWarning(UserWarning):
    """Warn that a reference keeps, for compatibility, a target other than the one JSON Schema resolves."""


class DefaultValueTypeWarning(UserWarning):
    """Warn that a generated default is still serialized instead of its runtime type."""


class DocumentationAnnotationWarning(UserWarning):
    """Warn that a served document leaves out an annotation that has no JSON form."""


class LeftoverFilesWarning(UserWarning):
    """Warn that a generated package holds Python files that the generation left in place and does not generate."""


class NamingHintWarning(UserWarning):
    """Point a command line run whose class names are mostly numbered collisions to ``--recommend-options``."""


class InvalidClassNameError(Error):
    """Raised when a schema title cannot be converted to a valid Python class name."""

    def __init__(self, class_name: str) -> None:
        """Initialize with class name."""
        self.class_name = class_name
        from datamodel_code_generator.python_literal import stable_repr  # noqa: PLC0415

        message = f"title={stable_repr(class_name)} is invalid class name."
        super().__init__(message=message)


class InvalidFileFormatError(Error):
    """Raised when the input file format is invalid or cannot be parsed."""

    def __init__(
        self,
        original_error: Exception,
        input_file_type: InputFileType | None = None,
        *,
        source: str | Path | None = None,
    ) -> None:
        """Initialize with original error, input file type, and source context."""
        self.original_error = original_error
        self.input_file_type = input_file_type
        self.source = source
        error_detail = f"{type(original_error).__name__}: {original_error}"
        source_detail = f" at {redact_url(str(source))}" if source is not None else ""
        if input_file_type is not None:
            message = f"Invalid file format for {input_file_type.value}{source_detail}: {error_detail}"
        else:
            message = f"Invalid file format{source_detail}: {error_detail}"
        super().__init__(message=message)


class SchemaParseError(Error):
    """Raised when an error occurs during schema parsing with path context."""

    def __init__(
        self,
        message: str,
        path: list[str] | None = None,
        original_error: Exception | None = None,
    ) -> None:
        """Initialize with message, schema path, and optional original error."""
        self.path = path or []
        self.original_error = original_error
        full_message = self._format_message(message)
        super().__init__(message=full_message)

    def _format_message(self, message: str) -> str:
        """Format message with schema path context."""
        if self.path:
            path_str = redact_path(self.path)
            return f"Error at schema path '{path_str}': {message}"
        return message


class SchemaFetchError(Error):
    """Raised when fetching a remote schema fails (HTTP error, unexpected content type)."""


class Exit(IntEnum):
    """Exit reasons."""

    OK = _ExitCode.OK
    DIFF = _ExitCode.DIFF
    ERROR = _ExitCode.ERROR
    KeyboardInterrupt = _ExitCode.INTERRUPTED


LiteralType.__module__ = _LEGACY_MODULE
DefaultPutDict.__module__ = _LEGACY_MODULE
Error.__module__ = _ROOT_MODULE
_StructuredOutputUnavailableError.__module__ = _ROOT_MODULE
_InvalidConfigTomlError.__module__ = _ROOT_MODULE
DanglingRefWarning.__module__ = _ROOT_MODULE
SchemaResourceRefWarning.__module__ = _ROOT_MODULE
DefaultValueTypeWarning.__module__ = _ROOT_MODULE
DocumentationAnnotationWarning.__module__ = _ROOT_MODULE
LeftoverFilesWarning.__module__ = _ROOT_MODULE
NamingHintWarning.__module__ = _ROOT_MODULE
InvalidClassNameError.__module__ = _ROOT_MODULE
InvalidFileFormatError.__module__ = _ROOT_MODULE
SchemaParseError.__module__ = _ROOT_MODULE
SchemaFetchError.__module__ = _ROOT_MODULE
Exit.__module__ = "datamodel_code_generator.__main__"

__all__ = ["DefaultPutDict", "LiteralType", "ResponseObserver", "YamlScalar", "YamlValue"]
