"""Preserve operation-only producer imports and actual emitted import overrides."""

from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path

import pytest

from datamodel_code_generator import DanglingRefWarning, DataModelType, GenerateConfig, OpenAPIScope
from datamodel_code_generator._openapi_type_binding import require_type_bindings
from tests.conftest import assert_output
from tests.data.python.binding_type_snapshot import type_snapshot
from tests.data.python.generation_session_inputs import compare_api_session, generate_product

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/binding"
EXPECTED = DATA / "expected/main/generation_platform/session-review"


@pytest.mark.parametrize("backend", [DataModelType.PydanticV2BaseModel, DataModelType.PydanticV2Dataclass])
@pytest.mark.parametrize(
    ("scope", "emitted", "collision"),
    [
        (OpenAPIScope.Schemas, False, False),
        (OpenAPIScope.Paths, False, False),
        (OpenAPIScope.Api, False, False),
        (OpenAPIScope.Schemas, True, False),
        (OpenAPIScope.Api, True, False),
        (OpenAPIScope.Schemas, False, True),
        (OpenAPIScope.Api, False, True),
    ],
)
@pytest.mark.parametrize("override", ["none", "unrelated", "serializer"])
def test_operation_serializer_imports(
    backend: DataModelType, scope: OpenAPIScope, override: str, *, emitted: bool, collision: bool
) -> None:
    """A policy-only wrapper keeps its producer import until the engine emits and remaps it."""
    variant = "collision" if collision else str(emitted)
    source = (SOURCE / f"session-serialize-imports-{variant}.json").resolve()
    config = GenerateConfig(
        input_file_type="openapi",
        openapi_scopes=[scope],
        output_model_type=backend,
        use_serialize_as_any=True,
        import_overrides={"UUID": "uuid"}
        if override == "unrelated"
        else {"SerializeAsAny": "pydantic.functional_serializers"}
        if override == "serializer"
        else None,
        input_filename="serialize-imports.json",
        formatters=[],
        disable_timestamp=True,
    )
    product, retained = generate_product(source, config)
    product.close()
    remapped = override == "serializer" and emitted
    assert_output(
        product.artifacts[0].content.decode(),
        EXPECTED / "serialize-imports" / f"{backend.name}-{scope.value}-{variant}-{remapped}.py",
    )
    batch = product.batch
    names = {symbol.id: symbol.name for symbol in batch.symbols}
    request = next(use for use in batch.type_uses if use.id.role == "request_body")
    field = next((field.model_facts.type for field in batch.fields if names[field.consumer] == "Container"), None)
    assert_output(
        json.dumps(
            {
                "request": type_snapshot(request.type, names),
                "field": type_snapshot(field, names),
                "demands": [
                    item.code for item in require_type_bindings(batch, tuple(use.id for use in batch.type_uses))
                ],
            },
            indent=2,
        )
        + "\n",
        EXPECTED / "serialize-imports" / ("types-collision.txt" if collision else f"types-{emitted}-{remapped}.txt"),
    )
    comparison = compare_api_session(source, config)
    assert_output(
        f"bytes equal: {comparison['outputs_equal']}\n"
        f"engine calls equal: {comparison['engine_calls_equal']}\n"
        f"factory reads: {comparison['factory_reads']}\n"
        f"retained parsers: {comparison['retained_parsers']}\n"
        f"retained graph: {retained}\n",
        EXPECTED / "split-discriminators/parity.txt",
    )


@pytest.mark.parametrize("backend", list(DataModelType))
@pytest.mark.parametrize("variant", ["plain", "partial", "dotted"])
@pytest.mark.parametrize("remapped", [False, True])
def test_global_import_producer(backend: DataModelType, variant: str, *, remapped: bool) -> None:
    """Use actual imports even when a field projection fails or its import cache is invalidated."""
    source = (SOURCE / f"session-global-imports-{variant}.json").resolve()
    dotted = variant == "dotted"
    config = GenerateConfig(
        input_file_type="openapi",
        openapi_scopes=[OpenAPIScope.Api],
        output_model_type=backend,
        import_overrides={"UUID": "custom_uuid"} if remapped else None,
        reuse_model=dotted,
        reuse_scope="tree" if dotted else "module",
        shared_module_name="shared.types" if dotted else "shared",
        treat_dot_as_module=dotted,
        all_exports_scope="recursive" if dotted else None,
        all_exports_collision_strategy="full-prefix",
        input_filename="global-imports.json",
        formatters=[],
        disable_timestamp=True,
    )
    with pytest.warns(DanglingRefWarning, match="Unresolved local") if variant == "partial" else nullcontext():
        product, retained = generate_product(source, config)
    with pytest.warns(DanglingRefWarning, match="Unresolved local") if variant == "partial" else nullcontext():
        comparison = compare_api_session(source, config)
    product.close()
    expected = EXPECTED / "serialize-imports" / f"global-{backend.name}-{variant}-{remapped}"
    for artifact in product.artifacts:
        assert_output(artifact.content.decode(), expected / Path(*artifact.path))
    request = next(use for use in product.batch.type_uses if use.id.role == "request_body")
    assert_output(
        json.dumps(
            {
                "request": type_snapshot(request.type, {}),
                "demands": [item.code for item in require_type_bindings(product.batch, (request.id,))],
            },
            indent=2,
        )
        + "\n",
        EXPECTED / "serialize-imports" / f"global-types-{remapped}.txt",
    )
    assert_output(
        f"bytes equal: {comparison['outputs_equal']}\n"
        f"engine calls equal: {comparison['engine_calls_equal']}\n"
        f"factory reads: {comparison['factory_reads']}\n"
        f"retained parsers: {comparison['retained_parsers']}\n"
        f"retained graph: {retained}\n",
        EXPECTED / "split-discriminators/parity.txt",
    )


@pytest.mark.parametrize("backend", list(DataModelType))
def test_imported_helper_in_unresolved_field(backend: DataModelType) -> None:
    """Retain an adopted leaf import even when its enclosing field cannot be projected."""
    source = (SOURCE / "session-imported-helper.json").resolve()
    config = GenerateConfig(
        input_file_type="openapi",
        openapi_scopes=[OpenAPIScope.Api],
        output_model_type=backend,
        use_standard_primitive_types=True,
        import_overrides={"UUID": "custom_uuid"},
        input_filename="imported-helper.json",
        formatters=[],
        disable_timestamp=True,
    )
    with pytest.warns(DanglingRefWarning, match="Unresolved local"):
        product, retained = generate_product(source, config)
    with pytest.warns(DanglingRefWarning, match="Unresolved local"):
        comparison = compare_api_session(source, config)
    product.close()
    helper = next(
        use
        for use in product.batch.type_uses
        if use.id.schema_site.pointer == "/components/schemas/Bad/properties/value/anyOf/0"
    )
    assert_output(
        json.dumps(
            {
                "helper": type_snapshot(helper.type, {}),
                "demands": sorted({item.code for item in require_type_bindings(product.batch, (helper.id,))}),
            },
            indent=2,
        )
        + "\n",
        EXPECTED / "serialize-imports/partial-helper.txt",
    )
    assert_output(product.artifacts[0].content.decode(), EXPECTED / "serialize-imports" / f"helper-{backend.name}.py")
    assert_output(
        f"bytes equal: {comparison['outputs_equal']}\n"
        f"engine calls equal: {comparison['engine_calls_equal']}\n"
        f"factory reads: {comparison['factory_reads']}\n"
        f"retained parsers: {comparison['retained_parsers']}\n"
        f"retained graph: {retained}\n",
        EXPECTED / "split-discriminators/parity.txt",
    )
