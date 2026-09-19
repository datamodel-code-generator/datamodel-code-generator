"""Exercise capture against real generation and independent source/output fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import DataModelType, OpenAPIScope, PythonVersionMin
from datamodel_code_generator._generation_contract import (
    AttemptId,
    BindingCaptureError,
    SourceDocumentId,
    SourceLocation,
)
from datamodel_code_generator.parser.openapi_contract import ContractApiOpenAPIParser, ContractOpenAPIParser
from tests.conftest import assert_output

if TYPE_CHECKING:
    from typing import Literal

    from datamodel_code_generator.model.base import DataModelFieldBase
    from datamodel_code_generator.parser.openapi_contract_store import RootCollapse
    from datamodel_code_generator.reference import Reference
    from datamodel_code_generator.types import DataType

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/binding"
EXPECTED = DATA / "expected/main/generation_platform/binding"


@pytest.mark.parametrize("api", [False, True])
def test_actual_reference_capture(api: bool) -> None:
    """Preserve model bytes while linking actual recursive resolver results."""
    parser_type = ContractApiOpenAPIParser if api else ContractOpenAPIParser
    parser = parser_type(
        SOURCE / "resolver.json",
        attempt_id=AttemptId(7),
        openapi_scopes=[OpenAPIScope.Api if api else OpenAPIScope.Schemas],
        formatters=[],
    )
    lease = parser.source_lease
    try:
        assert_output(parser.parse(), EXPECTED / "resolver.py")
        document = lease.documents()[0]
        root = lease.borrow(SourceLocation(document.id, "", "declaration"))
        reference = parser.model_resolver.references["resolver.json#/components/schemas/Pet"]
        events = parser.binding_resolver.resolutions
        by_sequence = {event.sequence: event for event in events}
        assert_output(
            json.dumps(
                {
                    "document_uris": [entry.uri for entry in lease.documents()],
                    "borrowed_root": root is parser.raw_obj,
                    "borrowed_response": lease.borrow(
                        SourceLocation(document.id, "/paths/~1pets/get/responses/404/description", "declaration")
                    )
                    == "missing",
                    "repeated_identity": lease.document_id(document.uri) == document.id,
                    "canonical_results": all(
                        event.output == "resolver.json#/components/schemas/Pet" for event in events
                    ),
                    "shared_reference": all(
                        event.reference == parser.binding_ledger.identity(reference)
                        for event in events
                        if event.operation == "add_ref"
                    ),
                    "nested_calls": any(
                        event.parent is not None and by_sequence[event.parent].operation == "add_ref"
                        for event in events
                    ),
                    "attempt": parser.binding_ledger.attempt_id,
                },
                indent=2,
            )
            + "\n",
            EXPECTED / "resolver-state.txt",
        )
    finally:
        parser.dispose()
        lease.close()
    with pytest.raises(BindingCaptureError, match="Binding ledger is closed"):
        parser.binding_ledger.identity(reference)
    with pytest.raises(RuntimeError, match="Source lease is closed"):
        lease.documents()
    lease.close()


@pytest.mark.parametrize(
    "case",
    [
        "external",
        "traversal",
        "media-32",
        "resources",
        "local-roots",
        "anchors",
        "typed-reuse",
        "method-case",
        "items-object",
        "items-primitive",
    ],
)
def test_api_capture_engine_calls(case: str) -> None:
    """Keep original resolver, loader, validation and getter counts unchanged."""
    import sys

    from datamodel_code_generator.parser.openapi_scope import ApiOpenAPIParser
    from tests.data.python.binding_engine_observer import BindingEngineObserver

    source = DATA / "generation_platform/api_scope" / f"{case}.json"
    ordinary = ApiOpenAPIParser(source, openapi_scopes=[OpenAPIScope.Api], formatters=[])
    captured = ContractApiOpenAPIParser(
        source, attempt_id=AttemptId(1), openapi_scopes=[OpenAPIScope.Api], formatters=[]
    )
    outputs = []
    counts = []
    previous = sys.getprofile()
    try:
        for parser in (ordinary, captured):
            # Match the existing process-wide field-import cache state for both runs.
            parser.data_model_field_type._field_imports_cache.clear()
            observer = BindingEngineObserver()
            sys.setprofile(observer.record)
            try:
                outputs.append(parser.parse())
            finally:
                sys.setprofile(previous)
            counts.append(observer.calls)
        assert_output(
            json.dumps(
                {"identical_model_bytes": outputs[0] == outputs[1], "identical_engine_calls": counts[0] == counts[1]},
                indent=2,
            )
            + "\n",
            EXPECTED / "engine-parity.txt",
        )
        if case in {"items-object", "items-primitive"}:
            assert_output(
                json.dumps(
                    [
                        {
                            "source": projection.source.pointer,
                            "kind": projection.kind,
                            "array_origins": [
                                origin.location.pointer for origin in captured.schema_origins.origins(projection.target)
                            ],
                            "reference_origins": [
                                origin.location.pointer
                                for origin in captured.schema_origins.origins(projection.item_reference)
                            ],
                        }
                        for projection in captured.schema_origins.projections
                    ],
                    indent=2,
                )
                + "\n",
                EXPECTED / "item-stream-origins.txt",
            )
    finally:
        ordinary.dispose()
        captured.dispose()
        captured.source_lease.close()


def test_legacy_effective_operation_capture() -> None:
    """Retain effective inherited values and every actual body/status/media return."""
    parser = ContractOpenAPIParser(
        SOURCE / "legacy-operations.json",
        attempt_id=AttemptId(1),
        openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Paths],
        allow_responses_without_content=True,
        formatters=[],
    )
    try:
        assert_output(parser.parse(), EXPECTED / "resolver.py")
        assert_output(
            json.dumps(
                {
                    "origins": [operation.origin_state for operation in parser.legacy_operations],
                    "original_paths": [
                        [candidate.declaration.tokens for candidate in operation.candidates]
                        for operation in parser.legacy_operations
                    ],
                    "request_media": [list(item.types) for item in parser.request_types],
                    "response_media": [
                        [(status, list(media)) for status, media in item.types.items()]
                        for item in parser.response_types
                    ],
                    "security_is_borrowed": parser.legacy_operations[0].effective["security"]
                    is parser.raw_obj["security"],
                    "types_have_owner": all(
                        item.operation is parser.legacy_operations[0] for item in parser.request_types
                    ),
                },
                indent=2,
            )
            + "\n",
            EXPECTED / "legacy-operations.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize("case", ["candidates", "ambiguous"])
def test_legacy_original_operation_candidates(case: str) -> None:
    """Reject ambiguous original occurrences without selecting by traversal order."""
    parser = ContractOpenAPIParser(
        SOURCE / f"legacy-{case}.yaml",
        attempt_id=AttemptId(1),
        openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Paths],
        openapi_include_paths=["/chosen"] if case == "candidates" else None,
        formatters=[],
    )
    try:
        parser.parse()
        assert_output(
            json.dumps([operation.origin_state for operation in parser.legacy_operations]) + "\n",
            EXPECTED / ("legacy-known.txt" if case == "candidates" else "legacy-ambiguous.txt"),
        )
    finally:
        parser.dispose()
        parser.source_lease.close()


def test_source_lease_pointer_boundaries() -> None:
    """Read array occurrences and reject bad pointer shapes after actual generation."""
    parser = ContractApiOpenAPIParser(
        SOURCE / "resolver.json", attempt_id=AttemptId(1), openapi_scopes=[OpenAPIScope.Api], formatters=[]
    )
    lease = parser.source_lease
    try:
        assert_output(parser.parse(), EXPECTED / "resolver.py")
        document = lease.documents()[0]
        assert_output(
            json.dumps(lease.borrow(SourceLocation(document.id, "/components/schemas/Pet/required/0", "schema")))
            + "\n",
            EXPECTED / "source-required.txt",
        )
        with pytest.raises(BindingCaptureError, match="plain JSON pointer"):
            lease.borrow(SourceLocation(document.id, "#/components", "schema"))
        with pytest.raises(BindingCaptureError, match="does not identify an observed node"):
            lease.borrow(SourceLocation(document.id, "/openapi/child", "schema"))
        with pytest.raises(BindingCaptureError, match="does not identify an observed node"):
            lease.borrow(SourceLocation(document.id, "/components/schemas/Pet/required/01", "schema"))
        frame = parser.binding_frames.schemas[0]
    finally:
        parser.dispose()
        lease.close()
    with pytest.raises(BindingCaptureError, match="Declaration capture is closed"):
        parser.binding_frames.append(frame)


@pytest.mark.parametrize("failure", ["capture", "unexpected"])
def test_capture_failure_latches_before_propagation(failure: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep first observer failure distinct from ordinary parser exceptions."""
    parser = ContractApiOpenAPIParser(
        SOURCE / "resolver.json", attempt_id=AttemptId(1), openapi_scopes=[OpenAPIScope.Api], formatters=[]
    )
    error_type = BindingCaptureError if failure == "capture" else ValueError

    def fail_registration(_uri: str, _raw: object) -> None:
        message = "injected capture failure"
        raise error_type(message)

    monkeypatch.setattr(parser.source_lease, "register", fail_registration)
    try:
        with pytest.raises(BindingCaptureError) as first:
            parser.parse()
        with pytest.raises(BindingCaptureError):
            parser.parse()
        assert_output(
            json.dumps({
                "latched_first": parser.binding_ledger.failure is first.value,
                "cause": type(first.value.__cause__).__name__,
            })
            + "\n",
            EXPECTED / f"failure-{failure}.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize(("document", "pointer"), json.loads((SOURCE / "invalid-source-locations.json").read_text()))
def test_source_lease_rejects_unobserved_locations(document: int, pointer: str) -> None:
    """Reject invalid source identities and lookups after real model generation."""
    parser = ContractApiOpenAPIParser(
        SOURCE / "resolver.json", attempt_id=AttemptId(1), openapi_scopes=[OpenAPIScope.Api], formatters=[]
    )
    try:
        assert_output(parser.parse(), EXPECTED / "resolver.py")
        with pytest.raises(BindingCaptureError, match=r"^Source location does not identify an observed node$"):
            parser.source_lease.borrow(SourceLocation(SourceDocumentId(document), pointer, "schema"))
    finally:
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize("backend", list(DataModelType))
@pytest.mark.parametrize("case", ["observation", "replacements", "inherited-overrides"])
def test_replacement_capture_engine_parity(backend: DataModelType, case: str) -> None:
    """Preserve bytes and original engine calls across real copies and replacements."""
    import sys

    from datamodel_code_generator.enums import CollapseRootModelsNameStrategy, ReadOnlyWriteOnlyModelType
    from datamodel_code_generator.model import get_data_model_types
    from datamodel_code_generator.parser.openapi_scope import ApiOpenAPIParser
    from tests.data.python.binding_engine_observer import BindingEngineObserver

    source = DATA / "generation_platform/observation.json" if case == "observation" else SOURCE / f"{case}.json"
    model_types = get_data_model_types(backend, target_python_version=PythonVersionMin)
    options = {
        "data_model_type": model_types.data_model,
        "data_model_root_type": model_types.root_model,
        "data_model_field_type": model_types.field_model,
        "data_type_manager_type": model_types.data_type_manager,
        "dump_resolve_reference_action": model_types.dump_resolve_reference_action,
        "openapi_scopes": [OpenAPIScope.Api],
        "collapse_root_models": True,
        "collapse_root_models_name_strategy": CollapseRootModelsNameStrategy.Child,
        "reuse_model": True,
        "collapse_reuse_models": True,
        "read_only_write_only_model_type": ReadOnlyWriteOnlyModelType.All,
        "formatters": [],
    }
    ordinary = ApiOpenAPIParser(source, **options)
    captured = ContractApiOpenAPIParser(source, attempt_id=AttemptId(1), **options)
    previous = sys.getprofile()
    outputs, counts = [], []
    try:
        for parser in (ordinary, captured):
            # Match the existing process-wide field-import cache state for both runs.
            parser.data_model_field_type._field_imports_cache.clear()
            observer = BindingEngineObserver()
            sys.setprofile(observer.record)
            try:
                outputs.append(parser.parse())
            finally:
                sys.setprofile(previous)
            counts.append(observer.calls)
        for output in outputs:
            assert_output(output, EXPECTED / f"{case}-{backend.name}.py")
        assert_output(
            json.dumps(
                {"identical_model_bytes": outputs[0] == outputs[1], "identical_engine_calls": counts[0] == counts[1]},
                indent=2,
            )
            + "\n",
            EXPECTED / "engine-parity.txt",
        )
        assert_output(
            json.dumps(
                {
                    "root_relations_completed": all(item.completed for item in captured.binding_ledger.collapses),
                    "root_recipes_preserved": all(
                        item.root_field.parent is item.reference.source for item in captured.binding_ledger.collapses
                    ),
                    "scoped_owners_preserved": all(
                        bool(item.models)
                        for item in captured.binding_ledger.replacements
                        if item.kind == "scoped_reference"
                    ),
                    "copy_targets_distinct": all(
                        item.target not in item.sources
                        for item in captured.binding_ledger.copies
                        if hasattr(item, "sources")
                    ),
                },
                indent=2,
            )
            + "\n",
            EXPECTED / "replacement-invariants.txt",
        )
    finally:
        ordinary.dispose()
        captured.dispose()
        captured.source_lease.close()


def test_capture_cross_module_type_copies() -> None:
    """Preserve existing modular bytes and the actual nonrecursive copy count."""
    from collections import Counter

    from datamodel_code_generator import ModuleSplitMode
    from tests.conftest import assert_parser_modules

    parser = ContractOpenAPIParser(
        DATA / "generation_platform/observation.json",
        attempt_id=AttemptId(1),
        formatters=[],
        openapi_scopes=["schemas", "paths"],
        read_only_write_only_model_type="all",
        collapse_root_models=True,
        reuse_model=True,
    )
    try:
        assert_parser_modules(parser.parse(module_split_mode=ModuleSplitMode.Single), EXPECTED.parent / "split")
        assert_output(
            json.dumps(dict(sorted(Counter(type(item).__name__ for item in parser.binding_ledger.copies).items())))
            + "\n",
            EXPECTED / "split-copies.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()


def test_capture_actual_deduplication_pair() -> None:
    """Use a same-name duplicate chosen by the engine, without computing a dedup key."""
    from datamodel_code_generator.parser.openapi import OpenAPIParser

    source = DATA / "openapi/duplicate_model_simplify.yaml"
    parser = ContractOpenAPIParser(source, attempt_id=AttemptId(1), formatters=[])
    ordinary = OpenAPIParser(source, formatters=[])
    try:
        ordinary_result = ordinary.parse()
        captured_result = parser.parse()
        assert_output(
            json.dumps(
                {
                    "identical_output": ordinary_result == captured_result,
                    "global_redirects": sum(item.kind == "reference" for item in parser.binding_ledger.replacements),
                    "distinct_identities": all(
                        item.original != item.replacement
                        for item in parser.binding_ledger.replacements
                        if item.kind == "reference"
                    ),
                },
                indent=2,
            )
            + "\n",
            EXPECTED / "deduplication.txt",
        )
    finally:
        ordinary.dispose()
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize("failure", ["missing_root", "structural_cycle", "after_enter"])
def test_capture_collapse_failures(failure: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject malformed recipes and retain incomplete context entry on real parse failures."""
    from datamodel_code_generator.parser import base
    from datamodel_code_generator.parser.openapi_contract_store import ContractGenerationStore

    parser = ContractApiOpenAPIParser(
        SOURCE / "replacements.json",
        attempt_id=AttemptId(1),
        openapi_scopes=[OpenAPIScope.Api],
        collapse_root_models=True,
        formatters=[],
    )
    if failure == "after_enter":

        def fail_import(*_args: object) -> None:
            message = "injected import failure"
            raise RuntimeError(message)

        monkeypatch.setattr(base, "_register_data_type_import", fail_import)
    else:
        original = ContractGenerationStore._begin_collapse

        def corrupt_root(
            self: ContractGenerationStore,
            data_type: DataType,
            replacement: DataType | Reference,
            owner: DataType | DataModelFieldBase,
            kind: Literal["field", "nested", "reference"],
        ) -> RootCollapse:
            model = next(model for model in parser.results if model.reference is data_type.reference)
            if failure == "missing_root":
                fields = model.fields
                model.fields = []
                try:
                    return original(self, data_type, replacement, owner, kind)
                finally:
                    model.fields = fields
            root_type = model.fields[0].data_type
            root_type.data_types.append(root_type)
            try:
                return original(self, data_type, replacement, owner, kind)
            finally:
                root_type.data_types.pop()

        monkeypatch.setattr(ContractGenerationStore, "_begin_collapse", corrupt_root)
    try:
        with pytest.raises(RuntimeError):
            parser.parse()
        assert_output(
            json.dumps(
                {
                    "latched_capture_failure": isinstance(parser.binding_ledger.failure, BindingCaptureError),
                    "collapse_completion": [item.completed for item in parser.binding_ledger.collapses],
                },
                indent=2,
            )
            + "\n",
            EXPECTED / ("collapse-after-enter.txt" if failure == "after_enter" else "collapse-invalid-recipe.txt"),
        )
    finally:
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize("mode", ["all", "constraints", "none"])
def test_capture_inherited_merge_sources(mode: str) -> None:
    """Retain both actual fields and merge mode for deferred inherited types."""
    from datamodel_code_generator.enums import AllOfMergeMode, ReadOnlyWriteOnlyModelType
    from datamodel_code_generator.parser.openapi import OpenAPIParser
    from datamodel_code_generator.parser.openapi_contract_store import FieldCopy

    source = DATA / "openapi/allof_partial_override_inherited_types.yaml"
    options = {
        "formatters": [],
        "read_only_write_only_model_type": ReadOnlyWriteOnlyModelType.All,
        "allof_merge_mode": AllOfMergeMode(mode),
    }
    ordinary = OpenAPIParser(source, **options)
    parser = ContractOpenAPIParser(source, attempt_id=AttemptId(1), **options)
    try:
        expected = ordinary.parse()
        actual = parser.parse()
        merges = [
            item for item in parser.binding_ledger.copies if isinstance(item, FieldCopy) and item.merge_mode is not None
        ]
        assert_output(
            json.dumps(
                {
                    "identical_output": expected == actual,
                    "actual_merges": bool(merges),
                    "preserved_pairs": all(len(item.sources) == 2 for item in merges),
                    "preserved_mode": all(item.merge_mode == AllOfMergeMode(mode) for item in merges),
                },
                indent=2,
            )
            + "\n",
            EXPECTED / "inherited-merge.txt",
        )
    finally:
        ordinary.dispose()
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize("api", [False, True])
def test_actual_validated_property_origins(api: bool) -> None:
    """Keep nested wire keys, booleans and tuple children at their original pointers."""
    parser_type = ContractApiOpenAPIParser if api else ContractOpenAPIParser
    parser = parser_type(
        SOURCE / "field-origins.json",
        attempt_id=AttemptId(1),
        openapi_scopes=[OpenAPIScope.Api if api else OpenAPIScope.Schemas],
        formatters=[],
    )
    try:
        assert_output(parser.parse(), EXPECTED / "field-origins.py")
        assert_output(
            json.dumps(
                [
                    {
                        "wire": entry.wire_name,
                        "required": entry.required_by_node,
                        "locations": [origin.location.pointer for origin in entry.origins],
                    }
                    for entry in parser.field_origins.values()
                ],
                indent=2,
            )
            + "\n",
            EXPECTED / "field-origins.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()


def test_api_alias_property_occurrences() -> None:
    """Select the actual declaration occurrence when the raw mapping is a YAML alias."""
    parser = ContractApiOpenAPIParser(
        SOURCE / "aliased-origins.yaml", attempt_id=AttemptId(1), openapi_scopes=[OpenAPIScope.Api], formatters=[]
    )
    try:
        assert_output(parser.parse(), EXPECTED / "aliased-origins.py")
        assert_output(
            json.dumps(
                [[origin.location.pointer for origin in entry.origins] for entry in parser.field_origins.values()],
                indent=2,
            )
            + "\n",
            EXPECTED / "aliased-origins.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()


def test_actual_default_producers() -> None:
    """Distinguish an explicit equal-valued override from a schema/default fallback."""
    from datamodel_code_generator.parser.openapi_scope import ApiOpenAPIParser

    options = {
        "openapi_scopes": [OpenAPIScope.Api],
        "default_value_overrides": json.loads((SOURCE / "default-overrides.json").read_text()),
        "formatters": [],
    }
    ordinary = ApiOpenAPIParser(SOURCE / "default-origins.json", **options)
    parser = ContractApiOpenAPIParser(SOURCE / "default-origins.json", attempt_id=AttemptId(1), **options)
    try:
        expected = ordinary.parse()
        actual = parser.parse()
        assert_output(
            json.dumps(
                {
                    "identical_output": actual == expected,
                    "defaults": [
                        {
                            "field": entry.field_name,
                            "original_has_default": entry.had_default,
                            "selected_has_default": entry.result[1],
                            "producer": entry.producer,
                        }
                        for entry in parser.binding_resolver.default_resolutions
                    ],
                    "constructions": [entry.original_name for entry in parser.field_constructions.values()],
                },
                indent=2,
            )
            + "\n",
            EXPECTED / "default-origins.txt",
        )
    finally:
        ordinary.dispose()
        parser.dispose()
        parser.source_lease.close()


def test_actual_conditional_property_origins() -> None:
    """Preserve branch pointers when ordinary validation materializes new properties."""
    import sys

    from datamodel_code_generator.parser.openapi_scope import ApiOpenAPIParser
    from tests.data.python.binding_engine_observer import BindingEngineObserver

    options = {"openapi_scopes": [OpenAPIScope.Api], "generate_schema_validators": True, "formatters": []}
    ordinary = ApiOpenAPIParser(SOURCE / "conditional-origins.json", **options)
    parser = ContractApiOpenAPIParser(SOURCE / "conditional-origins.json", attempt_id=AttemptId(1), **options)
    previous = sys.getprofile()
    outputs, counts = [], []
    try:
        for current in (ordinary, parser):
            current.data_model_field_type._field_imports_cache.clear()
            observer = BindingEngineObserver()
            sys.setprofile(observer.record)
            try:
                outputs.append(current.parse())
            finally:
                sys.setprofile(previous)
            counts.append(observer.calls)
        assert_output(
            json.dumps(
                {"identical_model_bytes": outputs[0] == outputs[1], "identical_engine_calls": counts[0] == counts[1]},
                indent=2,
            )
            + "\n",
            EXPECTED / "engine-parity.txt",
        )
        assert_output(
            json.dumps(
                {
                    entry.wire_name: [origin.location.pointer for origin in entry.origins]
                    for entry in parser.field_origins.values()
                },
                indent=2,
            )
            + "\n",
            EXPECTED / "conditional-origins.txt",
        )
    finally:
        ordinary.dispose()
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize("backend", list(DataModelType))
@pytest.mark.parametrize(
    "case",
    [
        "inherited-origins",
        "combined-origins",
        "allof-union-origins",
        "merged-origins",
        "root-origins",
        "synthetic-origins",
    ],
)
def test_materialized_property_origins(backend: DataModelType, case: str) -> None:
    """Preserve original keys through forward inheritance and filtered combined branches."""
    import sys

    from datamodel_code_generator.enums import ReadOnlyWriteOnlyModelType
    from datamodel_code_generator.model import get_data_model_types
    from datamodel_code_generator.parser.openapi_scope import ApiOpenAPIParser
    from tests.data.python.binding_engine_observer import BindingEngineObserver

    model_types = get_data_model_types(backend, target_python_version=PythonVersionMin)
    options = {
        "data_model_type": model_types.data_model,
        "data_model_root_type": model_types.root_model,
        "data_model_field_type": model_types.field_model,
        "data_type_manager_type": model_types.data_type_manager,
        "dump_resolve_reference_action": model_types.dump_resolve_reference_action,
        "openapi_scopes": [OpenAPIScope.Api],
        "use_title_as_name": True,
        "read_only_write_only_model_type": ReadOnlyWriteOnlyModelType.RequestResponse,
        "formatters": [],
    }
    ordinary = ApiOpenAPIParser(SOURCE / f"{case}.json", **options)
    captured = ContractApiOpenAPIParser(SOURCE / f"{case}.json", attempt_id=AttemptId(1), **options)
    previous = sys.getprofile()
    outputs, counts = [], []
    try:
        for parser in (ordinary, captured):
            parser.data_model_field_type._field_imports_cache.clear()
            observer = BindingEngineObserver()
            sys.setprofile(observer.record)
            try:
                outputs.append(parser.parse())
            finally:
                sys.setprofile(previous)
            counts.append(observer.calls)
        for output in outputs:
            assert_output(output, EXPECTED / f"{case}-{backend.name}.py")
        assert_output(
            json.dumps(
                {"identical_model_bytes": outputs[0] == outputs[1], "identical_engine_calls": counts[0] == counts[1]},
                indent=2,
            )
            + "\n",
            EXPECTED / "engine-parity.txt",
        )
        assert_output(
            json.dumps(
                {"all_fields_resolved": all(entry.origins for entry in captured.field_origins.values())}, indent=2
            )
            + "\n",
            EXPECTED / "resolved-origins.txt",
        )
        locations: dict[str, list[str]] = {}
        for entry in captured.field_origins.values():
            pointers = locations.setdefault(entry.wire_name, [])
            pointers.extend(
                origin.location.pointer for origin in entry.origins if origin.location.pointer not in pointers
            )
        assert_output(json.dumps(locations, indent=2) + "\n", EXPECTED / f"{case}.txt")
        if case == "synthetic-origins":
            assert_output(
                json.dumps(
                    {
                        "all_synthetic_resolved": all(entry.locations for entry in captured.synthetic_fields),
                        "required_only": sorted({
                            location.pointer
                            for entry in captured.synthetic_fields
                            if entry.kind == "required_only"
                            for location in entry.locations
                        }),
                        "root_value": sorted({
                            location.pointer
                            for entry in captured.synthetic_fields
                            if entry.kind == "root_value"
                            for location in entry.locations
                        }),
                    },
                    indent=2,
                )
                + "\n",
                EXPECTED / "synthetic-keywords.txt",
            )
    finally:
        ordinary.dispose()
        captured.dispose()
        captured.source_lease.close()


@pytest.mark.parametrize("backend", list(DataModelType))
def test_side_effect_free_type_projection(backend: DataModelType) -> None:
    """Project real post-render field types without calling annotation/import getters."""
    import sys

    from datamodel_code_generator._generation_contract import FieldSlot, SymbolId
    from datamodel_code_generator._shared_types import LiteralType
    from datamodel_code_generator.model import get_data_model_types
    from datamodel_code_generator.model.binding import (
        ExpectedFieldDeclaration,
        FrozenImportBindings,
        index_builtin_field_declarations,
    )
    from datamodel_code_generator.parser.openapi_contract_store import _type_recipe
    from datamodel_code_generator.parser.openapi_contract_types import FinalTypeProjector, ReferenceTypeBinding
    from tests.data.python.binding_engine_observer import BindingEngineObserver
    from tests.data.python.binding_inputs import binding_backend_name, builtin_field_imports
    from tests.data.python.binding_type_snapshot import type_snapshot

    model_types = get_data_model_types(backend, target_python_version=PythonVersionMin)
    parser = ContractApiOpenAPIParser(
        SOURCE / "type-projection.json",
        attempt_id=AttemptId(1),
        openapi_scopes=[OpenAPIScope.Api],
        data_model_type=model_types.data_model,
        data_model_root_type=model_types.root_model,
        data_model_field_type=model_types.field_model,
        data_type_manager_type=model_types.data_type_manager,
        dump_resolve_reference_action=model_types.dump_resolve_reference_action,
        use_unique_items_as_set=True,
        enum_field_as_literal=LiteralType.All,
        formatters=[],
    )
    previous = sys.getprofile()
    try:
        body = str(parser.parse())
        assert_output(body, EXPECTED / f"type-projection-{backend.name}.py")
        references = {
            parser.binding_ledger.identity(model.reference): ReferenceTypeBinding(SymbolId(index), False, False, False)
            for index, model in enumerate(parser.results)
        }
        projector = FinalTypeProjector(references, {})
        observer = BindingEngineObserver()
        sys.setprofile(observer.record)
        try:
            declarations: list[ExpectedFieldDeclaration] = []
            projections: dict[str, dict[str, object]] = {}
            for index, model in enumerate(parser.results):
                fields = projections[model.name] = {}
                for field in model.fields:
                    projection = projector.project(_type_recipe(field.data_type, parser.binding_ledger, set()))
                    fields[field.name] = type_snapshot(projection)
                    projected_type = next(value for value in (projection.value,) if value is not None)
                    declarations.append(
                        ExpectedFieldDeclaration(
                            AttemptId(1),
                            SymbolId(index),
                            FieldSlot(AttemptId(1), SymbolId(index), parser.binding_ledger.identity(field)),
                            model.name,
                            field.name,
                            binding_backend_name(backend),
                            projected_type,
                        )
                    )
            artifact_index = index_builtin_field_declarations(
                body,
                expected=tuple(declarations),
                imports=FrozenImportBindings(builtin_field_imports()),
            )
        finally:
            sys.setprofile(previous)
        assert_output(
            json.dumps(
                [
                    {
                        "model": declaration.expected.model_name,
                        "field": declaration.expected.native_name,
                        "annotation": declaration.annotation,
                        "assignment": declaration.assignment,
                        "facts": type_snapshot(declaration.facts),
                    }
                    for declaration in artifact_index.fields
                ],
                indent=2,
            )
            + "\n",
            EXPECTED / f"field-artifact-{backend.name}.txt",
        )
        assert_output(json.dumps(projections, indent=2) + "\n", EXPECTED / f"type-projection-{backend.name}.txt")
        assert_output(
            json.dumps({"additional_engine_calls": sum(observer.calls.values())}) + "\n",
            EXPECTED / "type-projection-calls.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()
