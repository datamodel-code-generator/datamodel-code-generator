"""Skip remote release analysis for changes that cannot affect released behavior."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable  # ruff: ignore[typing-only-standard-library-import]  # fast runtime import
from pathlib import Path

FILE_CHANGE_KEYS = frozenset({"path", "previous_path"})
SAFE_EXACT_PATHS = frozenset({
    ".github/workflows/codeql.yaml",
    ".github/workflows/codspeed.yaml",
    ".github/workflows/release-draft.yaml",
    "scripts/prepare_release_draft_analysis.py",
    "scripts/report_release_analysis_failure.py",
    "tox.ini",
})
SAFE_ROOTS = frozenset({"tests"})
SAFE_REASONING = "Only CI, release automation, or tests changed; generated output and public interfaces are unaffected."
CHANGELOG_SAFE_EXACT_PATHS = frozenset({
    ".coderabbit.yaml",
    ".github/dependabot.yaml",
    ".github/workflows/changelog.yaml",
    ".github/workflows/cli-docs.yaml",
    ".github/workflows/codeql.yaml",
    ".github/workflows/codespell.yaml",
    ".github/workflows/codspeed.yaml",
    ".github/workflows/config-types.yaml",
    ".github/workflows/generated-docs-sync.yaml",
    ".github/workflows/lint.yaml",
    ".github/workflows/perf-memory.yaml",
    ".github/workflows/release-draft.yaml",
    ".github/workflows/release-notify.yaml",
    ".github/workflows/schema-docs.yaml",
    ".github/workflows/test.yaml",
    ".pre-commit-config.yaml",
    "CHANGELOG.md",
    "docs/data/release-benchmarks.json",
    "scripts/benchmark_builtin_templates.py",
    "scripts/check_architecture_boundaries.py",
    "scripts/generate_changelog.sh",
    "scripts/measure_generation_memory.py",
    "scripts/measure_startup.py",
    "scripts/prepare_release_draft_analysis.py",
    "scripts/report_release_analysis_failure.py",
    "scripts/select_ci_test_shard.py",
    "scripts/validate_release_draft_analysis.py",
    "tox.ini",
})


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line paths supplied by the release workflow."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--changed-files-path", required=True, type=Path)
    parser.add_argument("--expected-changed-files", required=True, type=int)
    parser.add_argument("--analysis-path", required=True, type=Path)
    parser.add_argument("--github-output-path", required=True, type=Path)
    return parser.parse_args(argv)


def _is_safe_path(path: str) -> bool:
    """Return whether one exact changed path cannot affect released behavior."""
    if not path or path != path.strip():
        return False
    if path in SAFE_EXACT_PATHS:
        return True
    match path.split("/", 1):
        case [root, relative_path] if root in SAFE_ROOTS and relative_path:
            return True
    return False


def _is_safe_changelog_path(path: str) -> bool:
    """Return whether one exact changed path can be omitted from release notes."""
    if not path or path != path.strip():
        return False
    if path in CHANGELOG_SAFE_EXACT_PATHS:
        return True
    match path.split("/", 2):
        case ["tests", relative_path, *_] if relative_path:
            return True
    return False


def _paths_from_record(raw_record: str) -> tuple[str, ...] | None:
    """Parse one exact GitHub API file record without ambiguous framing."""
    if not (record := raw_record.strip()):
        return None
    try:
        file_change = json.loads(record)
    except json.JSONDecodeError:
        return None
    if not isinstance(file_change, dict) or file_change.keys() != FILE_CHANGE_KEYS:
        return None
    match file_change:
        case {"path": str(path), "previous_path": None}:
            return (path,)
        case {"path": str(path), "previous_path": str(previous_path)}:
            return path, previous_path
    return None


def _classify_file_changes(changed_files: Iterable[str], expected_changed_files: int) -> tuple[bool, bool]:
    """Return semantic-analysis and changelog policies from one file-record stream."""
    if expected_changed_files <= 0:
        return True, False

    skip_semantic_analysis = True
    skip_changelog = True
    file_count = 0
    for raw_record in changed_files:
        if not (paths := _paths_from_record(raw_record)):
            return True, False
        file_count += 1
        skip_semantic_analysis = skip_semantic_analysis and all(map(_is_safe_path, paths))
        skip_changelog = skip_changelog and all(map(_is_safe_changelog_path, paths))
        if not skip_semantic_analysis and not skip_changelog:
            return True, False

    if file_count != expected_changed_files:
        return True, False
    return not skip_semantic_analysis, skip_changelog


def _release_draft_policies(changed_files_path: Path, expected_changed_files: int) -> tuple[bool, bool]:
    """Return fail-closed semantic-analysis and changelog decisions."""
    try:
        with changed_files_path.open(encoding="utf-8") as changed_files:
            return _classify_file_changes(changed_files, expected_changed_files)
    except (OSError, UnicodeError):
        return True, False


def main(argv: list[str] | None = None) -> None:
    """Write release-analysis and changelog-routing decisions for the workflow."""
    args = _parse_args(argv)
    requires_claude, skip_changelog = _release_draft_policies(args.changed_files_path, args.expected_changed_files)
    if not requires_claude:
        args.analysis_path.write_text(
            json.dumps({
                "has_breaking_changes": False,
                "breaking_changes_content": "",
                "reasoning": SAFE_REASONING,
            })
            + "\n",
            encoding="utf-8",
        )
    with args.github_output_path.open("a", encoding="utf-8") as github_output:
        github_output.write(f"requires_claude={str(requires_claude).lower()}\n")
        github_output.write(f"skip_changelog={str(skip_changelog).lower()}\n")


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess tests
    main()
