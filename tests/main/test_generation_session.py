"""Exercise real accepted batches and products through the unchanged generation driver."""

from __future__ import annotations

import json
import operator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import (
    DanglingRefWarning,
    GenerateConfig,
    OpenAPIScope,
    _prepare_generate_facade_config,
    _run_generation,
)
from datamodel_code_generator._generation_contract import (
    GeneratedEnumMember,
    GeneratedSymbolType,
    ImportedType,
    LiteralType,
)
from datamodel_code_generator._openapi_generation import OpenAPIGenerationSession, SourceLease
from datamodel_code_generator._openapi_type_binding import require_type_bindings
from datamodel_code_generator.parser.openapi import OpenAPIParser
from datamodel_code_generator.parser.openapi_contract import BindingResolverMixin
from datamodel_code_generator.parser.openapi_contract_store import BindingLedger
from tests.conftest import assert_output
from tests.data.python.binding_type_snapshot import final_import_snapshot, type_snapshot
from tests.data.python.generation_contract_consumers import (
    client_plan,
    metadata_snapshot,
    plan_snapshot,
    server_plan,
    wire_plan,
)
from tests.data.python.generation_session_inputs import generate_product, session_protocol_failure

if TYPE_CHECKING:
    from collections.abc import Callable

    from datamodel_code_generator._generation_contract import GeneratedTypeContractBatch
    from tests.data.python.generation_contract_consumers import TargetPlan

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform"
EXPECTED = DATA / "expected/main/generation_platform"


@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
@pytest.mark.parametrize("consumer", [server_plan, client_plan], ids=["server", "client"])
def test_accepted_product_dry_consumer(
    backend: str,
    consumer: Callable[[GeneratedTypeContractBatch, SourceLease], TargetPlan],
) -> None:
    """Run each target separately, compare fixed-main bytes, and plan after all sources close."""
    product, retained = generate_product(
        (SOURCE / "binding/session-contract.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            output_model_type=backend,
            input_filename="session.json",
            formatters=["black", "isort"],
            disable_timestamp=True,
        ),
    )
    plan = consumer(product.batch, product.source_lease)
    product.close()
    assert_output(product.artifacts[0].content.decode(), EXPECTED / "session" / (backend.replace(".", "_") + ".py"))
    assert_output(wire_plan(product.batch), EXPECTED / "session/wire-plan.txt")
    suffix = "-typeddict" if backend == "typing.TypedDict" else ""
    assert_output(plan_snapshot(plan), EXPECTED / "session" / f"{consumer.__name__.replace('_', '-')}{suffix}.txt")
    assert_output(
        f"retained graph: {retained}\n"
        f"unavailable origins: {sum(field.origin_state != 'known' for field in product.batch.fields)}\n"
        f"unavailable field facts: {sum(field.model_facts is None for field in product.batch.fields)}\n",
        EXPECTED / "session/lifetime.txt",
    )


@pytest.mark.parametrize(
    "case", ["local", "loaded", "shadow", "unobserved", "missing", "cycle", "invalid", "anchor", "nonobject"]
)
@pytest.mark.parametrize("consumer", [server_plan, client_plan], ids=["server", "client"])
def test_dry_consumer_metadata_references(case: str, consumer: Callable[..., TargetPlan]) -> None:
    """Resolve only borrowed metadata and reject required unavailable targets without fetching."""
    product, retained = generate_product(
        (SOURCE / f"binding/session-metadata-{case}.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            input_filename="metadata.json",
            formatters=[],
            disable_timestamp=True,
        ),
    )
    if case in {"local", "loaded", "shadow"}:
        plan = consumer(product.batch, product.source_lease)
        product.close()
        assert_output(plan_snapshot(plan), EXPECTED / "session/metadata" / f"{consumer.__name__}.txt")
    else:
        with pytest.raises(
            ValueError, match=r"document_not_observed|pointer_missing|invalid_pointer|invalid_target|cycle"
        ) as failure:
            consumer(product.batch, product.source_lease)
        product.close()
        assert_output(str(failure.value) + "\n", EXPECTED / "session/metadata" / f"{case}-failure.txt")
    assert_output(metadata_snapshot(product.batch), EXPECTED / "session/metadata" / f"{case}.txt")
    for artifact in product.artifacts:
        assert_output(artifact.content.decode(), EXPECTED / "session/metadata" / case / Path(*artifact.path))
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
@pytest.mark.parametrize("consumer", [server_plan, client_plan], ids=["server", "client"])
@pytest.mark.parametrize("referenced", [False, True])
def test_schema_only_callback_and_helper_plan(
    backend: str, consumer: Callable[..., TargetPlan], *, referenced: bool
) -> None:
    """Plan a selected route and primitive helper while retaining ungenerated callback schemas."""
    suffix = "-referenced" if referenced else ""
    product, retained = generate_product(
        (SOURCE / f"binding/session-schema-callback{suffix}.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Schemas],
            output_model_type=backend,
            input_filename="callback.json",
            formatters=[],
            disable_timestamp=True,
        ),
    )
    selected = tuple(operation.id for operation in product.batch.operations if operation.id.kind == "path")
    helpers = tuple(
        use.id
        for use in product.batch.type_uses
        if use.id.schema_site.pointer == "/components/schemas/Item/properties/id"
    )
    plan = consumer(product.batch, product.source_lease, selected=selected, helpers=helpers)
    callback_uses = tuple(use.id for use in product.batch.type_uses if use.id.role == "request_body")
    product.close()
    assert_output(plan_snapshot(plan), EXPECTED / "session/callback" / f"{consumer.__name__}{suffix}.txt")
    assert_output(
        "\n".join(error.code for error in require_type_bindings(product.batch, callback_uses)) + "\n",
        EXPECTED / "session/callback/required.txt",
    )
    assert_output(product.artifacts[0].content.decode(), EXPECTED / "session/callback" / f"{backend}.py")
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize("scope", [OpenAPIScope.Schemas, OpenAPIScope.Api])
def test_referenced_callback_parameter_locations(scope: OpenAPIScope) -> None:
    """Retain independent declaration and root-use pointers in both explicit scopes."""
    product, retained = generate_product(
        (SOURCE / "binding/session-schema-callback-referenced.json").resolve(),
        GenerateConfig(input_file_type="openapi", openapi_scopes=[scope], formatters=[], disable_timestamp=True),
    )
    product.close()
    assert_output(
        "".join(
            f"{parameter.name}\n  declaration: {parameter.declaration.location.pointer}\n"
            f"  use: {parameter.use_site.pointer}\n"
            for operation in product.batch.operations
            if operation.id.kind == "callback"
            for parameter in operation.parameters
        ),
        EXPECTED / "session/callback/parameter-locations.txt",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
def test_final_discriminator_enum_members(backend: str) -> None:
    """Keep adopted enum member identities through discriminator replacement and inherited copies."""
    product, retained = generate_product(
        (DATA / "openapi/discriminator_enum.yaml").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            output_model_type=backend,
            input_filename="enum.json",
            formatters=[],
            disable_timestamp=True,
            use_enum_values_in_discriminator=True,
            use_subclass_enum=True,
            use_serialize_as_any=True,
            reuse_model=True,
        ),
    )
    product.close()
    names = {symbol.id: symbol.name for symbol in product.batch.symbols}
    assert_output(
        "".join(
            f"{names[field.consumer]}.{field.slot.name}: "
            + (
                f"{names[value.symbol]}.{value.name}"
                if isinstance(value, GeneratedEnumMember)
                else json.dumps(value.value)
            )
            + "\n"
            for field in product.batch.fields
            if names[field.consumer] in {"RequestV1", "RequestV2"}
            and field.slot is not None
            and field.model_facts is not None
            and isinstance(field.model_facts.type, LiteralType)
            for value in field.model_facts.type.values
        ),
        EXPECTED / "session-review/no-diagnostics.txt"
        if backend == "typing.TypedDict"
        else EXPECTED
        / "session-review/final-enum"
        / ("literals.txt" if backend == "pydantic_v2.dataclass" else "members.txt"),
    )
    assert_output(
        "\n".join(
            error.code
            for error in require_type_bindings(product.batch, tuple(use.id for use in product.batch.type_uses))
        ),
        EXPECTED / "session-review/no-diagnostics.txt",
    )
    assert_output(product.artifacts[0].content.decode(), EXPECTED / "session-review/final-enum" / f"{backend}.py")
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
@pytest.mark.parametrize("enum_values", [False, True])
@pytest.mark.parametrize("alias", [False, True])
@pytest.mark.parametrize("copied", [False, True])
def test_discriminator_override_helper(backend: str, *, enum_values: bool, alias: bool, copied: bool) -> None:
    """Demand the actual replaced field type while preserving its original enum declaration."""
    product, retained = generate_product(
        (SOURCE / f"binding/session-discriminator-{'copied' if copied else 'override'}.yaml").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            output_model_type=backend,
            input_filename="override.yaml",
            formatters=[],
            disable_timestamp=True,
            use_enum_values_in_discriminator=enum_values,
            use_subclass_enum=True,
            reuse_model=True,
            aliases={"version": "kind"} if alias else None,
        ),
    )
    product.close()
    names = {symbol.id: symbol.name for symbol in product.batch.symbols}
    pointers = {
        "/components/schemas/RequestBase/properties/version",
        "/components/schemas/RequestV1/properties/version",
    }
    if copied:
        pointers.add("/components/schemas/Grandchild/properties/version")
    selected = tuple(use for use in product.batch.type_uses if use.id.schema_site.pointer in pointers)
    expected = EXPECTED / f"session-review/discriminator-{'copied' if copied else 'override'}"
    assert_output(
        json.dumps({use.id.schema_site.pointer: type_snapshot(use.type, names) for use in selected}, indent=2) + "\n",
        expected
        / ("inherited.txt" if backend == "typing.TypedDict" else "members.txt" if enum_values else "literals.txt"),
    )
    assert_output(
        "\n".join(error.code for error in require_type_bindings(product.batch, tuple(use.id for use in selected))),
        EXPECTED / "session-review/no-diagnostics.txt",
    )
    assert_output(product.artifacts[0].content.decode(), expected / f"{backend}-{enum_values}-{alias}.py")
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
def test_divergent_discriminator_copy_helpers(backend: str) -> None:
    """Keep divergent final copies ambiguous without rejecting their actual declaring model."""
    product, retained = generate_product(
        (SOURCE / "binding/session-discriminator-ambiguous.yaml").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            output_model_type=backend,
            input_filename="ambiguous.yaml",
            formatters=[],
            disable_timestamp=True,
            use_enum_values_in_discriminator=True,
            use_subclass_enum=True,
            reuse_model=True,
        ),
    )
    product.close()
    selected = sorted(
        (
            use
            for use in product.batch.type_uses
            if use.id.schema_site.pointer
            in {
                "/components/schemas/Grandchild",
                "/components/schemas/Grandchild/properties/version",
            }
        ),
        key=lambda use: use.id.schema_site.pointer,
    )
    expected = EXPECTED / "session-review/discriminator-ambiguous"
    assert_output(
        "".join(
            f"{use.id.schema_site.pointer}: "
            f"{','.join(error.code for error in require_type_bindings(product.batch, (use.id,))) or use.state}\n"
            for use in selected
        ),
        expected / ("equal.txt" if backend == "typing.TypedDict" else "ambiguous.txt"),
    )
    assert_output(product.artifacts[0].content.decode(), expected / f"{backend}.py")
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
def test_final_root_item_terminals(backend: str) -> None:
    """Keep created root types terminal and existing root origins distinct from reference uses."""
    product, retained = generate_product(
        (SOURCE / "binding/session-root-items.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            output_model_type=backend,
            input_filename="items.json",
            formatters=[],
            disable_timestamp=True,
            use_title_as_name=True,
        ),
    )
    product.close()
    names = {symbol.id: symbol.name for symbol in product.batch.symbols}
    selected = tuple(
        use
        for use in product.batch.type_uses
        if use.id.schema_site.pointer.endswith("/items") and "/properties/" in use.id.schema_site.pointer
    )
    expected = EXPECTED / "session-review/root-items"
    assert_output(
        "".join(f"{use.id.schema_site.pointer}: {names[use.type.symbol]}\n" for use in selected), expected / "types.txt"
    )
    assert_output(
        "\n".join(error.code for error in require_type_bindings(product.batch, tuple(use.id for use in selected))),
        EXPECTED / "session-review/no-diagnostics.txt",
    )
    assert_output(product.artifacts[0].content.decode(), expected / f"{backend}.py")
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
@pytest.mark.parametrize("annotated", [False, True])
def test_final_bound_import_overrides(backend: str, *, annotated: bool) -> None:
    """Retain overridden imports within nested bound expressions, constraints and containers."""
    product, retained = generate_product(
        (SOURCE / "binding/session-final-imports.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            output_model_type=backend,
            input_filename="imports.json",
            formatters=[],
            disable_timestamp=True,
            use_annotated=annotated,
            field_constraints=annotated,
            import_overrides=json.loads((SOURCE / "binding/session-final-import-overrides.json").read_text()),
        ),
    )
    product.close()
    assert_output(
        final_import_snapshot(product.batch),
        EXPECTED
        / "session-review/final-imports"
        / ("pydantic-imports.txt" if backend.startswith("pydantic") else "builtin-imports.txt"),
    )
    assert_output(
        product.artifacts[0].content.decode(), EXPECTED / "session-review/final-imports" / f"{backend}-{annotated}.py"
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize(
    "case", ["metadata-types", "container-types", "empty-fixed-tuple", "emitted-defaults", "synthetic-origins"]
)
@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
@pytest.mark.parametrize("annotated", [False, True])
@pytest.mark.parametrize("collapse", [False, True])
@pytest.mark.parametrize("remapped", [False, True])
def test_final_builtin_type_demands(
    case: str, backend: str, *, annotated: bool, collapse: bool, remapped: bool
) -> None:
    """Demand complete final types after ordinary emission, including nested metadata and fixed tuples."""
    product, retained = generate_product(
        (SOURCE / f"binding/{case}.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            output_model_type=backend,
            input_filename="final-types.json",
            import_overrides={"Field": "pydantic", "Meta": "msgspec"} if remapped else None,
            formatters=[],
            disable_timestamp=True,
            use_annotated=annotated,
            field_constraints=annotated,
            field_extra_keys={"default_factory"},
            collapse_root_models=collapse,
        ),
    )
    product.close()
    assert_output(
        "\n".join(
            error.code
            for error in require_type_bindings(product.batch, tuple(use.id for use in product.batch.type_uses))
        ),
        EXPECTED / "session-review/no-diagnostics.txt",
    )
    assert_output(
        product.artifacts[0].content.decode(),
        EXPECTED / "session-review/final-types" / f"{case}-{backend}-{annotated}{'-collapsed' if collapse else ''}.py",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize(
    "case",
    [
        "enums",
        "forward",
        "reuse",
        "parameter-override",
        "media-precedence",
        "media-ignored",
        "media-stream",
        "imports",
        "builtin-alias",
        "discriminator-synthetic",
    ],
)
@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
def test_reviewed_binding_regressions(case: str, backend: str) -> None:
    """Verify independent reviewer inputs against ordinary fixed-main model bytes."""
    source = (
        DATA / "openapi/circular_imports_acyclic.yaml"
        if case == "imports"
        else DATA / "openapi/builtin_type_field_names.yaml"
        if case == "builtin-alias"
        else SOURCE / f"binding/session-{case}.json"
    )
    product, retained = generate_product(
        source.resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            output_model_type=backend,
            input_filename="review.json",
            formatters=[],
            disable_timestamp=True,
            reuse_model=case == "reuse",
        ),
    )
    product.close()
    for artifact in product.artifacts:
        assert_output(
            artifact.content.decode(),
            EXPECTED / "session-review" / case / backend.replace(".", "_") / Path(*artifact.path),
        )
    assert_output(
        "\n".join(diagnostic.code for diagnostic in product.batch.diagnostics),
        EXPECTED / "session-review/no-diagnostics.txt",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")
    if case == "reuse":
        names = {symbol.id: symbol.name for symbol in product.batch.symbols}
        assert_output(
            "\n".join(
                f"{use.id.owner.use_site.pointer}: {names[field.consumer]}.{field.slot.name} <- {field.schema.pointer}"
                for use in product.batch.type_uses
                if use.id.role == "request_body"
                for field in use.members
            )
            + "\n",
            EXPECTED / "session-review/reuse-origins.txt",
        )
    elif case == "parameter-override":
        assert_output(
            "\n".join(
                parameter.use_site.pointer
                for operation in product.batch.operations
                for parameter in operation.parameters
            )
            + "\n",
            EXPECTED / "session-review/parameter-override.txt",
        )
    elif case.startswith("media-"):
        assert_output(
            "\n".join(
                f"{use.id.schema_site.pointer} {use.id.projection} {use.state}"
                for use in product.batch.type_uses
                if use.id.role == "request_body"
            ),
            EXPECTED / f"session-review/{case}.txt",
        )


@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
def test_helper_property_and_item_demands(backend: str) -> None:
    """Resolve processed property/item occurrences after the parser and source lease close."""
    product, retained = generate_product(
        (SOURCE / "binding/session-contract.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            output_model_type=backend,
            input_filename="session.json",
            formatters=["black", "isort"],
            disable_timestamp=True,
        ),
    )
    product.close()
    expected = EXPECTED / "session-review/helper-types.txt"
    pointers = json.loads(expected.read_text())
    selected = tuple(
        use for use in product.batch.type_uses if use.id.role == "schema" and use.id.schema_site.pointer in pointers
    )
    assert_output(
        json.dumps({use.id.schema_site.pointer: type_snapshot(use.type) for use in selected}, indent=2) + "\n", expected
    )
    assert_output(
        "\n".join(
            diagnostic.code for diagnostic in require_type_bindings(product.batch, tuple(use.id for use in selected))
        ),
        EXPECTED / "session-review/no-diagnostics.txt",
    )
    assert_output(product.artifacts[0].content.decode(), EXPECTED / "session" / (backend.replace(".", "_") + ".py"))
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
def test_nested_helper_producers(backend: str) -> None:
    """Keep true, filtered union ordinals, dictionary values and pattern occurrences usable."""
    product, retained = generate_product(
        (SOURCE / "binding/session-helper-producers.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            output_model_type=backend,
            input_filename="helper-producers.json",
            formatters=[],
            disable_timestamp=True,
        ),
    )
    product.close()
    expected = EXPECTED / "session-review/helper-producers.txt"
    wanted = json.loads(expected.read_text())
    uses = tuple(
        use for use in product.batch.type_uses if use.id.role == "schema" and use.id.schema_site.pointer in wanted
    )
    assert_output(
        json.dumps({use.id.schema_site.pointer: type_snapshot(use.type) for use in uses}, indent=2, sort_keys=True)
        + "\n",
        expected,
    )
    assert_output(
        "\n".join(item.code for item in require_type_bindings(product.batch, tuple(use.id for use in uses))),
        EXPECTED / "session-review/no-diagnostics.txt",
    )
    assert_output(product.artifacts[0].content.decode(), EXPECTED / "session-review/helper-producers" / f"{backend}.py")
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
def test_missing_reference_does_not_accept_any_fallback(backend: str) -> None:
    """Preserve the real warning and model bytes while rejecting unresolved required bindings."""
    with pytest.warns(DanglingRefWarning, match=r"Unresolved local \$ref '#/components/schemas/Missing'"):
        product, retained = generate_product(
            (SOURCE / "binding/session-missing-reference.json").resolve(),
            GenerateConfig(
                input_file_type="openapi",
                openapi_scopes=[OpenAPIScope.Api],
                output_model_type=backend,
                input_filename="missing-reference.json",
                formatters=[],
                disable_timestamp=True,
            ),
        )
    product.close()
    assert_output(
        "".join(
            f"{use.id.schema_site.pointer}: "
            + ",".join(item.code for item in require_type_bindings(product.batch, (use.id,)))
            + "\n"
            for use in product.batch.type_uses
            if use.id.role == "request_body"
            or use.id.schema_site.pointer == "/components/schemas/Record/properties/bad"
        ),
        EXPECTED / "session-review/missing-reference.txt",
    )
    assert_output(
        product.artifacts[0].content.decode(), EXPECTED / "session-review/missing-reference" / f"{backend}.py"
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize("scope", [OpenAPIScope.Schemas, OpenAPIScope.Paths, OpenAPIScope.Api])
@pytest.mark.parametrize("collapse", [False, True])
@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
def test_unresolved_reference_after_collapse(scope: OpenAPIScope, collapse: bool, backend: str) -> None:
    """A removed Any fallback never validates its containing model or leaks a phantom origin."""
    with pytest.warns(DanglingRefWarning, match=r"Unresolved local \$ref '#/components/schemas/Missing'"):
        product, retained = generate_product(
            (SOURCE / "binding/session-missing-reference.json").resolve(),
            GenerateConfig(
                input_file_type="openapi",
                openapi_scopes=[scope],
                output_model_type=backend,
                collapse_root_models=collapse,
                input_filename="missing-reference.json",
                formatters=[],
                disable_timestamp=True,
            ),
        )
    product.close()
    facts = {}
    for use in product.batch.type_uses:
        if use.id.role == "request_body" or use.id.schema_site.pointer in {
            "/components/schemas/Record",
            "/components/schemas/Record/properties/bad",
        }:
            facts[use.id.schema_site.pointer] = sorted({
                item.code for item in require_type_bindings(product.batch, (use.id,))
            })
    assert_output(
        json.dumps(facts, indent=2, sort_keys=True) + "\n", EXPECTED / f"session-review/unresolved-{scope.value}.txt"
    )
    assert_output(
        product.artifacts[0].content.decode(),
        EXPECTED / "session-review/unresolved" / f"{scope.value}-{collapse}-{backend}.py",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize("scope", [OpenAPIScope.Schemas, OpenAPIScope.Paths, OpenAPIScope.Api])
@pytest.mark.parametrize("case", ["legacy-discriminator", "legacy-nullable"])
def test_legacy_reference_use_policy(scope: OpenAPIScope, case: str) -> None:
    """A use keeps adopted nullability and cannot substitute a base for an unobserved discriminator union."""
    product, retained = generate_product(
        (SOURCE / f"binding/session-{case}.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[scope],
            input_filename="legacy-use.json",
            formatters=[],
            disable_timestamp=True,
        ),
    )
    product.close()
    use = next(use for use in product.batch.type_uses if use.id.role == "request_body")
    names = {symbol.id: symbol.name for symbol in product.batch.symbols}
    assert_output(
        json.dumps(
            {
                "type": type_snapshot(use.type, names) if use.type is not None else None,
                "diagnostics": [item.code for item in require_type_bindings(product.batch, (use.id,))],
            },
            indent=2,
        )
        + "\n",
        EXPECTED / f"session-review/{case}-{scope.value}.txt",
    )
    assert_output(
        product.artifacts[0].content.decode(), EXPECTED / "session-review/legacy-use" / f"{case}-{scope.value}.py"
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


def test_contentless_api_product() -> None:
    """The actual selected factory permits an accepted operation with zero model artifacts."""
    product, retained = generate_product(
        (SOURCE / "api_scope/contentless.json").resolve(),
        GenerateConfig(input_file_type="openapi", openapi_scopes=[OpenAPIScope.Api], formatters=[]),
    )
    try:
        assert_output(
            json.dumps(
                {
                    "attempt": product.batch.attempt,
                    "operations": len(product.batch.operations),
                    "artifacts": len(product.artifacts),
                    "symbols": len(product.batch.symbols),
                    "diagnostics": [diagnostic.code for diagnostic in product.batch.diagnostics],
                    "retained_graph": retained,
                    "documents": len(product.source_lease.documents()),
                },
                indent=2,
            )
            + "\n",
            EXPECTED / "contentless-product.txt",
        )
    finally:
        product.close()
    with pytest.raises(RuntimeError, match="Source lease is closed"):
        product.source_lease.documents()


@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
@pytest.mark.parametrize("scope", ["paths", "parameters", "api"])
def test_legacy_parameter_field_capture(backend: str, scope: str) -> None:
    """Capture actual primitive parameter fields with and without a parameter model."""
    scopes = [OpenAPIScope.Paths, OpenAPIScope.Parameters] if scope == "parameters" else [OpenAPIScope(scope)]
    product, retained = generate_product(
        (SOURCE / "binding/session-legacy-parameters.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=scopes,
            output_model_type=backend,
            input_filename="parameters.json",
            formatters=[],
            disable_timestamp=True,
        ),
    )
    product.close()
    assert_output(
        "".join(
            f"{use.id.name}: {use.state}; diagnostics="
            f"{','.join(item.code for item in require_type_bindings(product.batch, (use.id,))) or 'none'}; "
            f"{use.id.schema_site.pointer}\n"
            for use in product.batch.type_uses
            if use.id.role == "parameter"
        ),
        EXPECTED / "session-review/legacy-parameters.txt",
    )
    assert_output(
        product.artifacts[0].content.decode(), EXPECTED / "session-review/legacy-parameters" / f"{scope}-{backend}.py"
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
@pytest.mark.parametrize("reuse", [False, True])
@pytest.mark.parametrize("collapse", [False, True])
def test_missing_reference_reuse_preserves_valid_any(backend: str, reuse: bool, collapse: bool) -> None:
    """Actual redirects retain failure on the missing use without poisoning a real Any schema."""
    with pytest.warns(DanglingRefWarning, match=r"Unresolved local \$ref '#/components/schemas/Missing'"):
        product, retained = generate_product(
            (SOURCE / "binding/session-missing-reuse.json").resolve(),
            GenerateConfig(
                input_file_type="openapi",
                openapi_scopes=[OpenAPIScope.Api],
                output_model_type=backend,
                reuse_model=reuse,
                collapse_root_models=collapse,
                input_filename="missing-reuse.json",
                formatters=[],
                disable_timestamp=True,
            ),
        )
    product.close()
    assert_output(
        "".join(
            f"{use.id.owner.use_site.pointer}: "
            f"{','.join(sorted({item.code for item in require_type_bindings(product.batch, (use.id,))})) or 'bound'}\n"
            for use in product.batch.type_uses
            if use.id.role == "request_body"
        ),
        EXPECTED / "session-review/missing-reuse.txt",
    )
    assert_output(
        product.artifacts[0].content.decode(),
        EXPECTED / "session-review/missing-reuse" / f"{reuse}-{collapse}-{backend}.py",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
@pytest.mark.parametrize("case", ["collapsed-reuse", "inherited-array", "directional-array"])
def test_removed_missing_reference_preserves_valid_model(backend: str, case: str) -> None:
    """Keep failed source uses distinct after reuse, inherited copies, and directional inspection."""
    source = "reuse" if case == "collapsed-reuse" else case
    with pytest.warns(DanglingRefWarning, match=r"Unresolved local \$ref '#/components/schemas/Missing'"):
        product, retained = generate_product(
            (SOURCE / f"binding/session-missing-{source}.json").resolve(),
            GenerateConfig(
                input_file_type="openapi",
                openapi_scopes=[OpenAPIScope.Api],
                output_model_type=backend,
                reuse_model=True,
                collapse_reuse_models=True,
                read_only_write_only_model_type="request-response" if case == "directional-array" else "all",
                input_filename="missing-reuse.json",
                formatters=[],
                disable_timestamp=True,
            ),
        )
    product.close()
    assert_output(
        json.dumps(
            {
                use.id.owner.use_site.pointer: sorted({
                    item.code for item in require_type_bindings(product.batch, (use.id,))
                })
                for use in product.batch.type_uses
                if use.id.role == "request_body"
            },
            indent=2,
        )
        + "\n",
        EXPECTED / f"session-review/missing-{case}.txt",
    )
    assert_output(
        product.artifacts[0].content.decode(),
        EXPECTED / "session-review/missing-removed" / f"{case}-{backend}.py",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


def test_collapsed_operation_keeps_final_import_override() -> None:
    """Bind a removed root through the import identity actually emitted by ordinary generation."""
    product, retained = generate_product(
        (SOURCE / "binding/session-import-override.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            input_filename="review.json",
            formatters=[],
            disable_timestamp=True,
            collapse_root_models=True,
            output_datetime_class="AwareDatetime",
            import_overrides={"AwareDatetime": "custom_dates"},
        ),
    )
    product.close()
    assert_output(product.artifacts[0].content.decode(), EXPECTED / "session-review/import-override.py")
    assert_output(
        "\n".join(
            f"{use.type.import_.from_}.{use.type.import_.import_}"
            for use in product.batch.type_uses
            if use.id.role == "request_body" and isinstance(use.type, ImportedType)
        )
        + "\n",
        EXPECTED / "session-review/import-override.txt",
    )
    assert_output(
        "\n".join(diagnostic.code for diagnostic in product.batch.diagnostics),
        EXPECTED / "session-review/no-diagnostics.txt",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize(
    "change",
    json.loads((SOURCE / "binding/session-artifact-changes.json").read_text()),
    ids=operator.itemgetter("name"),
)
def test_required_types_reject_changed_artifacts(change: dict[str, str]) -> None:
    """Validate the actual emitted bytes and propagate failures only when a type is demanded."""
    product, retained = generate_product(
        (SOURCE / "binding/session-contract.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            input_filename="session.json",
            formatters=["black", "isort"],
            disable_timestamp=True,
        ),
        artifact_rewrite=(change["original"], change["replacement"]),
    )
    product.close()
    demanded = tuple(binding.id for binding in product.batch.type_uses if binding.id.role == "request_body")
    diagnostics = require_type_bindings(product.batch, demanded)
    assert_output(
        "".join(code + "\n" for code in dict.fromkeys(diagnostic.code for diagnostic in diagnostics)),
        EXPECTED / "session-review" / (change["expected"] + ".txt"),
    )
    assert_output(
        "\n".join(diagnostic.code for diagnostic in require_type_bindings(product.batch, ())),
        EXPECTED / "session-review/no-diagnostics.txt",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize("case", ["type_alias_mutual_recursive", "type_alias_forward_ref_multiple"])
def test_recursive_collapsed_roots_stay_unbound(case: str) -> None:
    """Report recursive roots without an emitted symbol instead of failing the ordinary attempt."""
    product, retained = generate_product(
        (DATA / f"openapi/{case}.yaml").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            input_filename="recursive.yaml",
            collapse_root_models=True,
            formatters=[],
            disable_timestamp=True,
        ),
    )
    product.close()
    expected = EXPECTED / "session-review/recursive-roots" / case
    for artifact in product.artifacts:
        assert_output(artifact.content.decode(), expected / Path(*artifact.path))
    assert_output(
        "".join(
            f"{use.id.schema_site.pointer}: {use.state} {use.reason}\n"
            for use in product.batch.type_uses
            if use.id.role == "schema"
        ),
        expected / "schema-uses.txt",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


def test_dynamic_builtin_member_names_stay_unbound() -> None:
    """Keep fixed-main bytes while refusing to corroborate a module that spells a dynamic builtin."""
    product, retained = generate_product(
        (SOURCE / "binding/session-dynamic-names.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            input_filename="dynamic-names.json",
            set_default_enum_member=True,
            formatters=[],
            disable_timestamp=True,
        ),
    )
    product.close()
    assert_output(product.artifacts[0].content.decode(), EXPECTED / "session-review/dynamic-names.py")
    assert_output(
        "".join(code + "\n" for code in dict.fromkeys(diagnostic.code for diagnostic in product.batch.diagnostics)),
        EXPECTED / "session-review/unsupported-artifact.txt",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
@pytest.mark.parametrize(
    "change",
    json.loads((SOURCE / "binding/session-selection-changes.json").read_text()),
    ids=operator.itemgetter("name"),
)
def test_artifact_failures_follow_required_models_and_helpers(backend: str, change: dict[str, str]) -> None:
    """Reject an altered producer while retaining an independent model in the same file."""
    product, retained = generate_product(
        (SOURCE / "binding/session-selection.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            output_model_type=backend,
            input_filename="selection.json",
            formatters=[],
            disable_timestamp=True,
        ),
        artifact_rewrite=("bad: str", change["replacement"]),
    )
    product.close()
    uses = tuple(
        use.id
        for use in product.batch.type_uses
        if use.id.role == "request_body" or use.id.schema_site.pointer == "/components/schemas/Bad/properties/bad"
    )
    assert_output(
        "".join(
            f"{use.schema_site.pointer}: "
            f"{','.join(dict.fromkeys(item.code for item in require_type_bindings(product.batch, (use,)))) or 'bound'}"
            "\n"
            for use in uses
        ),
        EXPECTED / "session-review" / (change["expected"] + ".txt"),
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
@pytest.mark.parametrize("mode", ["request-response", "all"])
@pytest.mark.parametrize("reuse", [False, True])
@pytest.mark.parametrize("collapse", [False, True])
def test_directional_types_and_exclusions(backend: str, mode: str, reuse: bool, collapse: bool) -> None:
    """Bind actual direction variants and retain excluded fields in original source order."""
    product, retained = generate_product(
        (SOURCE / "binding/session-directions.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            output_model_type=backend,
            input_filename="directions.json",
            formatters=[],
            disable_timestamp=True,
            read_only_write_only_model_type=mode,
            reuse_model=reuse,
            collapse_root_models=collapse,
        ),
    )
    product.close()
    assert_output(
        product.artifacts[0].content.decode(),
        EXPECTED / "session-review/directions" / f"{backend}-{mode}-{reuse}-{collapse}.py",
    )
    names = {symbol.id: symbol.name for symbol in product.batch.symbols}
    uses = tuple(use for use in product.batch.type_uses if use.id.role in {"request_body", "response_body"})
    assert_output(
        "".join(
            f"{use.id.role} {names[use.type.symbol]}\n"
            + "".join(
                f"  {member.wire_name}: {member.exclusion or 'emitted'} <- {member.schema.pointer}\n"
                for member in use.members
            )
            for use in uses
            if isinstance(use.type, GeneratedSymbolType)
        ),
        EXPECTED / "session-review" / f"directions-{mode}.txt",
    )
    assert_output(
        "\n".join(
            diagnostic.code for diagnostic in require_type_bindings(product.batch, tuple(use.id for use in uses))
        ),
        EXPECTED / "session-review/no-diagnostics.txt",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
@pytest.mark.parametrize("mode", ["request-response", "all"])
@pytest.mark.parametrize("reuse", [False, True])
@pytest.mark.parametrize("collapse", [False, True])
def test_directional_producer_origins(backend: str, mode: str, reuse: bool, collapse: bool) -> None:
    """Preserve original wire facts through canonical reuse, inheritance, and root variants."""
    product, retained = generate_product(
        (SOURCE / "binding/session-directional-producers.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            output_model_type=backend,
            input_filename="directional-producers.json",
            formatters=[],
            disable_timestamp=True,
            read_only_write_only_model_type=mode,
            reuse_model=reuse,
            collapse_root_models=collapse,
        ),
    )
    product.close()
    assert_output(
        product.artifacts[0].content.decode(),
        EXPECTED / "session-review/directional-producers" / f"{backend}-{mode}-{reuse}-{collapse}.py",
    )
    uses = tuple(use for use in product.batch.type_uses if use.id.role in {"request_body", "response_body"})
    assert_output(
        "".join(
            f"{use.id.owner.use_site.pointer} {use.id.role}\n"
            + "".join(
                f"  {member.member_kind} {member.wire_name or '-'}: {member.exclusion or 'emitted'} <- "
                f"{member.schema.pointer if member.schema else 'unknown'}\n"
                for member in use.members
            )
            for use in uses
        ),
        EXPECTED / "session-review/directional-producers.txt",
    )
    assert_output(
        "\n".join(
            diagnostic.code for diagnostic in require_type_bindings(product.batch, tuple(use.id for use in uses))
        ),
        EXPECTED / "session-review/no-diagnostics.txt",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
@pytest.mark.parametrize("scope", ["schemas", "paths", "api"])
def test_legacy_scope_type_demands(backend: str, scope: str) -> None:
    """Use actual emitted components under old scopes and diagnose only missing demanded uses."""
    product, retained = generate_product(
        (SOURCE / "binding/session-legacy.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope(scope)],
            output_model_type=backend,
            input_filename="legacy.json",
            formatters=[],
            disable_timestamp=True,
        ),
    )
    product.close()
    assert_output(product.artifacts[0].content.decode(), EXPECTED / "session-review/legacy" / f"{backend}-{scope}.py")
    assert_output(
        "".join(
            f"{use.id.owner.use_site.pointer} {use.id.role}: "
            f"{','.join(item.code for item in require_type_bindings(product.batch, (use.id,))) or use.state}\n"
            for use in product.batch.type_uses
            if use.id.role in {"request_body", "response_body"}
        ),
        EXPECTED / "session-review" / f"legacy-{scope}.txt",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


def test_unselected_legacy_declaration_boundaries() -> None:
    """Keep unresolved wire declarations out of selected schema generation without fetching them."""
    product, retained = generate_product(
        (SOURCE / "binding/session-legacy-source.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Schemas],
            input_filename="legacy-source.json",
            formatters=[],
            disable_timestamp=True,
        ),
    )
    product.close()
    assert_output(
        "".join(
            f"{operation.id.use_site.pointer}: "
            + (",".join(parameter.name for parameter in operation.parameters) or "-")
            + "\n"
            for operation in product.batch.operations
        )
        + "".join(
            f"{diagnostic.code}: " + ",".join(source.pointer for source in diagnostic.source_locations) + "\n"
            for diagnostic in product.batch.diagnostics
        ),
        EXPECTED / "session-review/legacy-source.txt",
    )
    assert_output(product.artifacts[0].content.decode(), EXPECTED / "session-review/legacy-source.py")
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
@pytest.mark.parametrize("collapse", [False, True])
def test_legacy_media_producer_types(backend: str, *, collapse: bool) -> None:
    """Bind real legacy body/response producers and both views of each item stream."""
    product, retained = generate_product(
        (SOURCE / "binding/session-legacy-media.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Paths],
            output_model_type=backend,
            input_filename="legacy-media.json",
            collapse_root_models=collapse,
            formatters=[],
            disable_timestamp=True,
        ),
    )
    for location in (field.schema for field in product.batch.fields if field.schema is not None):
        product.source_lease.borrow(location)
    product.close()
    uses = tuple(use for use in product.batch.type_uses if use.id.role in {"request_body", "response_body"})
    assert_output(
        "\n".join(error.code for error in require_type_bindings(product.batch, tuple(use.id for use in uses))),
        EXPECTED / "session-review/no-diagnostics.txt",
    )
    names = {symbol.id: symbol.name for symbol in product.batch.symbols}
    assert_output(
        json.dumps(
            {
                use.id.schema_site.pointer: type_snapshot(use.type, names)
                for use in uses
                if use.id.projection == "value" and use.id.media == "application/json"
            },
            indent=2,
        )
        + "\n",
        EXPECTED / "session-review/legacy-media" / ("literals.txt" if backend == "typing.TypedDict" else "types.txt"),
    )
    assert_output(
        product.artifacts[0].content.decode(),
        EXPECTED / "session-review/legacy-media" / f"{backend}-{collapse}.py",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
@pytest.mark.parametrize(("case", "status_names"), [("shared", False), ("shared", True), ("external", False)])
def test_legacy_media_use_identity(backend: str, case: str, *, status_names: bool) -> None:
    """Keep each real body/status return and borrowed external declaration independent."""
    product, retained = generate_product(
        (SOURCE / f"binding/session-legacy-{case}.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Paths],
            output_model_type=backend,
            input_filename=f"legacy-{case}.json",
            use_status_code_in_response_name=status_names,
            formatters=[],
            disable_timestamp=True,
        ),
    )
    for location in (field.schema for field in product.batch.fields if field.schema is not None):
        product.source_lease.borrow(location)
    uses = tuple(use for use in product.batch.type_uses if use.id.role in {"request_body", "response_body"})
    for location in (use.schema for use in uses if use.schema is not None):
        product.source_lease.borrow(location)
    if case == "external":
        responses = product.batch.operations[0].responses
        for response in responses:
            product.source_lease.borrow(response.declaration.location)
        assert_output(
            json.dumps(
                [
                    [response.name, response.declaration.location.pointer, type_snapshot(response.facts)]
                    for response in responses
                ],
                indent=2,
            )
            + "\n",
            EXPECTED / "session-review/legacy-external/responses.txt",
        )
    product.close()
    expected = EXPECTED / f"session-review/legacy-{case}"
    assert_output(
        "\n".join(error.code for error in require_type_bindings(product.batch, tuple(use.id for use in uses))),
        EXPECTED / "session-review/no-diagnostics.txt",
    )
    names = {symbol.id: symbol.name for symbol in product.batch.symbols}
    assert_output(
        json.dumps(
            [
                {
                    "use": use.id.schema_site.pointer,
                    "projection": use.id.projection,
                    "type": type_snapshot(use.type, names),
                    "schema": use.schema.pointer if use.schema is not None else None,
                    "members": [
                        [names[member.consumer], member.wire_name, member.schema.pointer if member.schema else None]
                        for member in use.members
                    ],
                }
                for use in uses
            ],
            indent=2,
        )
        + "\n",
        expected / f"types-{status_names}.txt",
    )
    assert_output(product.artifacts[0].content.decode(), expected / f"{backend}-{status_names}.py")
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
def test_legacy_shared_stream_types(backend: str) -> None:
    """Bind shared item declarations to actual independent roots and item models."""
    product, retained = generate_product(
        (SOURCE / "binding/session-legacy-shared-stream.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Paths],
            output_model_type=backend,
            input_filename="legacy-shared-stream.json",
            use_status_code_in_response_name=True,
            formatters=[],
            disable_timestamp=True,
        ),
    )
    for location in (field.schema for field in product.batch.fields if field.schema is not None):
        product.source_lease.borrow(location)
    product.close()
    uses = tuple(use.id for use in product.batch.type_uses if use.id.role in {"request_body", "response_body"})
    assert_output(
        "\n".join(error.code for error in require_type_bindings(product.batch, uses)),
        EXPECTED / "session-review/no-diagnostics.txt",
    )
    assert_output(
        product.artifacts[0].content.decode(), EXPECTED / "session-review/legacy-shared-stream" / f"{backend}-paths.py"
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize("corrupt_root", [False, True])
def test_legacy_shared_stream_artifact_dependencies(*, corrupt_root: bool) -> None:
    """A corrupt stream root or item invalidates only its actual operation's two projections."""
    product, retained = generate_product(
        (SOURCE / "binding/session-legacy-shared-stream.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Paths],
            input_filename="legacy-shared-stream.json",
            use_status_code_in_response_name=True,
            formatters=[],
            disable_timestamp=True,
        ),
        artifact_rewrite=(
            ("root: list[BPostRequestItem]", "root: list[str]")
            if corrupt_root
            else (
                "class BPostRequestItem(BaseModel):\n    value: int",
                "class BPostRequestItem(BaseModel):\n    value: str",
            )
        ),
    )
    product.close()
    assert_output(
        "".join(
            f"{use.id.schema_site.pointer} {use.id.projection}: "
            + (
                ",".join(dict.fromkeys(error.code for error in require_type_bindings(product.batch, (use.id,))))
                or "bound"
            )
            + "\n"
            for use in product.batch.type_uses
            if use.id.role in {"request_body", "response_body"}
        ),
        EXPECTED / "session-review/legacy-shared-stream/corrupt-artifact.txt",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
@pytest.mark.parametrize("scope", ["paths", "api"])
def test_integer_yaml_response_status(backend: str, scope: str) -> None:
    """Borrow actual integer YAML status keys through their unambiguous textual pointers."""
    product, retained = generate_product(
        (SOURCE / "binding/session-integer-status.yaml").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope(scope)],
            output_model_type=backend,
            input_filename="integer-status.yaml",
            formatters=[],
            disable_timestamp=True,
        ),
    )
    for operation in product.batch.operations:
        for response in operation.responses:
            product.source_lease.borrow(response.declaration.location)
    uses = tuple(use for use in product.batch.type_uses if use.id.role == "response_body")
    for location in (use.schema for use in uses if use.schema is not None):
        product.source_lease.borrow(location)
    product.close()
    assert_output(
        "\n".join(error.code for error in require_type_bindings(product.batch, tuple(use.id for use in uses))),
        EXPECTED / "session-review/no-diagnostics.txt",
    )
    assert_output(
        "".join(
            f"{use.id.status} {use.id.projection} {use.schema.pointer if use.schema else 'missing'}\n" for use in uses
        ),
        EXPECTED / "session-review/integer-status/uses.txt",
    )
    assert_output(
        product.artifacts[0].content.decode(), EXPECTED / "session-review/integer-status" / f"{backend}-{scope}.py"
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


def test_ambiguous_yaml_response_status() -> None:
    """Reject competing string/integer source identities instead of binding the wrong declaration."""
    from datamodel_code_generator._generation_contract import BindingCaptureError

    with pytest.raises(BindingCaptureError, match="Source pointer is ambiguous between string and integer keys"):
        generate_product(
            (SOURCE / "binding/session-ambiguous-status.yaml").resolve(),
            GenerateConfig(
                input_file_type="openapi", openapi_scopes=[OpenAPIScope.Paths], formatters=[], disable_timestamp=True
            ),
        )


def test_artifact_imported_base_redefinition() -> None:
    """Reject replacement of a proven imported base before its generated subclass."""
    product, retained = generate_product(
        (SOURCE / "binding/session-selection.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            input_filename="selection.json",
            formatters=[],
            disable_timestamp=True,
        ),
        artifact_rewrite=(
            "from pydantic import BaseModel",
            "from pydantic import BaseModel\n\ndef BaseModel():\n    return 1\n",
        ),
    )
    product.close()
    assert_output(
        "".join(
            code + "\n"
            for code in dict.fromkeys(
                item.code
                for use in product.batch.type_uses
                if use.id.role == "request_body"
                for item in require_type_bindings(product.batch, (use.id,))
            )
        ),
        EXPECTED / "session-review/unsupported-artifact.txt",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize("case", sorted(path.stem for path in (EXPECTED / "session/protocol").glob("*.txt")))
def test_session_protocol_ownership_and_transfer(case: str) -> None:
    """Reject foreign or repeated transfers and release every real attempt's graph."""
    actual = session_protocol_failure((SOURCE / "observation.json").resolve(), case)
    assert_output(json.dumps(actual, indent=2) + "\n", EXPECTED / "session/protocol" / f"{case}.txt")


@pytest.mark.parametrize("case", sorted(path.stem for path in (EXPECTED / "session/artifacts").glob("*.txt")))
def test_product_artifact_integrity(case: str) -> None:
    """Demand final types only from unique, present, decodable, well-formed ordinary artifacts."""
    product, retained = generate_product(
        (SOURCE / "binding/session-selection.json").resolve(),
        GenerateConfig(
            input_file_type="openapi", openapi_scopes=[OpenAPIScope.Api], formatters=[], disable_timestamp=True
        ),
        artifact_failure=case,
    )
    product.close()
    codes = sorted({
        item.code
        for use in product.batch.type_uses
        if use.id.role == "request_body"
        for item in require_type_bindings(product.batch, (use.id,))
    })
    assert_output(json.dumps(codes, indent=2) + "\n", EXPECTED / "session/artifacts" / f"{case}.txt")
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


def test_product_artifact_validation_exception() -> None:
    """Propagate an invalid writer encoding after model disposal instead of accepting a product."""
    with pytest.raises(LookupError, match="unknown encoding: unknown-artifact-encoding"):
        generate_product(
            (SOURCE / "observation.json").resolve(),
            GenerateConfig(input_file_type="openapi", formatters=[], disable_timestamp=True),
            artifact_failure="unknown_encoding",
        )


@pytest.mark.parametrize("case", json.loads((SOURCE / "binding/batch-failures.json").read_text()))
def test_invalid_batch_demand(case: str) -> None:
    """Reject corrupt accepted identities and keep unrelated failures outside selected demands."""
    from tests.data.python.binding_batch_failures import corrupt_batch

    product, retained = generate_product(
        (SOURCE / "binding/session-legacy.json").resolve(),
        GenerateConfig(
            input_file_type="openapi", openapi_scopes=[OpenAPIScope.Api], formatters=[], disable_timestamp=True
        ),
    )
    product.close()
    batch, requested = corrupt_batch(product.batch, case)
    assert_output(
        "".join(
            f"{code}\n"
            for code in sorted({error.code for error in require_type_bindings(batch, (requested, requested))})
        ),
        EXPECTED / "session/batch-failures" / f"{case}.txt",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize("constructor_failure", [False, True])
def test_session_release_failure(constructor_failure: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing ledger cleanup still releases sources and preserves any constructor error."""
    source = (SOURCE / "observation.json").resolve()
    session = OpenAPIGenerationSession(
        output=Path("models.py"), model_package="models", root_selector_document=source.as_uri()
    )
    config = _prepare_generate_facade_config(
        GenerateConfig(
            input_file_type="openapi", formatters=[], type_mappings=["invalid"] if constructor_failure else []
        )
    )
    leases: list[SourceLease] = []
    real_close = BindingLedger.close
    lease_close = SourceLease.close

    def failed_release(ledger: BindingLedger) -> None:
        real_close(ledger)
        message = "ledger release failed"
        raise RuntimeError(message)

    def observe_release(lease: SourceLease) -> None:
        leases.append(lease)
        lease_close(lease)

    if not constructor_failure:
        _run_generation(source, config, Path.cwd(), use_output_cwd=False, capture=session)
        leases.append(session.source_lease)
    with monkeypatch.context() as fault:
        fault.setattr(BindingLedger, "close", failed_release)
        fault.setattr(SourceLease, "close", observe_release)
        if constructor_failure:
            with pytest.raises(ValueError, match="Invalid type mapping format: 'invalid'"):
                _run_generation(source, config, Path.cwd(), use_output_cwd=False, capture=session)
        else:
            with pytest.raises(RuntimeError, match="ledger release failed"):
                session.close()
    session.close()
    assert_output(str(len(set(leases))) + "\n", EXPECTED / "one-source-lease.txt")
    for lease in leases:
        with pytest.raises(RuntimeError, match="Source lease is closed"):
            lease.documents()


@pytest.mark.parametrize("case", ["freeze", "multiple", "reentrant"])
def test_session_cleanup_failure_identity(case: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never replace the original failure while closing multiple or reentrant attempt owners."""
    from tests.data.python.generation_session_inputs import session_cleanup_failures

    assert_output(
        json.dumps(session_cleanup_failures((SOURCE / "observation.json").resolve(), case, monkeypatch), indent=2)
        + "\n",
        EXPECTED / "session/cleanup" / f"{case}.txt",
    )


def test_session_cyclic_metadata_cleanup() -> None:
    """Reject recursive source metadata and release the real failed attempt without fault injection."""
    import yaml

    from tests.data.python.generation_session_inputs import run_generation_session

    source = (SOURCE / "binding/session-cyclic-metadata.yaml").resolve()
    actual, retained = run_generation_session(source, document=yaml.safe_load(source.read_text()))
    assert_output(
        json.dumps(
            {
                "error": actual["error"],
                "batch": actual["batch"],
                "retained_parsers": actual["retained_parsers"],
                "retained_graph": retained,
            },
            indent=2,
        )
        + "\n",
        EXPECTED / "session/cleanup/cyclic-metadata.txt",
    )


def test_parser_dispose_preserves_primary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ordinary disposal errors survive sidecar failures and every remaining owner closes."""
    source = (SOURCE / "observation.json").resolve()
    session = OpenAPIGenerationSession(
        output=Path("models.py"), model_package="models", root_selector_document=source.as_uri()
    )
    ordinary_dispose = OpenAPIParser.dispose
    ledger_close = BindingLedger.close
    resolver_close = BindingResolverMixin.close_capture
    released: list[int] = []

    def failed_dispose(parser: OpenAPIParser) -> None:
        ordinary_dispose(parser)
        message = "ordinary dispose failed"
        raise ValueError(message)

    def failed_ledger(ledger: BindingLedger) -> None:
        ledger_close(ledger)
        message = "ledger release failed"
        raise RuntimeError(message)

    def observe_resolver(resolver: BindingResolverMixin) -> None:
        resolver_close(resolver)
        released.append(len(resolver.resolutions) + len(resolver.default_resolutions))

    with monkeypatch.context() as fault:
        fault.setattr(OpenAPIParser, "dispose", failed_dispose)
        fault.setattr(BindingLedger, "close", failed_ledger)
        fault.setattr(BindingResolverMixin, "close_capture", observe_resolver)
        with pytest.raises(ValueError, match="ordinary dispose failed"):
            _run_generation(
                source,
                _prepare_generate_facade_config(GenerateConfig(input_file_type="openapi", formatters=[])),
                Path.cwd(),
                use_output_cwd=False,
                capture=session,
            )
    assert_output("\n".join(map(str, released)) + "\n", EXPECTED / "no-retained-graph.txt")
