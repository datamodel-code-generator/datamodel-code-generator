"""Private contracts for optional generation capture, without runtime graph imports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NewType, Protocol, TypeAlias, TypeVar

if TYPE_CHECKING:
    from datamodel_code_generator import _ParserSource  # pyright: ignore[reportPrivateUsage]
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
        """The same factory for initial and compatibility-retry construction."""
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


SourceDocumentId = NewType("SourceDocumentId", int)
GraphObjectId = NewType("GraphObjectId", int)
SymbolId = NewType("SymbolId", int)
Direction: TypeAlias = Literal["request", "response", "neutral"]
TypeUseRole: TypeAlias = Literal[
    "schema",
    "parameter",
    "request_body",
    "response_body",
    "response_header",
    "request_encoding_header",
    "response_encoding_header",
]


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """Identify a document read by the actual loader within one attempt."""

    id: SourceDocumentId
    uri: str


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """Keep an original declaration or use pointer separate from model names."""

    document: SourceDocumentId
    pointer: str
    role: Literal["declaration", "use", "schema"]


@dataclass(frozen=True, slots=True)
class DeclarationId:
    """Identify the actual declaration, including its declaring document."""

    location: SourceLocation


@dataclass(frozen=True, slots=True)
class OperationId:
    """Identify an operation by its root use site, retaining callback ancestry."""

    use_site: SourceLocation
    kind: Literal["path", "webhook", "callback"]
    parent: OperationId | None = None
    callback_expression: SourceLocation | None = None


@dataclass(frozen=True, slots=True)
class TypeUseId:
    """Distinguish source occurrences even when their final Python type is shared."""

    owner: OperationId | SourceLocation
    role: TypeUseRole
    use_site: SourceLocation
    schema_site: SourceLocation
    declaration: DeclarationId
    direction: Direction
    projection: Literal["value", "item_stream_array"] = "value"
    location: str | None = None
    name: str | None = None
    status: str | None = None
    media: str | None = None


@dataclass(frozen=True, slots=True)
class ReferenceResolution:
    """Record one completed resolver call without triggering another resolution."""

    sequence: int
    input: str | tuple[str, ...]
    output: str
    parent: int | None
    operation: Literal["resolve_ref", "add_ref"]
    reference: GraphObjectId | None = None
    already_resolved: bool = False


@dataclass(frozen=True, slots=True)
class NoneDefaultProvenance:
    """Keep accepted constructor defaults separate from wire-null provenance."""

    emitted_default: Literal["absent", "none", "value", "factory", "missing", "opaque"]
    origin: Literal[
        "synthesized_optional_fallback",
        "schema_default",
        "explicit_model_default",
        "explicit_nullable",
        "runtime_or_opaque",
        "not_applicable",
    ]
    annotation_null_origin: Literal[
        "optional_fallback",
        "schema",
        "model_configuration",
        "preexisting_type",
        "none",
        "opaque",
    ]
