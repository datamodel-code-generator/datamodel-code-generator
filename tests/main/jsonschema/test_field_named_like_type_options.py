"""Tell a field named like its type from that type, in a class and in the classes that redeclare it (#7103, #7358)."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from tests.conftest import assert_output
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

REDECLARED = JSON_SCHEMA_DATA_PATH / "field_named_like_type_redeclared.json"
ASSIGNED = JSON_SCHEMA_DATA_PATH / "field_named_like_type_assigned.json"
GUARDS = JSON_SCHEMA_DATA_PATH / "field_named_like_type_guards.json"
FOLLOW = "--rename-redeclared-fields-hiding-types"
FROM_CHECKERS = "--rename-fields-hiding-types-from-type-checkers"
BACKENDS = [
    "pydantic_v2.BaseModel",
    "pydantic_v2.dataclass",
    "dataclasses.dataclass",
    "msgspec.Struct",
    "typing.TypedDict",
]


def _fields_of_the_key(model: type) -> str:
    """Return, for each class of the module, the names of its fields that read the wire key ``Pet``."""
    namespace = vars(sys.modules[model.__module__])
    lines = []
    for name in ("Base", "Owner", "Mid", "Leaf", "Plain", "Hiding", "Bridge", "Below", "Top", "Middle", "Bottom"):
        fields = namespace[name].model_fields
        lines.append(f"{name}: {sorted(field for field, info in fields.items() if (info.alias or field) == 'Pet')}\n")
    return "".join(lines)


@pytest.mark.cli_doc(
    options=[FOLLOW],
    option_description="""Give a field that a subclass redeclares the name its base's field was given.

A field named like its type (`Pet: Pet`) is renamed with the wire name as its alias (`Pet_1` with `alias='Pet'`). A
subclass that redeclares the field with another type (`Pet: Dog`) has no type of that name to hide, so it kept its own
name beside the inherited renamed field, and the class held two fields that read the key `Pet`: a payload set both
and a dump by alias wrote the key twice. With the option the redeclared field takes the name of the field of its
family that was renamed, and overrides it; without it the output is as in 0.100.0. The option also applies to what
`--rename-fields-hiding-types-from-type-checkers` renames.""",
    input_schema="jsonschema/field_named_like_type_redeclared.json",
    cli_args=[FOLLOW, "--output-model-type", "pydantic_v2.BaseModel"],
    golden_output="main/jsonschema/field_named_like_type_options/redeclared_field_follows_rename[option].py",
    comparison_output="main/jsonschema/field_named_like_type_options/redeclared_field_follows_rename[released].py",
)
@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param([], id="released"),
        pytest.param([FOLLOW], id="option"),
        pytest.param([FOLLOW, "--use-annotated"], id="option-annotated"),
        pytest.param([FOLLOW, "--allow-population-by-field-name"], id="option-population-by-name"),
        pytest.param([FOLLOW, "--field-type-collision-strategy", "rename-type"], id="option-rename-type"),
    ],
)
def test_redeclared_field_follows_rename(extra_args: list[str], output_file: Path) -> None:
    """Rename the field that a subclass redeclares as its base's field was renamed, only under the option.

    ``Owner`` redeclares ``Pet: Pet`` of ``Base`` as ``Pet: Dog``, ``Leaf`` redeclares it a third time down a chain, and
    ``Hiding`` is the reverse: its base redeclares nothing and it is the subclass whose own type hides the name. Without
    the option these classes hold two fields for the key, as in 0.100.0; under ``rename-type`` no field is renamed.
    ``Below`` and ``Top`` redeclare the field across a class that only inherits it (``Bridge``, ``Middle``), below the
    renamed field and above it.
    """
    run_main_and_assert(
        input_path=REDECLARED,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", *extra_args],
        force_exec_validation=True,
    )
    with _generated_model(output_file, "field_named_like_type_redeclared", "Kennel") as model:
        assert_output(_fields_of_the_key(model), name="fields")


@pytest.mark.parametrize(
    ("backend", "extra_args"),
    [
        pytest.param("pydantic_v2.BaseModel", [], id="released"),
        pytest.param("pydantic_v2.BaseModel", [FOLLOW], id="option"),
        pytest.param("msgspec.Struct", [FROM_CHECKERS], id="option-msgspec"),
    ],
)
def test_redeclared_field_follows_rename_only_where_it_is_the_same_field(
    backend: str, extra_args: list[str], output_file: Path
) -> None:
    """Keep the name of a redeclared field that is not the field its base renamed.

    ``Aliased`` redeclares ``Pet`` under another wire key (``animal``), ``Crowded`` has a field of the new name
    already, and ``Sibling`` only shares the base ``Mixin`` with ``Both``, a subclass of the renamed field's class.
    """
    run_main_and_assert(
        input_path=GUARDS,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", backend, "--aliases", '{"Aliased.animal": "Pet"}', *extra_args],
        force_exec_validation=True,
    )


def test_redeclared_field_follows_rename_in_modules(output_dir: Path) -> None:
    """Rename the redeclared field of a class whose module is processed before and after the module of its base.

    ``a_owner`` redeclares the field of a base in ``b_base``, which is processed after it, and ``z_owner`` the field of
    a base in ``y_base``, which is processed before it.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "field_named_like_type_modules",
        output_path=output_dir,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", FOLLOW],
        force_exec_validation=True,
    )


@pytest.mark.cli_doc(
    options=[FROM_CHECKERS],
    option_description="""Tell a field named like its type from that type for type checkers.

A field that the class body assigns (`Address: Address | None = None`, `field(...)`) and that is named like the type
of its annotation is read by type checkers as the field itself, so ty and Pyright reject the annotation, although the
generated class works at run time. With the option `msgspec.Struct` output renames the field and keeps the wire name in
`name=` (`Address_1: Address | UnsetType = field(name='Address', default=UNSET)`), and `dataclasses.dataclass` output,
which has no alias to keep the wire name, renames the type (`class Address_1`), as `--field-type-collision-strategy
rename-type` does for Pydantic. A field that the class body does not assign, and a `TypedDict` key, keep their names.
A subclass that redeclares a renamed field takes its name, as with `--rename-redeclared-fields-hiding-types`. Without
the option the output is as in 0.100.0.""",
    input_schema="jsonschema/field_named_like_type_assigned.json",
    cli_args=[FROM_CHECKERS, "--output-model-type", "dataclasses.dataclass"],
    golden_output="main/jsonschema/field_named_like_type_options/static_hide_renames[option-dataclasses.dataclass].py",
    comparison_output="main/jsonschema/field_named_like_type_options/static_hide_renames[released-dataclasses.dataclass].py",
)
@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize(
    "extra_args",
    [pytest.param([], id="released"), pytest.param([FROM_CHECKERS], id="option")],
)
def test_static_hide_renames(backend: str, extra_args: list[str], output_file: Path) -> None:
    """Rename the field, or its type, that type checkers read as the field, for every output, only under the option.

    ``Phone`` and the list of ``Tag`` are optional, so the class body assigns them; ``Address`` is required with no
    default, so nothing binds its name; ``Business`` redeclares the inherited ``Phone`` with another type and
    ``Branch`` with its own type, which hides it again. Pydantic output renames the assigned fields without the
    option, and a TypedDict has no class attribute to hide a type.
    """
    run_main_and_assert(
        input_path=ASSIGNED,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", backend, *extra_args],
        force_exec_validation=True,
    )
