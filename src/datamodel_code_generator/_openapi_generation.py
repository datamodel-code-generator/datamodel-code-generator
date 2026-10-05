"""Attempt-owned source borrowing for optional OpenAPI contract consumers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, cast

from datamodel_code_generator._generation_contract import AttemptId, BindingCaptureError


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
        self._raw: list[dict[str, YamlValue]] = []

    def register(self, document: dict[str, YamlValue]) -> None:
        """Borrow a loaded document; its registration order is its document identity."""
        self._raw.append(document)

    def borrow(self, location: SourceLocation) -> YamlValue:
        """Read an original plain JSON pointer from an already borrowed document."""
        if location.pointer and not location.pointer.startswith("/"):
            msg = "Source locations require a plain JSON pointer"
            raise BindingCaptureError(msg)
        try:
            value: YamlValue = self._raw[location.document]
            for token in location.pointer.split("/")[1:]:
                key = token.replace("~1", "/").replace("~0", "~")
                index = key == "0" or (key.isascii() and key.isdecimal() and not key.startswith("0"))
                value = (
                    value[int(key)]
                    if isinstance(value, list) and index
                    else borrow_source_member(cast("dict[str, YamlValue]", value), key)
                )
        except (KeyError, IndexError, TypeError):
            msg = "Source location does not identify an observed node"
            raise BindingCaptureError(msg) from None
        return value

    def close(self) -> None:
        """Release all borrowed mappings; repeated cleanup remains harmless."""
        self._raw.clear()


class TargetGenerationSession:
    """Bind each target parser attempt while the existing driver alone selects acceptance."""

    _supports_api_scope: ClassVar[bool] = True

    def __init__(self, *, output: Path, model_package: str, root_selector_document: str) -> None:
        """Keep explicit logical addresses without loading an input or backend."""
        self._output = output
        self._model_package = model_package
        self._root_selector_document = root_selector_document
        self._next_attempt = 0
        self._candidate: BoundAttempt | None = None

    @property
    def parser_factory(self) -> OpenAPIParserFactory:
        """Advertise API support on the actual callable selected once by the driver."""
        return self

    def __call__(self, *, source: _ParserSource, config: OpenAPIParserConfig) -> OpenAPIParser:
        """Construct one target parser attempt."""
        from datamodel_code_generator._target_binding import TargetApiOpenAPIParser  # noqa: PLC0415

        self._next_attempt += 1
        parser = TargetApiOpenAPIParser(source, config=config)
        parser.attempt = AttemptId(self._next_attempt)
        return parser

    def freeze_attempt(self, parser: OpenAPIParser, results: str | dict[tuple[str, ...], Result]) -> AttemptId:
        """Bind an attempt before its parser is disposed, replacing the previous candidate on success."""
        from datamodel_code_generator._target_binding import MetadataCycleError, bind_operations  # noqa: PLC0415

        target: TargetApiOpenAPIParser = cast("TargetApiOpenAPIParser", parser)
        try:
            self._candidate = bind_operations(
                target,
                results,
                output=self._output,
                model_package=self._model_package,
                root_selector_document=self._root_selector_document,
            )
        except MetadataCycleError as error:
            if error.document in target._api_roots:  # pyright: ignore[reportPrivateUsage] # noqa: SLF001
                error.document = self._root_selector_document
            raise
        except Exception as cause:
            msg = "Binding capture freeze failed"
            raise BindingCaptureError(msg) from cause
        finally:
            target.release_records()
        return target.attempt

    def accept_attempt(self, attempt_id: AttemptId) -> None:
        """Accept the driver's chosen attempt, which is always the last one bound."""

    @staticmethod
    def discard_attempt(parser: OpenAPIParser) -> None:
        """Release a rejected attempt's records after the driver's original parser disposal."""
        target: TargetApiOpenAPIParser = cast("TargetApiOpenAPIParser", parser)
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
        for _, document in bound.documents:
            lease.register(document)
        self.close()
        return ModelGenerationProduct(artifacts, allow_empty_api, batch, lease)

    def close(self) -> None:
        """Release the bound candidate."""
        self._candidate = None


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
    from datamodel_code_generator._generation_contract import OpenAPIParserFactory
    from datamodel_code_generator._source import YamlValue
    from datamodel_code_generator._target_binding import BoundAttempt, TargetApiOpenAPIParser
    from datamodel_code_generator._target_contract import GeneratedTypeContractBatch, ModelArtifact, SourceLocation
    from datamodel_code_generator.config import OpenAPIParserConfig
    from datamodel_code_generator.parser.base import Result
    from datamodel_code_generator.parser.openapi import OpenAPIParser
