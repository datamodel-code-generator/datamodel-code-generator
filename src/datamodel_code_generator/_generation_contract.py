"""Private contracts for optional generation capture, without runtime graph imports."""

from __future__ import annotations

from typing import TYPE_CHECKING, NewType, Protocol, TypeVar

if TYPE_CHECKING:
    from datamodel_code_generator import _ParserSource
    from datamodel_code_generator.config import OpenAPIParserConfig
    from datamodel_code_generator.parser.base import Result
    from datamodel_code_generator.parser.openapi import OpenAPIParser

AttemptId = NewType("AttemptId", int)
BatchT_co = TypeVar("BatchT_co", covariant=True)


class BindingCaptureError(RuntimeError):
    """An internal capture inconsistency that must never become repair success."""


class OpenAPIParserFactory(Protocol):
    """Construct a parser with capture state established before its base constructor."""

    def __call__(self, *, source: _ParserSource, config: OpenAPIParserConfig) -> OpenAPIParser:
        """Create one fresh attempt using the existing effective parser config."""
        ...


class GenerationCaptureSession(Protocol[BatchT_co]):
    """Own optional attempt state and retain only the driver's selected value batch.

    Factories allocate monotonically increasing attempt identities. Recording failures
    must latch the first fatal exception before raising, so repair suppression cannot
    turn them into success. Freezing replaces the provisional candidate only after
    successful projection and releases superseded candidate resources. It must not
    change the parser graph. The caller owns closing a successful session after taking
    its accepted values; the core closes the session on failure.
    """

    @property
    def parser_factory(self) -> OpenAPIParserFactory:
        """Return the same factory for initial and compatibility-retry construction."""
        ...

    def freeze_attempt(self, parser: OpenAPIParser, results: str | dict[tuple[str, ...], Result]) -> AttemptId:
        """Freeze a provisional value candidate before the parser is disposed."""
        ...

    def accept_attempt(self, attempt_id: AttemptId) -> None:
        """Accept exactly the attempt selected by the existing retry driver."""
        ...

    def discard_attempt(self, parser: OpenAPIParser) -> None:
        """Release failed or rejected attempt state after existing parser cleanup."""
        ...

    def raise_if_failed(self) -> None:
        """Re-raise the first fatal recording failure, including suppressed repairs."""
        ...

    def take_accepted_batch(self) -> BatchT_co:
        """Transfer the accepted immutable values without exposing a live graph."""
        ...

    def close(self) -> None:
        """Release all remaining owned capture resources, including failed attempts."""
        ...
