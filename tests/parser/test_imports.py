"""Import behavior tests for parser modules."""

from __future__ import annotations

import ast
import inspect
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SUBPROCESS_TIMEOUT_SECONDS = 15


def _run_import_probe(code: str) -> str:
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    src_path = str(repo_root / "src")
    env["PYTHONPATH"] = src_path if not env.get("PYTHONPATH") else os.pathsep.join((src_path, env["PYTHONPATH"]))

    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )
    return result.stdout


@pytest.mark.allow_direct_assert
def test_jsonschema_parser_import_does_not_load_inactive_model_generators() -> None:
    """JSON Schema parser import should not load inactive output model generators."""
    module_names = (
        "datamodel_code_generator.model.dataclass",
        "datamodel_code_generator.model.msgspec",
        "datamodel_code_generator.model.typed_dict",
    )
    code = (
        "import sys\n"
        "import datamodel_code_generator.parser.jsonschema\n"
        f"module_names = {module_names!r}\n"
        "print('\\n'.join(name for name in module_names if name in sys.modules))\n"
    )

    assert _run_import_probe(code) == "\n"


@pytest.mark.allow_direct_assert
def test_plain_jsonschema_generation_does_not_load_structured_python_type_modules() -> None:
    """Keep all x-python-type-only modules off the ordinary schema path."""
    module_names = (
        "datamodel_code_generator._python_type_binding",
        "datamodel_code_generator._python_type_import_registry",
        "datamodel_code_generator._python_type_runtime",
        "datamodel_code_generator.parser._python_type_imports",
    )
    code = (
        "import sys\n"
        "from datamodel_code_generator.parser.jsonschema import JsonSchemaParser\n"
        'parser = JsonSchemaParser(\'{"title": "Model", "type": "object"}\')\n'
        "parser.parse(format_=False)\n"
        f"module_names = {module_names!r}\n"
        "print('\\n'.join(name for name in module_names if name in sys.modules))\n"
    )

    assert _run_import_probe(code) == "\n"


@pytest.mark.allow_direct_assert
def test_generation_import_does_not_load_pydantic_v2_dependency_policy() -> None:
    """Keep the generic generation index independent from the Pydantic v2 backend and version gate."""
    module_names = (
        "datamodel_code_generator.model.pydantic_v2",
        "datamodel_code_generator.model.pydantic_v2.version",
    )
    code = (
        "import sys\n"
        "import datamodel_code_generator.parser.generation\n"
        f"module_names = {module_names!r}\n"
        "print('\\n'.join(name for name in module_names if name in sys.modules))\n"
    )

    assert _run_import_probe(code) == "\n"


@pytest.mark.allow_direct_assert
def test_parser_model_compatibility_attributes_remain_available() -> None:
    """Parser modules should keep moved compatibility attributes available."""
    code = (
        "from datamodel_code_generator.parser import base, jsonschema\n"
        "from datamodel_code_generator.parser.base import dataclass_model, msgspec_model\n"
        "from datamodel_code_generator.parser.jsonschema import TypedDictModel\n"
        "assert base.dataclass_model.DataClass.__name__ == 'DataClass'\n"
        "assert base.msgspec_model.Struct.__name__ == 'Struct'\n"
        "assert jsonschema.TypedDictModel.__name__ == 'TypedDict'\n"
        "assert dataclass_model.DataClass.__name__ == 'DataClass'\n"
        "assert msgspec_model.Struct.__name__ == 'Struct'\n"
        "assert TypedDictModel.__name__ == 'TypedDict'\n"
        "print('ok')\n"
    )

    assert _run_import_probe(code) == "ok\n"


@pytest.mark.allow_direct_assert
def test_parser_model_compatibility_attributes_load_in_process() -> None:
    """Parser module compatibility shims should resolve expected model objects."""
    from datamodel_code_generator.parser import base, jsonschema

    assert base.dataclass_model.DataClass.__name__ == "DataClass"
    assert base.msgspec_model.Struct.__name__ == "Struct"
    assert jsonschema.TypedDictModel.__name__ == "TypedDict"


def test_parser_model_compatibility_attributes_reject_unknown_names() -> None:
    """Parser module compatibility shims should reject unknown attributes."""
    from datamodel_code_generator.parser import base, jsonschema

    with pytest.raises(AttributeError, match="missing_model"):
        _ = base.missing_model
    with pytest.raises(AttributeError, match="MissingTypedDictModel"):
        _ = jsonschema.MissingTypedDictModel


@pytest.mark.allow_direct_assert
def test_parser_behavior_capabilities_have_no_concrete_helper_dependencies() -> None:
    """Keep output-specific field and reuse behavior behind DataModel capabilities."""
    from datamodel_code_generator.model.base import DataModel
    from datamodel_code_generator.parser import base

    checker_tree = ast.parse(textwrap.dedent(inspect.getsource(DataModel._field_assignment_facts)))
    tree_reuse_source = inspect.getsource(base.structure.create_shared_module_from_duplicates)

    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(checker_tree))
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "has_field_assignment" for node in ast.walk(checker_tree)
    )
    assert "_is_pydantic_v2_base_model" not in tree_reuse_source
    assert "_is_dataclass_data_model" not in tree_reuse_source
