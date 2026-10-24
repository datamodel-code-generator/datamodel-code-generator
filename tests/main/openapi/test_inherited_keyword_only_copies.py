"""Give a dataclass a keyword-only copy of each inherited field that two of its bases put after a default.

``dataclasses`` collects the fields of a class with several bases in reverse MRO order, so a required field of one base
can follow a default of another, and creating the class raises ``non-default argument ... follows default argument``.
No base sees that order: the class redeclares the field keyword-only, as it writes its own fields that follow a default.
"""

from __future__ import annotations

import dataclasses
import json
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter, ValidationError

from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    OPEN_API_DATA_PATH,
    _generated_model,
    _generated_package_module,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from contextlib import AbstractContextManager
    from pathlib import Path

PAYLOADS = json.loads((DATA_PATH / "payloads" / "inherited_keyword_only_copies" / "payloads.json").read_text())
DATACLASS_TYPES = ("dataclasses.dataclass", "pydantic_v2.dataclass")
OPTION_SETS = {
    "default": (),
    "keep_model_order": ("--keep-model-order",),
    "use_annotated": ("--use-annotated",),
    "final_names": ("--order-constructor-fields-by", "final-names"),
}
FORWARD_REFERENCE_OPTIONS = ("--use-annotated", "--target-pydantic-version", "2")


def _model_verdict(model: Any, payload: object) -> str:
    """Return whether the generated model accepts a payload, with the first error type where it rejects it."""
    try:
        TypeAdapter(model).validate_python(payload)
    except ValidationError as error:
        return f"rejects ({error.errors()[0]['type']})"
    return "accepts"


def _record(input_name: str, open_model: Callable[[str], AbstractContextManager[Any]], warnings: list[str]) -> None:
    """Record the warnings, then each model's keyword-only fields and its verdicts on the schema's payloads."""
    document = yaml.safe_load((OPEN_API_DATA_PATH / input_name).read_text())
    lines = [f"warning: {message}" for message in warnings]
    for name, payloads in PAYLOADS[input_name].items():
        validator = Draft202012Validator({"$ref": f"#/components/schemas/{name}", "components": document["components"]})
        with open_model(name) as model:
            keyword_only = [field.name for field in dataclasses.fields(model) if field.kw_only]
            lines.append(f"{name} keyword-only: {', '.join(keyword_only) or '-'}")
            lines.extend(
                f"{name} {json.dumps(payload)}: schema {'accepts' if validator.is_valid(payload) else 'rejects'}, "
                f"model {_model_verdict(model, payload)}"
                for payload in (payloads["valid"], *payloads["invalid"])
            )
    assert_output("".join(f"{line}\n" for line in lines), name="payloads")


def _user_warnings(recwarn: pytest.WarningsRecorder) -> list[str]:
    return list(dict.fromkeys(str(warning.message) for warning in recwarn if warning.category is UserWarning))


def _module_models(output_file: Path) -> Callable[[str], AbstractContextManager[Any]]:
    """Open a class of the generated module, imported while the block runs."""
    return lambda name: _generated_model(output_file, f"inherited_keyword_only_copies_{name}", name)


def _package_models(output_dir: Path) -> Callable[[str], AbstractContextManager[Any]]:
    """Open a class, named ``module.Class``, of the generated package, imported while the block runs."""

    @contextmanager
    def open_model(name: str) -> Iterator[Any]:
        module_path, _, class_name = name.rpartition(".")
        with _generated_package_module(output_dir, module_path) as module:
            yield getattr(module, class_name)

    return open_model


@pytest.mark.parametrize("output_model_type", DATACLASS_TYPES)
@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_inherited_keyword_only_copies(
    output_file: Path, recwarn: pytest.WarningsRecorder, output_model_type: str, option_set: str
) -> None:
    """Redeclare keyword-only the inherited fields that a later base's default precedes, so each class imports.

    ``Branch(Twig, Local)`` puts ``Twig.id`` after ``Local.x``; ``Leaf`` inherits the copy and makes its own ``z``
    keyword-only; ``Fork`` and ``Pair`` copy the fields their own bases set up, ``Pair`` two of them.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_bases_required_after_default.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--formatters", "builtin", "--output-model-type", output_model_type, *OPTION_SETS[option_set]],
    )
    _record("allof_bases_required_after_default.yaml", _module_models(output_file), _user_warnings(recwarn))


@pytest.mark.parametrize("output_model_type", ["msgspec.Struct", "pydantic_v2.BaseModel", "typing.TypedDict"])
def test_inherited_keyword_only_copies_other_backends(output_file: Path, output_model_type: str) -> None:
    """Write no copy where the output has no positional constructor: msgspec flattens the bases, the others name."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_bases_required_after_default.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--formatters", "builtin", "--output-model-type", output_model_type],
    )


def test_inherited_keyword_only_copies_overridden_base(
    output_file: Path, recwarn: pytest.WarningsRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Copy no field of a base that a model-level type override replaces, as its types name models not written.

    ``Twig`` is imported from ``twig_app``, whose ``id`` is keyword-only, so ``Branch`` keeps its body; ``Fork`` and
    ``Pair`` still copy ``o`` of the generated ``Other``.
    """
    package_dir = output_file.parent / "twig_app"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text(
        "from dataclasses import dataclass\n\n\n@dataclass(kw_only=True)\nclass Twig:\n    id: int\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(output_file.parent))
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_bases_required_after_default.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            "--formatters",
            "builtin",
            "--output-model-type",
            "dataclasses.dataclass",
            "--type-overrides",
            json.dumps({"Twig": "twig_app.Twig"}),
        ],
    )
    _record("allof_bases_required_after_default.yaml", _module_models(output_file), _user_warnings(recwarn))


@pytest.mark.parametrize("output_model_type", DATACLASS_TYPES)
@pytest.mark.parametrize("option_set", ["default", "final_names"])
def test_inherited_keyword_only_copies_modules(
    output_dir: Path, recwarn: pytest.WarningsRecorder, output_model_type: str, option_set: str
) -> None:
    """Name the copy's types from the module of the class that takes it, and read every base after its own copies.

    Module ``b`` is processed before ``a2``, whose ``Mid`` copies ``code`` from ``a``; ``b.Leaf`` and ``b.Tail``
    read ``Mid`` with that copy, and ``b.Tail`` copies ``extra``, typed by ``a.Code``. Module ``a`` stays as it was.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_bases_required_after_default_modules.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--formatters", "builtin", "--output-model-type", output_model_type, *OPTION_SETS[option_set]],
    )
    _record("allof_bases_required_after_default_modules.yaml", _package_models(output_dir), _user_warnings(recwarn))


def test_base_forward_reference_assignment(output_file: Path, recwarn: pytest.WarningsRecorder) -> None:
    """Order the child's constructor after the base's forward reference is written by assignment.

    For Pydantic before 2.11 the required ``Base.ref``, which names the later ``Later``, is written as
    ``= Field(...)``, a dataclass default, so ``Child`` makes its own required ``z`` keyword-only.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_base_forward_reference_assignment.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            "--formatters",
            "builtin",
            "--output-model-type",
            "pydantic_v2.dataclass",
            *FORWARD_REFERENCE_OPTIONS,
        ],
    )
    _record("allof_base_forward_reference_assignment.yaml", _module_models(output_file), _user_warnings(recwarn))


def test_base_forward_reference_assignment_modules(output_dir: Path, recwarn: pytest.WarningsRecorder) -> None:
    """Keep the keyword-only ``z`` of a child whose base, in a module processed later, writes ``ref`` by assignment."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_base_forward_reference_assignment_modules.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[
            "--formatters",
            "builtin",
            "--output-model-type",
            "pydantic_v2.dataclass",
            *FORWARD_REFERENCE_OPTIONS,
        ],
    )
    _record(
        "allof_base_forward_reference_assignment_modules.yaml", _package_models(output_dir), _user_warnings(recwarn)
    )
