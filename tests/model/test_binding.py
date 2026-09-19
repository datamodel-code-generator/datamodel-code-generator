"""Match accepted builtin declarations to actual field identities through real generation."""

from __future__ import annotations

import json
import re
from operator import itemgetter
from pathlib import Path

import pytest

from datamodel_code_generator import DataModelType, OpenAPIScope, PythonVersionMin
from datamodel_code_generator._generation_contract import (
    AttemptId,
    BindingCaptureError,
    BuiltinType,
    FieldSlot,
    GenericType,
    SymbolId,
)
from datamodel_code_generator.imports import Import
from datamodel_code_generator.model import get_data_model_types
from datamodel_code_generator.model.binding import (
    ExpectedFieldDeclaration,
    FieldProjectionContext,
    FrozenImportBindings,
    freeze_builtin_field_facts,
    freeze_none_default_provenance,
    index_builtin_field_declarations,
)
from datamodel_code_generator.parser.openapi_contract import ContractApiOpenAPIParser
from tests.conftest import assert_output
from tests.data.python.binding_inputs import functional_field_expectations
from tests.data.python.binding_type_snapshot import type_snapshot

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/binding"
EXPECTED = DATA / "expected/main/generation_platform/binding"


@pytest.mark.parametrize("total_false", [False, True])
def test_functional_typed_dict_field_index(*, total_false: bool) -> None:
    """Keep actual empty/escaped/Unicode keys and Required/ReadOnly placement."""
    types = get_data_model_types(DataModelType.TypingTypedDict, target_python_version=PythonVersionMin)
    parser = ContractApiOpenAPIParser(
        SOURCE / "functional-fields.json",
        attempt_id=AttemptId(1),
        data_model_type=types.data_model,
        data_model_root_type=types.root_model,
        data_model_field_type=types.field_model,
        data_type_manager_type=types.data_type_manager,
        dump_resolve_reference_action=types.dump_resolve_reference_action,
        openapi_scopes=[OpenAPIScope.Api],
        use_total_false_for_typed_dict=total_false,
        use_frozen_field=True,
        formatters=[],
    )
    try:
        body = str(parser.parse())
        assert_output(body, EXPECTED / f"functional-fields-{total_false}.py")
        expected = functional_field_expectations(parser)
        index = index_builtin_field_declarations(
            body,
            expected=expected,
            imports=FrozenImportBindings((
                Import(import_="TypedDict", from_="typing_extensions" if total_false else "typing"),
                *(Import(import_=name, from_="typing_extensions") for name in ("ReadOnly", "Required", "NotRequired")),
            )),
        )
        assert_output(
            json.dumps(
                [
                    {
                        "key": field.expected.entry_key,
                        "ordinal": field.expected.entry_ordinal,
                        "annotation": field.annotation,
                        "assignment": field.assignment,
                        "facts": type_snapshot(field.facts),
                    }
                    for field in index.fields
                ],
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            EXPECTED / f"functional-fields-{total_false}.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize("backend", list(DataModelType))
@pytest.mark.parametrize("annotated", [False, True])
@pytest.mark.parametrize("scope_noise", [False, True])
def test_emitted_default_facts(backend: DataModelType, *, annotated: bool, scope_noise: bool) -> None:
    """Observe real mutable-default factories, explicit null and builtin missing sentinels."""
    from tests.data.python.binding_inputs import binding_backend_name, builtin_binding_config, builtin_field_imports

    parser = ContractApiOpenAPIParser(
        SOURCE / "emitted-defaults.json",
        attempt_id=AttemptId(1),
        config=builtin_binding_config(backend, annotated=annotated, missing_sentinel=True),
    )
    try:
        body = str(parser.parse())
        assert_output(body, EXPECTED / f"emitted-defaults-{backend.name}-{annotated}.py")
        model = parser.results[0]
        field_types = {
            "missing": BuiltinType("str"),
            "explicit_null": BuiltinType("str"),
            "literal": BuiltinType("int"),
            "items": GenericType(BuiltinType("list"), (BuiltinType("int"),)),
            "mapping": GenericType(BuiltinType("dict"), (BuiltinType("str"), BuiltinType("bool"))),
            "factory": GenericType(BuiltinType("list"), (BuiltinType("str"),)),
            "required": BuiltinType("bool"),
        }
        expected = tuple(
            ExpectedFieldDeclaration(
                AttemptId(1),
                SymbolId(0),
                FieldSlot(AttemptId(1), SymbolId(0), parser.binding_ledger.identity(field), ordinal, field.name),
                model.name,
                field.name,
                binding_backend_name(backend),
                field_types[field.name],
            )
            for ordinal, field in enumerate(model.fields)
        )
        if scope_noise:
            body += (SOURCE / "field-scope-noise.txt").read_text()
        field_module = "msgspec" if backend == DataModelType.MsgspecStruct else "dataclasses"
        index = index_builtin_field_declarations(
            body,
            expected=expected,
            imports=FrozenImportBindings((*builtin_field_imports(), Import(import_="field", from_=field_module))),
        )
        assert_output(
            json.dumps({field.expected.native_name: type_snapshot(field.facts) for field in index.fields}, indent=2)
            + "\n",
            EXPECTED / f"emitted-defaults-{backend.name}-{annotated}.txt",
        )
        fields_by_id = {parser.binding_ledger.identity(field): field for field in model.fields}
        provenance = {}
        backend_facts = {}
        for declaration in index.fields:
            slot = declaration.expected.slot
            origin = parser.field_origins[slot.field]
            projection = FieldProjectionContext(
                original_required=origin.required_by_node,
                schema_default=any(
                    isinstance(source.raw, dict) and "default" in source.raw for source in origin.origins
                ),
                explicit_model_default=False,
                explicit_nullable=False,
                preexisting_null=False,
                configuration_nullable=False,
                builtin_semantics=True,
                backend=binding_backend_name(backend),
                constructor_init=True,
                kw_only=backend == DataModelType.PydanticV2BaseModel,
            )
            backend_facts[declaration.expected.native_name] = type_snapshot(
                freeze_builtin_field_facts(fields_by_id[slot.field], emitted=declaration.facts, projection=projection)
            )
            provenance[declaration.expected.native_name] = type_snapshot(
                freeze_none_default_provenance(
                    fields_by_id[slot.field], emitted=declaration.facts, projection=projection
                )
            )
        assert_output(
            json.dumps(backend_facts, indent=2) + "\n",
            EXPECTED / f"backend-field-facts-{backend.name}-{annotated}.txt",
        )
        assert_output(
            json.dumps(provenance, indent=2) + "\n",
            EXPECTED / f"none-provenance-{backend.name}-{annotated}.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize(
    "change", json.loads((SOURCE / "invalid-functional-artifacts.json").read_text()), ids=itemgetter("id")
)
def test_functional_artifact_changes_are_rejected(change: dict[str, str]) -> None:
    """Reject semantic declaration changes after an otherwise valid builtin generation."""
    from tests.data.python.binding_inputs import builtin_binding_config

    config = builtin_binding_config(DataModelType.TypingTypedDict)
    config.use_frozen_field = True
    parser = ContractApiOpenAPIParser(SOURCE / "functional-fields.json", attempt_id=AttemptId(1), config=config)
    try:
        body = str(parser.parse())
        assert_output(body, EXPECTED / "functional-fields-False.py")
        changed = body.replace(change["old"], change["new"])
        with pytest.raises(BindingCaptureError, match=re.escape(change["error"])):
            index_builtin_field_declarations(
                changed,
                expected=functional_field_expectations(parser),
                imports=FrozenImportBindings((Import(import_="TypedDict", from_="typing"),)),
            )
    finally:
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize("annotated", [False, True])
def test_emitted_meta_uses_projected_node_locations(*, annotated: bool) -> None:
    """Observe renderer-normalized bounds and retain the location of each Meta layer."""
    import sys

    from tests.data.python.binding_engine_observer import BindingEngineObserver
    from tests.data.python.binding_inputs import (
        builtin_binding_config,
        builtin_field_imports,
        projected_field_expectations,
    )

    config = builtin_binding_config(DataModelType.MsgspecStruct, annotated=annotated)
    config.field_constraints = True
    config.collapse_root_models = True
    parser = ContractApiOpenAPIParser(SOURCE / "emitted-meta.json", attempt_id=AttemptId(1), config=config)
    previous = sys.getprofile()
    try:
        body = str(parser.parse())
        assert_output(body, EXPECTED / f"emitted-meta-{annotated}.py")
        observer = BindingEngineObserver()
        sys.setprofile(observer.record)
        try:
            index = index_builtin_field_declarations(
                body,
                expected=projected_field_expectations(parser, "Metadata"),
                imports=FrozenImportBindings(
                    builtin_field_imports(),
                    tuple((SymbolId(index), model.name) for index, model in enumerate(parser.results)),
                ),
            )
        finally:
            sys.setprofile(previous)
        assert_output(
            json.dumps(
                {field.expected.native_name: type_snapshot(field.facts.meta_layers) for field in index.fields}, indent=2
            )
            + "\n",
            EXPECTED / f"emitted-meta-{annotated}.txt",
        )
        assert_output(
            json.dumps({"additional_engine_calls": sum(observer.calls.values())}) + "\n",
            EXPECTED / "type-projection-calls.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize(
    "change", json.loads((SOURCE / "invalid-type-artifacts.json").read_text()), ids=itemgetter("id")
)
def test_artifact_type_must_match_existing_projection(change: dict[str, str]) -> None:
    """Reject changed type identities, child positions, literal types and constraints."""
    from datamodel_code_generator._format_types import DatetimeClassType
    from datamodel_code_generator._shared_types import LiteralType
    from tests.data.python.binding_inputs import (
        builtin_binding_config,
        builtin_field_imports,
        projected_field_expectations,
    )

    backend = DataModelType[change["backend"]]
    config = builtin_binding_config(backend)
    config.use_unique_items_as_set = True
    config.enum_field_as_literal = LiteralType.All
    config.target_datetime_class = DatetimeClassType.Awaredatetime
    parser = ContractApiOpenAPIParser(SOURCE / "type-projection.json", attempt_id=AttemptId(1), config=config)
    try:
        body = str(parser.parse())
        assert_output(body, EXPECTED / f"type-projection-{backend.name}.py")
        with pytest.raises(BindingCaptureError, match="Accepted field annotation does not match its projected type"):
            index_builtin_field_declarations(
                body.replace(change["old"], change["new"]),
                expected=projected_field_expectations(parser, "Types", backend),
                imports=FrozenImportBindings(
                    builtin_field_imports(),
                    tuple((SymbolId(index), model.name) for index, model in enumerate(parser.results)),
                ),
            )
    finally:
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize("backend", list(DataModelType))
@pytest.mark.parametrize("configured", [False, True])
def test_builtin_model_facts_match_adopted_settings(
    backend: DataModelType, tmp_path: Path, *, configured: bool
) -> None:
    """Compare adopted options with fixed-main syntax and native backend metadata."""
    import subprocess
    import sys

    from datamodel_code_generator.model.binding import ModelProjectionContext, freeze_builtin_model_facts
    from tests.data.python.binding_engine_observer import BindingEngineObserver
    from tests.data.python.binding_inputs import binding_backend_name, builtin_model_config

    parser = ContractApiOpenAPIParser(
        SOURCE / "model-facts.json",
        attempt_id=AttemptId(1),
        config=builtin_model_config(backend, configured=configured),
    )
    previous = sys.getprofile()
    try:
        body = str(parser.parse())
        assert_output(body, EXPECTED / f"model-facts-{backend.name}-{configured}.py")
        observer = BindingEngineObserver()
        sys.setprofile(observer.record)
        try:
            facts = freeze_builtin_model_facts(
                parser.results[0],
                projection=ModelProjectionContext(binding_backend_name(backend), builtin_semantics=True),
            )
        finally:
            sys.setprofile(previous)
        assert_output(
            json.dumps(type_snapshot(facts), indent=2) + "\n",
            EXPECTED / f"model-facts-{backend.name}-{configured}.txt",
        )
        assert_output(
            json.dumps({"additional_engine_calls": sum(observer.calls.values())}) + "\n",
            EXPECTED / "type-projection-calls.txt",
        )
        generated = tmp_path / "model.py"
        generated.write_text(body)
        native = subprocess.run(
            [sys.executable, str(DATA / "python/backend_model_runtime.py"), backend.name, str(generated)],
            check=True,
            capture_output=True,
            text=True,
        )
        assert_output(native.stdout, EXPECTED / f"model-runtime-{backend.name}-{configured}.txt")
    finally:
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize("change", json.loads((SOURCE / "tag-field-artifacts.json").read_text()), ids=itemgetter("id"))
def test_tag_field_nonemission(change: dict[str, str | None]) -> None:
    """Keep tag ownership without inventing an emitted declaration or constructor argument."""
    from datamodel_code_generator._shared_types import LiteralType
    from tests.data.python.binding_inputs import builtin_binding_config, projected_field_expectations

    backend = DataModelType.MsgspecStruct
    config = builtin_binding_config(backend)
    config.enum_field_as_literal = LiteralType.All
    config.use_one_literal_as_default = True
    parser = ContractApiOpenAPIParser(
        DATA / "openapi/discriminator_enum_single_value_msgspec.yaml", attempt_id=AttemptId(1), config=config
    )
    try:
        body = str(parser.parse())
        assert_output(body, EXPECTED / "tag-fields.py")
        expected = tuple(
            field
            for name in ("StartedEvent", "StoppedEvent")
            for field in projected_field_expectations(parser, name, backend)
        )
        imports = FrozenImportBindings(())
        if (error := change["error"]) is not None:
            with pytest.raises(BindingCaptureError, match=error):
                index_builtin_field_declarations(
                    body.replace(str(change["old"]), str(change["new"])), expected=expected, imports=imports
                )
            return
        index = index_builtin_field_declarations(body, expected=expected, imports=imports)
        fields = {parser.binding_ledger.identity(field): field for model in parser.results for field in model.fields}
        projection = FieldProjectionContext(
            original_required=True,
            schema_default=False,
            explicit_model_default=False,
            explicit_nullable=False,
            preexisting_null=False,
            configuration_nullable=False,
            builtin_semantics=True,
            backend="msgspec",
            constructor_init=True,
            kw_only=False,
        )
        assert_output(
            json.dumps(
                [
                    {
                        "model": declaration.expected.model_name,
                        "name": declaration.expected.native_name,
                        "annotation": declaration.annotation,
                        "assignment": declaration.assignment,
                        "line": declaration.line,
                        "column": declaration.column,
                        "facts": type_snapshot(declaration.facts),
                        "constructor_init": type_snapshot(
                            freeze_builtin_field_facts(
                                fields[declaration.expected.slot.field],
                                emitted=declaration.facts,
                                projection=projection,
                            ).constructor_init
                        ),
                    }
                    for declaration in index.fields
                ],
                indent=2,
            )
            + "\n",
            EXPECTED / "tag-fields.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize(
    "change", json.loads((SOURCE / "metadata-type-artifacts.json").read_text()), ids=itemgetter("id")
)
def test_projected_metadata_matches_accepted_type(change: dict[str, str | bool]) -> None:
    """Keep projected discriminator metadata at its actual nested type location."""
    from tests.data.python.binding_inputs import (
        builtin_binding_config,
        builtin_field_imports,
        projected_field_expectations,
    )

    backend = DataModelType.PydanticV2BaseModel
    config = builtin_binding_config(backend)
    config.collapse_root_models = True
    parser = ContractApiOpenAPIParser(SOURCE / "metadata-types.json", attempt_id=AttemptId(1), config=config)
    try:
        body = str(parser.parse())
        assert_output(body, EXPECTED / "metadata-types.py")
        expected = projected_field_expectations(parser, "Envelope", backend)
        imports = FrozenImportBindings(
            builtin_field_imports(), tuple((SymbolId(index), model.name) for index, model in enumerate(parser.results))
        )
        if change["error"]:
            with pytest.raises(
                BindingCaptureError, match="Accepted field annotation does not match its projected type"
            ):
                index_builtin_field_declarations(
                    body.replace(str(change["old"]), str(change["new"])), expected=expected, imports=imports
                )
            return
        index = index_builtin_field_declarations(body, expected=expected, imports=imports)
        assert_output(
            json.dumps([field.expected.native_name for field in index.fields], indent=2) + "\n",
            EXPECTED / "metadata-type-fields.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize("standard", [False, True])
def test_container_projection_preserves_actual_imports(*, standard: bool) -> None:
    """Preserve FrozenSet spelling and the actual typing or collections.abc identity."""
    from tests.data.python.binding_inputs import builtin_binding_config, projected_field_expectations

    backend = DataModelType.PydanticV2BaseModel
    config = builtin_binding_config(backend)
    config.use_unique_items_as_set = True
    config.use_generic_container_types = True
    config.use_standard_collections = standard
    parser = ContractApiOpenAPIParser(SOURCE / "container-types.json", attempt_id=AttemptId(1), config=config)
    try:
        body = str(parser.parse())
        assert_output(body, EXPECTED / f"container-types-{standard}.py")
        expected = projected_field_expectations(parser, "Container", backend)
        imports = FrozenImportBindings((
            Import(import_="FrozenSet", from_="typing"),
            *(
                Import(import_=name, from_="collections.abc" if standard else "typing")
                for name in ("Sequence", "Mapping")
            ),
        ))
        index = index_builtin_field_declarations(body, expected=expected, imports=imports)
        assert_output(
            json.dumps(
                {field.expected.native_name: type_snapshot(field.expected.type) for field in index.fields}, indent=2
            )
            + "\n",
            EXPECTED / f"container-types-{standard}.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize(
    "change", json.loads((SOURCE / "decimal-type-artifacts.json").read_text()), ids=itemgetter("id")
)
@pytest.mark.parametrize("deserialize", [False, True])
def test_decimal_constraint_matches_finite_value(change: dict[str, str | bool], *, deserialize: bool) -> None:
    """Corroborate actual Decimal constraints without evaluating annotation calls."""
    from tests.data.python.binding_inputs import (
        builtin_binding_config,
        builtin_field_imports,
        projected_field_expectations,
    )

    backend = DataModelType.PydanticV2BaseModel
    config = builtin_binding_config(backend)
    config.use_decimal_for_multiple_of = True
    if deserialize:
        from datamodel_code_generator.enums import DefaultValueType

        config.deserialize_default_values = [DefaultValueType.Decimal]
    parser = ContractApiOpenAPIParser(
        SOURCE / ("decimal-default-types.json" if deserialize else "decimal-types.json"),
        attempt_id=AttemptId(1),
        config=config,
    )
    try:
        body = str(parser.parse())
        assert_output(body, EXPECTED / ("decimal-default-types.py" if deserialize else "decimal-types.py"))
        expected = projected_field_expectations(parser, "Prices", backend)
        imports = FrozenImportBindings((
            *builtin_field_imports(),
            Import(import_="condecimal", from_="pydantic"),
            Import(import_="Decimal", from_="decimal"),
        ))
        body = body.replace(str(change["old"]), str(change["new"]))
        if change["error"] or (deserialize and change["id"] == "equivalent-exponent"):
            with pytest.raises(
                BindingCaptureError, match="Accepted field annotation does not match its projected type"
            ):
                index_builtin_field_declarations(body, expected=expected, imports=imports)
            return
        index = index_builtin_field_declarations(body, expected=expected, imports=imports)
        assert_output(
            json.dumps([field.expected.native_name for field in index.fields], indent=2) + "\n",
            EXPECTED / "decimal-type-fields.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize("change", json.loads((SOURCE / "bound-type-artifacts.json").read_text()), ids=itemgetter("id"))
def test_bound_expression_matches_actual_structure(change: dict[str, str | bool]) -> None:
    """Use retained semantic expressions for callables, literal values, tuples and unions."""
    from tests.data.python.binding_inputs import (
        builtin_binding_config,
        builtin_field_imports,
        projected_field_expectations,
    )

    backend = DataModelType.PydanticV2BaseModel
    parser = ContractApiOpenAPIParser(
        SOURCE / "bound-types.json", attempt_id=AttemptId(1), config=builtin_binding_config(backend)
    )
    try:
        body = str(parser.parse())
        assert_output(body, EXPECTED / "bound-types.py")
        expected = projected_field_expectations(parser, "Bindings", backend)
        imports = FrozenImportBindings((
            *builtin_field_imports(),
            Import(import_="Callable", from_="collections.abc"),
            Import(import_="external"),
        ))
        if change["error"]:
            with pytest.raises(
                BindingCaptureError, match="Accepted field annotation does not match its projected type"
            ):
                index_builtin_field_declarations(
                    body.replace(str(change["old"]), str(change["new"])), expected=expected, imports=imports
                )
            return
        index = index_builtin_field_declarations(body, expected=expected, imports=imports)
        assert_output(
            json.dumps([field.expected.native_name for field in index.fields], indent=2) + "\n",
            EXPECTED / "bound-type-fields.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()
