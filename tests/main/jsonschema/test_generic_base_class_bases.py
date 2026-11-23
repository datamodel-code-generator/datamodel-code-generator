"""Keep the classes a model is configured to inherit beside the generic base class (#7316)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import JSON_SCHEMA_DATA_PATH, OPEN_API_DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT = JSON_SCHEMA_DATA_PATH / "generic_base_class_bases" / "api.json"
"""``Price`` extends a schema of another document, ``Note`` names ``customBasePath``, ``Child`` extends a schema with
``x-python-import`` and ``Tag`` and ``Plain`` name no class."""
OPTION = "--keep-bases-beside-generic-base-class"
OPTION_SETS = pytest.mark.parametrize(
    "option_set", [pytest.param((), id="released"), pytest.param((OPTION,), id="kept")]
)
SHAPES = (
    pytest.param(("--base-class-map", json.dumps({"Tag": "mypkg.base.TagBase"})), id="base-class-map"),
    pytest.param(
        ("--base-class-map", json.dumps({"Tag": ["mypkg.base.TagBase", "mypkg.base.Audit"]})), id="base-class-map-list"
    ),
    pytest.param(("--base-class", "mypkg.base.Mine"), id="base-class"),
    pytest.param(("--base-class", "mypkg.base.BaseModel"), id="base-class-named-like-pydantic-base"),
    pytest.param(("--base-class", "mypkg.base.Struct"), id="base-class-named-like-msgspec-base"),
    pytest.param(("--external-ref-mapping", "common.json=mypkg.common"), id="external-ref-mapping"),
    pytest.param(("--type-overrides", json.dumps({"Plain": "mypkg.plain.Plain"})), id="type-overrides"),
)


@pytest.mark.cli_doc(
    options=["--keep-bases-beside-generic-base-class"],
    option_description="""Keep configured base classes beside the generated base class.

Under `--use-generic-base-class` the shared base class stands in for the base class of the output model type, so a
model that inherits only a class it is configured to inherit takes the generated base class alone and the import of
the class stays unused: `class Tag(BaseModel)` beside `from mypkg.base import TagBase`. With this option the model
keeps the class, as it keeps a generated base: `class Tag(TagBase, BaseModel)`. The classes are the one
`--base-class-map`, `customBasePath` or `--base-class` names, and the class `--external-ref-mapping` or
`x-python-import` imports for a schema the model extends, so `Price` keeps the fields of `Money`.

The generated base class is listed last, so the options written to it win over the configuration of the kept class,
as the configuration of a model's own class body does without it. Where a kept class is named like the generated base
class (`BaseModel`, `Struct` for msgspec), the generated one is named `BaseModelModel` (`StructModel`).""",
    input_schema="jsonschema/generic_base_class_bases/api.json",
    cli_args=[
        "--use-generic-base-class",
        "--extra-fields",
        "forbid",
        "--base-class-map",
        '{"Tag": "mypkg.base.TagBase"}',
        "--keep-bases-beside-generic-base-class",
    ],
    golden_output="main/jsonschema/generic_base_class_bases/generic_base_class_bases[pydantic_v2-base-class-map-kept].py",
    comparison_output=(
        "main/jsonschema/generic_base_class_bases/generic_base_class_bases[pydantic_v2-base-class-map-released].py"
    ),
    related_options=["--use-generic-base-class", "--base-class-map", "--base-class"],
)
@OPTION_SETS
@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize(
    "output_model_type", ["pydantic_v2.BaseModel", "msgspec.Struct"], ids=["pydantic_v2", "msgspec"]
)
def test_generic_base_class_bases(
    output_file: Path, output_model_type: str, shape: tuple[str, ...], option_set: tuple[str, ...]
) -> None:
    """Keep the configured bases of a model beside the generic base class under the option.

    Without it ``Tag``, ``Note``, ``Child`` and ``Price`` inherit only the generated base class, as in 0.100.0, and
    ``Price`` has no field of ``Money``; with it each keeps its class, listed before the generated one. ``Plain`` and
    the models that extend a generated model are the same either way. A kept class named like the generated base class
    (``mypkg.base.BaseModel``, ``mypkg.base.Struct``) gives the generated one the name ``BaseModelModel``
    (``StructModel``), as a class that an import binds does any generated class.
    """
    run_main_and_assert(
        input_path=INPUT,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            *("--output-model-type", output_model_type, "--use-generic-base-class", "--extra-fields", "forbid"),
            *(("--use-msgspec-forbid-unknown-fields",) if output_model_type == "msgspec.Struct" else ()),
            *("--formatters", "builtin", "--disable-timestamp"),
            *shape,
            *option_set,
        ],
    )


@OPTION_SETS
def test_generic_base_class_bases_in_modules(output_dir: Path, option_set: tuple[str, ...]) -> None:
    """Keep a configured base beside the generic base class of a model of a module that does not hold it (#7316).

    The generic base class is written in the first module and imported by the other, and each module imports the class
    its model is configured to inherit.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "generic_base_class_bases_modules.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[
            *("--use-generic-base-class", "--extra-fields", "forbid", "--formatters", "builtin", "--disable-timestamp"),
            *(
                "--base-class-map",
                json.dumps({"models.Pet": "mypkg.base.PetBase", "things.Thing": ["mypkg.base.ThingBase"]}),
            ),
            *option_set,
        ],
    )
