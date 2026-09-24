"""Common model-codec errors and value-free wire validation issues."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

_MAX_MESSAGE_LENGTH: Final = 1024


@dataclass(frozen=True, slots=True, kw_only=True)
class WireIssue:
    """Describe one wire validation failure by pointers only, never by the rejected value."""

    code: str
    message: str
    instance_pointer: str
    schema_id: str
    schema_pointer: str

    def __post_init__(self) -> None:
        """Keep issue records finite and identifier-coded."""
        if not all(part.isidentifier() for part in self.code.split(".")):
            msg = "A wire issue code must be a dotted identifier"
            raise ValueError(msg)
        if len(self.message) > _MAX_MESSAGE_LENGTH:
            msg = "A wire issue message must not exceed 1024 characters"
            raise ValueError(msg)


class CodecError(Exception):
    """Base class for every model-codec failure."""


class CodecConfigurationError(CodecError):
    """Reject an unusable codec configuration before any request or response is processed."""


class CodecSelectionError(CodecConfigurationError):
    """Reject a selector that does not name a declared use, status, or media."""


class CodecAdapterError(CodecConfigurationError):
    """Reject a codec adapter that broke its version 1 contract at startup or at a callback boundary."""


class CodecBindingError(CodecError):
    """Reject a value, presence tree, or context that belongs to a different binding."""


class WireValidationError(CodecError):
    """Report wire values that violate their directional schema, retaining every issue."""

    def __init__(self, issues: tuple[WireIssue, ...]) -> None:
        """Keep the ordered issues without embedding rejected values in the message."""
        self.issues = issues
        first = issues[0] if issues else None
        super().__init__(
            f"{first.message} at {first.instance_pointer or '/'}" if first else "The wire value is invalid"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class NativeIssue:
    """Describe one native rejection by backend error type and location, never by the rejected value."""

    code: str
    pointer: str
    native_path: tuple[str | int, ...]


class NativeValidationError(CodecError):
    """Report a native constructor, validator, or converter rejecting a schema-valid value."""

    def __init__(self, issues: tuple[NativeIssue, ...]) -> None:
        """Keep the ordered native issues, naming only the first in the message."""
        self.issues = issues
        first = issues[0] if issues else None
        super().__init__(
            f"The native type rejected the value ({first.code}) at {first.pointer or '/'}"
            if first
            else "The native type rejected the value"
        )


class ModelProjectionError(CodecError):
    """Report a value that cannot be projected into or out of the bound native type."""


class ParameterEncodingError(CodecError):
    """Report a parameter value that its declared style cannot represent reversibly."""


class CodecResourceLimitError(CodecError):
    """Report a subject or cumulative input exceeding a codec resource limit."""
