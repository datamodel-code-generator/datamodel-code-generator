"""Tests for cross-layer architecture guardrails."""

from __future__ import annotations

import runpy
import subprocess
import sys
from fnmatch import fnmatchcase
from pathlib import Path
from shutil import copyfile

import pytest
import tomllib

from scripts import check_architecture_boundaries
from tests.conftest import assert_output

ROOT = Path(__file__).parents[1]
FIXTURE_ROOT = Path(__file__).parent / "data" / "architecture_boundaries"
EXPECTED_ROOT = Path(__file__).parent / "data" / "expected" / "architecture_boundaries"


def test_source_tree_respects_architecture_boundaries() -> None:
    """Keep production dependencies within declared capabilities and bounded legacy exceptions."""
    violations = check_architecture_boundaries.check_paths([ROOT / "src" / "datamodel_code_generator"])

    assert_output(
        check_architecture_boundaries.format_report(violations),
        EXPECTED_ROOT / "clean.txt",
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
        EXPECTED_ROOT / "violations.txt",
    )


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        (
            FIXTURE_ROOT / "shared_model_dynamic_import" / "forbidden.py",
            EXPECTED_ROOT / "shared_model_dynamic_import.txt",
        ),
        (FIXTURE_ROOT / "shared_model_dynamic_import" / "allowed.py", EXPECTED_ROOT / "clean.txt"),
    ],
)
def test_architecture_boundary_detector_handles_shared_model_dynamic_imports(
    fixture: Path,
    expected: Path,
) -> None:
    """Reject concrete dynamic imports while allowing shadowed aliases without a target."""
    violations = check_architecture_boundaries.check_files([(fixture, "shared-model")], allowlist={})

    assert_output(
        check_architecture_boundaries.format_report(violations),
        expected,
    )


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

    assert_output(classification, EXPECTED_ROOT / "classifications.txt")


def test_host_private_access_allows_only_declared_host_members() -> None:
    """Pass self, cls and Host parameters that declare the member; fail other receivers, aliases and members."""
    fixture = FIXTURE_ROOT / "host_private_access" / "concern.py"
    violations = check_architecture_boundaries.check_files([], allowlist={}, host_files=[fixture])

    assert_output(check_architecture_boundaries.format_report(violations))


def test_host_private_access_follows_the_binding_rules() -> None:
    """Report a private read through a name an inner scope or a comprehension rebinds, not the Host read beside it."""
    fixture = FIXTURE_ROOT / "host_private_access" / "bindings.py"
    violations = check_architecture_boundaries.check_files([], allowlist={}, host_files=[fixture])

    assert_output(check_architecture_boundaries.format_report(violations))


@pytest.mark.skipif(sys.version_info < (3, 12), reason="type parameter syntax needs Python 3.12 or later")
def test_host_private_access_follows_type_parameter_scopes(tmp_path: Path) -> None:
    """Hide a Host parameter behind a type parameter or a type statement of the same name."""
    stored = FIXTURE_ROOT / "host_private_access" / "type_parameters.py.txt"
    fixture = tmp_path / "type_parameters.py"
    copyfile(stored, fixture)
    violations = check_architecture_boundaries.check_files([], allowlist={}, host_files=[fixture])
    report = check_architecture_boundaries.format_report(violations)

    assert_output(report.replace(fixture.as_posix(), stored.relative_to(ROOT).as_posix().removesuffix(".txt")))


def test_host_private_access_exempts_only_method_receivers() -> None:
    """Exempt self and cls of a method and its closures; treat them elsewhere as ordinary parameters."""
    fixture = FIXTURE_ROOT / "host_private_access" / "receivers.py"
    violations = check_architecture_boundaries.check_files([], allowlist={}, host_files=[fixture])

    assert_output(check_architecture_boundaries.format_report(violations))


def test_host_private_access_scope_equals_the_slf001_ignores() -> None:
    """Check with the host rule exactly the files where the ruff per-file ignores turn SLF001 off."""
    ignores = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["ruff"]["lint"][
        "per-file-ignores"
    ]
    globs = [glob for glob, rules in ignores.items() if glob.startswith("src/") and "private-member-access" in rules]
    source_root = ROOT / "src" / "datamodel_code_generator"
    named = [
        source_root.joinpath(*relative.split("/"))
        for relative in (
            "parser/_new/x.py",
            "parser/_new/sub/y.py",
            "parser/_passes/sub/z.py",
            "parser/base.py",
            "parser/jsonschema.py",
            "model/_field_imports.py",
            "model/base.py",
        )
    ]

    def scopes(path: Path) -> tuple[bool, bool]:
        relative = path.relative_to(ROOT).as_posix()
        return any(fnmatchcase(relative, glob) for glob in globs), check_architecture_boundaries._in_host_scope(path)

    differing = [
        path.relative_to(ROOT).as_posix() for path in sorted((ROOT / "src").rglob("*.py")) if len(set(scopes(path))) > 1
    ]
    lines = [f"{path.relative_to(source_root).as_posix()}: ignored, checked = {scopes(path)}" for path in named]

    assert_output("\n".join([*globs, *lines, f"src files where they differ: {differing}", ""]))


def test_host_private_access_baseline_only_shrinks(tmp_path: Path) -> None:
    """Allow exactly the accesses the baseline counts and report the entries the code no longer has."""
    fixture = FIXTURE_ROOT / "host_private_access" / "concern.py"
    baseline = tmp_path / "baseline.toml"
    baseline.write_text(
        check_architecture_boundaries.format_host_baseline(
            check_architecture_boundaries.check_files([], allowlist={}, host_files=[fixture])
        ),
        encoding="utf-8",
    )
    allowlist = check_architecture_boundaries.load_host_baseline(baseline)
    grown = {
        key: check_architecture_boundaries.AllowlistEntry(entry.reason, entry.count + 1)
        for key, entry in allowlist.items()
        if key.target == "sys._getframe"
    }
    reports = [
        check_architecture_boundaries.format_report(
            check_architecture_boundaries.check_files([], allowlist=entries, host_files=[fixture])
        )
        for entries in (allowlist, {**allowlist, **grown})
    ]
    missing = check_architecture_boundaries.load_host_baseline(tmp_path / "missing.toml")

    assert_output(
        f"{baseline.read_text(encoding='utf-8')}baseline: {reports[0]}grown: {reports[1]}missing: {missing}\n"
    )


def test_host_private_access_cli_writes_the_baseline(tmp_path: Path) -> None:
    """Write the baseline of a source tree from the executable guard, after which the tree checks clean."""
    script_path = tmp_path / "scripts" / "check_architecture_boundaries.py"
    source_path = tmp_path / "src" / "datamodel_code_generator" / "parser" / "_concern.py"
    script_path.parent.mkdir()
    source_path.parent.mkdir(parents=True)
    copyfile(ROOT / "scripts" / "check_architecture_boundaries.py", script_path)
    copyfile(FIXTURE_ROOT / "host_private_access" / "concern.py", source_path)

    completed = subprocess.run(
        [sys.executable, str(script_path), "--write-host-baseline"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert_output(
        f"exit: {completed.returncode}\nstderr: {completed.stderr!r}\n"
        f"{(script_path.parent / 'host_private_access_baseline.toml').read_text(encoding='utf-8')}"
    )


def test_architecture_boundary_detector_allows_shared_and_composition_model_dependencies() -> None:
    """Keep neutral registry access and concrete backend composition available to their owners."""
    files: list[tuple[Path, check_architecture_boundaries.Layer]] = [
        (FIXTURE_ROOT / "shared_model" / "allowed.py", "shared-model"),
        (FIXTURE_ROOT / "model_composition" / "allowed.py", "model-composition"),
    ]
    violations = check_architecture_boundaries.check_files(files, allowlist={})

    assert_output(
        check_architecture_boundaries.format_report(violations),
        EXPECTED_ROOT / "clean.txt",
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
        EXPECTED_ROOT / "stale_allowlist.txt",
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
        EXPECTED_ROOT / "reference_allowlist.txt",
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

    assert_output("\n".join(messages) + "\n", EXPECTED_ROOT / "allowlist_contract.txt")


def test_architecture_boundary_cli_reports_actionable_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exercise the clean CLI, an abnormal dependency, and the executable entry point."""
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
    failure_output = capsys.readouterr()
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
            f"{failure_output.err}"
            f"entrypoint exit: {exit_info.value.code}\n"
            f"entrypoint stdout: {entrypoint_output.out!r}\n"
            f"entrypoint stderr: {entrypoint_output.err!r}\n"
        ),
        EXPECTED_ROOT / "cli.txt",
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
        EXPECTED_ROOT / "cli_subprocess.txt",
    )


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        (FIXTURE_ROOT / "shared_model" / "forbidden.py", EXPECTED_ROOT / "cli_subprocess_forbidden.txt"),
        (
            FIXTURE_ROOT / "shared_model_dynamic_import" / "forbidden.py",
            EXPECTED_ROOT / "cli_subprocess_shared_model_dynamic_import.txt",
        ),
    ],
)
def test_architecture_boundary_cli_reports_shared_model_backend_access(
    tmp_path: Path,
    fixture: Path,
    expected: Path,
) -> None:
    """Run the executable guard against isolated shared-model backend access fixtures."""
    script_path = tmp_path / "scripts" / "check_architecture_boundaries.py"
    source_path = tmp_path / "src" / "datamodel_code_generator" / "model" / "base.py"
    script_path.parent.mkdir()
    source_path.parent.mkdir(parents=True)
    copyfile(ROOT / "scripts" / "check_architecture_boundaries.py", script_path)
    copyfile(fixture, source_path)

    completed = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert_output(
        f"exit: {completed.returncode}\nstdout: {completed.stdout!r}\nstderr:\n{completed.stderr}",
        expected,
    )
