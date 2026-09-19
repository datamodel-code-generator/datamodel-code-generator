"""Verify ordinarily emitted artifact bytes against an accepted value contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from datamodel_code_generator._generation_contract import BindingCaptureError, BindingDiagnostic
from datamodel_code_generator.model.binding import (
    index_builtin_field_declarations,
    same_artifact_model_facts,
    same_emitted_field_facts,
    split_artifact_models,
)

if TYPE_CHECKING:
    from datamodel_code_generator._generation_contract import GeneratedTypeContractBatch
    from datamodel_code_generator.parser.openapi_contract_fields import FinalArtifactBinding


@dataclass(frozen=True, slots=True)
class ModelArtifact:
    """Retain logical relative paths and bytes from the ordinary emission owner."""

    path: tuple[str, ...]
    content: bytes
    encoding: str = "utf-8"


def validate_artifact_bindings(
    batch: GeneratedTypeContractBatch,
    bindings: tuple[FinalArtifactBinding, ...],
    artifacts: tuple[ModelArtifact, ...],
) -> tuple[BindingDiagnostic, ...]:
    """Check final declarations once, without another renderer, import, or formatter call."""
    files = {artifact.path: artifact for artifact in artifacts}
    if len(files) != len(artifacts):
        return (BindingDiagnostic("BND_ARTIFACT_AMBIGUOUS"),)
    names = {symbol.id: symbol.name for symbol in batch.symbols}
    diagnostics: list[BindingDiagnostic] = []
    for binding in bindings:
        expected_models = split_artifact_models(binding.index)
        for path in (binding.address.relative_path, *binding.address.secondary_definitions):
            if (artifact := files.get(path)) is None:
                diagnostics.append(BindingDiagnostic("BND_SYMBOL_NOT_EMITTED", details=(("artifact", "/".join(path)),)))
                continue
            try:
                actual = index_builtin_field_declarations(
                    artifact.content.decode(artifact.encoding),
                    expected=binding.expected,
                    imports=binding.imports,
                    collect_errors=True,
                )
            except (BindingCaptureError, UnicodeError):
                diagnostics.append(
                    BindingDiagnostic("BND_TYPE_EXPRESSION_UNSUPPORTED", details=(("artifact", "/".join(path)),))
                )
                continue
            definitions = {definition.name: definition.kind for definition in actual.definitions}
            diagnostics.extend(
                BindingDiagnostic("BND_SYMBOL_NOT_EMITTED", details=(("symbol", symbol), ("artifact", "/".join(path))))
                for symbol in binding.models
                if definitions.get(names[symbol]) not in {"class", "type_alias", "assignment"}
            )
            actual_fields = {(field.expected.consumer, field.expected.slot): field for field in actual.fields}
            actual_models = split_artifact_models(actual)
            invalid = set(actual.invalid_models)
            for field in binding.index.fields:
                other = actual_fields.get((field.expected.consumer, field.expected.slot))
                if (
                    other is None
                    or field.expected != other.expected
                    or not same_emitted_field_facts(field.facts, other.facts)
                ):
                    invalid.add(field.expected.model_name)
            for symbol in binding.models:
                name = names[symbol]
                if (
                    name in invalid
                    or name not in actual_models
                    or name not in expected_models
                    or not same_artifact_model_facts(expected_models[name], actual_models[name])
                ):
                    diagnostics.append(
                        BindingDiagnostic(
                            "BND_TYPE_EXPRESSION_UNSUPPORTED",
                            details=(("symbol", symbol), ("artifact", "/".join(path))),
                        )
                    )
    return tuple(diagnostics)
