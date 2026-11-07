"""Tests for the datamodel-code-generator agent skill documentation."""

from __future__ import annotations

import re
import subprocess
import sys
from json import dumps
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.build_datamodel_codegen_skill_docs import build_cli_options_reference
from tests.conftest import assert_exact_directory_content, assert_output

SKILL_DIR = Path(__file__).parents[3] / "skills" / "datamodel-code-generator"
EXPECTED = Path(__file__).parents[2] / "data" / "expected" / "skills" / "datamodel-code-generator"
SKILL_FILES = [
    SKILL_DIR / "SKILL.md",
    SKILL_DIR / "references" / "workflows.md",
    SKILL_DIR / "references" / "cli-options.md",
    SKILL_DIR / "references" / "troubleshooting.md",
]
FLAG_RE = re.compile(r"(?<![\w-])--[a-zA-Z0-9][a-zA-Z0-9-]*")
NON_DATAMODEL_FLAGS = frozenset({"--from", "--with"})


def _relative_posix(path: Path, parent: Path) -> str:
    return path.relative_to(parent).as_posix()


def _read_frontmatter(path: Path) -> dict[str, Any]:
    """Read and validate YAML frontmatter from a skill document."""
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if match is None:
        pytest.fail(f"{path} is missing YAML frontmatter")
    frontmatter = yaml.safe_load(match.group(1))
    if not isinstance(frontmatter, dict):
        pytest.fail(f"{path} frontmatter must be a mapping")
    return frontmatter


def _documented_flags() -> dict[str, set[Path]]:
    """Collect datamodel-codegen flags referenced by the skill files."""
    flags: dict[str, set[Path]] = {}
    for path in SKILL_FILES:
        text = path.read_text(encoding="utf-8")
        for flag in FLAG_RE.findall(text):
            flags.setdefault(flag, set()).add(path)
    return flags


def test_skill_frontmatter_validates() -> None:
    """Skill frontmatter summary matches checked-in expected output."""
    frontmatter = _read_frontmatter(SKILL_DIR / "SKILL.md")
    name = frontmatter.get("name")
    description = frontmatter.get("description")
    summary = {
        "description_present": isinstance(description, str) and bool(description.strip()),
        "description_within_limit": isinstance(description, str) and len(description) <= 1024,
        "license": frontmatter.get("license"),
        "metadata": frontmatter.get("metadata"),
        "name": name,
        "name_matches_directory": name == SKILL_DIR.name,
        "name_valid": isinstance(name, str) and re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?", name) is not None,
        "skill_files": {_relative_posix(path, SKILL_DIR): path.is_file() for path in SKILL_FILES},
    }

    assert_output(dumps(summary, indent=2, sort_keys=True) + "\n", EXPECTED / "frontmatter.txt")


def test_skill_frontmatter_requires_yaml_header(tmp_path: Path) -> None:
    """Skill frontmatter reader reports files without YAML frontmatter."""
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("# Missing frontmatter\n", encoding="utf-8")

    with pytest.raises(pytest.fail.Exception, match="missing YAML frontmatter"):
        _read_frontmatter(skill_file)


def test_skill_frontmatter_requires_mapping(tmp_path: Path) -> None:
    """Skill frontmatter reader reports non-mapping YAML frontmatter."""
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("---\n- invalid\n---\n", encoding="utf-8")

    with pytest.raises(pytest.fail.Exception, match="frontmatter must be a mapping"):
        _read_frontmatter(skill_file)


def test_skill_cli_options_reference_is_generated(tmp_path: Path) -> None:
    """CLI option reference matches the generated output."""
    output = tmp_path / "references" / "cli-options.md"
    output.parent.mkdir()
    output.write_text(build_cli_options_reference(), encoding="utf-8")
    assert_exact_directory_content(tmp_path, SKILL_DIR, pattern="references/cli-options.md")


def test_skill_uvx_examples_name_the_installable_package() -> None:
    """The uvx examples must not mistake the CLI name for the PyPI package."""
    paths = [
        SKILL_DIR / "SKILL.md",
        SKILL_DIR / "references" / "workflows.md",
        SKILL_DIR / "references" / "troubleshooting.md",
    ]
    invalid = [
        f"{path.relative_to(SKILL_DIR)}:{line_number}"
        for path in paths
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if re.match(r"^\s*uvx\s+(?:--from\s+datamodel-codegen\s+)?datamodel-codegen(?:\s|$)", line)
    ]
    if invalid:
        pytest.fail(f"uvx resolves datamodel-codegen as a package, not a command: {', '.join(invalid)}")


@pytest.mark.parametrize(
    "command",
    [
        "    uvx datamodel-codegen --help",
        "uvx --from datamodel-codegen datamodel-codegen --help",
    ],
)
def test_skill_uvx_guard_rejects_cli_name_as_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    """The guard catches bad package names in both documented uvx forms."""
    monkeypatch.setattr(sys.modules[__name__], "SKILL_DIR", tmp_path)
    for relative in ("SKILL.md", "references/workflows.md", "references/troubleshooting.md"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(command + "\n" if relative == "references/workflows.md" else "", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match=r"references/workflows\.md:1"):
        test_skill_uvx_examples_name_the_installable_package()


def test_skill_documented_flags_exist_in_cli_help() -> None:
    """Documented datamodel-codegen flags must exist in current CLI help."""
    result = subprocess.run(
        [sys.executable, "-m", "datamodel_code_generator", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"datamodel-codegen --help failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
    help_text = result.stdout
    unknown = {
        flag: sorted(_relative_posix(path, SKILL_DIR.parents[1]) for path in paths)
        for flag, paths in _documented_flags().items()
        if flag not in NON_DATAMODEL_FLAGS and flag not in help_text
    }

    assert_output(dumps(unknown, indent=2, sort_keys=True) + "\n", EXPECTED / "documented_flag_drift.txt")


def test_skill_documented_flags_reports_help_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Documented flag check reports subprocess output when help fails."""

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["datamodel-codegen"], 2, stdout="out", stderr="err")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(pytest.fail.Exception, match="datamodel-codegen --help failed"):
        test_skill_documented_flags_exist_in_cli_help()
