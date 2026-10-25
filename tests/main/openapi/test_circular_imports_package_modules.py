"""Merge into ``_internal.py`` the modules that a cycle imports through one of its packages.

A package of a cycle becomes a forwarder that imports ``_internal.py``. ``_internal.py`` imports a module below such a
package through it, so the package ran first and found ``_internal.py`` half built: ``import`` failed with ``cannot
import name ... from partially initialized module``. The modules below a package of the cycle that the cycle imports
now join the merge (``--naming-strategy full-path`` puts the inline enum of a dotted schema in such a module), unless
every merged module is that package or below it, where the package runs first.
"""

from __future__ import annotations

import importlib
import json
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, OPEN_API_DATA_PATH, _generated_package_module, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

PAYLOADS_PATH = DATA_PATH / "payloads"
BELOW_PACKAGE = "circular_imports_module_below_package"
BELOW_CYCLE_PACKAGE = "circular_imports_module_below_cycle_package"
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


def _verdicts(output_dir: Path, name: str, models: dict[str, tuple[str, str]]) -> str:
    """Import every module of the generated package on its own, then compare the schema and the models on payloads.

    Each module is imported first in a fresh interpreter state, as a user's first import of the package can be any.
    """
    for path in sorted(output_dir.rglob("*.py")):
        parts = path.relative_to(output_dir).with_suffix("").parts
        with _generated_package_module(output_dir, ".".join(parts[:-1] if parts[-1] == "__init__" else parts)):
            pass
    components = yaml.safe_load((OPEN_API_DATA_PATH / f"{name}.yaml").read_text())["components"]
    payloads = json.loads((PAYLOADS_PATH / name / f"{name}.json").read_text())
    lines: list[str] = []
    with _generated_package_module(output_dir, ""):
        for schema_name, documents in payloads.items():
            module_path, class_name = models[schema_name]
            module = importlib.import_module(f"{output_dir.name}.{module_path}" if module_path else output_dir.name)
            model = getattr(module, class_name)
            validator = Draft202012Validator({"$ref": f"#/components/schemas/{schema_name}", "components": components})
            lines.extend(
                f"{schema_name} {json.dumps(document)}: schema {_schema_verdict(validator, document)}, "
                f"model {_model_verdict(model, document)}\n"
                for document in documents
            )
    return "".join(lines)


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
    assert_output(_verdicts(output_dir, BELOW_PACKAGE, BELOW_PACKAGE_MODELS), name="payloads")


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
    assert_output(_verdicts(output_dir, BELOW_CYCLE_PACKAGE, BELOW_CYCLE_PACKAGE_MODELS), name="payloads")
