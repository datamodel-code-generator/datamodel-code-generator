"""Merge into ``_internal.py`` the modules through which it would import itself back.

A package of a cycle becomes a forwarder that imports ``_internal.py``. ``_internal.py`` imports a module below such a
package through it, so the package ran first and found ``_internal.py`` half built: ``import`` failed with ``cannot
import name ... from partially initialized module``. The modules below a package of the cycle that the cycle imports
now join the merge (``--naming-strategy full-path`` puts the inline enum of a dotted schema in such a module), unless
every merged module is that package or below it, where the package runs first. So do the modules that ``_internal.py``
imports and that use a model it holds, and another cycle that ``_internal.py`` imports below a package of the merge.
"""

from __future__ import annotations

import importlib
import json
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    _generated_package_module,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

PAYLOADS_PATH = DATA_PATH / "payloads"
BELOW_PACKAGE = "circular_imports_module_below_package"
BELOW_CYCLE_PACKAGE = "circular_imports_module_below_cycle_package"
USING_BELOW_PACKAGE = "circular_imports_module_using_module_below_package"
NESTED_CYCLE = "circular_imports_nested_cycle_below_package"
USING_ANCESTOR_PACKAGE = "circular_imports_module_using_ancestor_package"
BELOW_PACKAGE_MODELS = {
    "flow_details": ("", "FlowDetails"),
    "treasury.received_debit": ("treasury", "ReceivedDebit"),
    "treasury.transaction_entry": ("treasury", "TransactionEntry"),
    "treasury.sub.detail": ("treasury.sub", "Detail"),
    "treasury.sub.deep.leaf": ("treasury.sub.deep", "Leaf"),
}
BELOW_CYCLE_PACKAGE_MODELS = {
    "a.x": ("a", "X"),
    "a.b.y": ("a.b", "Y"),
    "a.c.z": ("a.c", "Z"),
}
USING_BELOW_PACKAGE_MODELS = {
    "a.item": ("a", "Item"),
    "x.thing": ("x", "Thing"),
    "a.c.leaf": ("a.c", "Leaf"),
    "d.dep": ("d", "Dep"),
}
USING_ANCESTOR_PACKAGE_MODELS = {
    "a.item": ("a", "Item"),
    "x.thing": ("x", "Thing"),
    "q.d.dep": ("q.d", "Dep"),
    "q.base": ("q.d", "Base"),
}
NESTED_CYCLE_MODELS = {
    "root_thing": ("", "RootThing"),
    "a.item": ("a", "Item"),
    "a.sub.s": ("a.sub", "S"),
    "a.peer.p": ("a.peer", "P"),
}
EXPORTS_SCOPE = "circular_imports_exports_scope_module_below_package"
EXPORTS_SCOPE_MODELS = {
    "x.thing": ("x", "Thing"),
    "y.thing": ("y", "Thing"),
    "p.t.leaf": ("p.t", "Leaf"),
    "p.item": ("p", "Item"),
    "p.u.user": ("p.u", "User"),
}
DOTTED_MODULES = "circular_imports_exports_scope_dotted_modules"
DOTTED_MODULES_PATH = JSON_SCHEMA_DATA_PATH / "circular_imports_exports_scope_dotted"
DOTTED_MODULES_MODELS = {
    "x.thing.json": ("x.thing", "Thing"),
    "y.thing.json": ("y.thing", "Thing"),
    "p.t.leaf.json": ("p.t.leaf", "Leaf"),
    "p.item.json": ("p.item", "Item"),
    "p.u.user.json": ("p.u.user", "User"),
    "root_thing.json": ("root_thing", "RootThing"),
}


def _schema_verdict(validator: Draft202012Validator, document: dict[str, Any]) -> str:
    """Return whether the schema accepts a document."""
    return "accepts" if validator.is_valid(document) else "rejects"


def _model_verdict(model: Any, document: dict[str, Any]) -> str:
    """Return whether a generated model validates a document."""
    try:
        model.model_validate(document)
    except ValidationError:
        return "rejects"
    return "accepts"


def _verdicts(
    output_dir: Path,
    name: str,
    models: dict[str, tuple[str, str]],
    validator_of: Callable[[str], Draft202012Validator],
) -> str:
    """Import every module of the generated package on its own, then compare the schema and the models on payloads.

    Each module is imported first in a fresh interpreter state, as a user's first import of the package can be any.
    """
    for path in sorted(output_dir.rglob("*.py")):
        parts = path.relative_to(output_dir).with_suffix("").parts
        with _generated_package_module(output_dir, ".".join(parts[:-1] if parts[-1] == "__init__" else parts)):
            pass
    payloads = json.loads((PAYLOADS_PATH / name / f"{name}.json").read_text())
    lines: list[str] = []
    with _generated_package_module(output_dir, ""):
        for schema_name, documents in payloads.items():
            module_path, class_name = models[schema_name]
            module = importlib.import_module(f"{output_dir.name}.{module_path}" if module_path else output_dir.name)
            model = getattr(module, class_name)
            validator = validator_of(schema_name)
            lines.extend(
                f"{schema_name} {json.dumps(document)}: schema {_schema_verdict(validator, document)}, "
                f"model {_model_verdict(model, document)}\n"
                for document in documents
            )
    return "".join(lines)


def _open_api_verdicts(output_dir: Path, name: str, models: dict[str, tuple[str, str]]) -> str:
    """Compare the schemas of the components of an OpenAPI document and the generated models on payloads."""
    components = yaml.safe_load((OPEN_API_DATA_PATH / f"{name}.yaml").read_text())["components"]
    return _verdicts(
        output_dir,
        name,
        models,
        lambda schema_name: Draft202012Validator({
            "$ref": f"#/components/schemas/{schema_name}",
            "components": components,
        }),
    )


def _dotted_verdicts(output_dir: Path) -> str:
    """Compare the schema files of a directory and the generated models on payloads."""
    registry: Registry[Any] = Registry().with_resources([
        (f"https://example.test/{path.name}", Resource.from_contents(json.loads(path.read_text()), DRAFT202012))
        for path in DOTTED_MODULES_PATH.glob("*.json")
    ])
    return _verdicts(
        output_dir,
        DOTTED_MODULES,
        DOTTED_MODULES_MODELS,
        lambda file_name: Draft202012Validator({"$ref": f"https://example.test/{file_name}"}, registry=registry),
    )


@pytest.mark.parametrize(
    "extra_args",
    [[], ["--naming-strategy", "full-path"]],
    ids=["default", "full-path"],
)
def test_circular_imports_module_below_package(output_dir: Path, extra_args: list[str]) -> None:
    """Merge the modules a cycle imports below the ``treasury`` package into ``_internal.py``, so the package imports.

    ``treasury.sub`` and ``treasury.sub.deep`` are imported by the cycle of ``flow_details`` and ``treasury``; under
    ``--naming-strategy full-path`` so is the module of the inline enum of ``treasury.transaction_entry``, which the
    name ``treasury.TransactionEntryTreasury.type`` puts below ``treasury``. Every module of the package imports, each
    public class stays importable from the module of its schema, and the models accept and reject the payloads as the
    schema does.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / f"{BELOW_PACKAGE}.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", *extra_args],
    )
    assert_output(_open_api_verdicts(output_dir, BELOW_PACKAGE, BELOW_PACKAGE_MODELS), name="payloads")


def test_circular_imports_module_below_cycle_package(output_dir: Path) -> None:
    """Keep the module below the package that holds the whole cycle, which its package imports before ``_internal.py``.

    ``a`` and ``a.b`` form the cycle, so ``a/_internal.py`` imports ``a.c`` while ``a`` is running already: no module
    joins the merge, and every module of the package imports.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / f"{BELOW_CYCLE_PACKAGE}.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )
    assert_output(_open_api_verdicts(output_dir, BELOW_CYCLE_PACKAGE, BELOW_CYCLE_PACKAGE_MODELS), name="payloads")


def test_circular_imports_module_using_module_below_package(output_dir: Path) -> None:
    """Merge ``d`` too, which uses the model of ``a.c`` that joins the merge, as ``_internal.py`` imports ``d``.

    ``a`` and ``x`` form the cycle, which imports ``a.c`` below the package ``a`` and ``d``. With ``a.c`` alone in
    ``_internal.py``, ``d`` imported its model from ``_internal.py`` while ``_internal.py`` imported ``d``: no module of
    the package imported, ``a`` first included, which imported before ``a.c`` joined the merge.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / f"{USING_BELOW_PACKAGE}.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )
    assert_output(_open_api_verdicts(output_dir, USING_BELOW_PACKAGE, USING_BELOW_PACKAGE_MODELS), name="payloads")


def test_circular_imports_module_using_ancestor_package(output_dir: Path) -> None:
    """Keep the name ``Base`` that ``q.d`` imported from its package ``q`` when ``q.d`` joins the merge.

    ``q.d`` uses the model of ``a.c``, which joins the merge of the cycle of ``a`` and ``x``, and ``_internal.py``
    imports ``q.d``, so ``q.d`` joins too and becomes a forwarder. It imported ``Base`` from ``q`` by name, so the
    forwarder imports it from there as well: ``q.base`` validates through ``q.d.Base``.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / f"{USING_ANCESTOR_PACKAGE}.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )
    assert_output(
        _open_api_verdicts(output_dir, USING_ANCESTOR_PACKAGE, USING_ANCESTOR_PACKAGE_MODELS), name="payloads"
    )


def test_circular_imports_nested_cycle_below_package(output_dir: Path) -> None:
    """Merge the cycle of ``a.sub`` and ``a.peer`` into the ``_internal.py`` of the cycle of the root and ``a``.

    The cycle of the root package and ``a`` imports ``a.sub``, of the other cycle, whose ``a/_internal.py`` it imported
    through ``a``, a forwarder of the first ``_internal.py`` while that was half built: no module of the package
    imported. Both cycles now share one ``_internal.py``.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / f"{NESTED_CYCLE}.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
    )
    assert_output(_open_api_verdicts(output_dir, NESTED_CYCLE, NESTED_CYCLE_MODELS), name="payloads")


@pytest.mark.parametrize("scope", ["children", "recursive"])
def test_circular_imports_exports_scope_module_below_package(output_dir: Path, scope: str) -> None:
    """Merge the modules ``_internal.py`` imports below ``p`` that ``p`` re-exports, so ``p`` does not import it back.

    ``x`` and ``y`` form the cycle, and ``x`` uses ``p.t``. With ``--all-exports-scope`` the ``__init__`` module of
    ``p`` imports ``p.t`` and ``p.u``, which uses the cycle: importing ``_internal.py`` imported ``p.t``, so ``p`` ran
    and imported ``p.u``, which imported ``_internal.py`` half built. ``p.t`` and ``p.u`` join the merge, while
    ``p.item`` stays where it is. Every module imports, and the models accept and reject the payloads as the schema
    does.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / f"{EXPORTS_SCOPE}.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", "--all-exports-scope", scope],
    )
    assert_output(_open_api_verdicts(output_dir, EXPORTS_SCOPE, EXPORTS_SCOPE_MODELS), name="payloads")


def test_circular_imports_exports_scope_dotted_modules(output_dir: Path) -> None:
    """Place the dotted modules in their packages, whose ``__init__`` modules re-export the modules below them.

    ``--treat-dot-as-module`` makes ``p.t.leaf.json`` the module ``p/t/leaf.py``, below the packages ``p.t`` and ``p``
    that no schema file stands for. Their ``__init__`` modules import the modules below them as in the OpenAPI
    document of the previous test, and the root module holds ``RootThing``.
    """
    run_main_and_assert(
        input_path=DOTTED_MODULES_PATH,
        output_path=output_dir,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--treat-dot-as-module",
            "--all-exports-scope",
            "recursive",
        ],
    )
    assert_output(_dotted_verdicts(output_dir), name="payloads")
