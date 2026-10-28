"""Build the release benchmark documentation shell.

Usage:
    python scripts/build_release_benchmark_docs.py
    python scripts/build_release_benchmark_docs.py --check
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from scripts.collect_release_benchmarks import BENCHMARK_CASES, DEFAULT_FORMATTERS
    from scripts.release_benchmark_safety import (
        SAFE_FIELD_PATTERN,
        safe_optional_field,
        safe_release_version,
        validated_benchmark_entry,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from collect_release_benchmarks import BENCHMARK_CASES, DEFAULT_FORMATTERS
    from release_benchmark_safety import (
        SAFE_FIELD_PATTERN,
        safe_optional_field,
        safe_release_version,
        validated_benchmark_entry,
    )

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "docs" / "data" / "release-benchmarks.json"
NOTES_PATH = ROOT / "docs" / "data" / "release-benchmark-notes.json"
DOCS_PATH = ROOT / "docs" / "performance-benchmarks.md"
CASE_ORDER = ("small", "large")
CASE_LABELS = {
    "small": "Small",
    "large": "Large",
}
CASE_GUIDANCE = {
    "small": "Compact fixture that emphasizes CLI startup, parsing, and formatter overhead",
    "large": "Larger fixture that emphasizes parser and model graph throughput",
}
INPUT_TYPE_LABELS = {
    "jsonschema": "JSON Schema",
    "openapi": "OpenAPI",
}
INPUT_TYPE_GUIDANCE = {
    "jsonschema": "JSON Schema to Pydantic v2 model generation",
    "openapi": "OpenAPI component resolution and Pydantic v2 model generation",
}
FORMATTER_LABELS = {
    "default": "black/isort default",
    "builtin": "Built-in",
    "ruff": "Ruff",
}


@dataclass(frozen=True, slots=True)
class BenchmarkEntry:
    """One validated release benchmark result identity."""

    version: str
    input_type: str
    case: str
    formatter: str


@dataclass(frozen=True, slots=True)
class BenchmarkNote:
    """One human-authored release benchmark note identity."""

    version: str
    summary: str
    input_type: str
    case: str


@dataclass(frozen=True, slots=True)
class BenchmarkData:
    """Release benchmark dataset loaded from JSON."""

    schema_version: int
    metadata: dict[str, Any]
    entries: tuple[BenchmarkEntry, ...]
    notes: tuple[BenchmarkNote, ...]


@dataclass(frozen=True, slots=True)
class GeneratedDoc:
    """Generated file content."""

    path: Path
    content: str


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _integer(value: object, *, default: int = 0) -> int:
    match value:
        case int():
            return value
        case float():
            return int(value)
        case str() if value.isdecimal():
            return int(value)
        case _:
            return default


def _label(value: str) -> str:
    return " ".join(part.capitalize() for part in value.replace("_", "-").split("-") if part) or value


def _case_label(case: str) -> str:
    return CASE_LABELS.get(case, _label(case))


def _input_type_label(input_type: str) -> str:
    return INPUT_TYPE_LABELS.get(input_type, _label(input_type))


def _formatter_label(formatter: str) -> str:
    return FORMATTER_LABELS.get(formatter, _label(formatter))


def _order_key(value: str, order: tuple[str, ...]) -> tuple[int, str]:
    try:
        return order.index(value), value
    except ValueError:
        return len(order), value


def _benchmark_case_paths() -> dict[tuple[str, str], str]:
    paths: dict[tuple[str, str], str] = {}
    for benchmark_case in BENCHMARK_CASES:
        try:
            input_path = benchmark_case.path.relative_to(ROOT)
        except ValueError:
            input_path = benchmark_case.path
        paths[benchmark_case.input_type, benchmark_case.name] = input_path.as_posix()
    return paths


def _entry_from_raw(raw: object, *, path: Path, index: int) -> BenchmarkEntry:
    if not isinstance(raw, dict):
        msg = f"Benchmark entry #{index} must be an object"
        raise TypeError(msg)
    entry = validated_benchmark_entry(raw, path=path, index=index)
    return BenchmarkEntry(
        version=str(entry["version"]),
        input_type=str(entry["input_type"]),
        case=str(entry["case"]),
        formatter=str(entry["formatter"]),
    )


def _note_from_raw(raw: object, *, path: Path, index: int) -> BenchmarkNote:
    if not isinstance(raw, dict):
        msg = f"Benchmark note #{index} must be an object"
        raise TypeError(msg)
    if not (version := safe_release_version(raw.get("version"), path=path, field="version", index=index)):
        msg = f"Benchmark note #{index} is missing version"
        raise ValueError(msg)
    if not (summary := _string(raw.get("summary")).strip()):
        msg = f"Benchmark note #{index} is missing summary"
        raise ValueError(msg)
    return BenchmarkNote(
        version=version,
        summary=summary,
        input_type=safe_optional_field(raw, "input_type", SAFE_FIELD_PATTERN, path=path, index=index),
        case=safe_optional_field(raw, "case", SAFE_FIELD_PATTERN, path=path, index=index),
    )


def load_benchmark_notes(path: Path | None = NOTES_PATH) -> tuple[BenchmarkNote, ...]:
    """Load optional human-authored release benchmark notes."""
    if path is None or not path.exists():
        return ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = f"Benchmark notes in {path} must be a JSON object"
        raise TypeError(msg)
    raw_notes = payload.get("notes", [])
    if not isinstance(raw_notes, list):
        msg = f"Benchmark notes in {path} must contain a notes list"
        raise TypeError(msg)
    return tuple(_note_from_raw(raw, path=path, index=index) for index, raw in enumerate(raw_notes, start=1))


def load_benchmark_data(path: Path = DATA_PATH, *, notes_path: Path | None = NOTES_PATH) -> BenchmarkData:
    """Load and validate release benchmark JSON data."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = f"Benchmark data in {path} must be a JSON object"
        raise TypeError(msg)
    raw_entries = payload.get("entries", [])
    if not isinstance(raw_entries, list):
        msg = f"Benchmark data in {path} must contain an entries list"
        raise TypeError(msg)
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    return BenchmarkData(
        schema_version=_integer(payload.get("schema_version"), default=1),
        metadata=metadata,
        entries=tuple(_entry_from_raw(raw, path=path, index=index) for index, raw in enumerate(raw_entries, start=1)),
        notes=load_benchmark_notes(notes_path),
    )


def _scenario_sort_key(scenario: tuple[str, str]) -> tuple[tuple[int, str], str]:
    input_type, case = scenario
    return _order_key(case, CASE_ORDER), input_type


def _scenario_keys(entries: tuple[BenchmarkEntry, ...]) -> tuple[tuple[str, str], ...]:
    scenarios = {(entry.input_type, entry.case) for entry in entries}
    return tuple(sorted(scenarios, key=_scenario_sort_key))


def _scenario_formatters(data: BenchmarkData, *, input_type: str, case: str) -> tuple[str, ...]:
    formatters = {entry.formatter for entry in data.entries if entry.input_type == input_type and entry.case == case}
    return tuple(sorted(formatters, key=lambda formatter: _order_key(formatter, DEFAULT_FORMATTERS)))


def _scenario_description(input_type: str, case: str) -> str:
    case_guidance = CASE_GUIDANCE.get(case, f"{_case_label(case)} fixture")
    input_guidance = INPUT_TYPE_GUIDANCE.get(input_type, f"{_input_type_label(input_type)} generation")
    return f"{case_guidance} for {input_guidance}."


def _scenario_fixture(input_type: str, case: str, benchmark_case_paths: dict[tuple[str, str], str]) -> str:
    if input_path := benchmark_case_paths.get((input_type, case)):
        return f"`{input_path}`"
    return "not declared by `scripts/collect_release_benchmarks.py`"


def _markdown_table_cell(value: str) -> str:
    return value.replace("|", "\\|")


def _scenario_table_lines(data: BenchmarkData) -> tuple[str, ...]:
    benchmark_case_paths = _benchmark_case_paths()
    lines = [
        "| Scenario | Input fixture | Formatters | Represents |",
        "| --- | --- | --- | --- |",
    ]
    for input_type, case in _scenario_keys(data.entries):
        formatter_labels = ", ".join(
            f"`{formatter}` ({_formatter_label(formatter)})"
            for formatter in _scenario_formatters(data, input_type=input_type, case=case)
        )
        row = (
            f"{_case_label(case)} / {_input_type_label(input_type)}",
            _scenario_fixture(input_type, case, benchmark_case_paths),
            formatter_labels,
            _scenario_description(input_type, case),
        )
        lines.append("| " + " | ".join(_markdown_table_cell(cell) for cell in row) + " |")
    return tuple(lines)


def render_scenario_guidance(data: BenchmarkData) -> tuple[str, ...]:
    """Render static benchmark scenario and metric guidance from the loaded data."""
    lines = [
        "## Scenario Guide",
        "",
        (
            "Each scenario combines an input type with a case size. This guide is generated from the scenario keys "
            "in the benchmark JSON and the collector case definitions, so it changes when the benchmark matrix "
            "changes."
        ),
        "",
    ]
    if data.entries:
        lines.extend(_scenario_table_lines(data))
    else:
        lines.append("No benchmark scenarios have been collected yet.")
    lines.extend((
        "",
        "## Interpreting Metrics",
        "",
        (
            "- `median_ms` is the primary comparison value: it is the median generation duration after warmup runs, "
            "and lower is faster."
        ),
        (
            "- `min_ms`, `max_ms`, and `stdev_ms` describe the measured spread for the same row; wide ranges usually "
            "mean CI runner noise rather than a deliberate code change."
        ),
        (
            "- Formatter comparisons are scoped to the same scenario and version. `default` is the "
            "black/isort default baseline, while `builtin` and `ruff` ratios compare their medians to that baseline."
        ),
        (
            "- `ok` rows have timing data. `unsupported` means the formatter or option was unavailable in that "
            "release. `failed` means installation or command execution failed, so timing cells are intentionally "
            "empty."
        ),
        "",
    ))
    return tuple(lines)


def render_release_benchmark_markdown(data: BenchmarkData) -> str:
    """Render the release benchmark documentation shell."""
    data_state = "available" if data.entries else "empty"
    return "\n".join((
        "# Performance Benchmarks",
        "",
        "<!-- Generated by scripts/build_release_benchmark_docs.py. Do not edit manually. -->",
        "",
        (
            "This page tracks datamodel-code-generator release and main-branch benchmark results collected on "
            "GitHub Actions. The data covers only datamodel-code-generator and uses Ubuntu runners so "
            "release-to-release changes can be compared without mixing in third-party generator results. "
            "Automatic backfills select versions from PyPI download-by-version data when that public dataset is "
            "available."
        ),
        "",
        (
            "datamodel-code-generator supports many schema styles and production use cases, so it includes a broad "
            "set of useful options. As releases add more capabilities, these benchmarks help keep the implementation "
            "measured, managed, and tuned so code generation stays fast in everyday use."
        ),
        "",
        *render_scenario_guidance(data),
        "## Historical Trends",
        "",
        (
            "The benchmark data on this page is loaded from `docs/data/release-benchmarks.json` and rendered in the "
            "browser. Release runs update that JSON file directly, so the published page reflects new release "
            "measurements after the docs deployment without rewriting this Markdown file."
        ),
        "",
        f'<div class="release-benchmark-app" data-release-benchmark-app data-release-benchmark-state="{data_state}">',
        '  <p class="release-benchmark-app__status" data-release-benchmark-status>Loading benchmark data...</p>',
        "  <noscript>JavaScript is required to load the release benchmark data.</noscript>",
        "</div>",
        "",
        "## Collection Policy",
        "",
        "- The benchmark workflow runs on `ubuntu-24.04`.",
        (
            "- Benchmark results are collected on GitHub Actions CI runners, so median timings can vary slightly "
            "with runner load and workflow timing; rerun benchmarks before treating small differences as regressions."
        ),
        "- The Python version is the workflow input, defaulting to the latest configured CI Python.",
        "- Release packages are installed from PyPI in isolated virtual environments.",
        (
            "- Every benchmark update measures `main` from an exact commit resolved at workflow start and replaces "
            "the previous `main` snapshot."
        ),
        "- Input coverage currently focuses on OpenAPI and JSON Schema.",
        "- Historical updates commit `docs/data/release-benchmarks.json`; the page renders that JSON client-side.",
        "",
    ))


def _generated_docs(data_path: Path, docs_path: Path, notes_path: Path | None) -> tuple[GeneratedDoc, ...]:
    data = load_benchmark_data(data_path, notes_path=notes_path)
    return (GeneratedDoc(docs_path, render_release_benchmark_markdown(data).rstrip() + "\n"),)


def _doc_is_current(doc: GeneratedDoc) -> bool:
    return doc.path.exists() and doc.path.read_text(encoding="utf-8") == doc.content


def build_docs(
    *,
    check: bool,
    data_path: Path = DATA_PATH,
    notes_path: Path | None = NOTES_PATH,
    docs_path: Path = DOCS_PATH,
) -> int:
    """Generate or check release benchmark documentation outputs."""
    docs = _generated_docs(data_path, docs_path, notes_path)
    if check:
        if not (out_of_date := tuple(doc.path for doc in docs if not _doc_is_current(doc))):
            print("Release benchmark docs are up to date.")
            return 0
        for path in out_of_date:
            print(f"Release benchmark docs are out of date: {path.relative_to(ROOT)}", file=sys.stderr)
        print("Run 'python scripts/build_release_benchmark_docs.py' to update.", file=sys.stderr)
        return 1

    for doc in docs:
        doc.path.parent.mkdir(parents=True, exist_ok=True)
        doc.path.write_text(doc.content, encoding="utf-8")
    return 0


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Build release benchmark documentation")
    parser.add_argument("--check", action="store_true", help="Check whether generated benchmark docs are up to date")
    parser.add_argument("--data", type=Path, default=DATA_PATH, help="Benchmark JSON data path")
    parser.add_argument("--notes", type=Path, default=NOTES_PATH, help="Benchmark notes JSON data path")
    parser.add_argument("--docs", type=Path, default=DOCS_PATH, help="Markdown output path")
    return parser.parse_args()


def main() -> int:
    """Script entrypoint."""
    args = parse_args()
    return build_docs(check=args.check, data_path=args.data, notes_path=args.notes, docs_path=args.docs)


if __name__ == "__main__":
    sys.exit(main())
