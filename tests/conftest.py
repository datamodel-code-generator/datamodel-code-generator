"""Test configuration and shared fixtures."""

from __future__ import annotations

import difflib
import importlib
import inspect
import json
import os
import re
import socket
import sys
import time
from collections import Counter
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice, starmap
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, Protocol, TypedDict, cast
from urllib.parse import urlparse

import pytest
import time_machine
from inline_snapshot import external_file, get_snapshot_value, register_format_alias
from inline_snapshot._external._storage._protocol import StorageLookupError
from inline_snapshot._global_state import state as inline_snapshot_state
from typing_extensions import Required

from datamodel_code_generator import MIN_VERSION
from datamodel_code_generator.http import _get_httpx

if TYPE_CHECKING:
    import warnings

CLI_DOC_COLLECTION_OUTPUT = Path(__file__).parent / "cli_doc" / ".cli_doc_collection.json"
CI_SHARD_WEIGHTS_PATH = Path(__file__).parents[1] / "scripts" / "ci_shard_weights.json"
CLI_DOC_SCHEMA_VERSION = 1
TEST_DEFAULT_FORMATTER_ENV = "DATAMODEL_CODE_GENERATOR_TEST_DEFAULT_FORMATTER"
BUILTIN_FORMATTER_VALUE = "builtin"
_VERSION_PATTERN = re.compile(r"^\d+\.\d+$")
_MOCK_PUBLIC_IP = "93.184.216.34"


class CliDocKwargs(TypedDict, total=False):
    """Type definition for @pytest.mark.cli_doc marker keyword arguments."""

    options: Required[list[str]]
    """CLI option names to document (e.g., ["--foo", "--bar"])."""

    option_description: Required[str]
    """User-facing description of the CLI option for generated documentation.

    This text appears in the CLI reference docs. Write as if explaining
    to end users what the option does and when to use it.
    Do NOT describe what the test verifies - describe the feature itself.

    Example (good): "Ignore pyproject.toml configuration file. This is useful
                     when you want to override project defaults with CLI arguments."
    Example (bad):  "Test that --ignore-pyproject flag works correctly."
    """

    cli_args: Required[list[str]]
    """CLI arguments to pass to the command (e.g., ["--foo", "value"])."""

    input_schema: str | None
    """Path to input schema file relative to tests/data/."""

    config_content: str | None
    """Content of pyproject.toml config to create for the test."""

    input_model: str | None
    """Input model in 'module:name' format for --input-model tests."""

    golden_output: str | None
    """Path to expected output file relative to tests/data/expected/."""

    version_outputs: dict[str, str] | None
    """Version-specific outputs: {"3.10": "path/to/expected.py"}."""

    model_outputs: dict[str, str] | None
    """Model-type-specific outputs: {"pydantic_v2": "path/to/expected.py"}."""

    expected_stdout: str | None
    """Path to expected stdout file relative to tests/data/expected/."""

    extra_outputs: list[dict[str, str]] | None
    """Additional output files to show in docs. Each item has title, path, and optional language."""

    related_options: list[str] | None
    """Related CLI options to link in documentation."""

    aliases: list[str] | None
    """Alternative option names (e.g., ["--capitalise-enum-members"])."""


@dataclass(frozen=True)
class MockHttpxResponse:
    """URL-bound HTTP client mock response for remote schema e2e tests."""

    url: str
    content: str | Path
    status_code: int = 200
    headers: Mapping[str, str] | None = None


HttpxHeaders = Mapping[str, str] | Sequence[tuple[str, str]]
HttpxParams = Mapping[str, str] | Sequence[tuple[str, str]]


class HttpxGetMock(Protocol):
    """Typed mock interface for HTTP client calls used by e2e URL tests."""

    call_count: int
    call_args: Any
    call_args_list: Sequence[Any]

    def __call__(
        self,
        url: str,
        *,
        headers: HttpxHeaders | None = None,
        verify: bool = True,
        follow_redirects: bool = False,
        params: HttpxParams | None = None,
        timeout: float = 5.0,
    ) -> Any:
        """Record an HTTP client get-style call."""
        raise NotImplementedError

    def assert_called(self) -> None:
        """Assert that the mock was called."""
        raise NotImplementedError

    def assert_not_called(self) -> None:
        """Assert that the mock was not called."""
        raise NotImplementedError

    def assert_called_once_with(self, *args: Any, **kwargs: Any) -> None:
        """Assert that the mock was called once with the expected arguments."""
        raise NotImplementedError

    def assert_has_calls(self, calls: Sequence[Any], any_order: bool = False) -> None:
        """Assert that the mock has the expected recorded calls."""
        raise NotImplementedError


class HttpxGetMockFactory(Protocol):
    """Factory fixture type for URL-bound HTTP client mocks."""

    def __call__(self, *responses: MockHttpxResponse) -> HttpxGetMock:
        """Patch the selected HTTP client's get call with URL-bound responses."""
        raise NotImplementedError


def create_httpx_get_mock(mocker: Any) -> HttpxGetMock:
    """Create a typed recording mock for selected-client get assertions."""
    return cast("HttpxGetMock", mocker.create_autospec(_get_httpx().get))


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add pytest options and ini settings."""
    parser.addoption(
        "--collect-cli-docs",
        action="store_true",
        default=False,
        help="Collect CLI documentation metadata from tests marked with @pytest.mark.cli_doc",
    )
    parser.addini(
        "assert_helper_direct_assert_exempt_files",
        "Test files under tests/ that are exempt from the shared assertion helper direct-assert guard.",
        type="linelist",
        default=(),
    )


def pytest_configure(config: pytest.Config) -> None:
    """Register the cli_doc marker."""
    config.addinivalue_line(
        "markers",
        "cli_doc(options, option_description, cli_args, input_schema=None, golden_output=None, "
        "version_outputs=None, model_outputs=None, expected_stdout=None, config_content=None, "
        "aliases=None, **kwargs): "
        "Mark test as CLI documentation source. "
        "option_description: User-facing description for CLI docs (required). "
        "Either golden_output, version_outputs, model_outputs, or expected_stdout is required. "
        "aliases: list of alternative option names (e.g., ['--capitalise-enum-members']).",
    )
    config._cli_doc_items: list[dict[str, Any]] = []


def _validate_cli_doc_marker(node_id: str, kwargs: CliDocKwargs) -> list[str]:  # noqa: ARG001, PLR0912, PLR0914  # pragma: no cover
    """Validate marker required fields and types.

    Only called when --collect-cli-docs is used (cli-docs tox job, which doesn't contribute to coverage).
    """
    errors: list[str] = []

    if "options" not in kwargs:
        errors.append("Missing required field: 'options'")
    if "option_description" not in kwargs:
        errors.append("Missing required field: 'option_description'")
    elif not isinstance(kwargs["option_description"], str):
        errors.append(f"'option_description' must be a string, got {type(kwargs['option_description']).__name__}")
    elif not kwargs["option_description"].strip():
        errors.append("'option_description' must not be empty")
    if "cli_args" not in kwargs:
        errors.append("Missing required field: 'cli_args'")

    has_golden = "golden_output" in kwargs and kwargs["golden_output"] is not None
    has_versions = "version_outputs" in kwargs and kwargs["version_outputs"] is not None
    has_models = "model_outputs" in kwargs and kwargs["model_outputs"] is not None
    has_stdout = "expected_stdout" in kwargs and kwargs["expected_stdout"] is not None
    if not has_golden and not has_versions and not has_models and not has_stdout:
        errors.append("Either 'golden_output', 'version_outputs', 'model_outputs', or 'expected_stdout' is required")

    has_input_schema = "input_schema" in kwargs and kwargs["input_schema"] is not None
    has_config_content = "config_content" in kwargs and kwargs["config_content"] is not None
    has_input_model = "input_model" in kwargs and kwargs["input_model"] is not None
    if not has_input_schema and not has_config_content and not has_input_model and not has_stdout:
        errors.append(
            "Either 'input_schema', 'config_content', or 'input_model' is required "
            "(or 'expected_stdout' with cli_args as input)"
        )

    if "options" in kwargs:
        opts = kwargs["options"]
        if not isinstance(opts, list):
            errors.append(f"'options' must be a list, got {type(opts).__name__}")
        elif not opts:
            errors.append("'options' must be a non-empty list")
        elif not all(isinstance(o, str) for o in opts):
            errors.append("'options' must be a list of strings")

    if "cli_args" in kwargs:
        args = kwargs["cli_args"]
        if not isinstance(args, list):
            errors.append(f"'cli_args' must be a list, got {type(args).__name__}")
        elif not all(isinstance(a, str) for a in args):
            errors.append("'cli_args' must be a list of strings")

    if "input_schema" in kwargs:
        schema = kwargs["input_schema"]
        if not isinstance(schema, str):
            errors.append(f"'input_schema' must be a string, got {type(schema).__name__}")

    if has_input_model:
        input_model = kwargs["input_model"]
        if not isinstance(input_model, str):
            errors.append(f"'input_model' must be a string, got {type(input_model).__name__}")
        else:
            parts = input_model.split(":", 1)
            if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
                errors.append(f"'input_model' must be in 'module:name' format, got {input_model!r}")

    if has_golden:
        golden = kwargs["golden_output"]
        if not isinstance(golden, str):
            errors.append(f"'golden_output' must be a string, got {type(golden).__name__}")

    if has_versions:
        versions = kwargs["version_outputs"]
        if not isinstance(versions, dict):
            errors.append(f"'version_outputs' must be a dict, got {type(versions).__name__}")
        else:
            for key, value in versions.items():
                if not isinstance(key, str):
                    errors.append(f"'version_outputs' keys must be strings, got {type(key).__name__}")
                elif not _VERSION_PATTERN.match(key):
                    errors.append(f"Invalid version key '{key}': must match X.Y format (e.g., '3.10')")
                if not isinstance(value, str):
                    errors.append(f"'version_outputs' values must be strings, got {type(value).__name__}")

    if has_models:
        models = kwargs["model_outputs"]
        if not isinstance(models, dict):
            errors.append(f"'model_outputs' must be a dict, got {type(models).__name__}")
        else:
            valid_keys = {
                "pydantic_v2",
                "pydantic_v2.dataclass",
                "dataclass",
                "dataclasses.dataclass",
                "typeddict",
                "typing.TypedDict",
                "msgspec",
                "msgspec.Struct",
            }
            for key, value in models.items():
                if not isinstance(key, str):
                    errors.append(f"'model_outputs' keys must be strings, got {type(key).__name__}")
                elif key not in valid_keys:
                    errors.append(f"Invalid model key '{key}': must be one of {valid_keys}")
                if not isinstance(value, str):
                    errors.append(f"'model_outputs' values must be strings, got {type(value).__name__}")

    if "related_options" in kwargs:
        related = kwargs["related_options"]
        if not isinstance(related, list):
            errors.append(f"'related_options' must be a list, got {type(related).__name__}")
        elif not all(isinstance(r, str) for r in related):
            errors.append("'related_options' must be a list of strings")

    if "extra_outputs" in kwargs:
        extra_outputs = kwargs["extra_outputs"]
        if not isinstance(extra_outputs, list):
            errors.append(f"'extra_outputs' must be a list, got {type(extra_outputs).__name__}")
        else:
            for index, output in enumerate(extra_outputs):
                if not isinstance(output, dict):
                    errors.append(f"'extra_outputs[{index}]' must be a dict, got {type(output).__name__}")
                    continue
                for key in ("title", "path"):
                    value = output.get(key)
                    if not isinstance(value, str) or not value:
                        errors.append(f"'extra_outputs[{index}].{key}' must be a non-empty string")
                language = output.get("language", "")
                if not isinstance(language, str):
                    errors.append(f"'extra_outputs[{index}].language' must be a string")

    if "aliases" in kwargs:
        aliases = kwargs["aliases"]
        if aliases is not None:
            if not isinstance(aliases, list):
                errors.append(f"'aliases' must be a list, got {type(aliases).__name__}")
            elif not all(isinstance(a, str) for a in aliases):
                errors.append("'aliases' must be a list of strings")

    return errors


def slow_test_durations() -> dict[str, int]:
    """Return the longest measured CI duration per slow test node id across shard weight profiles."""
    durations: dict[str, int] = {}
    for profile in json.loads(CI_SHARD_WEIGHTS_PATH.read_text(encoding="utf-8"))["profiles"].values():
        for nodeid, duration in profile["slow"].items():
            durations[nodeid] = max(duration, durations.get(nodeid, 0))
    return durations


def worksteal_chunk_sizes(total: int, workers: int) -> list[int]:
    """Mirror how the pytest-xdist work-stealing scheduler splits a collection into initial worker chunks."""
    sizes: list[int] = []
    pending = total
    for remaining in range(workers, 0, -1):
        size = pending // remaining
        sizes.append(size)
        pending -= size
    return sizes


def order_slow_tests_first(items: list[pytest.Item], durations: dict[str, int], workers: int) -> list[pytest.Item]:
    """Start every worker chunk with a balanced share of the measured slow tests, longest first."""
    slow = sorted(
        (item for item in items if item.nodeid in durations),
        key=lambda item: (-durations[item.nodeid], item.nodeid),
    )
    light = iter(item for item in items if item.nodeid not in durations)
    groups: list[list[pytest.Item]] = [[] for _ in range(workers)]
    loads = [0] * workers
    for item in slow:
        target = loads.index(min(loads))
        groups[target].append(item)
        loads[target] += durations[item.nodeid]
    ordered: list[pytest.Item] = []
    for group, size in zip(groups, worksteal_chunk_sizes(len(items), workers), strict=True):
        ordered.extend(group)
        ordered.extend(islice(light, max(0, size - len(group))))
    ordered.extend(light)
    return ordered


def pytest_collection_modifyitems(
    session: pytest.Session,  # noqa: ARG001
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Collect CLI doc metadata from tests with cli_doc marker and start each worker with slow tests.

    Always collects metadata for use by test_cli_doc_coverage.py.
    Only validates markers when --collect-cli-docs is used.
    Spreading measured slow tests over the initial xdist chunks keeps work stealing from ending on them.
    """
    workers = getattr(config, "workerinput", {}).get("workercount", 1)
    items[:] = order_slow_tests_first(items, slow_test_durations(), workers)
    collect_cli_docs = config.getoption("--collect-cli-docs", default=False)
    validation_errors: list[tuple[str, list[str]]] = []

    for item in items:
        marker = item.get_closest_marker("cli_doc")
        if marker is None:
            continue

        if collect_cli_docs:  # pragma: no cover
            errors = _validate_cli_doc_marker(item.nodeid, cast("CliDocKwargs", marker.kwargs))
            if errors:
                validation_errors.append((item.nodeid, errors))
                continue

        # Get option_description from marker kwargs (required field)
        option_description = marker.kwargs.get("option_description", "")

        config._cli_doc_items.append({
            "node_id": item.nodeid,
            "marker_kwargs": marker.kwargs,
            "option_description": option_description,
        })

    if validation_errors:  # pragma: no cover
        error_msg = "CLI doc marker validation errors:\n"
        for node_id, errors in validation_errors:
            error_msg += f"\n  {node_id}:\n"
            error_msg += "\n".join(f"    - {e}" for e in errors)
        pytest.fail(error_msg, pytrace=False)


def pytest_runtestloop(session: pytest.Session) -> bool | None:  # pragma: no cover
    """Skip test execution when --collect-cli-docs is used."""
    if session.config.getoption("--collect-cli-docs"):
        return True
    return None


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:  # noqa: ARG001  # pragma: no cover
    """Save collected CLI doc metadata to JSON file."""
    config = session.config
    if not config.getoption("--collect-cli-docs"):
        return

    items = getattr(config, "_cli_doc_items", [])

    output = {
        "schema_version": CLI_DOC_SCHEMA_VERSION,
        "items": items,
    }

    CLI_DOC_COLLECTION_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with Path(CLI_DOC_COLLECTION_OUTPUT).open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)


class CodeValidationStats:
    """Track code validation statistics."""

    def __init__(self) -> None:
        """Initialize statistics counters."""
        self.compile_count = 0
        self.compile_time = 0.0
        self.exec_count = 0
        self.exec_time = 0.0
        self.errors: list[tuple[str, str]] = []

    def record_compile(self, elapsed: float) -> None:
        """Record a compile operation."""
        self.compile_count += 1
        self.compile_time += elapsed

    def record_exec(self, elapsed: float) -> None:
        """Record an exec operation."""
        self.exec_count += 1
        self.exec_time += elapsed

    def record_error(self, file_path: str, error: str) -> None:  # pragma: no cover
        """Record a validation error."""
        self.errors.append((file_path, error))


_validation_stats = CodeValidationStats()


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:  # noqa: ARG001  # pragma: no cover
    """Print code validation and CLI doc collection summary at the end of test run."""
    if config.getoption("--collect-cli-docs", default=False):
        items = getattr(config, "_cli_doc_items", [])
        terminalreporter.write_sep("=", "CLI Documentation Collection")
        terminalreporter.write_line(f"Collected {len(items)} CLI doc items -> {CLI_DOC_COLLECTION_OUTPUT}")

    if _validation_stats.compile_count > 0:
        terminalreporter.write_sep("=", "Code Validation Summary")
        terminalreporter.write_line(
            f"Compiled {_validation_stats.compile_count} files in {_validation_stats.compile_time:.3f}s "
            f"(avg: {_validation_stats.compile_time / _validation_stats.compile_count * 1000:.2f}ms)"
        )
        if _validation_stats.exec_count > 0:
            terminalreporter.write_line(
                f"Executed {_validation_stats.exec_count} files in {_validation_stats.exec_time:.3f}s "
                f"(avg: {_validation_stats.exec_time / _validation_stats.exec_count * 1000:.2f}ms)"
            )
        if _validation_stats.errors:
            terminalreporter.write_line(f"\nValidation errors: {len(_validation_stats.errors)}")
            for file_path, error in _validation_stats.errors:
                terminalreporter.write_line(f"  {file_path}: {error}")


def _parse_time_string(time_str: str) -> datetime:
    """Parse time string to datetime with UTC timezone."""
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(time_str, fmt)  # noqa: DTZ007
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt  # noqa: TRY300
        except ValueError:  # noqa: PERF203  # pragma: no branch
            continue
    return datetime.fromisoformat(time_str.replace("Z", "+00:00"))  # pragma: no cover


def freeze_time(time_to_freeze: str, **kwargs: Any) -> time_machine.travel:  # noqa: ARG001
    """Freeze time using time-machine (100-200x faster than freezegun)."""
    dt = _parse_time_string(time_to_freeze)
    return time_machine.travel(dt, tick=False)


def _normalize_line_endings(text: str) -> str:
    """Normalize line endings to LF for cross-platform comparison.

    This keeps snapshot comparisons platform-neutral; byte-level generated-file
    newline invariants are pinned separately.
    """
    return text.replace("\r\n", "\n")


def _normalize_generated_text(text: str) -> str:
    """Normalize generated text for file comparisons."""
    text = _normalize_line_endings(text)
    return text if text.endswith("\n") else f"{text}\n"


def _get_tox_env() -> str:  # pragma: no cover
    """Get the current tox environment name from TOX_ENV_NAME or fallback.

    Strips parallel-only suffixes since inline-snapshot requires -n0 (single process).
    Only called in assertion failure hints.
    """
    import os

    env = os.environ.get("TOX_ENV_NAME", "<version>")
    # Remove parallel-only suffixes since inline-snapshot needs single process mode.
    return env.removesuffix("-nocov-parallel").removesuffix("-parallel")


def _format_snapshot_hint(action: str) -> str:  # pragma: no cover
    """Format a hint message for inline-snapshot commands with rich formatting.

    Only called when assertions fail.
    """
    from io import StringIO

    from rich.console import Console
    from rich.text import Text

    tox_env = _get_tox_env()
    command = f"  tox run -e {tox_env} -- --inline-snapshot={action}"

    description = "To update the expected file, run:" if action == "fix" else "To create the expected file, run:"

    output = StringIO()
    console = Console(file=output, force_terminal=True, width=200, soft_wrap=False)

    console.print(Text(description, style="default"))
    console.print(Text(command, style="bold cyan"))

    return output.getvalue()


def _format_new_content(content: str) -> str:  # pragma: no cover
    """Format new content (for create mode) with green color.

    Only called when expected file not found.
    """
    from io import StringIO

    from rich.console import Console
    from rich.text import Text

    output = StringIO()
    console = Console(file=output, force_terminal=True, width=200, soft_wrap=False)

    for line in content.splitlines():
        console.print(Text(f"+{line}", style="green"))

    return output.getvalue()


def _format_diff(expected: str, actual: str, expected_path: Path) -> str:  # pragma: no cover
    """Format a unified diff between expected and actual content with colors.

    Only called when content differs from expected.
    """
    from io import StringIO

    from rich.console import Console
    from rich.text import Text

    expected_lines = expected.splitlines(keepends=True)
    actual_lines = actual.splitlines(keepends=True)
    diff_lines = list(
        difflib.unified_diff(
            expected_lines,
            actual_lines,
            fromfile=str(expected_path),
            tofile="actual",
        )
    )

    if not diff_lines:
        return ""

    output = StringIO()
    console = Console(file=output, force_terminal=True, width=200, soft_wrap=False)

    for line in diff_lines:
        line_stripped = line.rstrip("\n")
        # Skip header lines since file path is already in the error message
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("@@"):
            console.print(Text(line_stripped, style="cyan"))
        elif line.startswith("-"):
            console.print(Text(line_stripped, style="red"))
        elif line.startswith("+"):
            console.print(Text(line_stripped, style="green"))
        else:
            # Use default to override pytest's red color for E lines
            console.print(Text(line_stripped, style="default"))

    return output.getvalue()


def _infer_expected_file(caller_function_name: str) -> str:
    name = caller_function_name
    for prefix in ("test_main_", "test_"):  # pragma: no branch
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    return f"{name}.py"


def _assert_with_external_file(content: str, expected_path: Path) -> None:
    """Assert content matches external file, handling line endings."""
    __tracebackhide__ = True
    try:
        expected = external_file(expected_path)
    except FileNotFoundError:  # pragma: no cover
        hint = _format_snapshot_hint("create")
        formatted_content = _format_new_content(content)
        msg = f"Expected file not found: {expected_path}\n{hint}\n{formatted_content}"
        raise AssertionError(msg) from None
    normalized_content = _normalize_line_endings(content)

    def raise_content_mismatch(normalized_expected: str) -> NoReturn:
        hint = _format_snapshot_hint("fix")
        diff = _format_diff(normalized_expected, normalized_content, expected_path)
        msg = f"Content mismatch for {expected_path}\n{hint}\n{diff}"
        raise AssertionError(msg) from None

    def record_snapshot_update() -> None:
        if matched := expected == normalized_content:
            return
        msg = f"inline-snapshot did not record {expected_path}: {matched}"
        raise AssertionError(msg) from None

    match expected:
        case str():
            if normalized_content == (normalized_expected := _normalize_line_endings(expected)):
                return
            raise_content_mismatch(normalized_expected)

        case _:
            update_flags = inline_snapshot_state().update_flags
            try:
                expected_value = get_snapshot_value(expected, "old")
            except StorageLookupError:
                expected_value = Ellipsis

            if expected_value is Ellipsis:
                if update_flags.create:
                    # Let inline-snapshot materialize missing external files only in create mode.
                    record_snapshot_update()
                    return
                hint = _format_snapshot_hint("create")
                formatted_content = _format_new_content(content)
                msg = f"Expected file not found: {expected_path}\n{hint}\n{formatted_content}"
                raise AssertionError(msg) from None

            if normalized_content == (normalized_expected := _normalize_line_endings(cast("str", expected_value))):
                return

            if update_flags.fix:
                # Let inline-snapshot update existing external files only in fix mode.
                record_snapshot_update()
                return
            raise_content_mismatch(normalized_expected)


class AssertFileContent(Protocol):
    """Protocol for file content assertion callable."""

    def __call__(
        self,
        output_file: Path,
        expected_name: str | Path | None = None,
        encoding: str = "utf-8",
        transform: Callable[[str], str] | None = None,
    ) -> None:
        """Assert file content matches expected output."""
        ...


def create_assert_file_content(
    base_path: Path,
) -> AssertFileContent:
    """Create an assert function bound to a specific expected path.

    Args:
        base_path: The base path for expected files (e.g., EXPECTED_JSON_SCHEMA_PATH).

    Returns:
        A function that asserts file content matches expected.

    Usage:
        # In test module
        assert_file_content = create_assert_file_content(EXPECTED_JSON_SCHEMA_PATH)

        # In tests - infer from function name
        assert_file_content(output_file)  # test_main_foo -> foo.py

        # Explicit filename
        assert_file_content(output_file, "custom.py")
        assert_file_content(output_file, "subdir/bar.py")
        assert_file_content(output_file, f"{expected_output}/file.py")
    """

    def _assert_file_content(
        output_file: Path,
        expected_name: str | Path | None = None,
        encoding: str = "utf-8",
        transform: Callable[[str], str] | None = None,
    ) -> None:
        """Assert that file content matches expected external file."""
        __tracebackhide__ = True
        if expected_name is None:
            frame = inspect.currentframe()
            assert frame is not None
            assert frame.f_back is not None
            expected_name = _infer_expected_file(frame.f_back.f_code.co_name)
            del frame

        expected_path = base_path / expected_name
        content = output_file.read_text(encoding=encoding)
        if transform is not None:
            content = transform(content)
        _assert_with_external_file(content, expected_path)

    return _assert_file_content


def assert_output(
    output: object,
    expected_path: Path,
) -> None:
    """Assert that output string matches expected external file.

    Args:
        output: The output string to compare (e.g., captured.out, parser.parse()).
        expected_path: Path to the expected file.

    Usage:
        assert_output(captured.out, EXPECTED_PATH / "output.py")
        assert_output(parser.parse(), EXPECTED_PATH / "output.py")
    """
    __tracebackhide__ = True
    if not isinstance(output, str):  # pragma: no cover
        pytest.fail(f"Expected generated output to be str, got {type(output).__name__}")
    _assert_with_external_file(output, expected_path)


def assert_mutable_copy_is_isolated(
    *,
    original: object,
    copied: object,
    mutate_copied: Callable[[Any], None],
    label: str,
) -> None:
    """Assert that mutating a copied mutable value does not mutate its original."""
    __tracebackhide__ = True
    original_snapshot = deepcopy(original)
    copied_snapshot = deepcopy(copied)
    if copied != original:  # pragma: no cover
        pytest.fail(f"{label} copy differs from original before mutation: original={original!r}, copied={copied!r}")
    if copied is original:  # pragma: no cover
        pytest.fail(f"{label} copy reuses the original object")
    mutate_copied(copied)
    if copied == copied_snapshot:  # pragma: no cover
        pytest.fail(f"{label} mutation did not change the copied value")
    if original != original_snapshot:  # pragma: no cover
        pytest.fail(f"{label} mutation changed the original: before={original_snapshot!r}, after={original!r}")


def _tracks_mutation(value: object) -> bool:
    return isinstance(value, (dict, list))


@contextmanager
def assert_inputs_not_mutated(inputs: Mapping[str, object] | None) -> Generator[None, None, None]:
    """Assert guarded mutable inputs are unchanged after the wrapped operation."""
    __tracebackhide__ = True
    if inputs is None:
        yield
        return

    snapshots = {label: deepcopy(value) for label, value in inputs.items() if _tracks_mutation(value)}
    yield
    for label, before in snapshots.items():
        if (current := inputs[label]) != before:  # pragma: no cover - exercised by helper tests
            pytest.fail(f"{label} was mutated: before={before!r}, after={current!r}")


def assert_directory_content(
    output_dir: Path,
    expected_dir: Path,
    pattern: str = "*.py",
    encoding: str = "utf-8",
) -> None:
    """Assert all files in output_dir match expected files in expected_dir.

    Args:
        output_dir: Directory containing generated output files.
        expected_dir: Directory containing expected files.
        pattern: Glob pattern for files to compare (default: "*.py").
        encoding: File encoding (default: "utf-8").

    Usage:
        assert_directory_content(tmp_path / "model", EXPECTED_PATH / "main_modular")
    """
    __tracebackhide__ = True
    output_files = {p.relative_to(output_dir) for p in output_dir.rglob(pattern)}
    expected_files = {p.relative_to(expected_dir) for p in expected_dir.rglob(pattern)}

    # Check for extra expected files (output missing files that are expected)
    extra = expected_files - output_files
    assert not extra, f"Expected files not in output: {extra}"

    # Compare all output files (including new ones not yet in expected)
    for output_path in output_dir.rglob(pattern):
        relative_path = output_path.relative_to(output_dir)
        expected_path = expected_dir / relative_path
        result = output_path.read_text(encoding=encoding)
        _assert_with_external_file(result, expected_path)


def assert_exact_directory_content(
    output_dir: Path,
    expected_dir: Path,
    pattern: str = "*.py",
    encoding: str = "utf-8",
) -> None:
    """Assert all files in output_dir match expected_dir exactly without snapshot indirection."""
    __tracebackhide__ = True
    output_files = {p.relative_to(output_dir) for p in output_dir.rglob(pattern)}
    expected_files = {p.relative_to(expected_dir) for p in expected_dir.rglob(pattern)}

    extra = expected_files - output_files
    assert not extra, f"Expected files not in output: {extra}"

    missing = output_files - expected_files
    assert not missing, f"Output has files not in expected: {missing}"

    for output_path in output_dir.rglob(pattern):
        relative_path = output_path.relative_to(output_dir)
        expected_path = expected_dir / relative_path
        output = _normalize_line_endings(output_path.read_text(encoding=encoding))
        expected = _normalize_line_endings(expected_path.read_text(encoding=encoding))
        if output != expected:
            diff = _format_diff(expected, output, expected_path)
            msg = f"Content mismatch for {expected_path}\n{diff}"
            raise AssertionError(msg)


def _get_full_body(result: object) -> str:
    """Get full body from Result."""
    if isinstance(result, str):
        return result
    return getattr(result, "body", "")


def assert_parser_results(
    results: dict,
    expected_dir: Path,
    pattern: str = "*.py",
) -> None:
    """Assert parser results match expected files.

    Args:
        results: Dictionary with string keys mapping to objects with .body attribute.
        expected_dir: Directory containing expected files.
        pattern: Glob pattern for files to compare (default: "*.py").

    Usage:
        results = {delimiter.join(p): r for p, r in parser.parse().items()}
        assert_parser_results(results, EXPECTED_PATH / "parser_output")
    """
    __tracebackhide__ = True
    for expected_path in expected_dir.rglob(pattern):
        key = str(expected_path.relative_to(expected_dir))
        if key not in results:
            msg = f"Parser result missing expected file: {key}"
            raise AssertionError(msg)
        result_obj = results.pop(key)
        _assert_with_external_file(_get_full_body(result_obj), expected_path)
    assert not results, list(results)


def assert_parser_modules(
    modules: dict,
    expected_dir: Path,
) -> None:
    """Assert parser modules match expected files.

    Args:
        modules: Dictionary with tuple keys mapping to objects with .body attribute.
        expected_dir: Directory containing expected files.

    Usage:
        modules = parser.parse()
        assert_parser_modules(modules, EXPECTED_PATH / "parser_modular")
    """
    __tracebackhide__ = True
    output_files = set(starmap(Path, modules))
    expected_files = {path.relative_to(expected_dir) for path in expected_dir.rglob("*.py")}

    extra = expected_files - output_files
    assert not extra, f"Expected files not in parser modules: {extra}"

    missing = output_files - expected_files
    assert not missing, f"Parser modules not in expected files: {missing}"

    for paths, result in modules.items():
        expected_path = expected_dir.joinpath(*paths)
        _assert_with_external_file(_get_full_body(result), expected_path)


def assert_generated_modules_output(
    modules: Mapping[tuple[str, ...], Any],
    expected_dir: Path,
    transform: Callable[[str], str] | None = None,
) -> None:
    """Assert generate(output=None) module output matches expected files exactly."""
    __tracebackhide__ = True
    output_files = set(starmap(Path, modules))
    expected_files = {path.relative_to(expected_dir) for path in expected_dir.rglob("*.py")}

    extra = expected_files - output_files
    assert not extra, f"Expected files not in generated modules: {extra}"

    missing = output_files - expected_files
    assert not missing, f"Generated modules not in expected files: {missing}"

    for paths, result in modules.items():
        expected_path = expected_dir.joinpath(*paths)
        content = _get_full_body(result)
        if transform is not None:
            content = transform(content)
        _assert_with_external_file(content, expected_path)


def write_generated_modules(output_dir: Path, modules: dict[tuple[str, ...], Any]) -> None:
    """Write parser-generated module output to a package directory."""
    for module_path, result in modules.items():
        file_path = output_dir.joinpath(*module_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(_get_full_body(result), encoding="utf-8")


def assert_runtime_result_model(output_dir: Path) -> None:
    """Assert generated modular output resolves cross-module Pydantic references at runtime."""
    sys.path.insert(0, str(output_dir.parent))
    importlib.invalidate_caches()
    try:
        from model._internal import Result

        result = Result.model_validate({"event": {"id": "abc"}})
        assert result.event is not None
        assert result.event.__class__.__name__ == "Event"
    finally:
        sys.path.pop(0)
        for name in [module for module in sys.modules if module == "model" or module.startswith("model.")]:
            del sys.modules[name]


def assert_runtime_import_package(output_dir: Path, expected_dir: Path) -> None:
    """Assert generated modular package matches expected files and imports correctly at runtime."""
    assert_exact_directory_content(output_dir, expected_dir)
    assert_runtime_result_model(output_dir)


def assert_generated_runtime_package(
    output_dir: Path,
    modules: dict[tuple[str, ...], Any],
    expected_dir: Path,
) -> None:
    """Write parser-generated modules, compare the package tree, and verify runtime imports."""
    write_generated_modules(output_dir, modules)
    assert_runtime_import_package(output_dir, expected_dir)


def assert_error_message(
    capsys: pytest.CaptureFixture[str],
    expected: str,
) -> None:
    """Assert that stderr contains the expected error message.

    Args:
        capsys: pytest capsys fixture for capturing output.
        expected: Expected substring in stderr.

    Usage:
        return_code = run_main_with_args([...], expected_exit=Exit.ERROR, capsys=capsys)
        assert_error_message(capsys, "Error message")
    """
    __tracebackhide__ = True
    captured = capsys.readouterr()
    assert expected in captured.err, f"Expected '{expected}' in stderr, got: {captured.err}"


def _assert_httpx_params_contain(mock_get: HttpxGetMock, params_contains: Mapping[str, str]) -> None:
    missing_by_call = []
    for index, recorded_call in enumerate(mock_get.call_args_list, start=1):
        actual_params = dict(recorded_call.kwargs.get("params") or {})
        missing = {key: value for key, value in params_contains.items() if actual_params.get(key) != value}
        if missing:
            missing_by_call.append(f"call {index}: missing {missing}; actual params: {actual_params}")
    assert not missing_by_call, "Expected query parameters not found: " + "; ".join(missing_by_call)


def _assert_httpx_call_options(
    mock_get: HttpxGetMock,
    *,
    headers: HttpxHeaders | None,
    params: HttpxParams | None,
    verify: bool | None,
    timeout: float | None,
) -> None:
    if headers is None and params is None and verify is None and timeout is None:
        return
    mock_get.assert_called()
    for httpx_call in mock_get.call_args_list:
        call_kwargs = httpx_call.kwargs
        if headers is not None:
            assert call_kwargs.get("headers") == headers
        if params is not None:
            assert call_kwargs.get("params") == params
        if verify is not None:
            assert call_kwargs.get("verify") is verify
        if timeout is not None:
            assert call_kwargs.get("timeout") == timeout


def _assert_httpx_urls(
    mock_get: HttpxGetMock,
    *,
    expected_url: str | None,
    expected_urls: list[str] | None,
    any_order: bool,
) -> None:
    if expected_url is None and expected_urls is None:
        return

    mock_get.assert_called()
    actual_urls = [httpx_call.args[0] for httpx_call in mock_get.call_args_list]

    if expected_url is not None:
        assert mock_get.call_count == 1
        assert actual_urls == [expected_url]

    if expected_urls is not None:
        if any_order:
            assert Counter(actual_urls) == Counter(expected_urls)
        else:
            assert actual_urls == expected_urls


def assert_httpx_get_kwargs(
    mock_get: HttpxGetMock,
    *,
    call_count: int | None = None,
    called: bool | None = None,
    expected_url: str | None = None,
    expected_urls: list[str] | None = None,
    any_order: bool = False,
    headers: HttpxHeaders | None = None,
    params: HttpxParams | None = None,
    verify: bool | None = None,
    timeout: float | None = None,
    params_contains: Mapping[str, str] | None = None,
) -> None:
    """Assert common HTTP client call options used by URL e2e tests."""
    __tracebackhide__ = True
    if called is False:
        mock_get.assert_not_called()
        return
    if called is True:
        mock_get.assert_called()
    if call_count is not None:
        assert mock_get.call_count == call_count
    _assert_httpx_urls(mock_get, expected_url=expected_url, expected_urls=expected_urls, any_order=any_order)
    _assert_httpx_call_options(mock_get, headers=headers, params=params, verify=verify, timeout=timeout)
    if params_contains is not None:
        mock_get.assert_called()
        _assert_httpx_params_contain(mock_get, params_contains)


@pytest.fixture
def mock_httpx_get(mocker: Any) -> HttpxGetMockFactory:
    """Patch the selected HTTP client's get call for URL e2e tests."""

    def _mock_httpx_get(
        *responses: MockHttpxResponse,
    ) -> HttpxGetMock:
        queued_responses: dict[str, list[MockHttpxResponse]] = {}
        for response in responses:
            queued_responses.setdefault(response.url, []).append(response)

        def _getaddrinfo_for_public_host(
            _host: bytes | str,
            _port: int | str | None,
            *_args: Any,
            **_kwargs: Any,
        ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (_MOCK_PUBLIC_IP, 0))]

        def _response_for_url(url: str, *_args: Any, **_kwargs: Any) -> Any:
            if url not in queued_responses or not queued_responses[url]:  # pragma: no cover
                pytest.fail(
                    f"Unexpected HTTP client URL: {url!r}. Registered URLs: {sorted(queued_responses)}",
                    pytrace=False,
                )
            response_config = queued_responses[url].pop(0)
            response = mocker.Mock()
            response.status_code = response_config.status_code
            response.headers = dict(response_config.headers or {})
            response.text = (
                response_config.content.read_text(encoding="utf-8")
                if isinstance(response_config.content, Path)
                else response_config.content
            )
            response.content = response.text.encode("utf-8")
            return response

        def _response_for_validated_url(
            _httpx_module: Any,
            url: str,
            *,
            headers: HttpxHeaders | None,
            verify: bool,
            follow_redirects: bool,
            query_parameters: HttpxParams | None,
            timeout: float,
            pinned_host: str | None,
            pinned_ips: object,
        ) -> Any:
            if pinned_host is not None:
                from datamodel_code_generator.http import _normalize_dns_host

                parsed_host = urlparse(url).hostname
                if parsed_host is None:  # pragma: no cover
                    pytest.fail(f"Expected pinned fetch URL to include a host: {url!r}", pytrace=False)
                expected_host = _normalize_dns_host(parsed_host)
                if expected_host is None:  # pragma: no cover
                    pytest.fail(f"Expected pinned fetch URL to include a valid host: {url!r}", pytrace=False)
                if pinned_host != expected_host:  # pragma: no cover
                    pytest.fail(f"Expected pinned host {expected_host!r}, got {pinned_host!r}", pytrace=False)
                if not pinned_ips:  # pragma: no cover
                    pytest.fail(f"Expected pinned IPs for URL: {url!r}", pytrace=False)
            return httpx_get_mock(
                url,
                headers=headers,
                verify=verify,
                follow_redirects=follow_redirects,
                params=query_parameters,
                timeout=timeout,
            )

        def _response_for_session(
            _session: Any,
            httpx_module: Any,
            url: str,
            **kwargs: Any,
        ) -> Any:
            return _response_for_validated_url(httpx_module, url, **kwargs)

        mocker.patch("socket.getaddrinfo", autospec=True, side_effect=_getaddrinfo_for_public_host)
        httpx_get_mock = cast(
            "HttpxGetMock",
            mocker.patch.object(_get_httpx(), "get", autospec=True, side_effect=_response_for_url),
        )
        mocker.patch(
            "datamodel_code_generator.http._get_http_response",
            autospec=True,
            side_effect=_response_for_validated_url,
        )
        mocker.patch(
            "datamodel_code_generator.http._HTTPFetchSession.get_response",
            autospec=True,
            side_effect=_response_for_session,
        )
        return httpx_get_mock

    return _mock_httpx_get


def assert_warnings_contain(recorded_warnings: list[warnings.WarningMessage], *expected_messages: str) -> None:
    """Assert recorded warnings include each expected message fragment."""
    __tracebackhide__ = True
    messages = [str(warning.message) for warning in recorded_warnings]
    missing = [expected for expected in expected_messages if not any(expected in message for message in messages)]
    assert not missing, f"Expected warning messages not found: {missing}; actual warnings: {messages}"


def assert_warnings_do_not_contain(recorded_warnings: list[warnings.WarningMessage], *unexpected_messages: str) -> None:
    """Assert recorded warnings do not include any unexpected message fragment."""
    __tracebackhide__ = True
    messages = [str(warning.message) for warning in recorded_warnings]
    found = [unexpected for unexpected in unexpected_messages if any(unexpected in message for message in messages)]
    assert not found, f"Unexpected warning messages found: {found}; actual warnings: {messages}"


def assert_no_uncommented_generated_code(
    generated_content: str,
    *,
    forbidden_starts: tuple[str, ...] = (),
    forbidden_contains: tuple[str, ...] = (),
) -> None:
    """Assert generated Python does not contain forbidden uncommented code fragments."""
    __tracebackhide__ = True
    uncommented_lines = [line.strip() for line in generated_content.split("\n") if not line.strip().startswith("#")]
    for forbidden_start in forbidden_starts:
        matches = [line for line in uncommented_lines if line.startswith(forbidden_start)]
        assert not matches, f"Generated code contains forbidden line prefix {forbidden_start!r}: {matches}"
    for forbidden_fragment in forbidden_contains:
        matches = [line for line in uncommented_lines if forbidden_fragment in line]
        assert not matches, f"Generated code contains forbidden fragment {forbidden_fragment!r}: {matches}"


def assert_generate_wrote_file(result: object, output_file: Path) -> None:
    """Assert generate(..., output=path) wrote a file and returned None."""
    __tracebackhide__ = True
    assert result is None
    assert output_file.exists()


def assert_generated_file_matches_output(result: object, output_file: Path) -> None:
    """Assert generate(output=None) text matches the same generation written to a file."""
    __tracebackhide__ = True
    assert isinstance(result, str)
    actual = _normalize_generated_text(output_file.read_text(encoding="utf-8"))
    expected = _normalize_generated_text(result)
    if expected != actual:  # pragma: no cover
        diff = _format_diff(expected, actual, output_file)
        msg = f"Generated output differs from {output_file}\n{diff}"
        raise AssertionError(msg)


@pytest.fixture(autouse=True)
def _isolate_builtin_formatter_config(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Keep marked tests from inheriting the repository Ruff formatter config."""
    if request.node.get_closest_marker("isolate_builtin_formatter_config") is None:
        return
    if os.environ.get(TEST_DEFAULT_FORMATTER_ENV) != BUILTIN_FORMATTER_VALUE:
        return

    settings_path = tmp_path_factory.mktemp("builtin_formatter_config")
    (settings_path / "pyproject.toml").write_text(
        "[tool.datamodel-codegen]\nbuiltin-format-line-length = 88\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(settings_path)


@pytest.fixture(scope="session", autouse=True)
def _inline_snapshot_file_formats() -> None:
    register_format_alias(".py", ".txt")
    register_format_alias(".pyi", ".txt")
    register_format_alias(".snapshot", ".txt")


@pytest.fixture(scope="session")
def min_version() -> str:
    """Return minimum Python version as string."""
    return f"3.{MIN_VERSION}"


@pytest.fixture(scope="session", autouse=True)
def _preload_heavy_modules() -> None:
    """Pre-import heavy modules once per session to warm up the import cache.

    This reduces per-test overhead when running with pytest-xdist,
    as each worker only pays the import cost once at session start.
    """
    import black  # noqa: F401
    import inflect  # noqa: F401
    import isort  # noqa: F401
    import msgspec  # noqa: F401
    import pydantic_core  # noqa: F401

    import datamodel_code_generator  # noqa: F401


def validate_generated_code(
    code: str,
    file_path: str,
    *,
    do_exec: bool = False,
) -> None:
    """Validate generated code by compiling and optionally executing it.

    Args:
        code: The generated Python code to validate.
        file_path: Path to the file (for error reporting).
        do_exec: Whether to execute the code after compiling (default: False).
    """
    try:
        start = time.perf_counter()
        compiled = compile(code, file_path, "exec")
        _validation_stats.record_compile(time.perf_counter() - start)

        if do_exec:
            start = time.perf_counter()
            exec(compiled, {})
            _validation_stats.record_exec(time.perf_counter() - start)
    except SyntaxError as e:  # pragma: no cover
        _validation_stats.record_error(file_path, f"SyntaxError: {e}")
        raise
    except Exception as e:  # pragma: no cover
        _validation_stats.record_error(file_path, f"{type(e).__name__}: {e}")
        raise
