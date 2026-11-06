"""Write a TypedDict whose key narrows a key of its base without the base, which a type checker would hold it to."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any, get_type_hints

import msgspec
import pytest
from jsonschema import Draft202012Validator

from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    _generated_model,
    _generated_package_module,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

INPUT_PATH = JSON_SCHEMA_DATA_PATH / "typed_dict_narrowed_keys.json"
DOCUMENTS_PATH = DATA_PATH / "payloads" / "typed_dict_narrowed_keys" / "documents.json"


def _verdict(model: Any, document: dict[str, Any]) -> str:
    """Return whether a generated TypedDict accepts a JSON document."""
    try:
        msgspec.convert(document, model)
    except msgspec.ValidationError:
        return "rejects"
    return "accepts"


@pytest.mark.parametrize(
    "options",
    [
        pytest.param([], id="default"),
        pytest.param(["--base-class", "typing_extensions.TypedDict"], id="base-class"),
        pytest.param(["--base-class-map", '{"Base": "typing_extensions.TypedDict"}'], id="base-class-map"),
    ],
)
def test_typed_dict_narrowed_keys(output_file: Path, options: list[str]) -> None:
    """Declare every key of the bases on a TypedDict that declares a key of a base with another annotation.

    ``NullDropped`` drops the null of ``a``, ``TypeNarrowed`` types ``b`` by a model and ``Required`` requires ``a``,
    which a type checker rejects as a mutable key of a subclass; ``Restated`` declares ``c`` as ``Base`` does and keeps
    its base. ``NarrowedAgain`` narrows the key ``NullDropped`` narrowed, and ``KeptNarrowing`` declares it as
    ``NullDropped`` does, so it keeps that base once ``NullDropped`` writes its keys itself. A class that drops its
    bases takes the configured base class; where ``--base-class-map`` gives ``Base`` one the others do not take, they
    keep their bases. The models accept the documents of the schema and reject the others.
    """
    run_main_and_assert(
        input_path=INPUT_PATH,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--output-model-type", "typing.TypedDict", *options],
    )
    validator = Draft202012Validator(json.loads(INPUT_PATH.read_text()))
    lines: list[str] = []
    with _generated_model(output_file, "typed_dict_narrowed_keys_model", "Model") as model:
        get_type_hints(model, localns=vars(sys.modules[model.__module__]))
        lines.extend(
            f"{json.dumps(document)}: schema {'accepts' if validator.is_valid(document) else 'rejects'}, "
            f"model {_verdict(model, document)}\n"
            for document in json.loads(DOCUMENTS_PATH.read_text())
        )
    assert_output("".join(lines), name="payloads")


def test_typed_dict_narrowed_keys_of_one_python_name(output_file: Path) -> None:
    """Keep apart the keys of one Python name where a TypedDict that narrows a key writes the keys of its base.

    ``Child`` narrows ``x`` and declares ``foo_bar`` beside the ``foo-bar`` of ``Base``, so it declares both keys, each
    required as its own schema says. The model accepts the documents of the schema and rejects the others.
    """
    input_path = JSON_SCHEMA_DATA_PATH / "typed_dict_narrowed_keys_python_names.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--output-model-type", "typing.TypedDict"],
    )
    validator = Draft202012Validator(json.loads(input_path.read_text()))
    documents = [
        {"foo_bar": 1},
        {"foo_bar": 1, "foo-bar": "s", "x": "t"},
        {"foo-bar": "s"},
        {"foo_bar": 1, "foo-bar": 2},
        {"foo_bar": 1, "x": None},
    ]
    with _generated_model(output_file, "typed_dict_narrowed_keys_python_names_model", "Model") as model:
        lines = [
            f"{json.dumps(document)}: schema {'accepts' if validator.is_valid(document) else 'rejects'}, "
            f"model {_verdict(model, document)}\n"
            for document in documents
        ]
    assert_output("".join(lines), name="payloads")


def test_typed_dict_narrowed_keys_modules(output_dir: Path) -> None:
    """Import the models the keys of a base in another module name where a TypedDict writes them without the base.

    ``Child`` narrows ``a`` of ``Base``, so it writes ``detail`` and ``other`` itself and imports ``Detail`` from the
    module of ``Base`` and ``Other`` from its own module, but no longer ``Base``; ``GrandChild`` keeps ``Child``.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "typed_dict_narrowed_keys_modules",
        output_path=output_dir,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--output-model-type", "typing.TypedDict"],
    )
    with _generated_package_module(output_dir, "grand_child") as module:
        get_type_hints(module.GrandChild)
