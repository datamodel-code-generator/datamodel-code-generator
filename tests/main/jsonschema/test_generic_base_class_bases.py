"""Keep the classes a model is configured to inherit beside the generic base class (#7316)."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

import msgspec
import pydantic
import pytest

from tests.conftest import assert_output
from tests.main.conftest import (
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    _forget_generated_package,
    _generated_model,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

INPUT = JSON_SCHEMA_DATA_PATH / "generic_base_class_bases" / "api.json"
"""``Price`` extends a schema of another document, ``Note`` names ``customBasePath``, ``Child`` extends a schema with
``x-python-import`` and ``Tag`` and ``Plain`` name no class; ``BaseModelModel`` and ``StructModel`` are named like the
generated base class renamed beside a kept class of its name."""
CONFIGURED_CLASSES = {
    "mypkg/base.py": ("TagBase", "Audit", "NoteBase", "Mine", "BaseModel", "Struct"),
    "mypkg/common.py": ("Money",),
    "mypkg/ext.py": ("Ext",),
    "mypkg/plain.py": ("Plain",),
}
"""The classes the outputs import, each of which accepts unknown fields: the generated base class forbids them."""
ACCEPTING_CLASS = {
    "pydantic_v2.BaseModel": "class {}(pydantic.BaseModel):\n    model_config = pydantic.ConfigDict(extra='allow')\n",
    "msgspec.Struct": "class {}(msgspec.Struct, forbid_unknown_fields=False):\n    pass\n",
}
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

The options written to the generated base class win over the configuration of the kept class, as the configuration
of a model's own class body does without it: the generated base class is listed last for Pydantic, which takes the
configuration of the last base, and first for msgspec, which takes the options of the first base. Where a kept class is
named like the generated base class (`BaseModel`, `Struct` for msgspec), the generated one is named `BaseModelModel`
(`StructModel`), with `Model` added again while a class or an import that a model of the output writes takes
the name.""",
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
    output_file: Path,
    output_model_type: str,
    shape: tuple[str, ...],
    option_set: tuple[str, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the configured bases of a model beside the generic base class under the option.

    Without it ``Tag``, ``Note``, ``Child`` and ``Price`` inherit only the generated base class, as in 0.100.0, and
    ``Price`` has no field of ``Money``; with it each keeps its class, listed after the generated one for msgspec,
    whose first base gives an option the bases set differently, and before it for Pydantic, whose last base does.
    ``Plain`` and the models that extend a generated model are the same either way. A kept class named like the
    generated base class (``mypkg.base.BaseModel``, ``mypkg.base.Struct``) gives the generated one the first name of
    ``BaseModelModel``, ``BaseModelModelModel`` (``StructModel``, ...) that no class or import of the output binds, as a
    class that an import binds does any generated class. Every model that inherits the generated base class rejects an
    unknown field although each class it keeps accepts them (the ``unknown_fields`` record of each class the output
    binds).
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
    for module, names in CONFIGURED_CLASSES.items():
        (path := tmp_path / "configured" / module).parent.mkdir(parents=True, exist_ok=True)
        library = output_model_type.split(".", maxsplit=1)[0].removesuffix("_v2")
        path.write_text(f"import {library}\n\n" + "\n".join(map(ACCEPTING_CLASS[output_model_type].format, names)))
    monkeypatch.syspath_prepend(str(tmp_path / "configured"))
    _forget_generated_package("mypkg")
    try:
        with _generated_model(output_file, "generic_base_class_bases_output", "Model") as model:
            verdicts = "".join(
                f"{name}: {_unknown_field_verdict(value)}\n"
                for name, value in sorted(vars(sys.modules[model.__module__]).items())
                if isinstance(value, type)
                and issubclass(value, (pydantic.BaseModel, msgspec.Struct))
                and value not in {pydantic.BaseModel, msgspec.Struct}
            )
    finally:
        _forget_generated_package("mypkg")
    assert_output(verdicts, name="unknown_fields")


def _unknown_field_verdict(model: type[pydantic.BaseModel | msgspec.Struct]) -> str:
    """Return whether a Pydantic model or msgspec Struct accepts an object with a field it does not declare."""
    try:
        if issubclass(model, msgspec.Struct):
            msgspec.json.decode(b'{"unexpected": 1}', type=model)
        else:
            model.model_validate({"unexpected": 1})
    except (pydantic.ValidationError, msgspec.ValidationError):
        return "rejects"
    return "accepts"


@OPTION_SETS
def test_generic_base_class_bases_in_modules(output_dir: Path, option_set: tuple[str, ...]) -> None:
    """Keep a configured base beside the generic base class of a model of a module that does not hold it (#7316).

    The generic base class is written in the first module and imported by the other, and each module imports the class
    its model is configured to inherit. Beside ``mypkg.base.BaseModel``, the global base class, the generic one is
    named ``BaseModelModelModel``, as ``models.BaseModelModel`` binds ``BaseModelModel`` in the module that imports it.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "generic_base_class_bases_modules.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[
            *("--use-generic-base-class", "--extra-fields", "forbid", "--formatters", "builtin", "--disable-timestamp"),
            *("--base-class", "mypkg.base.BaseModel"),
            *(
                "--base-class-map",
                json.dumps({"models.Pet": "mypkg.base.PetBase", "things.Thing": ["mypkg.base.ThingBase"]}),
            ),
            *option_set,
        ],
    )
