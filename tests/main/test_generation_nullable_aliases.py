"""Keep nullable alias values distinct from RootModel instances and nullable container items."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datamodel_code_generator import DataModelType, GenerateConfig, OpenAPIScope
from datamodel_code_generator._openapi_type_binding import require_type_bindings
from datamodel_code_generator.format import PythonVersion
from tests.conftest import assert_output
from tests.data.python.generation_session_inputs import generate_product

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/binding"
EXPECTED = DATA / "expected/main/generation_platform"


@pytest.mark.parametrize(
    ("backend", "root_alias"),
    [*((backend, False) for backend in DataModelType), (DataModelType.PydanticV2BaseModel, True)],
)
@pytest.mark.parametrize("collapse", [False, True])
@pytest.mark.parametrize("annotated", [False, True])
@pytest.mark.parametrize("remapped", [False, True])
def test_nullable_alias_provenance(
    backend: DataModelType, *, root_alias: bool, collapse: bool, annotated: bool, remapped: bool
) -> None:
    """Compare literal omission proofs and fixed-main bytes through the real driver and disposal."""
    product, retained = generate_product(
        (SOURCE / "session-nullable-aliases.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            output_model_type=backend,
            input_filename="nullable-aliases.json",
            collapse_root_models=collapse,
            use_annotated=annotated,
            field_constraints=annotated,
            use_root_model_type_alias=root_alias,
            import_overrides={"Any": "typing_extensions"} if remapped else None,
            formatters=[],
            disable_timestamp=True,
        ),
    )
    product.close()
    variant = "RootModelAlias" if root_alias else backend.name
    assert_output(
        product.artifacts[0].content.decode(),
        EXPECTED / "session-review/nullable-aliases" / f"{variant}-{collapse}-{annotated}-{remapped}.py",
    )
    names = {symbol.id: symbol.name for symbol in product.batch.symbols}
    fields = sorted(
        (field for field in product.batch.fields if names[field.consumer] == "Model"),
        key=lambda field: field.slot.name,
    )
    assert_output(
        "".join(
            f"{field.slot.name}: {provenance.emitted_default} / {provenance.origin} / "
            f"{provenance.annotation_null_origin}\n"
            for field in fields
            for provenance in (field.model_facts.none_default_provenance,)
        ),
        EXPECTED / "session-review/nullable-aliases" / f"provenance-{variant}-{collapse}.txt",
    )
    assert_output(
        "\n".join(
            error.code
            for error in require_type_bindings(product.batch, tuple(use.id for use in product.batch.type_uses))
        ),
        EXPECTED / "session-review/no-diagnostics.txt",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize("replacement", json.loads((SOURCE / "root-alias-artifacts.json").read_text()))
def test_root_alias_rejects_changed_wrapper(replacement: str) -> None:
    """Reject removed, replaced, or malformed RootModel applications after ordinary emission."""
    product, retained = generate_product(
        (SOURCE / "session-nullable-aliases.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            use_root_model_type_alias=True,
            formatters=[],
            disable_timestamp=True,
        ),
        artifact_rewrite=("Nullable = RootModel[str | None]", replacement),
    )
    product.close()
    errors = require_type_bindings(product.batch, tuple(use.id for use in product.batch.type_uses))
    assert_output(
        "\n".join(sorted({error.code for error in errors})) + "\n",
        EXPECTED / "session-review/unsupported-artifact.txt",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize("backend", list(DataModelType))
@pytest.mark.parametrize("version", [PythonVersion.PY_310, PythonVersion.PY_312])
@pytest.mark.parametrize("annotated", [False, True])
def test_final_type_alias_forms(backend: DataModelType, version: PythonVersion, *, annotated: bool) -> None:
    """Verify builtin aliases before and after PEP 695 without the host AST grammar or live graph."""
    product, retained = generate_product(
        (SOURCE / "session-nullable-aliases.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            output_model_type=backend,
            use_type_alias=True,
            target_python_version=version,
            use_annotated=annotated,
            field_constraints=annotated,
            input_filename="alias-forms.json",
            formatters=[],
            disable_timestamp=True,
        ),
    )
    product.close()
    assert_output(
        product.artifacts[0].content.decode(),
        EXPECTED / "session-review/alias-forms" / f"{backend.name}-{version.value}-{annotated}.py",
    )
    assert_output(
        "\n".join(
            error.code
            for error in require_type_bindings(product.batch, tuple(use.id for use in product.batch.type_uses))
        ),
        EXPECTED / "session-review/no-diagnostics.txt",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize("replacement", json.loads((SOURCE / "type-alias-artifacts.json").read_text()))
def test_type_alias_rejects_changed_value(replacement: dict[str, str]) -> None:
    """Reject absent values and TypeAliasType calls with a different name or arity."""
    product, retained = generate_product(
        (SOURCE / "session-nullable-aliases.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            use_type_alias=True,
            target_python_version=PythonVersion.PY_310,
            formatters=[],
            disable_timestamp=True,
        ),
        artifact_rewrite=('Nullable = TypeAliasType("Nullable", str | None)', replacement["replacement"]),
    )
    product.close()
    errors = require_type_bindings(product.batch, tuple(use.id for use in product.batch.type_uses))
    assert_output(
        "\n".join(sorted({error.code for error in errors})) + "\n",
        EXPECTED / "session-review" / replacement["expected"],
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")
