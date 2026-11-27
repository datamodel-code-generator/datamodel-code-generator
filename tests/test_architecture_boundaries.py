"""Tests for the architecture layers of the package and the import cycles of ``scripts/``."""

from __future__ import annotations

from pathlib import Path

from scripts._import_cycles import import_cycles
from scripts._package_layers import classify_source_path
from tests.conftest import assert_output

ROOT = Path(__file__).parents[1]
FIXTURE_ROOT = Path(__file__).parent / "data" / "architecture_boundaries"


def test_architecture_boundary_source_classification() -> None:
    """Keep entrypoint, model, schema graph, parser core, requirement, constraint and check flag ownership explicit."""
    source_root = ROOT / "src" / "datamodel_code_generator"
    paths = (
        source_root / "__init__.py",
        source_root / "parser" / "base.py",
        source_root / "parser" / "_passes" / "_shared.py",
        source_root / "parser" / "_facts.py",
        source_root / "parser" / "_schema_graph.py",
        source_root / "parser" / "_intersection.py",
        source_root / "parser" / "_null_facts.py",
        source_root / "parser" / "_schema_dispatch.py",
        source_root / "parser" / "_phases.py",
        source_root / "config.py",
        source_root / "input_model.py",
        source_root / "reference.py",
        source_root / "model" / "base.py",
        source_root / "model" / "__init__.py",
        source_root / "model" / "pydantic_v2" / "base_model.py",
        source_root / "model" / "runtime_validation.py",
        source_root / "model" / "_decisions" / "nullability.py",
        source_root / "model" / "_decisions" / "enum_kind.py",
        source_root / "model" / "_decisions" / "tag_value.py",
        source_root / "model" / "_decisions" / "literal_default.py",
        source_root / "model" / "_decisions" / "union_assembly.py",
        source_root / "_api_generation.py",
        source_root / "_target_config.py",
        source_root / "_requirements.py",
        source_root / "_constraint_applicability.py",
        source_root / "_regex_syntax.py",
        source_root / "types.py",
        source_root / "_runtime" / "client" / "client.py",
    )
    classification = "".join(
        f"{path.relative_to(source_root).as_posix()}: {classify_source_path(path)}\n" for path in paths
    )

    assert_output(classification)


def test_import_cycles_count_the_imports_that_close_them() -> None:
    """Each kind of import back raises the import-cycle keys it can close; the fixture's package says why.

    Imports that run when their module is imported close ``module-level`` cycles; function-local and
    ``TYPE_CHECKING`` imports close ``lazy`` ones only.
    """
    lines = []
    for fixture in sorted((FIXTURE_ROOT / "import_cycles").iterdir()):
        lines.append(f"== {fixture.name}")
        for key, found in import_cycles(fixture).items():
            lines += [f"{key}: {', '.join(f'{path}:{line}' for path, line in cycle)}" for cycle in found]
        lines.append("")
    assert_output("\n".join(lines))
