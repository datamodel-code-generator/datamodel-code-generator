"""Compatibility tests for shared field-constraint models."""

from __future__ import annotations

import ast
import hashlib
import json
import pickle
import subprocess
import sys
from pathlib import Path
from typing import get_type_hints

from pydantic import Field

from datamodel_code_generator.model import ConstraintsBase, pydantic_base
from datamodel_code_generator.model._constraints import Constraints as SharedConstraints
from datamodel_code_generator.model._constraints import PatternConstraints as SharedPatternConstraints
from datamodel_code_generator.model.pydantic_base import Constraints as LegacyConstraints
from datamodel_code_generator.model.pydantic_base import PatternConstraints as LegacyPatternConstraints
from datamodel_code_generator.types import UnionIntFloat
from tests.conftest import assert_output

SOURCE_PATH = Path(__file__).parents[2] / "src" / "datamodel_code_generator"
BOUNDARY_FIXTURE_PATH = Path(__file__).parents[1] / "data" / "model" / "constraints_boundary"
EXPECTED_MODEL_PATH = Path(__file__).parents[1] / "data" / "expected" / "model"


def _find_neutral_pydantic_base_imports(source_path: Path) -> list[str]:
    violations = []
    for source_file in source_path.rglob("*.py"):
        relative_path = source_file.relative_to(source_path)
        if relative_path == Path("model/pydantic_base.py") or relative_path.parts[:2] == ("model", "pydantic_v2"):
            continue

        for node in ast.walk(ast.parse(source_file.read_text(encoding="utf-8"))):
            match node:
                case ast.ImportFrom(module=module) if module and (
                    module == "pydantic_base" or module.endswith("model.pydantic_base")
                ):
                    violations.append(f"{relative_path}:{node.lineno}: from {module} import ...")
                case ast.ImportFrom(module=module, names=names, level=level) if forbidden_names := [
                    name.name for name in names if name.name == "pydantic_base"
                ]:
                    import_from = "." * level + (module or "")
                    violations.extend(
                        f"{relative_path}:{node.lineno}: from {import_from} import {forbidden_name}"
                        for forbidden_name in forbidden_names
                    )
                case ast.Import(names=names) if forbidden_names := [
                    name.name for name in names if name.name == "datamodel_code_generator.model.pydantic_base"
                ]:
                    violations.extend(
                        f"{relative_path}:{node.lineno}: import {forbidden_name}" for forbidden_name in forbidden_names
                    )
    return violations


def test_constraint_public_surface_compatibility() -> None:
    """Keep legacy imports, metadata, schemas, and pickles compatible."""
    lines = [
        f"Constraints identity: {SharedConstraints is LegacyConstraints}",
        f"PatternConstraints identity: {SharedPatternConstraints is LegacyPatternConstraints}",
        f"Field identity: {pydantic_base.Field is Field}",
        f"ConstraintsBase identity: {pydantic_base.ConstraintsBase is ConstraintsBase}",
        f"UnionIntFloat identity: {pydantic_base.UnionIntFloat is UnionIntFloat}",
    ]
    for constraint_type in (LegacyConstraints, LegacyPatternConstraints):
        value = constraint_type.model_validate({"minimum": 1, "pattern": "x", "minItems": 2})
        payload = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        restored = pickle.loads(payload)
        canonical_schema = json.dumps(
            constraint_type.model_json_schema(),
            sort_keys=True,
            separators=(",", ":"),
        )
        lines.extend((
            f"{constraint_type.__name__}:",
            f"  repr: {constraint_type!r}",
            f"  module: {constraint_type.__module__}",
            f"  qualname: {constraint_type.__qualname__}",
            f"  doc: {constraint_type.__doc__!r}",
            f"  mro: {[f'{base.__module__}.{base.__qualname__}' for base in constraint_type.__mro__]!r}",
            f"  annotations: {constraint_type.__annotations__!r}",
            f"  resolved_annotations: {sorted(get_type_hints(constraint_type))!r}",
            f"  fields: {[(name, field.alias) for name, field in constraint_type.model_fields.items()]!r}",
            f"  dump: {value.model_dump(mode='json', by_alias=True, exclude_unset=True)!r}",
            f"  schema_sha256: {hashlib.sha256(canonical_schema.encode()).hexdigest()}",
            f"  pickle_path: {b'datamodel_code_generator.model.pydantic_base' in payload}",
            f"  pickle_identity: {restored.__class__ is constraint_type}",
            f"  pickle_dump: {restored.model_dump(mode='json', by_alias=True, exclude_unset=True)!r}",
        ))

    assert_output("\n".join(lines) + "\n", EXPECTED_MODEL_PATH / "constraints_compatibility.txt")


def test_neutral_model_imports_do_not_load_pydantic_backend() -> None:
    """Keep dataclass and msgspec cold imports independent of the Pydantic backend."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from datamodel_code_generator.model import dataclass as dataclass_model

print(f"dataclass pydantic_base loaded: {'datamodel_code_generator.model.pydantic_base' in sys.modules}")
print(f"dataclass constraint module: {dataclass_model.Constraints.__module__}")

from datamodel_code_generator.model import msgspec as msgspec_model

print(f"msgspec pydantic_base loaded: {'datamodel_code_generator.model.pydantic_base' in sys.modules}")
print(f"msgspec constraint base module: {msgspec_model.Constraints.__mro__[1].__module__}")

from datamodel_code_generator.model._constraints import Constraints, PatternConstraints
from datamodel_code_generator.model.pydantic_base import Constraints as LegacyConstraints
from datamodel_code_generator.model.pydantic_base import PatternConstraints as LegacyPatternConstraints

print(f"legacy constraints identity: {LegacyConstraints is Constraints}")
print(f"legacy pattern identity: {LegacyPatternConstraints is PatternConstraints}")
""",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert_output(result.stdout, EXPECTED_MODEL_PATH / "constraints_imports.txt")


def test_only_pydantic_backend_imports_pydantic_base() -> None:
    """Prevent neutral layers from depending on the Pydantic backend again."""
    violations = _find_neutral_pydantic_base_imports(SOURCE_PATH)
    assert_output(
        "".join(f"{violation}\n" for violation in violations) or "No neutral-layer pydantic_base imports.\n",
        EXPECTED_MODEL_PATH / "constraints_boundary.txt",
    )


def test_pydantic_base_boundary_detector_reports_forbidden_imports() -> None:
    """Keep both supported import forms covered by an external fixture."""
    violations = _find_neutral_pydantic_base_imports(BOUNDARY_FIXTURE_PATH)
    assert_output(
        "".join(f"{violation}\n" for violation in violations),
        EXPECTED_MODEL_PATH / "constraints_boundary_violations.txt",
    )
