"""Compare generated output with the files on disk, for `--check` and `--diff-against`, and write the report."""

from __future__ import annotations

import difflib
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NamedTuple, TypeAlias

from datamodel_code_generator._paths import output_layout
from datamodel_code_generator._python_syntax import normalize_newlines

if TYPE_CHECKING:
    from datamodel_code_generator._structured_output import TargetPayload

_MAIN_MODULE = "datamodel_code_generator.__main__"


class _OutputDecodeError(UnicodeError):
    """An existing output file that the configured encoding cannot decode."""

    def __init__(self, path: Path, error: UnicodeError) -> None:
        super().__init__(str(error))
        self.path = path


def _read_existing_output(path: Path, encoding: str) -> str:
    """Read an existing output file with LF line endings, naming it when the encoding cannot decode it."""
    try:
        return normalize_newlines(path.read_text(encoding=encoding))
    except UnicodeError as e:
        raise _OutputDecodeError(path, e) from e


class OutputComparisonOptions(NamedTuple):
    """Formatting context for a generated-output comparison."""

    is_directory_output: bool
    input_diff: bool = False
    single_file_display_path: str | None = None
    directory_display_path: str | None = None

    def directory_file_path(self, path: Path) -> str:
        """Qualify a directory entry when comparing several output roots; a single-module output keeps its own name."""
        if not path.parts and self.single_file_display_path is not None:
            return self.single_file_display_path
        return (path if self.directory_display_path is None else Path(self.directory_display_path) / path).as_posix()

    @property
    def fromfile_suffix(self) -> str:
        """Return the unified-diff label suffix for the baseline output."""
        return " (baseline input)" if self.input_diff else ""

    @property
    def tofile_suffix(self) -> str:
        """Return the unified-diff label suffix for the current output."""
        return " (current input)" if self.input_diff else " (expected)"


OutputComparisonPolicy: TypeAlias = tuple[
    Literal["added", "missing"],
    str,
    str,
    Literal["extra", "removed"],
    str,
]


def _output_comparison_policy(*, input_diff: bool) -> OutputComparisonPolicy:
    """Return shared missing and extra reporting values for one comparison."""
    if input_diff:
        missing_message_suffix = "generated only from current input"
        return (
            "added",
            missing_message_suffix,
            missing_message_suffix,
            "removed",
            "generated only from baseline input",
        )
    return (
        "missing",
        "should be generated",
        "file does not exist but should be generated",
        "extra",
        "no longer generated",
    )


def _compare_single_file(
    generated_path: Path,
    actual_path: Path,
    encoding: str,
    comparison: OutputComparisonOptions,
) -> tuple[bool, list[str]]:
    """Compare generated file content with existing file.

    Returns:
        Tuple of (has_differences, diff_lines)
        - has_differences: True if files differ
        - diff_lines: List of diff lines for output
    """
    generated_content = normalize_newlines(generated_path.read_text(encoding=encoding))

    display_path = comparison.single_file_display_path or actual_path.as_posix()
    actual_content = _read_existing_output(actual_path, encoding)

    if generated_content == actual_content:
        return False, []
    return True, _unified_diff(comparison, display_path, actual_content, generated_content)


def _unified_diff(comparison: OutputComparisonOptions, display_path: str, actual: str, generated: str) -> list[str]:
    """Return the diff lines from an existing output to the generated one, labelled for the comparison."""
    return list(
        difflib.unified_diff(
            actual.splitlines(keepends=True),
            generated.splitlines(keepends=True),
            fromfile=f"{display_path}{comparison.fromfile_suffix}",
            tofile=f"{display_path}{comparison.tofile_suffix}",
        )
    )


class DirectoryChangedFile(NamedTuple):
    """One changed file found while comparing generated and existing directories."""

    path: str
    diff_lines: list[str]


def _compare_directories(
    generated_dir: Path,
    actual_dir: Path,
    encoding: str,
    comparison: OutputComparisonOptions,
) -> tuple[list[DirectoryChangedFile], list[str], list[str]]:
    """Compare generated directory with existing directory.

    Two inputs can generate a single-module file at the directory path: it is compared as the file at that path.
    """
    from datamodel_code_generator._leftover_files import python_files  # noqa: PLC0415

    changed_files: list[DirectoryChangedFile] = []

    generated_files = {path.relative_to(generated_dir) for path in generated_dir.rglob("*.py")}

    actual_files = python_files(actual_dir)

    if comparison.input_diff:
        for files, output in ((generated_files, generated_dir), (actual_files, actual_dir)):
            if output_layout(output) == "module":
                files.add(Path())

    missing_files = [comparison.directory_file_path(rel_path) for rel_path in sorted(generated_files - actual_files)]
    extra_files = [comparison.directory_file_path(rel_path) for rel_path in sorted(actual_files - generated_files)]

    for rel_path in sorted(generated_files & actual_files):
        generated_content = normalize_newlines((generated_dir / rel_path).read_text(encoding=encoding))
        actual_content = _read_existing_output(actual_dir / rel_path, encoding)
        if generated_content != actual_content:
            display_path = comparison.directory_file_path(rel_path)
            changed_files.append(
                DirectoryChangedFile(
                    display_path, _unified_diff(comparison, display_path, actual_content, generated_content)
                )
            )

    return changed_files, missing_files, extra_files


class ComparisonDifference(NamedTuple):
    """One generated-output difference, which becomes a payload model only when JSON is printed."""

    kind: Literal["changed", "missing", "extra", "added", "removed"]
    path: str
    message: str | None = None
    diff: str | None = None


class OutputComparison(NamedTuple):
    """Generated-file differences shared by --check and --diff-against."""

    differences: list[ComparisonDifference]
    content: str


def _compare_generated_single_file(
    generated_output: Path,
    actual_output: Path,
    encoding: str,
    comparison: OutputComparisonOptions,
) -> OutputComparison:
    """Build one single-file comparison report."""
    path = comparison.single_file_display_path or actual_output.as_posix()
    if (actual_exists := actual_output.exists()) != generated_output.exists():
        missing_kind, _, single_file_missing_message_suffix, extra_kind, extra_message_suffix = (
            _output_comparison_policy(input_diff=comparison.input_diff)
        )
        kind, message_suffix = (
            (extra_kind, extra_message_suffix) if actual_exists else (missing_kind, single_file_missing_message_suffix)
        )
        message = f"{kind.upper()}: {path} ({message_suffix})"
        return OutputComparison(
            differences=[ComparisonDifference(kind=kind, path=path, message=message)],
            content=f"{message}\n",
        )

    diff_found, diff_lines = (
        _compare_single_file(generated_output, actual_output, encoding, comparison) if actual_exists else (False, [])
    )
    if not diff_found:
        return OutputComparison(differences=[], content="")

    diff_text = "".join(diff_lines)
    return OutputComparison(
        differences=[ComparisonDifference(kind="changed", path=path, diff=diff_text)], content=diff_text
    )


def _compare_generated_directories(
    generated_output: Path,
    actual_output: Path,
    encoding: str,
    comparison: OutputComparisonOptions,
) -> OutputComparison:
    """Build one directory manifest comparison report."""
    differences: list[ComparisonDifference] = []
    content_parts: list[str] = []
    changed_files, missing_files, extra_files = _compare_directories(
        generated_output,
        actual_output,
        encoding,
        comparison,
    )
    missing_kind, missing_message_suffix, _, extra_kind, extra_message_suffix = _output_comparison_policy(
        input_diff=comparison.input_diff
    )
    for changed_file in changed_files:
        diff_text = "".join(changed_file.diff_lines)
        differences.append(ComparisonDifference(kind="changed", path=changed_file.path, diff=diff_text))
        content_parts.append(diff_text)
    for kind, message_suffix, paths in (
        (missing_kind, missing_message_suffix, missing_files),
        (extra_kind, extra_message_suffix, extra_files),
    ):
        for path in paths:
            message = f"{kind.upper()}: {path} ({message_suffix})"
            differences.append(ComparisonDifference(kind=kind, path=path, message=message))
            content_parts.append(f"{message}\n")
    return OutputComparison(differences=differences, content="".join(content_parts))


def _compare_generated_outputs(
    generated_output: Path,
    actual_output: Path,
    encoding: str,
    comparison: OutputComparisonOptions,
) -> OutputComparison:
    """Compare formatted generated output without retaining full file manifests in memory."""
    if comparison.is_directory_output:
        return _compare_generated_directories(generated_output, actual_output, encoding, comparison)
    return _compare_generated_single_file(generated_output, actual_output, encoding, comparison)


def _check_output_json(
    *,
    success: bool,
    content: str,
    differences: list[ComparisonDifference],
    kind: Literal["check", "input-diff"] = "check",
    target: TargetPayload | None = None,
) -> str:
    from datamodel_code_generator._structured_output import (  # ruff: ignore[import-outside-top-level]
        CheckDifferencePayload,
        check_output_json,
    )

    return check_output_json(
        success=success,
        content=content,
        differences=[CheckDifferencePayload(**difference._asdict()) for difference in differences],
        kind=kind,
        target=target,
    )


def _write_comparison_output(
    comparison: OutputComparison,
    output_format: str | None,
    *,
    kind: Literal["check", "input-diff"] = "check",
    target: TargetPayload | None = None,
) -> None:
    """Write a common comparison report in text or structured JSON, with the target of a server or client run."""
    if output_format == "json":
        sys.stdout.write(
            _check_output_json(
                success=not comparison.differences,
                content=comparison.content,
                differences=comparison.differences,
                kind=kind,
                target=target,
            )
            + "\n"
        )
        return
    sys.stdout.write(comparison.content)


OutputComparisonOptions.__module__ = _MAIN_MODULE
DirectoryChangedFile.__module__ = _MAIN_MODULE
ComparisonDifference.__module__ = _MAIN_MODULE
OutputComparison.__module__ = _MAIN_MODULE
