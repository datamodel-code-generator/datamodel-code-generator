"""E2E tests for bundled Agent Skill installation."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from datamodel_code_generator import chdir
from datamodel_code_generator.__main__ import Exit
from datamodel_code_generator.agent_skill import AgentName, AgentSkillError, SkillScope, install_agent_skill
from tests.conftest import assert_exact_directory_content, assert_output, create_assert_file_content
from tests.main.conftest import run_main_with_args

if TYPE_CHECKING:
    from collections.abc import Sequence

ROOT = Path(__file__).parents[2]
SKILL_DIR = ROOT / "skills" / "datamodel-code-generator"
EXPECTED = ROOT / "tests" / "data" / "expected" / "agent_skill"
assert_file_content = create_assert_file_content(EXPECTED)


def _run_installer_fast_path(args: Sequence[str], *, cwd: Path, home: Path) -> subprocess.CompletedProcess[str]:
    """Run one fresh-process check for the CLI fast path and packaged resource."""
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", "datamodel_code_generator", *args],
        capture_output=True,
        check=False,
        cwd=cwd,
        env=env,
        text=True,
    )


@pytest.mark.parametrize(
    ("agent", "scope", "skill_directory"),
    [
        ("codex", "project", Path(".agents") / "skills"),
        ("codex", "user", Path(".agents") / "skills"),
        ("claude-code", "project", Path(".claude") / "skills"),
        ("claude-code", "user", Path(".claude") / "skills"),
    ],
)
def test_install_skill_copies_bundled_files_for_supported_agents(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    agent: str,
    scope: str,
    skill_directory: Path,
) -> None:
    """The CLI installs every bundled skill file into the selected agent scope."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    with chdir(tmp_path):
        run_main_with_args(["--install-skill", agent, "--skill-scope", scope])

    target = (tmp_path if scope == "project" else home) / skill_directory / "datamodel-code-generator"
    captured = capsys.readouterr()
    assert_exact_directory_content(target, SKILL_DIR, pattern="*.md")
    assert_output(captured.out.replace(str(target), "<target>"), EXPECTED / "installed.txt")
    assert_output(captured.err, EXPECTED / "empty.txt")


def test_install_skill_fast_path_uses_the_packaged_resource(tmp_path: Path) -> None:
    """A fresh Python process uses the installer fast path and copies every bundled file."""
    home = tmp_path / "home"
    result = _run_installer_fast_path(["--install-skill", "codex"], cwd=tmp_path, home=home)
    if result.returncode != 0:  # pragma: no cover - reports a broken fresh-process test environment
        pytest.fail(f"skill installation failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")

    target = tmp_path / ".agents" / "skills" / "datamodel-code-generator"
    assert_exact_directory_content(target, SKILL_DIR, pattern="*.md")
    assert_output(result.stdout.replace(str(target), "<target>"), EXPECTED / "installed.txt")
    assert_output(result.stderr, EXPECTED / "empty.txt")


def test_install_skill_fast_path_parser(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The lightweight module parses installer arguments without entering generation."""
    from datamodel_code_generator._agent_skill_cli import run_agent_skill_installer

    with chdir(tmp_path):
        return_code = run_agent_skill_installer(["--install-skill", "claude-code"])

    target = tmp_path / ".claude" / "skills" / "datamodel-code-generator"
    captured = capsys.readouterr()
    assert_exact_directory_content(target, SKILL_DIR, pattern="*.md")
    assert_output(f"{return_code}\n", EXPECTED / "success_exit_code.txt")
    assert_output(captured.out.replace(str(target), "<target>"), EXPECTED / "installed.txt")
    assert_output(captured.err, EXPECTED / "empty.txt")


def test_install_skill_requires_explicit_overwrite_and_replaces_stale_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing skills stay untouched until the explicit overwrite option is passed."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    install_args = ["--install-skill", "codex"]
    with chdir(tmp_path):
        run_main_with_args(install_args)
    capsys.readouterr()

    target = tmp_path / ".agents" / "skills" / "datamodel-code-generator"
    (target / "local-note.txt").write_text("preserve me\n", encoding="utf-8")
    with chdir(tmp_path):
        run_main_with_args(install_args, expected_exit=Exit.ERROR)
    captured = capsys.readouterr()
    assert_file_content(target / "local-note.txt", "local-note.txt")
    assert_output(captured.out, EXPECTED / "empty.txt")
    assert_output(captured.err.replace(str(target), "<target>"), EXPECTED / "existing_skill.txt")

    with chdir(tmp_path):
        run_main_with_args([*install_args, "--overwrite-skill"])
    captured = capsys.readouterr()
    assert_exact_directory_content(target, SKILL_DIR, pattern="*.md")
    installed_files = "\n".join(sorted(path.relative_to(target).as_posix() for path in target.rglob("*")))
    assert_output(f"{installed_files}\n", EXPECTED / "installed_files.txt")
    assert_output(captured.out.replace(str(target), "<target>"), EXPECTED / "installed.txt")
    assert_output(captured.err, EXPECTED / "empty.txt")


def test_install_skill_never_replaces_a_non_directory_target(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Even explicit overwrite refuses a file where the skill directory belongs."""
    target = tmp_path / ".agents" / "skills" / "datamodel-code-generator"
    target.parent.mkdir(parents=True)
    target.write_text("not a directory\n", encoding="utf-8")

    with chdir(tmp_path):
        run_main_with_args(["--install-skill", "codex", "--overwrite-skill"], expected_exit=Exit.ERROR)
    captured = capsys.readouterr()
    assert_file_content(target, "non_directory.txt")
    assert_output(captured.out, EXPECTED / "empty.txt")
    assert_output(captured.err.replace(str(target), "<target>"), EXPECTED / "non_directory_target.txt")


@pytest.mark.parametrize("broken", [False, True], ids=["directory", "broken"])
def test_install_skill_never_replaces_a_symlink(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    broken: bool,
) -> None:
    """Explicit overwrite refuses both live and broken symlink targets."""
    target = tmp_path / ".agents" / "skills" / "datamodel-code-generator"
    target.parent.mkdir(parents=True)
    link_source = tmp_path / "missing" if broken else tmp_path / "external-skill"
    if not broken:
        link_source.mkdir()
        (link_source / "SKILL.md").write_text("external skill\n", encoding="utf-8")
    try:
        target.symlink_to(link_source, target_is_directory=True)
    except (NotImplementedError, OSError):  # pragma: no cover - platform capability
        pytest.skip("this platform cannot create directory symlinks")

    with chdir(tmp_path):
        run_main_with_args(["--install-skill", "codex", "--overwrite-skill"], expected_exit=Exit.ERROR)
    captured = capsys.readouterr()
    status = f"is_symlink={target.is_symlink()}\ntarget_exists={target.exists()}\n"
    assert_output(status, EXPECTED / f"{('broken' if broken else 'directory')}_symlink.txt")
    if not broken:
        assert_file_content(link_source / "SKILL.md", "external_skill.txt")
    assert_output(captured.out, EXPECTED / "empty.txt")
    assert_output(captured.err.replace(str(target), "<target>"), EXPECTED / "non_directory_target.txt")


@pytest.mark.parametrize("args", [["--skill-scope", "project"], ["--overwrite-skill"]])
def test_skill_options_require_the_installer(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
) -> None:
    """Installation-only options cannot silently alter a generation command."""
    with chdir(tmp_path):
        run_main_with_args(args, expected_exit=Exit.ERROR)
    captured = capsys.readouterr()
    assert_output(captured.out, EXPECTED / "empty.txt")
    assert_output(captured.err, EXPECTED / "installer_option_requires_install.txt")


def test_install_skill_restores_existing_skill_when_atomic_replacement_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed staged replacement restores the pre-existing skill before reporting the error."""
    target = tmp_path / ".agents" / "skills" / "datamodel-code-generator"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("previous skill\n", encoding="utf-8")
    original_replace = Path.replace
    error_message = "injected replacement failure"
    replacement_attempts = 0

    def fail_staged_replacement(path: Path, destination: Path) -> Path:
        nonlocal replacement_attempts
        if Path(destination) == target and replacement_attempts == 0:
            replacement_attempts += 1
            raise OSError(error_message)
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", fail_staged_replacement)

    with pytest.raises(AgentSkillError) as exception_info:
        install_agent_skill("codex", "project", overwrite=True, cwd=tmp_path)

    error = str(exception_info.value).replace(str(target), "<target>")
    assert_output(f"{error}\n", EXPECTED / "replacement_failure.txt")
    assert_exact_directory_content(target, EXPECTED / "preserved_skill", pattern="*.md")


def test_install_skill_preserves_recovery_copy_when_rollback_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed rollback leaves the previous skill in a reported recovery directory."""
    target = tmp_path / ".agents" / "skills" / "datamodel-code-generator"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("previous skill\n", encoding="utf-8")
    original_replace = Path.replace
    replacement_attempts = 0

    def fail_staged_and_rollback(path: Path, destination: Path) -> Path:
        nonlocal replacement_attempts
        if Path(destination) == target:
            replacement_attempts += 1
            if replacement_attempts <= 2:  # pragma: no branch - production invokes only replacement and rollback
                msg = "injected replacement failure"
                raise OSError(msg)
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", fail_staged_and_rollback)

    with pytest.raises(AgentSkillError) as exception_info:
        install_agent_skill("codex", "project", overwrite=True, cwd=tmp_path)

    recovery_directory = next(target.parent.glob(".datamodel-code-generator-backup-*"))
    error = (
        str(exception_info.value)
        .replace(str(tmp_path), "<tmp>")
        .replace(
            recovery_directory.name,
            "<recovery>",
        )
        .replace("\\", "/")
    )
    assert_output(f"{error}\n", EXPECTED / "rollback_failure.txt")
    assert_exact_directory_content(
        recovery_directory / "datamodel-code-generator",
        EXPECTED / "preserved_skill",
        pattern="*.md",
    )


def test_install_skill_reports_staging_error_without_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Filesystem failures become stable CLI errors and clean partial state."""
    from datamodel_code_generator import agent_skill

    def fail_staging_directory(*_args: object, **_kwargs: object) -> str:
        msg = "injected staging failure"
        raise OSError(msg)

    monkeypatch.setattr(agent_skill, "mkdtemp", fail_staging_directory)
    with chdir(tmp_path):
        run_main_with_args(["--install-skill", "codex"], expected_exit=Exit.ERROR)

    captured = capsys.readouterr()
    target = tmp_path / ".agents" / "skills" / "datamodel-code-generator"
    assert_output(captured.out, EXPECTED / "empty.txt")
    assert_output(captured.err.replace(str(target), "<target>"), EXPECTED / "staging_failure.txt")


@pytest.mark.parametrize(
    ("agent", "scope", "expected_name"),
    [
        ("unsupported", "project", "unsupported_agent.txt"),
        ("codex", "unsupported", "unsupported_scope.txt"),
    ],
)
def test_install_skill_rejects_unsupported_destination_values(
    tmp_path: Path,
    agent: str,
    scope: str,
    expected_name: str,
) -> None:
    """The typed installer still fails safely if called by an untyped client."""
    with pytest.raises(AgentSkillError) as exception_info:
        install_agent_skill(
            cast(AgentName, agent),  # noqa: TC006
            cast(SkillScope, scope),  # noqa: TC006
            overwrite=False,
            cwd=tmp_path,
        )

    assert_output(f"{exception_info.value}\n", EXPECTED / expected_name)
