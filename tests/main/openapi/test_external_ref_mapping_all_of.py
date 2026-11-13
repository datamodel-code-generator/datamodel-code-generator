"""allOf members that name a schema the output types by an import: of a mapped document, or with x-python-import.

Up to 0.86.0 such a member failed with "A Parser can not resolve classes". The model now inherits the class that
``--external-ref-mapping`` or ``x-python-import`` imports, as a field that names the schema is typed by it. What the
generator writes for the model reads the schema of that class as it reads a generated base: the fields a model
requires or narrows, the keyword-only fields of dataclasses and msgspec Structs, and the models nested in the schema,
which are written as a generated base gives them. An allOf of such a schema and keywords that add no field is typed by
the import, as a field that names the schema is.
"""

from __future__ import annotations

import contextlib
import json
import sys
from typing import TYPE_CHECKING, Any

import msgspec
import pytest
from pydantic import ValidationError

from datamodel_code_generator import DanglingRefWarning
from datamodel_code_generator.__main__ import Exit
from tests.conftest import assert_output
from tests.main.conftest import (
    JSON_SCHEMA_DATA_PATH,
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


def test_external_ref_mapping_all_of_union_branch(output_file: Path) -> None:
    """Read no mapped document for the null of the union that an allOf member's anyOf gives.

    Under --intersect-combined-schema-keywords that union admits null where every part of the allOf does. A branch the
    mapping types by its import (``Soil``) admits none and its document is not read, as without the option, so
    ``absent.yaml`` need not exist; the null branch of ``Ground`` and the untyped ``Plot`` give ``Field`` its None.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "external_ref_mapping_all_of_union" / "api.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            "--external-ref-mapping",
            "absent.yaml=mapped.models",
            "--intersect-combined-schema-keywords",
            *COMMON_ARGS,
        ],
    )


@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
def test_external_ref_mapping_all_of_missing_document(output_model_type: str, output_file: Path) -> None:
    """Inherit the class the mapping imports where the mapped document of an allOf member does not exist (#6277).

    The mapped schema describes the class the mapping imports, so no document is needed: each model is written from
    its own keywords, a property it requires that the missing schema would declare included (``Grove.twigs``), and
    the run warns once that the document does not exist. Up to 0.89.0 generation failed with "$ref file not found",
    while a field that names the schema (``Holder.twig``) never read the document.
    """
    with pytest.warns(DanglingRefWarning, match=r"shared\.yaml' that --external-ref-mapping maps does not exist"):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "external_ref_mapping_all_of_missing.yaml",
            output_path=output_file,
            input_file_type="openapi",
            extra_args=[
                "--output-model-type",
                output_model_type,
                "--external-ref-mapping",
                "absent/shared.yaml=mapped.models",
                *COMMON_ARGS,
            ],
        )


def test_external_ref_mapping_all_of_missing_document_file_uri(tmp_path: Path, output_file: Path) -> None:
    """Inherit the mapped class where the mapping and the allOf member name the missing document by a file URI (#6277).

    The mapping keeps a file URI as written, so the missing document is matched by its URI, not by the path it names.
    """
    missing_uri = (tmp_path / "absent" / "shared.yaml").as_uri()
    input_path = tmp_path / "external_ref_mapping_all_of_missing.yaml"
    input_path.write_text(
        (OPEN_API_DATA_PATH / "external_ref_mapping_all_of_missing.yaml")
        .read_text(encoding="utf-8")
        .replace("absent/shared.yaml", missing_uri),
        encoding="utf-8",
    )
    with pytest.warns(DanglingRefWarning, match=r"shared\.yaml' that --external-ref-mapping maps does not exist"):
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type="openapi",
            extra_args=["--external-ref-mapping", f"{missing_uri}=mapped.models", *COMMON_ARGS],
        )


def test_external_ref_mapping_all_of_missing_document_strict_refs(capsys: pytest.CaptureFixture[str]) -> None:
    """Keep the missing mapped document an error under --strict-refs, as before."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "external_ref_mapping_all_of_missing.yaml",
        input_file_type="openapi",
        extra_args=["--external-ref-mapping", "absent/shared.yaml=mapped.models", "--strict-refs", *COMMON_ARGS],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="$ref file not found:",
    )


def test_python_import_all_of_alias_base(output_file: Path) -> None:
    """Inherit the class that an alias's x-python-import names where an allOf member names the alias.

    Under --intersect-combined-schema-keywords a member naming a ``$ref`` alias takes the alias's target as its base,
    but an alias with ``x-python-import`` is typed by the class it imports wherever it is referenced, so ``Branch``
    inherits that class, not ``Stem``.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "python_import_all_of" / "alias.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--intersect-combined-schema-keywords", *COMMON_ARGS],
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


VALUE_KEYWORDS_PATH = JSON_SCHEMA_DATA_PATH / "external_ref_mapping_all_of_keywords"


@pytest.mark.parametrize("document", ["present", "missing"])
def test_external_ref_mapping_all_of_value_keywords(document: str, output_file: Path, tmp_path: Path) -> None:
    """Type an allOf of a mapped schema and keywords that add no field by the class the mapping imports (#6772).

    A field, an array item and the additional properties are typed as a field naming the schema directly is
    (``plain``), whether the mapped document exists or not; the other keywords, ``maxLength`` and ``description``,
    leave with the reference, as they leave with a local one. Up to 0.95.0 generation failed with "A Parser can not
    resolve classes".
    """
    input_path = VALUE_KEYWORDS_PATH / "order.json"
    if document == "missing":
        (input_path := tmp_path / "input" / "order.json").parent.mkdir()
        input_path.write_text((VALUE_KEYWORDS_PATH / "order.json").read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.warns(DanglingRefWarning) if document == "missing" else contextlib.nullcontext():
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=["--external-ref-mapping", "shared.json=mycompany.shared", *COMMON_ARGS],
        )


def test_external_ref_mapping_all_of_value_keywords_modules(output_dir: Path) -> None:
    """Import the mapped class only in the module whose field the allOf of the mapped schema types (#6772)."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "external_ref_mapping_all_of_keywords_modules",
        output_path=output_dir,
        input_file_type="jsonschema",
        extra_args=[
            "--external-ref-mapping",
            "../external_ref_mapping_all_of_keywords/shared.json=mycompany.shared",
            "--allow-remote-refs",
            *COMMON_ARGS,
        ],
    )


def test_external_ref_mapping_all_of_value_keywords_union(output_file: Path) -> None:
    """Keep the union of an allOf of a mapped schema, an anyOf of objects and a keyword: each branch inherits both."""
    run_main_and_assert(
        input_path=VALUE_KEYWORDS_PATH / "union.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--external-ref-mapping", "shared.json=mycompany.shared", *COMMON_ARGS],
    )


def test_external_ref_mapping_all_of_value_keywords_openapi(output_file: Path) -> None:
    """Type parameters, a response and properties by the mapped class where an allOf adds keywords to it (#6772).

    The request body of the allOf of a mapped object is a model that inherits the class, as a component is.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "external_ref_mapping_all_of_keywords" / "api.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            "--external-ref-mapping",
            "shared.yaml=mycompany.shared",
            "--openapi-scopes",
            "schemas",
            "paths",
            "parameters",
            *COMMON_ARGS,
        ],
    )


def test_python_import_all_of_value_keywords(output_file: Path) -> None:
    """Type an allOf of an x-python-import definition and keywords that add no field by its import (#6786).

    The class it imports (``Amount``) and the type expression in its place (``Price``, ``qualname``) type the field, the
    array item and the additional properties as a field naming the definition is; the definition an allOf base names
    (``Counts``) is still the base of ``Tally``, and an unused definition (``Unused``) imports nothing.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "python_import_all_of_keywords.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[*COMMON_ARGS],
    )


@pytest.mark.cli_doc(
    options=["--use-x-python-import-for-every-ref"],
    option_description="""Type every reference to a schema with `x-python-import` by the class it imports.

A `$ref` to a schema with `x-python-import` at a field is typed by the class it imports. Without this option an allOf
of such a schema and keywords that add no field, such as a `description`, is typed by the model of the schema where a
JSON pointer into another document (`shared.json#/$defs/Amount`), a pointer into the input outside its definitions
(`#/other/Code`) or a whole-document `$ref` (`tally.json`) names it, as in 0.96.0; with it the import types that
allOf as it types the `$ref`, and the model stays written only where it has fields or a root type. Under
`--read-only-write-only-model-type request-response` the option also leaves the readOnly and writeOnly properties of
such a schema out of the request/response split: a model whose field the import types does not split for them, nor
does a model whose allOf bases reach them through such a field.""",
    input_schema="jsonschema/python_import_all_of_keywords_pointer_targets/order.json",
    cli_args=["--use-x-python-import-for-every-ref"],
    golden_output="main/openapi/external_ref_mapping_all_of/python_import_all_of_value_keywords_pointer_targets[on].py",
    comparison_output=(
        "main/openapi/external_ref_mapping_all_of/python_import_all_of_value_keywords_pointer_targets[off].py"
    ),
)
@pytest.mark.parametrize("option_set", ["off", "on"])
def test_python_import_all_of_value_keywords_pointer_targets(output_file: Path, option_set: str) -> None:
    """Type an allOf of an x-python-import schema outside the input's definitions by its import, opt-in (#6797).

    With --use-x-python-import-for-every-ref a definition of another document (``Amount``), the root of a document a
    ``$ref`` names (``tally.json``, and ``ledger.json`` through ``#`` inside it) and a schema at a JSON pointer of the
    input (``Code``) type the allOf of them and keywords that add no field as a field naming them is (``plain``), as a
    definition of the input does. The model of each stays written where it has a root type, and the empty one leaves.
    Without it the allOf is typed by that model, as in 0.96.0.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "python_import_all_of_keywords_pointer_targets" / "order.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[*COMMON_ARGS, *(("--use-x-python-import-for-every-ref",) if option_set == "on" else ())],
        force_exec_validation=True,
    )
