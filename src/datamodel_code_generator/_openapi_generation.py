"""Attempt-owned source documents, whose pointers target settings name, for optional OpenAPI contract consumers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, cast

from datamodel_code_generator._document_reader import MISSING, descend
from datamodel_code_generator._generation_contract import AttemptId
from datamodel_code_generator._json_pointer import pointer_tokens
from datamodel_code_generator.enums import InputFileType


class SourceLease:
    """Hold the documents the model engine obtained until planning finishes, to check the pointers settings name.

    Consumers learn whether a pointer names a node, never the node: what a target reads of a schema comes from the
    contract batch. The one exception is a document a target serves as documentation, such as the source document a
    generated server serves, which it copies from `documents`, resolving references through the URIs the parser's
    schema resources name there (`declared`). This object never loads, validates, copies, or resolves a document, and
    it is deliberately excluded from immutable contract batches.
    """

    def __init__(self) -> None:
        """Create an empty lease without retaining an input or parser."""
        self._raw: list[dict[str, YamlValue]] = []
        self._locations: list[str] = []
        self._declared: dict[str, tuple[int, str]] = {}
        self._roots: tuple[str, ...] = ()

    def register(self, document: dict[str, YamlValue], location: str = "") -> None:
        """Borrow a loaded document and where it was read from; its registration order is its document identity.

        The contract batch's documents come first; the others the models' references loaded follow them.
        """
        self._raw.append(document)
        self._locations.append(location)

    def exists(self, location: SourceLocation) -> bool:
        """Return whether a plain JSON pointer names a node of a held document, an integer YAML key included."""
        tokens = pointer_tokens(location.pointer)
        return descend(self._raw[location.document], tokens, "canonical", MISSING) is not MISSING

    def documents(self) -> tuple[tuple[str, dict[str, YamlValue]], ...]:
        """Return every held document and where it was read from, for a target that copies them into documentation.

        Never modify them.
        """
        return tuple(zip(self._locations, self._raw, strict=True))

    def declare(self, declared: Mapping[str, tuple[int, str]], roots: tuple[str, ...]) -> None:
        """Keep what the parser's schema resources name in the held documents, and the base it gives each root."""
        self._declared = dict(declared)
        self._roots = roots

    def declared(self) -> Mapping[str, tuple[int, str]]:
        """Return each URI the parser's schema resources name in a held document, to its identity and a pointer."""
        return self._declared

    def roots(self) -> tuple[str, ...]:
        """Return the base URI the parser gives the root of each held document, empty for one it never prepared."""
        return self._roots

    def close(self) -> None:
        """Release all borrowed mappings; repeated cleanup remains harmless."""
        self._raw.clear()
        self._locations.clear()
        self._declared = {}
        self._roots = ()


class TargetGenerationSession:
    """Bind each target parser attempt while the existing driver alone selects acceptance."""

    _supports_api_scope: ClassVar[bool] = True

    def __init__(
        self, *, output: Path, model_package: str, root_selector_document: str, serves_source: bool = False
    ) -> None:
        """Keep explicit logical addresses without loading an input or backend.

        A session whose target serves the source document refuses the local files that document would publish.
        """
        self._output = output
        self._model_package = model_package
        self._root_selector_document = root_selector_document
        self._serves_source = serves_source
        self._next_attempt = 0
        self._candidate: BoundAttempt | None = None
        self._model_imports: frozenset[str] = frozenset()

    def parser_factory(self, input_file_type: InputFileType) -> TargetParserFactory | None:
        """Advertise API support on the actual callable the driver selects once, for the input kinds targets read.

        The session itself builds OpenAPI attempts, and `asyncapi_attempt` AsyncAPI ones.
        """
        factories: dict[InputFileType, TargetParserFactory] = {
            InputFileType.OpenAPI: self,
            InputFileType.AsyncAPI: self.asyncapi_attempt,
        }
        return factories.get(input_file_type)

    def __call__(self, *, source: _ParserSource, config: OpenAPIParserConfig | AsyncAPIParserConfig) -> OpenAPIParser:
        """Construct one target parser attempt of an OpenAPI input."""
        from datamodel_code_generator._target_binding import TargetApiOpenAPIParser  # noqa: PLC0415

        return self._numbered(
            TargetApiOpenAPIParser(
                source, config=cast("OpenAPIParserConfig", config), serves_source=self._serves_source
            )
        )

    def asyncapi_attempt(
        self, *, source: _ParserSource, config: OpenAPIParserConfig | AsyncAPIParserConfig
    ) -> OpenAPIParser:
        """Construct one target parser attempt of an AsyncAPI input."""
        from datamodel_code_generator._target_binding import TargetAsyncAPIParser  # noqa: PLC0415

        return self._numbered(TargetAsyncAPIParser(source, config=cast("AsyncAPIParserConfig", config)))

    def _numbered(self, parser: TargetParser) -> TargetParser:
        """Give a new attempt the next attempt identity."""
        self._next_attempt += 1
        parser.attempt = AttemptId(self._next_attempt)
        return parser

    def freeze_attempt(self, parser: OpenAPIParser, results: str | dict[tuple[str, ...], Result]) -> AttemptId:
        """Bind an attempt before its parser is disposed, replacing the previous candidate on success."""
        from datamodel_code_generator._target_binding import MetadataCycleError, bind_operations  # noqa: PLC0415

        target: TargetParser = cast("TargetParser", parser)
        try:
            self._candidate = bind_operations(
                target,
                results,
                output=self._output,
                model_package=self._model_package,
                root_selector_document=self._root_selector_document,
            )
            self._model_imports = frozenset(target.model_imports)
        except MetadataCycleError as error:
            if error.document in target._api_roots:  # noqa: SLF001
                error.document = self._root_selector_document
            raise
        finally:
            target.release_records()
        return target.attempt

    def accept_attempt(self, attempt_id: AttemptId) -> None:
        """Accept the driver's chosen attempt, which is always the last one bound."""

    @staticmethod
    def discard_attempt(parser: OpenAPIParser) -> None:
        """Release a rejected attempt's records after the driver's original parser disposal."""
        target: TargetParser = cast("TargetParser", parser)
        target.release_records()

    def raise_if_failed(self) -> None:
        """Binding failures propagate where they occur, so no repair can hide one."""

    def take_accepted_batch(self) -> GeneratedTypeContractBatch:
        """Transfer the accepted values."""
        return cast("BoundAttempt", self._candidate).batch

    def take_product(self, artifacts: tuple[ModelArtifact, ...], *, allow_empty_api: bool) -> ModelGenerationProduct:
        """Transfer the accepted batch and its source documents with the emitted artifacts."""
        batch = self.take_accepted_batch()
        bound = cast("BoundAttempt", self._candidate)
        lease = SourceLease()
        for location, document in bound.documents:
            lease.register(document, location)
        lease.declare(bound.declared, bound.roots)
        self.close()
        return ModelGenerationProduct(artifacts, allow_empty_api, batch, lease, self._model_imports)

    def close(self) -> None:
        """Release the bound candidate."""
        self._candidate = None


@dataclass(slots=True)
class ModelGenerationProduct:
    """Own completed ordinary artifacts, the names their imports name, and an accepted source lease during planning."""

    artifacts: tuple[ModelArtifact, ...]
    allow_empty_api: bool
    batch: GeneratedTypeContractBatch
    source_lease: SourceLease
    model_imports: frozenset[str]

    def close(self) -> None:
        """Release borrowed source mappings without invalidating frozen values or bytes."""
        self.source_lease.close()


if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from datamodel_code_generator._generation_contract import TargetParserFactory
    from datamodel_code_generator._shared_types import ParserSource as _ParserSource
    from datamodel_code_generator._source import YamlValue
    from datamodel_code_generator._target_binding import BoundAttempt, TargetParser
    from datamodel_code_generator._target_contract import (
        GeneratedTypeContractBatch,
        ModelArtifact,
        SourceLocation,
    )
    from datamodel_code_generator.config import AsyncAPIParserConfig, OpenAPIParserConfig
    from datamodel_code_generator.parser.base import Result
    from datamodel_code_generator.parser.openapi import OpenAPIParser
