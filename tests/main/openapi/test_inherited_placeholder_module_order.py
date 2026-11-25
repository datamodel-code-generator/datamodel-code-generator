"""A subclass listed before its parent, in a module processed first, that restates a property its parent requires.

The parent requires a property that only its own base declares, so its field is a placeholder until its module is
processed. A subclass in a module processed before the parent's takes the field through the parent's bases, as it does
when the parent is listed first: ``x: int``, not ``x: None``. A TypedDict copies the parent's placeholder and declares
the keys of its bases itself, as 0.102.0 does, unless --keep-bases-of-typed-dicts-listed-before-their-parents is set.
"""

from __future__ import annotations

import json
from contextlib import nullcontext
from itertools import permutations
from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import DATA_PATH, JSON_SCHEMA_DATA_PATH, OPEN_API_DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

DATA = OPEN_API_DATA_PATH / "inherited_placeholder_module_order"
KEEP_BASES = "--keep-bases-of-typed-dicts-listed-before-their-parents"
KEEP_BASES_OFF = "--no-keep-bases-of-typed-dicts-listed-before-their-parents"
MSGSPEC_ARGS = ("--output-model-type", "msgspec.Struct")
UNTAGGED_UNION = "msgspec cannot decode the discriminated union TagHolder.pet"
"""The warning of the union of shapes.yaml, whose Struct types msgspec decodes only with --use-msgspec-tagged-unions."""
UNTAKEN_DECIMAL_BOUND = "msgspec.Meta does not take ge on Decimal; MetaChild.x"
"""The warning of the bound of MetaChild.x, which msgspec leaves out of the Meta of a Decimal."""
MODEL_ARGS = {
    "pydantic": ("--output-model-type", "pydantic_v2.BaseModel"),
    "typeddict": ("--output-model-type", "typing.TypedDict"),
    "typeddict-kept-bases": ("--output-model-type", "typing.TypedDict", KEEP_BASES),
}
NAMINGS = {
    "child-first-modules": ("z.Child", "m.Mid", "a.Base"),
    "parent-first-modules": ("a.Child", "m.Mid", "z.Base"),
    "packages": ("com.example.pets.Child", "com.example.Mid", "com.example.Base"),
    "base-child-mid-modules": ("m.Child", "a.Mid", "z.Base"),
    "child-base-mid-modules": ("z.Child", "a.Mid", "m.Base"),
}
"""The schema names of Child, Mid and Base, named by the order their modules are processed in."""
ROLES = ("Child", "Mid", "Base")


def _write_schema(tmp_path: Path, naming: str, order: tuple[str, ...]) -> Path:
    """Write the three schemas under the names of ``naming``, listed in ``order``, and return the document's path."""
    names = dict(zip(ROLES, NAMINGS[naming], strict=True))
    schemas = {
        "Child": {
            "allOf": [
                {"$ref": f"#/components/schemas/{names['Mid']}"},
                {"properties": {"x": {"description": "The child's x."}, "c": {"type": "string"}}},
            ]
        },
        "Mid": {
            "allOf": [
                {"$ref": f"#/components/schemas/{names['Base']}"},
                {"required": ["x"], "properties": {"m": {"type": "string"}}},
            ]
        },
        "Base": {"type": "object", "properties": {"x": {"type": "integer"}}},
    }
    document = {
        "openapi": "3.0.3",
        "info": {"title": "t", "version": "1"},
        "paths": {},
        "components": {"schemas": {names[role]: schemas[role] for role in order}},
    }
    path = tmp_path / "schema.yaml"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


@pytest.mark.parametrize("model", MODEL_ARGS)
@pytest.mark.parametrize("order", ["-".join(order).lower() for order in permutations(ROLES)])
@pytest.mark.parametrize("naming", NAMINGS)
def test_subclass_takes_the_inherited_type_in_every_definition_and_module_order(
    tmp_path: Path, output_dir: Path, naming: str, order: str, model: str
) -> None:
    """Child gives x the type Base declares for every order of the schemas and of their modules.

    Each naming yields one output for every definition order. The TypedDict without the option yields two where
    Child's module is processed before Mid's: it declares the keys of Mid itself where Child is listed before Mid.
    """
    run_main_and_assert(
        input_path=_write_schema(tmp_path, naming, tuple(role.title() for role in order.split("-"))),
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[*MODEL_ARGS[model], "--formatters", "builtin"],
    )


@pytest.mark.parametrize(
    "override",
    [
        pytest.param({"Base.x": "decimal.Decimal"}, id="base-x"),
        pytest.param({"Mid.x": "decimal.Decimal"}, id="mid-x"),
        pytest.param({"Child.x": "decimal.Decimal"}, id="child-x"),
    ],
)
@pytest.mark.parametrize("naming", ["base-child-mid-modules", "child-base-mid-modules"])
def test_subclass_takes_the_inherited_type_under_class_scoped_type_overrides(
    tmp_path: Path, output_dir: Path, naming: str, override: dict[str, str]
) -> None:
    """Child, listed first, reads Base.x at the stage of its own module, before or after Base's module runs.

    With Base's module processed before Child's, Child takes the type a ``Base.x`` override gives; after it, the type
    Base declares, as Mid's own override does not reach Child.
    """
    run_main_and_assert(
        input_path=_write_schema(tmp_path, naming, ROLES),
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[*MODEL_ARGS["pydantic"], "--type-overrides", json.dumps(override), "--formatters", "builtin"],
    )


@pytest.mark.cli_doc(
    options=[KEEP_BASES],
    option_description="""Keep the generated bases of a TypedDict listed before its parent.

A schema that extends a parent through `allOf` and restates or requires a property the parent requires, where the
parent takes the property from its own base, is written in a module processed before the parent's when its package is
deeper (`com.example.pets.Child` over `com.example.Mid`) or its module name sorts later. Listed before the parent, it
takes the property through the parent's bases, as when the parent is listed first. Without this option a TypedDict
copies the parent's key before the key has a type, as 0.102.0 does: the copy narrows the key, so the TypedDict declares
the keys of its bases itself and drops them (`class Child(TypedDict)`), and where it keeps its bases, under a custom
template or a base the output imports in place of a generated model, the key is typed `None`. With this option the
TypedDict keeps `Mid` and types the key as Mid's bases declare it (`class Child(Mid)` with `x: int`). Other output
model types type such a key either way.""",
    input_schema="openapi/inherited_placeholder_module_order/child_first/schema.yaml",
    cli_args=["--output-model-type", "typing.TypedDict", KEEP_BASES],
    golden_output=(
        "main/openapi/inherited_placeholder_module_order/"
        "subclass_takes_the_inherited_type_in_every_definition_and_module_order[packages-base-child-mid-typeddict]/"
    ),
    comparison_output=(
        "main/openapi/inherited_placeholder_module_order/"
        "subclass_listed_before_its_parent_takes_the_inherited_type[typeddict]/"
    ),
    related_options=["--output-model-type"],
)
@pytest.mark.parametrize(
    "model_args",
    [
        pytest.param(MODEL_ARGS["pydantic"], id="pydantic"),
        pytest.param(("--output-model-type", "pydantic_v2.dataclass"), id="pydantic-dataclass"),
        pytest.param(("--output-model-type", "dataclasses.dataclass"), id="dataclass"),
        pytest.param(("--output-model-type", "msgspec.Struct"), id="msgspec"),
        pytest.param(MODEL_ARGS["typeddict"], id="typeddict"),
        pytest.param(MODEL_ARGS["typeddict-kept-bases"], id="typeddict-kept-bases"),
        pytest.param((*MODEL_ARGS["typeddict-kept-bases"], KEEP_BASES_OFF), id="typeddict-no-kept-bases"),
    ],
)
def test_subclass_listed_before_its_parent_takes_the_inherited_type(
    output_dir: Path, model_args: tuple[str, ...]
) -> None:
    """Child, in a deeper package than Mid, gives x the type of Base; a TypedDict keeps Mid only with the option."""
    run_main_and_assert(
        input_path=DATA / "child_first" / "schema.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[*model_args, "--formatters", "builtin"],
    )


@pytest.mark.parametrize("option_args", [pytest.param((), id="off"), pytest.param((KEEP_BASES,), id="on")])
@pytest.mark.parametrize(
    "kept_by",
    [
        pytest.param(("--type-overrides", '{"Base": "mypkg.models.Base"}'), id="stand-in-base"),
        pytest.param(("--custom-template-dir", str(DATA_PATH / "templates_docstring_escaping")), id="custom-template"),
    ],
)
def test_typed_dict_that_keeps_its_bases_types_the_key_with_the_option(
    output_dir: Path, kept_by: tuple[str, ...], option_args: tuple[str, ...]
) -> None:
    """A TypedDict that keeps its bases types the copied key None without the option, as 0.102.0 does.

    A base that the output imports in place of a generated model, or a custom TypedDict template, keeps Child's bases,
    so nothing declares the keys of Mid again; with the option Child takes x through Mid's bases.
    """
    run_main_and_assert(
        input_path=DATA / "child_first" / "schema.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[*MODEL_ARGS["typeddict"], *kept_by, *option_args, "--formatters", "builtin"],
    )


def test_subclass_listed_before_its_parent_validates_the_inherited_type(output_dir: Path) -> None:
    """The Pydantic Child accepts an integer x, which it rejected as long as x was typed None."""
    run_main_and_assert(
        input_path=DATA / "child_first" / "schema.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[*MODEL_ARGS["pydantic"], "--formatters", "builtin"],
        runtime_validation_module="com.example.pets",
        runtime_validation_model_name="Child",
        runtime_validation_data={"x": 5},
    )


@pytest.mark.parametrize("model", MODEL_ARGS)
@pytest.mark.parametrize(
    "option_args",
    [
        pytest.param(("--allof-merge-mode", "none"), id="merge-none"),
        pytest.param(("--default-values", '{"Mid.x": 3}'), id="default-values"),
        pytest.param(("--use-field-description",), id="field-description"),
        pytest.param(("--type-overrides", '{"Mid.x": "decimal.Decimal"}'), id="mid-type-override"),
    ],
)
def test_subclass_listed_before_its_parent_under_field_options(
    output_dir: Path, option_args: tuple[str, ...], model: str
) -> None:
    """Child takes Mid's class-scoped default and the merge mode of x as Mid does, and a TypedDict keeps its bytes."""
    run_main_and_assert(
        input_path=DATA / "child_first" / "schema.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[*MODEL_ARGS[model], *option_args, "--formatters", "builtin"],
    )


@pytest.mark.parametrize(
    "model_args",
    [
        pytest.param(MODEL_ARGS["pydantic"], id="pydantic"),
        pytest.param(MSGSPEC_ARGS, id="msgspec"),
        pytest.param(MODEL_ARGS["typeddict"], id="typeddict"),
        pytest.param(MODEL_ARGS["typeddict-kept-bases"], id="typeddict-kept-bases"),
    ],
)
def test_subclasses_listed_before_parents_whose_bases_give_the_property(
    output_dir: Path, model_args: tuple[str, ...]
) -> None:
    """Four levels, two bases, an aliased reference, a nullable list default, bounds, a discriminator and examples.

    Every parent's field is a placeholder that its own base resolves; FourMid's resolves through FourMid2's. BoundMid
    declares x with its bounds, so BoundChild takes its type already. msgspec warns that it cannot decode TagHolder.pet.
    """
    with pytest.warns(UserWarning, match=UNTAGGED_UNION) if model_args == MSGSPEC_ARGS else nullcontext():
        run_main_and_assert(
            input_path=DATA / "shapes.yaml",
            output_path=output_dir,
            input_file_type="openapi",
            extra_args=[*model_args, "--formatters", "builtin"],
        )


@pytest.mark.parametrize(
    "model_args",
    [
        pytest.param((*MODEL_ARGS["pydantic"], "--field-constraints"), id="pydantic-field-constraints"),
        pytest.param(MSGSPEC_ARGS, id="msgspec"),
    ],
)
def test_subclass_type_override_keeps_the_inherited_metadata(output_dir: Path, model_args: tuple[str, ...]) -> None:
    """A type override of the restated x keeps the bounds and examples MetaChild inherits, as with MetaMid first.

    msgspec warns that its Meta takes no bound on a Decimal and leaves the bound out, and that it cannot decode
    TagHolder.pet.
    """
    msgspec = model_args == MSGSPEC_ARGS
    with (
        pytest.warns(UserWarning, match=UNTAGGED_UNION) if msgspec else nullcontext(),
        pytest.warns(UserWarning, match=UNTAKEN_DECIMAL_BOUND) if msgspec else nullcontext(),
    ):
        run_main_and_assert(
            input_path=DATA / "shapes.yaml",
            output_path=output_dir,
            input_file_type="openapi",
            extra_args=[
                *model_args,
                "--type-overrides",
                '{"MetaChild.x": "decimal.Decimal"}',
                "--formatters",
                "builtin",
            ],
        )


@pytest.mark.parametrize("model", MODEL_ARGS)
def test_grandchild_in_a_module_between_its_parent_and_grandparent(output_dir: Path, model: str) -> None:
    """GrandChild copies Child's x after Child's module typed it; a TypedDict copies the placeholder and flattens.

    With the option the TypedDict reads through as the other output model types do, and GrandChild keeps Child.
    """
    run_main_and_assert(
        input_path=DATA / "grandchild.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[*MODEL_ARGS[model], "--formatters", "builtin"],
    )


@pytest.mark.parametrize(
    ("model_args", "reuse_args"),
    [
        pytest.param(MODEL_ARGS["pydantic"], ("--reuse-model",), id="pydantic-reuse"),
        pytest.param(MODEL_ARGS["pydantic"], ("--reuse-model", "--collapse-reuse-models"), id="pydantic-collapse"),
        pytest.param(MODEL_ARGS["pydantic"], ("--reuse-model", "--reuse-scope", "tree"), id="pydantic-tree"),
        pytest.param(MODEL_ARGS["typeddict"], ("--reuse-model",), id="typeddict-reuse"),
        pytest.param(MODEL_ARGS["typeddict-kept-bases"], ("--reuse-model",), id="typeddict-kept-bases-reuse"),
    ],
)
def test_equal_subclasses_reuse_one_model(
    output_dir: Path, model_args: tuple[str, ...], reuse_args: tuple[str, ...]
) -> None:
    """Child1, listed before Mid, now equals Child3, so reuse merges them as when Mid is listed first."""
    run_main_and_assert(
        input_path=DATA / "reuse.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[*model_args, *reuse_args, "--formatters", "builtin"],
    )


def test_json_schema_subclass_listed_before_its_parent(output_dir: Path) -> None:
    """A JSON Schema whose $defs give the same packages gives Child the inherited type too."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "inherited_placeholder_module_order.json",
        output_path=output_dir,
        input_file_type="jsonschema",
        extra_args=[*MODEL_ARGS["pydantic"], "--formatters", "builtin"],
    )
