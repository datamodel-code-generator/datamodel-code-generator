"""Tests for cross-layer architecture guardrails."""

from __future__ import annotations

import re
import runpy
import subprocess
import sys
from pathlib import Path
from shutil import copyfile

import pytest

from scripts import check_architecture_boundaries
from scripts._import_cycles import import_cycles
from tests.conftest import assert_output

ROOT = Path(__file__).parents[1]
FIXTURE_ROOT = Path(__file__).parent / "data" / "architecture_boundaries"
CHECKER_FILES = ("check_architecture_boundaries.py", "_import_cycles.py", "_source_model.py")
"""The guard and the modules it reads imports with, as a copy of the guard in a tree needs them."""


def _copy_checker(root: Path) -> Path:
    """Copy the guard and its modules into ``root/scripts`` and return the copied guard."""
    (root / "scripts").mkdir()
    for name in CHECKER_FILES:
        copyfile(ROOT / "scripts" / name, root / "scripts" / name)
    return root / "scripts" / "check_architecture_boundaries.py"


def test_source_tree_respects_architecture_boundaries() -> None:
    """Keep production dependencies within declared capabilities and bounded legacy exceptions."""
    violations = check_architecture_boundaries.check_paths([ROOT / "src" / "datamodel_code_generator"])

    assert_output(
        check_architecture_boundaries.format_report(violations),
    )


def test_architecture_boundary_detector_reports_cross_layer_dependencies() -> None:
    """Cover static, relative, dynamic, semantic, and private import violations."""
    files: list[tuple[Path, check_architecture_boundaries.Layer]] = [
        (FIXTURE_ROOT / "entrypoint" / "forbidden.py", "entrypoint"),
        (FIXTURE_ROOT / "parser" / "forbidden.py", "parser"),
        (FIXTURE_ROOT / "parser_passes" / "forbidden.py", "parser-passes"),
        (FIXTURE_ROOT / "parser_core" / "forbidden.py", "parser-core"),
        (FIXTURE_ROOT / "facts" / "forbidden.py", "facts"),
        (FIXTURE_ROOT / "schema_graph" / "forbidden.py", "schema-graph"),
        (FIXTURE_ROOT / "config" / "forbidden.py", "config"),
        (FIXTURE_ROOT / "input_model" / "forbidden.py", "input-model"),
        (FIXTURE_ROOT / "reference" / "forbidden.py", "reference"),
        (FIXTURE_ROOT / "output_model" / "forbidden.py", "output-model"),
        (FIXTURE_ROOT / "shared_model" / "forbidden.py", "shared-model"),
        (FIXTURE_ROOT / "shared" / "forbidden.py", "shared"),
        (FIXTURE_ROOT / "target" / "forbidden.py", "target"),
        (FIXTURE_ROOT / "requirements" / "forbidden.py", "requirements"),
        (FIXTURE_ROOT / "constraints" / "forbidden.py", "constraints"),
        (FIXTURE_ROOT / "regex" / "forbidden.py", "regex"),
        (FIXTURE_ROOT / "runtime" / "forbidden.py", "runtime"),
    ]
    violations = check_architecture_boundaries.check_files(files, allowlist={})

    assert_output(
        check_architecture_boundaries.format_report(violations),
    )


@pytest.mark.parametrize(
    "fixture",
    [
        pytest.param(FIXTURE_ROOT / "shared_model_dynamic_import" / "forbidden.py", id="shared_model_dynamic_import"),
        pytest.param(FIXTURE_ROOT / "shared_model_dynamic_import" / "allowed.py", id="clean"),
    ],
)
def test_architecture_boundary_detector_handles_shared_model_dynamic_imports(fixture: Path) -> None:
    """Reject concrete dynamic imports while allowing shadowed aliases without a target."""
    violations = check_architecture_boundaries.check_files([(fixture, "shared-model")], allowlist={})

    assert_output(check_architecture_boundaries.format_report(violations))


def test_architecture_boundary_detector_fails_closed_where_the_source_model_cannot_read(tmp_path: Path) -> None:
    """Report an import that leaves the top-level package and a module the source model refuses, as violations."""
    stored = [FIXTURE_ROOT / "unreadable" / "import.py.txt", FIXTURE_ROOT / "unreadable" / "source.py.txt"]
    files: list[tuple[Path, check_architecture_boundaries.Layer]] = []
    for path in stored:
        copied = tmp_path / path.name.removesuffix(".txt")
        copyfile(path, copied)
        files.append((copied, "shared"))
    report = check_architecture_boundaries.format_report(check_architecture_boundaries.check_files(files, allowlist={}))

    assert_output(report.replace(tmp_path.as_posix(), "tests/data/architecture_boundaries/unreadable"))


def test_architecture_boundary_detector_reports_a_dependency_the_source_model_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report a module of the package that a file's names lead to and the source model refuses, not skip it."""
    package = tmp_path / "src" / "datamodel_code_generator"
    package.mkdir(parents=True)
    copyfile(FIXTURE_ROOT / "unreadable" / "source.py.txt", package / "dependency.py")
    checked = tmp_path / "config.py"
    checked.write_text("from datamodel_code_generator.dependency import Backend\n\nBackend.run()\n", encoding="utf-8")
    monkeypatch.setattr(check_architecture_boundaries, "SOURCE_ROOT", package)
    report = check_architecture_boundaries.format_report(
        check_architecture_boundaries.check_files([(checked, "config")], allowlist={})
    )

    assert_output(report.replace(tmp_path.resolve().as_posix(), "TMP").replace(tmp_path.as_posix(), "TMP"))


def test_architecture_boundary_detector_rejects_root_and_main_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail each read of a root name or of __main__ below the root, in every import form and attribute read.

    Submodules read through the root pass, and so do the root's own re-exports and __main__ reading the root; a root
    binding that shadows a submodule, a name its ``__getattr__`` serves and the root reading __main__ fail.
    """
    package = FIXTURE_ROOT / "root_import" / "datamodel_code_generator"
    monkeypatch.setattr(check_architecture_boundaries, "SOURCE_ROOT", package)
    violations = check_architecture_boundaries.check_paths([package], allowlist={})

    assert_output(check_architecture_boundaries.format_report(violations))


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
        source_root / "model" / "_views.py",
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
        f"{path.relative_to(source_root).as_posix()}: {check_architecture_boundaries._classify_source_path(path)}\n"
        for path in paths
    )

    assert_output(classification)


def test_import_cycles_count_the_imports_that_close_them() -> None:
    """Each kind of import back raises the import-cycle keys it can close; the fixture's package says why.

    Imports that run when their module is imported close ``module-level`` cycles, which the guard also rejects;
    function-local and ``TYPE_CHECKING`` imports close ``lazy`` ones only.
    """
    lines = []
    for fixture in sorted((FIXTURE_ROOT / "import_cycles").iterdir()):
        lines.append(f"== {fixture.name}")
        for key, found in import_cycles(fixture).items():
            lines += [f"{key}: {', '.join(f'{path}:{line}' for path, line in cycle)}" for cycle in found]
        lines.append(
            check_architecture_boundaries.format_report(check_architecture_boundaries.import_cycle_violations(fixture))
        )
    assert_output("\n".join(lines))


def test_architecture_boundary_detector_allows_shared_and_composition_model_dependencies() -> None:
    """Keep neutral registry access and concrete backend composition available to their owners."""
    files: list[tuple[Path, check_architecture_boundaries.Layer]] = [
        (FIXTURE_ROOT / "shared_model" / "allowed.py", "shared-model"),
        (FIXTURE_ROOT / "model_composition" / "allowed.py", "model-composition"),
    ]
    violations = check_architecture_boundaries.check_files(files, allowlist={})

    assert_output(
        check_architecture_boundaries.format_report(violations),
    )


def test_architecture_boundary_detector_reports_stale_allowlist_entries() -> None:
    """Require legacy exceptions to disappear when their matching debt is removed."""
    clean_fixture = FIXTURE_ROOT / "stale" / "clean.py"
    stale_key = check_architecture_boundaries.BoundaryKey(
        "tests/data/architecture_boundaries/stale/clean.py",
        "missing_legacy_hook",
        "backend-import",
        "datamodel_code_generator.model.pydantic_v2",
    )
    violations = check_architecture_boundaries.check_files(
        [(clean_fixture, "parser")],
        allowlist={
            stale_key: check_architecture_boundaries.AllowlistEntry("fixture for stale-entry reporting"),
        },
    )

    assert_output(
        check_architecture_boundaries.format_report(violations),
    )


def test_reference_allowlist_is_symbol_specific() -> None:
    """Do not let a compatibility import allowance hide another backend symbol."""
    fixture = FIXTURE_ROOT / "reference" / "symbol_specific.py"
    key = check_architecture_boundaries.BoundaryKey(
        "tests/data/architecture_boundaries/reference/symbol_specific.py",
        "load_unrelated_backend_symbol",
        "reference-backend-import",
        "datamodel_code_generator.model.field_name.PydanticFieldNameResolver",
    )
    violations = check_architecture_boundaries.check_files(
        [(fixture, "reference")],
        allowlist={
            key: check_architecture_boundaries.AllowlistEntry("fixture for symbol-specific compatibility import")
        },
    )

    assert_output(
        check_architecture_boundaries.format_report(violations),
    )


def test_architecture_boundary_allowlist_contract_and_file_discovery(tmp_path: Path) -> None:
    """Cover invalid allowlist metadata, outside paths, and direct file targets."""

    def allowlist_error(reason: str, count: int, expected: str) -> str:
        with pytest.raises(ValueError, match=expected) as error:
            check_architecture_boundaries.AllowlistEntry(reason, count)
        return str(error.value)

    messages = [
        allowlist_error("", 1, "architecture boundary allowlist entries require a reason"),
        allowlist_error("bounded fixture", 0, "architecture boundary allowlist counts must be positive"),
    ]

    clean_fixture = FIXTURE_ROOT / "stale" / "clean.py"
    direct_files = check_architecture_boundaries.iter_python_files(
        [clean_fixture, clean_fixture.with_suffix(".txt")],
    )
    outside_path = tmp_path / "outside.py"
    outside_path_preserved = check_architecture_boundaries._display_path(outside_path) == outside_path.as_posix()
    messages.extend(
        (
            f"direct Python files: {len(direct_files)}",
            f"outside path preserved: {outside_path_preserved}",
        ),
    )

    assert_output("\n".join(messages) + "\n")


def test_architecture_boundary_cli_reports_actionable_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exercise the clean CLI, an abnormal dependency, and the executable entry point.

    The failure in a source file is stored by its file and symbol (the ``in`` part), not by its line and column, so
    an edit above it in that file leaves the stored output as it is.
    """
    success_code = check_architecture_boundaries.main([str(ROOT / "src" / "datamodel_code_generator")])
    clean_output = capsys.readouterr()

    legacy_key = check_architecture_boundaries.BoundaryKey(
        "src/datamodel_code_generator/reference.py",
        "_default_field_name_resolver_class",
        "reference-backend-import",
        "datamodel_code_generator.model.field_name.PydanticFieldNameResolver",
    )
    monkeypatch.delitem(check_architecture_boundaries.DEFAULT_ALLOWLIST, legacy_key)
    failure_code = check_architecture_boundaries.main([str(ROOT / "src" / "datamodel_code_generator" / "reference.py")])
    failure_by_symbol = re.sub(r"^(src/[^:]+):\d+:\d+:", r"\1:", capsys.readouterr().err, flags=re.MULTILINE)
    monkeypatch.undo()

    monkeypatch.setattr(sys, "argv", [str(ROOT / "scripts" / "check_architecture_boundaries.py")])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(ROOT / "scripts" / "check_architecture_boundaries.py"), run_name="__main__")
    entrypoint_output = capsys.readouterr()

    assert_output(
        (
            f"clean exit: {success_code}\n"
            f"clean stdout: {clean_output.out!r}\n"
            f"clean stderr: {clean_output.err!r}\n"
            f"failure exit: {failure_code}\n"
            f"{failure_by_symbol}"
            f"entrypoint exit: {exit_info.value.code}\n"
            f"entrypoint stdout: {entrypoint_output.out!r}\n"
            f"entrypoint stderr: {entrypoint_output.err!r}\n"
        ),
    )


def test_architecture_boundary_cli_checks_the_full_source_tree() -> None:
    """Run the installed architecture guard as a real process, as pre-commit does."""
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_architecture_boundaries.py")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert_output(
        f"exit: {completed.returncode}\nstdout: {completed.stdout!r}\nstderr:\n{completed.stderr}",
    )


@pytest.mark.parametrize(
    "fixture",
    [
        pytest.param(FIXTURE_ROOT / "shared_model" / "forbidden.py", id="forbidden"),
        pytest.param(FIXTURE_ROOT / "shared_model_dynamic_import" / "forbidden.py", id="shared_model_dynamic_import"),
    ],
)
def test_architecture_boundary_cli_reports_shared_model_backend_access(tmp_path: Path, fixture: Path) -> None:
    """Run the executable guard against isolated shared-model backend access fixtures."""
    script_path = _copy_checker(tmp_path)
    source_path = tmp_path / "src" / "datamodel_code_generator" / "model" / "base.py"
    source_path.parent.mkdir(parents=True)
    copyfile(fixture, source_path)

    completed = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert_output(f"exit: {completed.returncode}\nstdout: {completed.stdout!r}\nstderr:\n{completed.stderr}")
