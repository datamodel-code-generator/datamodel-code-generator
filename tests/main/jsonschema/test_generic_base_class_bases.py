"""Keep the classes a model is configured to inherit beside the generic base class (#7316)."""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING, get_args

import msgspec
import pydantic
import pytest

from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    _forget_generated_package,
    _generated_model,
    _generated_package_module,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path
    from types import ModuleType

INPUT = JSON_SCHEMA_DATA_PATH / "generic_base_class_bases" / "api.json"
"""``Price`` extends a schema of another document, ``Note`` names ``customBasePath``, ``Child`` extends a schema with
``x-python-import`` and ``Tag`` and ``Plain`` name no class; ``BaseModelModel`` and ``StructModel`` are named like the
generated base class renamed beside a kept class of its name."""
CONFIGURED_CLASSES = {
    "mypkg/base.py": ("TagBase", "Audit", "NoteBase", "Mine", "BaseModel", "Struct", "_BaseModel", "_Struct"),
    "mypkg/common.py": ("Money",),
    "mypkg/ext.py": ("Ext",),
    "mypkg/plain.py": ("Plain",),
    "zpkg/base.py": ("_BaseModel", "_Struct"),
}
"""The classes the outputs import, each of which accepts unknown fields: the generated base class forbids them.
``_BaseModel`` and ``_Struct`` are named like the alias of the library base of the generated base class, imported from
a module that sorts before the library (``mypkg``) and after it (``zpkg``)."""
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
    with (
        _configured_classes(tmp_path, monkeypatch, output_model_type),
        _generated_model(output_file, "generic_base_class_bases_output", "Model") as model,
    ):
        verdicts = "".join(
            f"{name}: {_unknown_field_verdict(value)}\n"
            for name, value in sorted(vars(sys.modules[model.__module__]).items())
            if isinstance(value, type)
            and issubclass(value, (pydantic.BaseModel, msgspec.Struct))
            and value not in {pydantic.BaseModel, msgspec.Struct}
        )
    assert_output(verdicts, name="unknown_fields")


@contextmanager
def _configured_classes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output_model_type: str
) -> Generator[None, None, None]:
    """Write the classes of ``CONFIGURED_CLASSES`` for the library of ``output_model_type`` and make them importable."""
    for module, names in CONFIGURED_CLASSES.items():
        (path := tmp_path / "configured" / module).parent.mkdir(parents=True, exist_ok=True)
        library = output_model_type.split(".", maxsplit=1)[0].removesuffix("_v2")
        path.write_text(f"import {library}\n\n" + "\n".join(map(ACCEPTING_CLASS[output_model_type].format, names)))
    monkeypatch.syspath_prepend(str(tmp_path / "configured"))
    packages = ("mypkg", "zpkg")
    for package in packages:
        _forget_generated_package(package)
    try:
        yield
    finally:
        for package in packages:
            _forget_generated_package(package)


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


LIBRARY_BASE_INPUTS = JSON_SCHEMA_DATA_PATH / "generic_base_class_library_base"
"""``Item`` with a field whose ``x-python-type`` names a class; ``plain`` names none, the ``patterns`` inputs also take
``patternProperties``."""
PYDANTIC = ("--output-model-type", "pydantic_v2.BaseModel")
MSGSPEC = ("--output-model-type", "msgspec.Struct", "--use-msgspec-forbid-unknown-fields")
FORBID = ("--extra-fields", "forbid")
VALIDATORS = ("--generate-schema-validators",)


def _class_name(cls: type, module: str) -> str:
    """Return the name of ``cls``, qualified by its module where that is not ``module``."""
    return cls.__qualname__ if cls.__module__ == module else f"{cls.__module__}.{cls.__qualname__}"


def _inheritance(module: ModuleType) -> str:
    """Return the bases of the classes ``module`` defines and the classes the type of each of their fields names."""
    lines = []
    for name, value in sorted(vars(module).items()):
        if not (isinstance(value, type) and value.__module__ == module.__name__):
            continue
        lines.append(f"{name}({', '.join(_class_name(base, module.__name__) for base in value.__bases__)})")
        if issubclass(value, msgspec.Struct):
            types = {field.name: field.type for field in msgspec.structs.fields(value)}
        else:
            types = {field: info.annotation for field, info in value.model_fields.items()}
        lines.extend(
            f"    {field}: {', '.join(_class_name(cls, module.__name__) for cls in get_args(kind) or (kind,))}"
            for field, kind in types.items()
        )
    return "".join(f"{line}\n" for line in lines)


def _import_verdict(output_file: Path) -> str:
    """Return the classes of the generated module and their bases, or the exception its import raises."""
    try:
        with _generated_model(output_file, "generic_base_class_library_base_output", "Item") as model:
            return _inheritance(sys.modules[model.__module__])
    except (NameError, TypeError) as error:
        return f"import fails: {type(error).__name__}\n"


@pytest.mark.parametrize(
    ("input_name", "options"),
    [
        pytest.param("class", (*PYDANTIC, *FORBID), id="pydantic_v2-class-released"),
        pytest.param("class", (*PYDANTIC, "--allow-population-by-field-name"), id="pydantic_v2-class-populate-by-name"),
        pytest.param("dotted_class", (*PYDANTIC, *FORBID), id="pydantic_v2-dotted-class-released"),
        pytest.param("union", (*PYDANTIC, *FORBID), id="pydantic_v2-union-released"),
        pytest.param("with_root_model", (*PYDANTIC, *FORBID), id="pydantic_v2-with-root-model-released"),
        pytest.param("class", (*PYDANTIC, *FORBID, OPTION), id="pydantic_v2-class-kept"),
        pytest.param(
            "class",
            (*PYDANTIC, *FORBID, OPTION, "--base-class", "mypkg.base.Mine"),
            id="pydantic_v2-class-kept-base-class",
        ),
        pytest.param(
            "class",
            (*PYDANTIC, *FORBID, OPTION, "--base-class", "mypkg.base._BaseModel"),
            id="pydantic_v2-class-kept-base-class-named-like-alias-before",
        ),
        pytest.param(
            "class",
            (*PYDANTIC, *FORBID, OPTION, "--base-class", "zpkg.base._BaseModel"),
            id="pydantic_v2-class-kept-base-class-named-like-alias-after",
        ),
        pytest.param(
            "class", (*PYDANTIC, *FORBID, "--preset", "standard-py312-20261123"), id="pydantic_v2-class-preset"
        ),
        pytest.param("struct", (*MSGSPEC, *FORBID), id="msgspec-class-released"),
        pytest.param("struct", (*MSGSPEC, *FORBID, OPTION), id="msgspec-class-kept"),
    ],
)
def test_generic_base_class_beside_its_library_base(
    output_file: Path, input_name: str, options: tuple[str, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Import the library base of the generic base class once where a field names it too (#7437).

    The generic base class inherits ``pydantic.BaseModel`` (``msgspec.Struct``) as ``_BaseModel`` (``_Struct``), and a
    field whose ``x-python-type`` names the same class takes that binding: the module imports the class once, the
    generic base class inherits it and the field is typed with it. Until 0.103.0 the import and the field took
    ``_BaseModel_aliased`` while the class statement kept ``_BaseModel``, which nothing bound, and the module failed to
    import. Beside a kept class named ``_BaseModel``, the library base takes the other alias in all three places.
    """
    run_main_and_assert(
        input_path=LIBRARY_BASE_INPUTS / f"{input_name}.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            *("--use-generic-base-class", "--formatters", "builtin", "--disable-timestamp"),
            *options,
        ],
    )
    with _configured_classes(tmp_path, monkeypatch, options[1]):
        verdict = _import_verdict(output_file)
    assert_output(verdict, name="inheritance")


def test_generic_base_class_beside_its_library_base_in_modules(output_dir: Path) -> None:
    """Import the library base once in a module that holds no generic base class but a field naming it (#7437).

    ``sub/other.py`` imports the generic base class from ``root.py`` and ``pydantic.BaseModel`` for its field, which
    takes the alias ``_BaseModel`` of the module that holds the generic base class; ``root.py`` is as without the
    field.
    """
    run_main_and_assert(
        input_path=LIBRARY_BASE_INPUTS / "modules",
        output_path=output_dir,
        input_file_type="jsonschema",
        extra_args=[
            *("--use-generic-base-class", "--formatters", "builtin", "--disable-timestamp"),
            *PYDANTIC,
            *FORBID,
        ],
    )
    with _generated_package_module(output_dir, "sub.other") as module:
        verdict = _inheritance(module)
    assert_output(verdict, name="inheritance")


def test_generic_base_class_path_in_custom_template(output_file: Path) -> None:
    """Give a custom template the path ``#/BaseModel`` of the generic base class, as released (#7437).

    The template writes ``{{ path }}``; the generic base class still tells the template its path is ``#/BaseModel``
    although the module imports the library base it inherits under an alias.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "schema_id.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            *("--use-generic-base-class", "--formatters", "builtin", "--disable-timestamp"),
            *("--custom-template-dir", str(DATA_PATH / "templates_schema_id")),
            *PYDANTIC,
            *FORBID,
        ],
    )


@pytest.mark.parametrize(
    ("input_name", "options"),
    [
        pytest.param(
            "class",
            (*PYDANTIC, *FORBID, "--base-class", "mypkg.base.BaseModel"),
            id="base-class-named-like-generic-with-field",
        ),
        pytest.param(
            "class_patterns",
            (*PYDANTIC, *FORBID, *VALIDATORS, "--base-class", "mypkg.base.BaseModel"),
            id="base-class-named-like-generic-with-validators",
        ),
        pytest.param("alias_class", (*PYDANTIC, *FORBID), id="field-named-like-alias"),
        pytest.param(
            "plain",
            (*PYDANTIC, *FORBID, "--base-class", "mypkg.base._BaseModel"),
            id="base-class-named-like-alias-before",
        ),
        pytest.param(
            "plain",
            (*PYDANTIC, *FORBID, "--base-class", "zpkg.base._BaseModel"),
            id="base-class-named-like-alias-after",
        ),
        pytest.param(
            "class",
            (*PYDANTIC, *FORBID, "--base-class", "mypkg.base._BaseModel"),
            id="base-class-named-like-alias-before-with-field",
        ),
        pytest.param(
            "class",
            (*PYDANTIC, *FORBID, "--base-class", "zpkg.base._BaseModel"),
            id="base-class-named-like-alias-after-with-field",
        ),
        pytest.param(
            "class_patterns",
            (*PYDANTIC, *FORBID, *VALIDATORS, "--base-class", "zpkg.base._BaseModel"),
            id="base-class-named-like-alias-with-validators",
        ),
        pytest.param(
            "plain",
            (*PYDANTIC, *FORBID, OPTION, "--base-class", "zpkg.base._BaseModel"),
            id="kept-base-class-named-like-alias",
        ),
        pytest.param(
            "class_patterns",
            (*PYDANTIC, *FORBID, *VALIDATORS, OPTION, "--base-class", "zpkg.base._BaseModel"),
            id="kept-base-class-named-like-alias-with-validators",
        ),
        pytest.param(
            "plain_patterns",
            (*PYDANTIC, *FORBID, *VALIDATORS, OPTION, "--base-class", "mypkg.base.BaseModel"),
            id="renamed-generic-with-validators",
        ),
        pytest.param(
            "class_patterns",
            (*PYDANTIC, *FORBID, *VALIDATORS, OPTION, "--base-class", "mypkg.base.BaseModel"),
            id="renamed-generic-with-validators-and-field",
        ),
        pytest.param(
            "plain", (*MSGSPEC, *FORBID, "--base-class", "zpkg.base._Struct"), id="msgspec-base-class-named-like-alias"
        ),
        pytest.param(
            "struct",
            (*MSGSPEC, *FORBID, "--base-class", "zpkg.base._Struct"),
            id="msgspec-base-class-named-like-alias-with-field",
        ),
    ],
)
def test_generic_base_class_alias_beside_other_imports(
    output_file: Path, input_name: str, options: tuple[str, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep the bindings of the generic base class's base beside imports of other classes of its names (#7437).

    A field naming ``mypkg.base._BaseModel`` takes ``_BaseModel_aliased`` beside the library base's ``_BaseModel``.
    Where ``--base-class`` names ``mypkg.base.BaseModel``, the generic base class inherits ``pydantic.BaseModel`` under
    the alias the field's import gives it, and the schema validator helpers inherit the generic base class written
    before them. A class that ``--base-class`` names ``_BaseModel`` (``_Struct``), which the generic base class drops,
    leaves its import, and the generic base class keeps naming ``_BaseModel``: it inherits the class of whichever of
    the two imports binds the name last, with or without a field naming the library base, as released (row
    ``legacy-generic-base-names-dropped-class-binding``, #7436). Kept beside the generic base class without such a
    field, the class conflicts with the library base in the bases of ``Item`` (#7436). A generic base class renamed
    beside a kept ``mypkg.base.BaseModel`` (``BaseModelModel``) leaves the helpers inheriting the kept class.
    """
    run_main_and_assert(
        input_path=LIBRARY_BASE_INPUTS / f"{input_name}.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            *("--use-generic-base-class", "--formatters", "builtin", "--disable-timestamp"),
            *options,
        ],
    )
    with _configured_classes(tmp_path, monkeypatch, options[1]):
        verdict = _import_verdict(output_file)
    assert_output(verdict, name="inheritance")
