"""allOf members that name a schema the output types by an import: of a mapped document, or with x-python-import.

Up to 0.86.0 such a member failed with "A Parser can not resolve classes". The model now inherits the class that
``--external-ref-mapping`` or ``x-python-import`` imports, as a field that names the schema is typed by it. What the
generator writes for the model reads the schema of that class as it reads a generated base: the fields a model
requires or narrows, the keyword-only fields of dataclasses and msgspec Structs, and the models nested in the schema,
which are written as a generated base gives them.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

import msgspec
import pytest
from pydantic import ValidationError

from tests.conftest import assert_output
from tests.main.conftest import (
    OPEN_API_DATA_PATH,
    _generated_model,
    _generated_package_module,
    _model_json_validator,
    run_main_and_assert,
    run_main_with_args,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

INPUT_PATH = OPEN_API_DATA_PATH / "external_ref_mapping_all_of"
COMMON_ARGS = ("--disable-timestamp", "--formatters", "builtin")
OUTPUT_MODEL_TYPES = (
    "pydantic_v2.BaseModel",
    "pydantic_v2.dataclass",
    "dataclasses.dataclass",
    pytest.param(
        "typing.TypedDict",
        marks=pytest.mark.skipif(
            sys.version_info < (3, 12), reason="Pydantic validates typing.TypedDict from Python 3.12 on"
        ),
    ),
    "msgspec.Struct",
)
PAYLOADS: tuple[tuple[str, tuple[dict[str, Any], ...]], ...] = (
    ("Branch", ({"id": 1, "twigs": [{"id": 2}], "name": "a"}, {"name": "a"})),
    ("Bush", ({"id": 1, "height": 2}, {"id": 1, "twigs": [{"id": "two"}]})),
    ("Tree", ({"crown": {"id": 1, "leaves": 2}}, {"crown": {"leaves": 2}})),
    ("Grove", ({"twigs": []}, {}, {"twigs": 5})),
    ("Thicket", ({"twigs": []}, {}, {"twigs": 5})),
    ("Hedge", ({"twigs": [], "soil": "clay"}, {"soil": "clay"}, {"twigs": 5})),
    ("Sprout", ({"name": "a"}, {}, {"name": "a", "twigs": 5})),
    ("Arbor", ({"shape": {"round": True}, "color": "green"}, {"shape": {}}, {"shape": {}, "color": "red"})),
    ("Blossom", ({"size": 1, "pod": {"size": 2}}, {"size": "one"})),
)
"""Documents per model, the first valid against its schema and the others invalid."""
GRANDCHILD_PATH = OPEN_API_DATA_PATH / "external_ref_mapping_all_of_grandchild"
GRANDCHILD_PAYLOADS: tuple[tuple[str, tuple[dict[str, Any], ...]], ...] = (
    (
        "Grand",
        (
            {"id": 1, "kids": [{"id": 2}], "leaf": {"color": "green"}, "name": "a"},
            {"id": 1},
            {"id": 1, "kids": []},
            {"id": 1, "leaf": {}},
            {"kids": [], "leaf": {}},
            {"id": 1, "kids": [{"kids": []}], "leaf": {}},
        ),
    ),
)
"""Documents for the models that require properties of the mapped schema, the first valid and the others invalid."""


def _generate_mapped_package(
    tmp_path: Path, package: str, output_model_type: str, mapped_document: Path = INPUT_PATH / "shared.yaml"
) -> None:
    """Generate the package the import names from the mapped document, as its users would."""
    (package_path := tmp_path / package).mkdir()
    (package_path / "__init__.py").write_text("", encoding="utf-8")
    run_main_with_args([
        "--input",
        str(mapped_document),
        "--output",
        str(package_path / "models.py"),
        "--input-file-type",
        "openapi",
        "--output-model-type",
        output_model_type,
        *COMMON_ARGS,
    ])


def _verdicts(module: Any, output_model_type: str, payloads: Iterable[tuple[str, Iterable[object]]]) -> str:
    """Return whether each model of a generated module accepts each document, one line per document."""
    lines: list[str] = []
    for model_name, documents in payloads:
        model = getattr(module, model_name)
        decode = (
            (lambda text, model=model: msgspec.json.decode(text, type=model))
            if output_model_type == "msgspec.Struct"
            else _model_json_validator(model)
        )
        for document in documents:
            text = json.dumps(document)
            try:
                decode(text)
            except (ValidationError, msgspec.ValidationError):
                verdict = "rejected"
            else:
                verdict = "accepted"
            lines.append(f"{model_name} {text}: {verdict}\n")
    return "".join(lines)


@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
def test_external_ref_mapping_all_of_base(
    output_model_type: str, output_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inherit the class the mapping imports where an allOf member names a schema of the mapped document.

    The member names the schema directly, through a component that gets no model (``Shrub``) and in a property
    (``Tree.crown``). A model that requires a property only the mapped schema declares, or narrows it, gets the field
    as with a generated base, also through a generated model (``Hedge``); dataclasses and msgspec Structs write a
    required field of their own as keyword-only; the inline models of the mapped schema are written (``Shape``,
    ``Color``); a local schema named like the mapped one gives way to the import (``TwigModel``); and a local schema
    that renders like a mapped one is no duplicate of it (``Pod``).
    """
    package = f"mapped_{output_model_type.replace('.', '_').lower()}"
    run_main_and_assert(
        input_path=INPUT_PATH / "api.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            "--output-model-type",
            output_model_type,
            "--external-ref-mapping",
            f"shared.yaml={package}.models",
            *COMMON_ARGS,
        ],
    )
    _generate_mapped_package(tmp_path, package, output_model_type)
    monkeypatch.syspath_prepend(str(tmp_path))
    with _generated_model(output_file, f"external_ref_mapping_all_of_{package}", "Branch") as branch:
        verdicts = _verdicts(sys.modules[branch.__module__], output_model_type, PAYLOADS)
    assert_output(verdicts, name="payloads")


@pytest.mark.parametrize(
    "options",
    [
        pytest.param(("--reuse-model",), id="reuse-model"),
        pytest.param(("--reuse-model", "--collapse-reuse-models"), id="collapse-reuse-models"),
        pytest.param(("--read-only-write-only-model-type", "all"), id="read-only-write-only-all"),
        pytest.param(("--type-overrides", json.dumps({"Twig": "other.Twig", "Bed": "other.Bed"})), id="type-overrides"),
        pytest.param(("--no-allow-remote-refs",), id="no-allow-remote-refs"),
    ],
)
def test_external_ref_mapping_all_of_base_options(options: tuple[str, ...], output_file: Path) -> None:
    """Keep the stand-in of a mapped base out of what the options change.

    Reuse neither merges a local schema into the mapped one nor the other way around; ``all`` writes no request or
    response variant of the mapped schema; the mapping wins over a type override that names the mapped class, and a
    model a type override replaces still passes the import to the models that inherit it; the mapped document loads
    under the remote reference policy as any referenced document does.
    """
    run_main_and_assert(
        input_path=INPUT_PATH / "api.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--external-ref-mapping", "shared.yaml=mapped.models", *COMMON_ARGS, *options],
    )


def test_external_ref_mapping_all_of_base_modules(
    output_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inherit the mapped class in models of several modules.

    The schema each module inherits is no member of any module: modules that use each other only through it are not
    merged, and the import cycle of two other modules merges them without it, where a local schema named like it
    gives way to the import.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "external_ref_mapping_all_of_modules",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[
            "--external-ref-mapping",
            "../external_ref_mapping_all_of/shared.yaml=mapped_modules.models",
            "--allow-remote-refs",
            *COMMON_ARGS,
        ],
    )
    _generate_mapped_package(tmp_path, "mapped_modules", "pydantic_v2.BaseModel")
    monkeypatch.syspath_prepend(str(tmp_path))
    verdicts: list[str] = []
    for module_path, model_name, documents in (
        ("a", "Branch", ({"id": 1, "bush": {"id": 2, "height": 3}}, {"bush": {"id": 2}})),
        ("c", "Arbor", ({"shape": {}, "hedge": {"leafy": {"local": "a"}}}, {"twigs": []})),
    ):
        with _generated_package_module(output_dir, module_path) as module:
            verdicts.append(_verdicts(module, "pydantic_v2.BaseModel", ((model_name, documents),)))
    assert_output("".join(verdicts), name="payloads")


@pytest.mark.parametrize(
    ("input_name", "mapped_document"),
    [
        pytest.param("beside.yaml", "shared.yaml", id="beside"),
        pytest.param("subdirectory.yaml", "sub/shared.yaml", id="subdirectory"),
    ],
)
@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
def test_external_ref_mapping_all_of_grandchild_modules(
    input_name: str,
    mapped_document: str,
    output_model_type: str,
    output_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read the mapped schema through the model of another module that inherits it, whatever the module order.

    ``zzz`` is processed before the module of ``Branch``, which inherits the mapped schema, and ``aaa`` after it; the
    model of each requires two properties only the mapped schema declares. Only the modules whose fields name the
    classes of the mapped document import them. In the subdirectory, the mapped document names its own schemas by
    references the mapping does not map: ``Leaf`` is written as a generated base gives it, and the import types the
    field that names the mapped schema.
    """
    package = f"mapped_{input_name.removesuffix('.yaml')}_{output_model_type.replace('.', '_').lower()}"
    run_main_and_assert(
        input_path=GRANDCHILD_PATH / input_name,
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[
            "--output-model-type",
            output_model_type,
            "--external-ref-mapping",
            f"{mapped_document}={package}.models",
            *COMMON_ARGS,
        ],
    )
    _generate_mapped_package(tmp_path, package, output_model_type, GRANDCHILD_PATH / mapped_document)
    monkeypatch.syspath_prepend(str(tmp_path))
    verdicts: list[str] = []
    for module_path in ("aaa", "zzz"):
        with _generated_package_module(output_dir, module_path) as module:
            verdicts.append(f"{module_path}:\n{_verdicts(module, output_model_type, GRANDCHILD_PAYLOADS)}")
    assert_output("".join(verdicts), name="payloads")


def test_external_ref_mapping_all_of_unmapped_extra_items(
    output_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Type by the import a mapped schema that a model written from the mapped document names in its extra items.

    The mapped document in a subdirectory names its own schemas by references the mapping does not map, so its
    inline model ``Bag`` is written as a generated base gives it, and its ``extra_items`` names the mapped class.
    """
    run_main_and_assert(
        input_path=GRANDCHILD_PATH / "extra_items.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            "--output-model-type",
            "typing.TypedDict",
            "--use-closed-typed-dict",
            "--external-ref-mapping",
            "sub/extra_items.yaml=mapped_extra_items.models",
            *COMMON_ARGS,
        ],
    )
    _generate_mapped_package(
        tmp_path, "mapped_extra_items", "typing.TypedDict", GRANDCHILD_PATH / "sub/extra_items.yaml"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    with _generated_model(output_file, "external_ref_mapping_all_of_extra_items", "Bag") as bag:
        extra_items = bag.__extra_items__
        assert_output(f"{extra_items.__module__}.{extra_items.__qualname__}\n", name="extra_items")


def test_external_ref_mapping_all_of_base_in_input_directory(output_dir: Path) -> None:
    """Keep the generated base where the mapped document is an input too and its schemas get models."""
    run_main_and_assert(
        input_path=INPUT_PATH,
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--external-ref-mapping", "shared.yaml=mapped.models", *COMMON_ARGS],
    )


@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
def test_python_import_all_of_base(
    output_model_type: str, output_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inherit the class that x-python-import names where an allOf member names the component.

    The schema of the component describes the class, as the schema of a mapped document does: a model that requires a
    property only the class declares gets the field, and dataclasses and msgspec Structs write a required field of
    their own as keyword-only. A component whose schema is no object, ``Bud`` with nothing beside x-python-import, is
    still the class it declares.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "python_import_all_of" / "api.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", output_model_type, *COMMON_ARGS],
    )
    for module_name in ("python_import_bases.models", "python_import_bases"):
        monkeypatch.delitem(sys.modules, module_name, raising=False)
    _generate_mapped_package(tmp_path, "python_import_bases", output_model_type)
    monkeypatch.syspath_prepend(str(tmp_path))
    with _generated_model(output_file, "python_import_all_of_base", "Branch") as branch:
        verdicts = _verdicts(
            sys.modules[branch.__module__],
            output_model_type,
            (
                ("Branch", ({"id": 1, "twigs": [{"id": 2}], "name": "a"}, {"name": "a"})),
                ("Grove", ({"id": 1, "twigs": []}, {"id": 1}, {"id": 1, "twigs": 5})),
                ("Sprout", ({"id": 1, "label": "a"}, {"id": 1}, {"label": "a"})),
                ("Holder", ({"twig": {"id": 1}}, {"twig": {"twigs": []}})),
                ("Seedling", ({"size": 1, "name": "a"}, {"size": "one"})),
            ),
        )
    assert_output(verdicts, name="payloads")


def test_python_import_all_of_grandchild_modules(
    output_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read the schema of an x-python-import component through the model of another module that inherits it.

    As with a mapped schema, ``zzz`` is processed before the module of ``Branch`` and ``aaa`` after it, and only the
    modules whose fields name the imported classes import them.
    """
    run_main_and_assert(
        input_path=GRANDCHILD_PATH / "python_import.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[*COMMON_ARGS],
    )
    _generate_mapped_package(
        tmp_path, "python_import_grandchild", "pydantic_v2.BaseModel", GRANDCHILD_PATH / "shared.yaml"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    verdicts: list[str] = []
    for module_path in ("aaa", "zzz"):
        with _generated_package_module(output_dir, module_path) as module:
            verdicts.append(f"{module_path}:\n{_verdicts(module, 'pydantic_v2.BaseModel', GRANDCHILD_PAYLOADS)}")
    assert_output("".join(verdicts), name="payloads")
