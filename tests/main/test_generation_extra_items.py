"""Preserve final imports and transitive dependencies of TypedDict extra values."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datamodel_code_generator import DataModelType, GenerateConfig, OpenAPIScope
from datamodel_code_generator._openapi_type_binding import require_type_bindings
from tests.conftest import assert_output
from tests.data.python.generation_session_inputs import generate_product

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/binding"
EXPECTED = DATA / "expected/main/generation_platform/session-review/extra-items"


@pytest.mark.parametrize("remapped", [False, True])
@pytest.mark.parametrize("standard", [False, True])
@pytest.mark.parametrize("corrupt_child", [False, True])
def test_extra_item_imports_and_dependencies(*, remapped: bool, standard: bool, corrupt_child: bool) -> None:
    """Use accepted module identities and reject direct, nested, and recursive broken dependencies."""
    product, retained = generate_product(
        (SOURCE / "session-extra-items.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            output_model_type=DataModelType.TypingTypedDict,
            input_filename="extra-items.json",
            use_standard_primitive_types=True,
            use_standard_collections=standard,
            use_closed_typed_dict=True,
            import_overrides={"UUID": "custom_uuid"} if remapped else None,
            formatters=[],
            disable_timestamp=True,
        ),
        artifact_rewrite=("bad: str", "bad: int") if corrupt_child else None,
    )
    product.close()
    imported = next(symbol for symbol in product.batch.symbols if symbol.name == "Imported").facts.extra_items.import_
    assert_output(f"{imported.from_}.{imported.import_}\n", EXPECTED / f"import-{remapped}.txt")
    rejected = {
        operation.path: bool(
            require_type_bindings(
                product.batch,
                tuple(use.id for use in product.batch.type_uses if use.id.owner == operation.id),
            )
        )
        for operation in product.batch.operations
    }
    assert_output(
        json.dumps({**rejected, "retained_graph": retained}, indent=2) + "\n",
        EXPECTED / f"demands-{corrupt_child}.txt",
    )
    if not corrupt_child:
        assert_output(product.artifacts[0].content.decode(), EXPECTED / f"{standard}-{remapped}.py")
