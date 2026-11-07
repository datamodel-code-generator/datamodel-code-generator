"""Runtime and output coverage for structured defaults through msgspec aliases."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

import msgspec
import pytest
from typing_extensions import TypeAliasType

from datamodel_code_generator import DataModelType, InputFileType, PythonVersion
from datamodel_code_generator.format import Formatter
from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    EXPECTED_GRAPHQL_PATH,
    GRAPHQL_DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    _generated_model,
    run_generate_file_and_assert,
    run_main_and_assert,
)
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH, assert_file_content

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("use_alias_type", [False, True])
@pytest.mark.parametrize(
    "target",
    [
        PythonVersion.PY_310,
        pytest.param(
            PythonVersion.PY_312,
            marks=pytest.mark.skipif(sys.version_info < (3, 12), reason="Runtime requires Python type statements"),
        ),
    ],
)
def test_msgspec_alias_structured_defaults(
    entrypoint: str, target: PythonVersion, use_alias_type: bool, output_file: Path
) -> None:
    """Construct real Struct defaults through aliases and preserve explicit validation."""
    input_path = JSON_SCHEMA_DATA_PATH / "msgspec_alias_defaults.json"
    # Black 22 cannot format PEP 695 type statements; keep their native runtime coverage.
    suffix = "_type" if use_alias_type and target == PythonVersion.PY_310 else ""
    expected = f"msgspec_alias_defaults_{target.value.replace('.', '_')}{suffix}.py"
    if entrypoint == "cli":
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type="jsonschema",
            assert_func=assert_file_content,
            expected_file=expected,
            extra_args=[
                "--output-model-type",
                "msgspec.Struct",
                "--use-type-alias",
                "--target-python-version",
                target.value,
                "--disable-timestamp",
                *(["--formatters", "builtin"] if target == PythonVersion.PY_312 else []),
                *(["--use-type-alias-type"] if use_alias_type else []),
            ],
        )
    else:
        run_generate_file_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type=InputFileType.JsonSchema,
            assert_func=assert_file_content,
            expected_file=expected,
            output_model_type=DataModelType.MsgspecStruct,
            use_type_alias=True,
            use_type_alias_type=use_alias_type,
            target_python_version=target,
            disable_timestamp=True,
            **({"formatters": [Formatter.BUILTIN]} if target == PythonVersion.PY_312 else {}),
        )

    payloads = json.loads((DATA_PATH / "payloads" / "msgspec_alias_defaults_runtime.json").read_text())
    with _generated_model(output_file, "msgspec_alias_defaults_runtime", "AliasDefaults") as model:
        if use_alias_type and target == PythonVersion.PY_310 and TypeAliasType.__module__ == "typing_extensions":
            # msgspec supports native aliases, but not the unchanged typing_extensions backport.
            with pytest.raises(TypeError, match="Type 'ItemAlias' is not supported"):
                msgspec.convert(payloads["default"], type=model)
            return
        first = msgspec.convert(payloads["default"], type=model)
        second = msgspec.convert(payloads["default"], type=model)
        explicit = msgspec.convert(payloads["explicit"], type=model)
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert(payloads["invalid"], type=model)
        actual = {
            "defaults": msgspec.to_builtins(first),
            "object_types": {name: type(getattr(first, name)).__name__ for name in payloads["objects"]},
            "list_item_types": {
                name: [type(value).__name__ for value in getattr(first, name)] for name in payloads["lists"]
            },
            "explicit": msgspec.to_builtins(explicit),
        }
        first.aliased.id = 99
        first.alias_list[0].id = 99
        first.empty_alias_list.append(first.aliased)
        actual["independent_defaults"] = msgspec.to_builtins(second)
    assert_output(f"{json.dumps(actual, indent=2)}\n", EXPECTED_JSON_SCHEMA_PATH / "msgspec_alias_defaults_runtime.txt")


@pytest.mark.parametrize(
    "output_model_type",
    [DataModelType.MsgspecStruct, DataModelType.PydanticV2BaseModel, DataModelType.DataclassesDataclass],
)
def test_msgspec_alias_default_controls(output_model_type: DataModelType, output_file: Path) -> None:
    """Keep direct refs, primitive aliases, empty factories and other backends byte-identical."""
    run_generate_file_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "msgspec_alias_default_controls.json",
        output_path=output_file,
        input_file_type=InputFileType.JsonSchema,
        assert_func=assert_file_content,
        expected_file=f"msgspec_alias_default_controls_{output_model_type.value.replace('.', '_')}.py",
        output_model_type=output_model_type,
        use_type_alias=True,
        target_python_version=PythonVersion.PY_310,
        disable_timestamp=True,
    )


def test_msgspec_recursive_alias_default(output_file: Path) -> None:
    """Keep recursive non-model alias defaults unchanged without looping during generation.

    msgspec decodes no type alias on a recursion that no Struct breaks (Values, and CycleA with CycleB), which the
    generation warns about.
    """
    with pytest.warns(
        UserWarning, match=r"msgspec cannot decode the type alias(es)? (Values|CycleA, CycleB), or a type that uses"
    ):
        run_generate_file_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "msgspec_alias_defaults_recursive.json",
            output_path=output_file,
            input_file_type=InputFileType.JsonSchema,
            assert_func=assert_file_content,
            expected_file="msgspec_alias_defaults_recursive.py",
            output_model_type=DataModelType.MsgspecStruct,
            use_type_alias=True,
            target_python_version=PythonVersion.PY_310,
            disable_timestamp=True,
        )


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("use_alias_type", [False, True])
def test_msgspec_graphql_scalar_alias_defaults(entrypoint: str, use_alias_type: bool, output_file: Path) -> None:
    """Scalar aliases with no model fields retain their original default factories."""
    expected = EXPECTED_GRAPHQL_PATH / f"msgspec_scalar_alias_defaults_{use_alias_type}.py"
    input_path = GRAPHQL_DATA_PATH / "msgspec_scalar_alias_defaults.graphql"
    if entrypoint == "cli":
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type="graphql",
            assert_func=assert_file_content,
            expected_file=expected,
            extra_args=[
                "--output-model-type",
                "msgspec.Struct",
                "--target-python-version",
                "3.10",
                "--disable-timestamp",
                *(["--use-type-alias-type"] if use_alias_type else []),
            ],
        )
    else:
        run_generate_file_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type=InputFileType.GraphQL,
            assert_func=assert_file_content,
            expected_file=expected,
            output_model_type=DataModelType.MsgspecStruct,
            target_python_version=PythonVersion.PY_310,
            use_type_alias_type=use_alias_type,
            disable_timestamp=True,
        )
    payloads = json.loads((DATA_PATH / "payloads" / "msgspec_scalar_alias_defaults.json").read_text())
    with _generated_model(output_file, "msgspec_scalar_alias_defaults_runtime", "ScalarDefaults") as model:
        if use_alias_type and TypeAliasType.__module__ == "typing_extensions":
            with pytest.raises(TypeError, match="Type 'Int' is not supported"):
                msgspec.convert(payloads["default"], type=model)
            return
        default = msgspec.convert(payloads["default"], type=model)
        explicit = msgspec.convert(payloads["explicit"], type=model)
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert(payloads["invalid"], type=model)
        actual = {"defaults": msgspec.to_builtins(default), "explicit": msgspec.to_builtins(explicit)}
    assert_output(
        f"{json.dumps(actual, indent=2)}\n", EXPECTED_GRAPHQL_PATH / "msgspec_scalar_alias_defaults_runtime.txt"
    )


def test_msgspec_union_member_defaults(output_file: Path) -> None:
    """Convert a union's default only to the member it fits, as the union writes it, so ``Model()`` builds its defaults.

    A Struct takes an object default, holding its tag if it holds the tag field, a type alias what its type takes; a
    list default beside a Struct member stays the value, and one of Structs beside a str or None converts to the list,
    as the list of a field does, with its item type admitting None where an item is None. Valid documents decode and
    invalid ones are rejected.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "msgspec_union_member_defaults.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "msgspec.Struct",
            "--use-msgspec-tagged-unions",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ],
    )
    documents = {
        "valid": {
            "listBesideStruct": {"a": "s"},
            "objectBesideList": ["t"],
            "listBesideAlias": 3,
            "listOfStructsBesideString": "r",
            "listOfStructsOrNull": None,
            "secondTaggedVariant": {"kind": "cat", "meow": "m"},
            "structMapsBesideString": [{"j": {"a": "p"}}],
            "nullItemBesideString": "o",
            "nullItem": [None, {"a": "l"}],
        },
        "invalid": {"listBesideStruct": [1]},
        "invalid_tag": {"secondTaggedVariant": {"kind": "cow"}},
    }
    with _generated_model(output_file, "msgspec_union_member_defaults_model", "Model") as model:
        default = model()
        actual: dict[str, object] = {
            "defaults": msgspec.to_builtins(default),
            "default_types": {field: type(getattr(default, field)).__name__ for field in default.__struct_fields__},
            "list_item_types": {
                field: [type(item).__name__ for item in value]
                for field in default.__struct_fields__
                if isinstance(value := getattr(default, field), list)
            },
        }
        actual.update((label, _converted(model, document)) for label, document in documents.items())
    assert_output(f"{json.dumps(actual, indent=2)}\n")


def test_msgspec_nullable_alias_item_union_default(output_file: Path) -> None:
    """Convert a list default beside a str to the list of a type alias that writes None, None items included.

    The alias of OpenAPI 3.0 ``nullable`` writes ``PodSpec | None``, so it takes the None item of the default, and
    ``Model()`` holds a Struct for the object item.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "msgspec_nullable_alias_item_default.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", "msgspec.Struct", "--formatters", "builtin", "--disable-timestamp"],
    )
    with _generated_model(output_file, "msgspec_nullable_alias_item_default_model", "Model") as model:
        default = model()
        actual = {
            "defaults": msgspec.to_builtins(default),
            "item_types": [type(item).__name__ for item in default.pods],
        }
    assert_output(f"{json.dumps(actual, indent=2)}\n")


def test_msgspec_array_like_struct_union_defaults(output_file: Path) -> None:
    """Convert an array default beside a str to a Struct msgspec reads from an array, so ``Model()`` holds Structs.

    ``Row`` takes ``array_like=True`` from the template data, so msgspec decodes it from an array, not an object, and
    ``['x']`` converts to ``Row``, ``[['y']]`` to ``list[Row]``.
    """
    (template_data := output_file.parent / "array_like.json").write_text(
        json.dumps({"Row": {"base_class_kwargs": {"array_like": True}}}), encoding="utf-8"
    )
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "msgspec_array_like_union_defaults.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "msgspec.Struct",
            "--extra-template-data",
            str(template_data),
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ],
    )
    with _generated_model(output_file, "msgspec_array_like_union_defaults_model", "Model") as model:
        default = model()
        actual = {
            "defaults": msgspec.to_builtins(default),
            "types": {
                "rowBesideString": type(default.rowBesideString).__name__,
                "rowsBesideString": [type(item).__name__ for item in default.rowsBesideString],
            },
        }
    assert_output(f"{json.dumps(actual, indent=2)}\n")


def test_msgspec_array_like_struct_object_defaults(output_file: Path) -> None:
    """Convert an object default for a Struct msgspec reads from an array from its attributes, so it decodes.

    ``Row`` takes ``array_like=True`` from the template data, so msgspec reads it from an array, or from the attributes
    of an object, not from an object: ``Model()`` and decoding ``{}`` hold the same Structs, for a field of the Struct,
    a union, a list, a type alias and a Struct holding it, by its arrays and maps too. An array default converts as
    before.
    """
    (template_data := output_file.parent / "array_like.json").write_text(
        json.dumps({"Row": {"base_class_kwargs": {"array_like": True}}}), encoding="utf-8"
    )
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "msgspec_array_like_object_defaults.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "msgspec.Struct",
            "--extra-template-data",
            str(template_data),
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ],
    )
    with _generated_model(output_file, "msgspec_array_like_object_defaults_model", "Model") as model:
        default = model()
        decoded = msgspec.json.decode(b"{}", type=model)
        actual = {
            "defaults": {field: repr(getattr(default, field)) for field in default.__struct_fields__},
            "decoded_equal": decoded == default,
        }
    assert_output(f"{json.dumps(actual, indent=2)}\n")


def test_msgspec_array_member_union_defaults(output_file: Path) -> None:
    """Convert a default beside two array-like members only to the member whose items it fits, so ``Model()`` builds.

    msgspec decodes no union of two array-like members, which generation warns about; the defaults still build, a
    list of str as the value, a list of objects as the list of Structs a type alias names and a list of a Literal's
    values as the list of the Literal.
    """
    with pytest.warns(
        UserWarning, match=r"msgspec cannot decode the union Model\.\w+: it holds the array-like members"
    ):
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "msgspec_union_array_member_defaults.json",
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=["--output-model-type", "msgspec.Struct", "--formatters", "builtin", "--disable-timestamp"],
        )
    with _generated_model(output_file, "msgspec_union_array_member_defaults_model", "Model") as model:
        default = model()
        actual = {
            "defaults": msgspec.to_builtins(default),
            "item_types": {
                field: [type(item).__name__ for item in getattr(default, field)] for field in default.__struct_fields__
            },
        }
    assert_output(f"{json.dumps(actual, indent=2)}\n")


@pytest.mark.parametrize(
    "extra_args",
    [pytest.param([], id="future-imports"), pytest.param(["--disable-future-imports"], id="no-future-imports")],
)
def test_msgspec_container_struct_defaults(extra_args: list[str], output_file: Path) -> None:
    """Convert a default of a map, a tuple, nested lists or a list of a union of Structs to the type it is written as.

    ``Model()`` holds the Structs the type names, keys and positions included, as decoding its encoded defaults does;
    a type alias of a map converts like the map, an empty one keeps the builtin factory, and defaults of types that
    name no Struct stay values. A Struct the field's hint quotes, as the field ``PodSpec`` hides the class without
    future imports, is named directly. Valid documents decode and invalid ones are rejected.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "msgspec_container_struct_defaults.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "msgspec.Struct",
            "--formatters",
            "builtin",
            "--disable-timestamp",
            *extra_args,
        ],
    )
    documents = {
        "valid": {"structMap": {"k": {"name": "o"}}, "pair": [{"name": "n"}, 2], "structMapOrString": "m"},
        "invalid": {"structMap": {"k": {"name": 1}}},
    }
    with _generated_model(output_file, "msgspec_container_struct_defaults_model", "Model") as model:
        default = model()
        actual: dict[str, object] = {
            "defaults": msgspec.to_builtins(default),
            "default_types": {field: _type_names(getattr(default, field)) for field in default.__struct_fields__},
            "decoded_defaults_equal": msgspec.json.decode(msgspec.json.encode(default), type=model) == default,
        }
        actual.update((label, _converted(model, document)) for label, document in documents.items())
    assert_output(f"{json.dumps(actual, indent=2)}\n")


def test_msgspec_struct_beside_map_defaults(output_file: Path) -> None:
    """Keep the default of a union of a Struct beside a map a value, also inside a list or a type alias.

    msgspec takes no such union, which generation warns about, so a conversion would raise in ``Model()``; the defaults
    stay the values they are written as.
    """
    with pytest.warns(UserWarning, match=r"msgspec cannot decode the (union|type alias)"):
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "msgspec_struct_beside_map_defaults.json",
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=["--output-model-type", "msgspec.Struct", "--formatters", "builtin", "--disable-timestamp"],
        )
    with _generated_model(output_file, "msgspec_struct_beside_map_defaults_model", "Model") as model:
        default = model()
        actual = {field: _type_names(getattr(default, field)) for field in default.__struct_fields__}
    assert_output(f"{json.dumps(actual, indent=2)}\n")


def _type_names(value: object) -> object:
    """Return the class names of a value and of the items, values and positions it holds."""
    match value:
        case dict():
            return {key: _type_names(item) for key, item in value.items()}
        case list() | tuple():
            return [type(value).__name__, *map(_type_names, value)]
    return type(value).__name__


def _converted(model: object, document: object) -> object:
    """Return the builtins of a document converted to a generated Struct, or why msgspec rejects it."""
    try:
        return msgspec.to_builtins(msgspec.convert(document, type=model))
    except msgspec.ValidationError as error:
        return f"rejected: {error}"
