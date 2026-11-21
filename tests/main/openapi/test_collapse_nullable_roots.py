"""Keep the null of a root model that admits null where --collapse-root-models inlines it."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import msgspec
import pydantic
import pytest

from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, OPEN_API_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT = OPEN_API_DATA_PATH / "collapse_nullable_roots.yaml"
PAYLOADS = DATA_PATH / "payloads" / "collapse_nullable_roots.json"
OPTION_SETS = {
    "released": (),
    "keep": ("--keep-reference-null-on-collapsed-root-models",),
    "keep_parent": (
        "--keep-reference-null-on-collapsed-root-models",
        *("--collapse-root-models-name-strategy", "parent", "--field-type-collision-strategy", "rename-type"),
    ),
    "keep_child": ("--keep-reference-null-on-collapsed-root-models", "--collapse-root-models-name-strategy", "child"),
}


@pytest.mark.cli_doc(
    options=["--keep-reference-null-on-collapsed-root-models"],
    option_description="""Keep the null of a root model that admits null where `--collapse-root-models` inlines it.

An OpenAPI 3.0 schema with `nullable: true` beside its `type` admits null, and its root model writes it
(`RootModel[list[str] | None]`, `Names: TypeAlias = list[str] | None`), but where `--collapse-root-models` inlines that
root model into a field, an array item or a map value, the inlined type is the root's type without the null, so the
field rejects the null the root model accepts. With this option the inlined type keeps it (`names: list[str] | None`),
also where a name strategy gives the field the model a nullable root names.""",
    input_schema="openapi/collapse_nullable_roots.yaml",
    cli_args=["--collapse-root-models", "--keep-reference-null-on-collapsed-root-models"],
    golden_output="main/openapi/collapse_nullable_roots/collapse_nullable_roots[pydantic_v2-keep].py",
    comparison_output="main/openapi/collapse_nullable_roots/collapse_nullable_roots[pydantic_v2-released].py",
)
@pytest.mark.parametrize("option_set", OPTION_SETS)
@pytest.mark.parametrize(
    "output_model_type", ["pydantic_v2.BaseModel", "msgspec.Struct"], ids=["pydantic_v2", "msgspec"]
)
def test_collapse_nullable_roots(output_file: Path, output_model_type: str, option_set: str) -> None:
    """Keep the null of a nullable root at every site collapse inlines it under the option (#5426).

    Without it a field, an array item and a map value that name an OpenAPI 3.0 `nullable: true` root, also through a
    root that names it, reject null, as in 0.89.0. With it they admit null, and so does a field that a name strategy
    gives the model a nullable root names, whether its first holder or, under the parent strategy, a later holder
    takes that model. Every non-null valid payload decodes and every invalid one is rejected under every option set.
    """
    run_main_and_assert(
        input_path=INPUT,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            *("--output-model-type", output_model_type, "--collapse-root-models"),
            *("--formatters", "builtin", "--disable-timestamp"),
            *OPTION_SETS[option_set],
        ],
    )
    payloads = json.loads(PAYLOADS.read_text())
    documents = [
        *((f"valid {json.dumps(payload)}", payload) for payload in payloads["valid"]),
        *((f"invalid {json.dumps(payload)}", payload) for payload in payloads["invalid"]),
    ]
    with _generated_model(output_file, "collapse_nullable_roots_model", "Holder") as model:
        assert_output(
            "".join(f"{label}: {_verdict(model, document)}\n" for label, document in documents), name="payloads"
        )


ANNOTATED_STRING_DOCUMENTS = (
    {"codes": ["abc", None], "byName": {"k": None, "l": "abcde"}},
    {"codes": ["abcdef"]},
    {"byName": {"k": "abcdef"}},
)


def test_collapse_nullable_annotated_string_roots(output_file: Path) -> None:
    """Keep the null of a constrained nullable string root that collapse inlines as an Annotated item or value (#6505).

    Under --use-annotated with --collapse-constrained-root-models-as-annotated, an array item and a map value that name
    an OpenAPI 3.0 `nullable: true` string root with `maxLength` take its Annotated string type; with
    --keep-reference-null-on-collapsed-root-models they admit null beside it instead of failing generation, and they
    keep rejecting a string longer than `maxLength`.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "collapse_nullable_annotated_string_roots.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            *("--output-model-type", "pydantic_v2.BaseModel", "--use-annotated", "--collapse-root-models"),
            *("--keep-reference-null-on-collapsed-root-models", "--collapse-constrained-root-models-as-annotated"),
            *("--formatters", "builtin", "--disable-timestamp"),
        ],
    )
    with _generated_model(output_file, "collapse_nullable_annotated_string_roots_model", "Holder") as model:
        assert_output(
            "".join(
                f"{json.dumps(document)}: {_verdict(model, document)}\n" for document in ANNOTATED_STRING_DOCUMENTS
            ),
            name="payloads",
        )


def _verdict(model: Any, document: object) -> str:
    """Return whether the generated model decodes a document."""
    try:
        if isinstance(model, type) and issubclass(model, msgspec.Struct):
            msgspec.convert(document, model)
        else:
            model.model_validate(document)
    except (msgspec.ValidationError, pydantic.ValidationError):
        return "rejected"
    return "accepted"
