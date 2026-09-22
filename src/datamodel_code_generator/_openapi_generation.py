"""Attempt-owned source borrowing for optional OpenAPI contract consumers."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, ClassVar, cast

from datamodel_code_generator._generation_contract import (
    AttemptId,
    BindingCaptureError,
    SourceDocument,
    SourceDocumentId,
    SourceLocation,
    clear_capture_tracebacks,
)


def borrow_source_member(value: dict[str, YamlValue], key: str) -> YamlValue:
    """Read textual source keys, including integer YAML statuses, without copying the mapping."""
    if key.isascii() and key.isdecimal() and str(number := int(key)) == key:
        numeric = cast("dict[str | int, YamlValue]", value)
        if number in numeric:
            if key in value:
                msg = "Source pointer is ambiguous between string and integer keys"
                raise BindingCaptureError(msg)
            return numeric[number]
    return value[key]


class SourceLease:
    """Borrow only documents obtained by the model engine, until planning finishes.

    Consumers may read borrowed nodes during planning but must project the values
    they need before closing. This object never loads, validates, copies, or resolves
    a document, and it is deliberately excluded from immutable contract batches.
    """

    def __init__(self) -> None:
        """Create an empty lease without retaining an input or parser."""
        self._documents: list[SourceDocument] = []
        self._raw: list[dict[str, YamlValue]] = []
        self._by_uri: dict[str, SourceDocumentId] = {}
        self._closed = False

    def _check_open(self) -> None:
        if self._closed:
            msg = "Source lease is closed"
            raise RuntimeError(msg)

    def register(self, uri: str, document: dict[str, YamlValue]) -> SourceDocumentId:
        """Borrow an actual loader result without conflating URI or node identity.

        Deferred pointer processing can reload the same source into another mapping. Document identity is its loader
        key; producer frames retain the actual raw node used by each validation call separately.
        """
        self._check_open()
        if (existing := self._by_uri.get(uri)) is not None:
            return existing
        identity = SourceDocumentId(len(self._documents))
        self._documents.append(SourceDocument(identity, uri))
        self._raw.append(document)
        self._by_uri[uri] = identity
        return identity

    def documents(self) -> tuple[SourceDocument, ...]:
        """Return document values in actual observation order without raw mappings."""
        self._check_open()
        return tuple(self._documents)

    def document_id(self, uri: str) -> SourceDocumentId | None:
        """Look up an already borrowed document without resolving alternate URIs."""
        self._check_open()
        return self._by_uri.get(uri)

    def borrow(self, location: SourceLocation) -> YamlValue:
        """Read an original plain JSON pointer from an already borrowed document."""
        self._check_open()
        msg = "Source location does not identify an observed node"
        if not 0 <= location.document < len(self._raw):
            raise BindingCaptureError(msg)
        value: YamlValue = self._raw[location.document]
        if not location.pointer:
            return value
        if not location.pointer.startswith("/"):
            msg = "Source locations require a plain JSON pointer"
            raise BindingCaptureError(msg)
        for token in location.pointer[1:].split("/"):
            key = token.replace("~1", "/").replace("~0", "~")
            match value:
                case dict():
                    pass
                case list():
                    if key != "0" and not (key.isascii() and key.isdecimal() and not key.startswith("0")):
                        raise BindingCaptureError(msg)
                case _:
                    raise BindingCaptureError(msg)
            try:
                value = borrow_source_member(value, key) if isinstance(value, dict) else value[int(key)]
            except (KeyError, IndexError, ValueError):
                raise BindingCaptureError(msg) from None
        return value

    def close(self) -> None:
        """Release all borrowed mappings; repeated cleanup remains harmless."""
        self._closed = True
        self._raw.clear()
        self._documents.clear()
        self._by_uri.clear()


@dataclass(slots=True)
class _AttemptResources:
    ledger: BindingLedger
    lease: SourceLease


class OpenAPIGenerationSession:
    """Own real attempt resources while the existing driver alone selects acceptance."""

    _supports_api_scope: ClassVar[bool] = True

    def __init__(self, *, output: Path, model_package: str, root_selector_document: str) -> None:
        """Keep explicit logical addresses without loading an input or backend."""
        self._output = output
        self._model_package = model_package
        self._root_selector_document = root_selector_document
        self._next_attempt = 0
        self._attempts: dict[AttemptId, _AttemptResources] = {}
        self._candidate: openapi_contract_freeze.FrozenGenerationAttempt | None = None
        self._accepted: AttemptId | None = None
        self._failure: BindingCaptureError | None = None
        self._closed = False
        self._taken = False

    @property
    def parser_factory(self) -> OpenAPIParserFactory:
        """Advertise API support on the actual callable selected once by the driver."""
        return self

    def __call__(self, *, source: _ParserSource, config: OpenAPIParserConfig) -> OpenAPIParser:
        """Construct one actual scope-selected parser with resource ownership already established."""
        from datamodel_code_generator import OpenAPIScope  # noqa: PLC0415
        from datamodel_code_generator.parser.openapi_contract import (  # noqa: PLC0415
            ContractApiOpenAPIParser,
            ContractOpenAPIParser,
        )
        from datamodel_code_generator.parser.openapi_contract_store import BindingLedger  # noqa: PLC0415

        self._check_open()
        self._next_attempt += 1
        attempt = AttemptId(self._next_attempt)
        resources = _AttemptResources(BindingLedger(attempt), SourceLease())
        self._attempts[attempt] = resources
        parser_type = (
            ContractApiOpenAPIParser if OpenAPIScope.Api in (config.openapi_scopes or ()) else ContractOpenAPIParser
        )
        try:
            return parser_type(
                source, attempt_id=attempt, binding_ledger=resources.ledger, source_lease=resources.lease, config=config
            )
        except BaseException:
            self._remember_failure(resources.ledger.failure)
            with suppress(BaseException):
                self._release(attempt)
            raise

    def _check_open(self) -> None:
        if self._closed:
            msg = "Generation capture session is closed"
            raise RuntimeError(msg)

    def _remember_failure(self, failure: BindingCaptureError | None) -> None:
        if self._failure is None and failure is not None:
            self._failure = failure

    def _release(self, attempt: AttemptId) -> None:
        if (resources := self._attempts.pop(attempt, None)) is not None:
            self._remember_failure(resources.ledger.failure)
            try:
                resources.ledger.close()
            except BaseException:
                with suppress(BaseException):
                    resources.lease.close()
                raise
            resources.lease.close()

    def freeze_attempt(self, parser: OpenAPIParser, results: str | dict[tuple[str, ...], Result]) -> AttemptId:
        """Replace the provisional batch only after complete value projection succeeds."""
        from datamodel_code_generator.parser.openapi_contract_freeze import freeze_generation_attempt  # noqa: PLC0415

        self._check_open()
        try:
            owned = self._owned_parser(parser)
            attempt = owned.binding_ledger.attempt_id
            self.raise_if_failed()
            frozen = freeze_generation_attempt(
                owned,
                results,
                output=self._output,
                model_package=self._model_package,
                root_selector_document=self._root_selector_document,
            )
        except BindingCaptureError as error:
            self._remember_failure(error)
            raise
        except Exception as cause:
            failure = BindingCaptureError("Binding capture freeze failed")
            self._remember_failure(failure)
            raise failure from cause
        if self._candidate is not None and self._candidate.batch.attempt != attempt:
            self._release(self._candidate.batch.attempt)
        self._candidate = frozen
        return attempt

    def _owned_parser(self, parser: OpenAPIParser) -> openapi_contract.BindingCaptureMixin:
        from datamodel_code_generator.parser.openapi_contract import BindingCaptureMixin  # noqa: PLC0415

        if not isinstance(parser, BindingCaptureMixin):
            msg = "BND_ATTEMPT_MISMATCH: parser is not owned by this capture session"
            raise BindingCaptureError(msg)
        resources = self._attempts.get(parser.binding_ledger.attempt_id)
        if (
            resources is None
            or resources.ledger is not parser.binding_ledger
            or resources.lease is not parser.source_lease
        ):
            msg = "BND_ATTEMPT_MISMATCH: parser resources belong to another session"
            raise BindingCaptureError(msg)
        return parser

    def accept_attempt(self, attempt_id: AttemptId) -> None:
        """Accept exactly the driver's chosen frozen attempt, with no content-based guess."""
        self._check_open()
        self.raise_if_failed()
        if self._candidate is None or self._candidate.batch.attempt != attempt_id or attempt_id not in self._attempts:
            failure = BindingCaptureError("BND_ATTEMPT_MISMATCH: selected attempt has no frozen candidate")
            self._remember_failure(failure)
            raise failure
        self._accepted = attempt_id
        for attempt in tuple(self._attempts):
            if attempt != attempt_id:
                self._release(attempt)

    def discard_attempt(self, parser: OpenAPIParser) -> None:
        """Release a rejected attempt after the driver's original parser disposal."""
        from datamodel_code_generator.parser.openapi_contract import BindingCaptureMixin  # noqa: PLC0415

        if isinstance(parser, BindingCaptureMixin):
            attempt = parser.binding_ledger.attempt_id
            if (resources := self._attempts.get(attempt)) is not None and resources.ledger is parser.binding_ledger:
                if self._candidate is not None and self._candidate.batch.attempt == attempt:
                    self._candidate = None
                self._release(attempt)

    def raise_if_failed(self) -> None:
        """Expose the first latched observer failure even after ordinary repair suppression."""
        for resources in self._attempts.values():
            self._remember_failure(resources.ledger.failure)
        if self._failure is not None:
            raise self._failure

    def take_accepted_batch(self) -> GeneratedTypeContractBatch:
        """Transfer graph-free values once, leaving the lease owned until planning closes."""
        self._check_open()
        self.raise_if_failed()
        if self._taken or self._candidate is None or self._candidate.batch.attempt != self._accepted:
            msg = "No accepted contract batch is available for transfer"
            raise RuntimeError(msg)
        self._taken = True
        return self._candidate.batch

    @property
    def source_lease(self) -> SourceLease:
        """Borrow accepted sources during planning, independently from immutable values."""
        self._check_open()
        if self._accepted is None or (resources := self._attempts.get(self._accepted)) is None:
            msg = "No accepted source lease is available"
            raise RuntimeError(msg)
        return resources.lease

    def take_product(self, artifacts: tuple[ModelArtifact, ...], *, allow_empty_api: bool) -> ModelGenerationProduct:
        """Transfer accepted ownership after ordinary emission and static validation."""
        from datamodel_code_generator._openapi_artifacts import validate_artifact_bindings  # noqa: PLC0415

        candidate = self._candidate
        if candidate is None:
            msg = "No accepted artifact evidence is available"
            raise BindingCaptureError(msg)
        try:
            batch = self.take_accepted_batch()
            diagnostics = validate_artifact_bindings(batch, candidate.artifacts, artifacts)
            product = ModelGenerationProduct(
                artifacts,
                allow_empty_api,
                replace(batch, diagnostics=(*batch.diagnostics, *diagnostics)) if diagnostics else batch,
                self.source_lease,
            )
            self._attempts.pop(batch.attempt)
        except BaseException:
            with suppress(BaseException):
                self.close()
            raise
        self.close()
        return product

    def close(self) -> None:
        """Release all remaining attempts, including accepted sources and failed freezes."""
        self._closed = True
        self._candidate = None
        self._accepted = None
        failure: BaseException | None = None
        for attempt in tuple(self._attempts):
            try:
                self._release(attempt)
            except BaseException as error:  # ruff: ignore[blind-except, try-except-in-loop] # lgtm [py/catch-base-exception] -- Close every owner, then propagate the first release failure below.
                if failure is None:
                    failure = error
        clear_capture_tracebacks(self._failure)
        if failure is not None:
            raise failure


@dataclass(slots=True)
class ModelGenerationProduct:
    """Own completed ordinary artifacts and an accepted source lease during planning."""

    artifacts: tuple[ModelArtifact, ...]
    allow_empty_api: bool
    batch: GeneratedTypeContractBatch
    source_lease: SourceLease

    def close(self) -> None:
        """Release borrowed source mappings without invalidating frozen values or bytes."""
        self.source_lease.close()


if TYPE_CHECKING:
    from pathlib import Path

    from datamodel_code_generator import _ParserSource  # pyright: ignore[reportPrivateUsage]
    from datamodel_code_generator._generation_contract import GeneratedTypeContractBatch, OpenAPIParserFactory
    from datamodel_code_generator._openapi_artifacts import ModelArtifact
    from datamodel_code_generator._source import YamlValue
    from datamodel_code_generator.config import OpenAPIParserConfig
    from datamodel_code_generator.parser import openapi_contract, openapi_contract_freeze
    from datamodel_code_generator.parser.base import Result
    from datamodel_code_generator.parser.openapi import OpenAPIParser
    from datamodel_code_generator.parser.openapi_contract_store import BindingLedger
