"""Test configuration and shared fixtures."""

from __future__ import annotations

import difflib
import hashlib
import importlib
import inspect
import json
import os
import re
import shlex
import socket
import sys
import time
from collections import Counter
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cache, cached_property
from itertools import islice, starmap
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import TYPE_CHECKING, Any, NamedTuple, NoReturn, Protocol, TypedDict, cast
from urllib.parse import urlparse

import pytest
import time_machine
from inline_snapshot._global_state import state as inline_snapshot_state
from typing_extensions import Required

from datamodel_code_generator import MIN_VERSION
from datamodel_code_generator.http import _get_httpx
from tests.units import (
    AT_HOME,
    BROKEN,
    CONFLICT,
    ORDER,
    REFS_SUFFIX,
    Desired,
    Event,
    Observations,
    Plan,
    Problem,
    RefLine,
    RefsIndex,
    UnsafeWriteError,
    apply_payload,
    derived_name,
    encode_payload,
    facts_fit,
    format_refs,
    item_problem,
    label_path,
    label_problem,
    merge_environments,
    nodeid_key,
    payload_predicate,
    payload_problem,
    predicate_matches,
    read_unit,
    resolve,
    split_label,
    tree_label,
    tree_member,
    uncollected_items,
    unit_digest,
    write_files,
)

if TYPE_CHECKING:
    import warnings
    from collections.abc import Iterable

CLI_DOC_COLLECTION_OUTPUT = Path(__file__).parent / "cli_doc" / ".cli_doc_collection.json"
CI_SHARD_WEIGHTS_PATH = Path(__file__).parents[1] / "scripts" / "ci_shard_weights.json"
CLI_DOC_SCHEMA_VERSION = 1
TEST_DEFAULT_FORMATTER_ENV = "DATAMODEL_CODE_GENERATOR_TEST_DEFAULT_FORMATTER"
BUILTIN_FORMATTER_VALUE = "builtin"
TARGET_PYTHON = (3, 11)
TARGET_TEST_ROOTS = (Path(__file__).parent / "api_generation", Path(__file__).parent / "model_codecs")
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
    parser.addoption(
        "--test-shard",
        metavar="INDEX/TOTAL",
        help="Run one of TOTAL shards of the selected tests, split by their measured CI durations",
    )
    parser.addoption(
        "--expected-observe",
        metavar="DIR",
        help="Record every expected-output comparison into DIR without failing on unrecorded or changed output "
        "(used by scripts/fix_expected.py --all-environments)",
    )
    parser.addoption(
        "--expected-apply",
        metavar="FILE",
        help="Apply expected output observed elsewhere (scripts/fix_expected.py) instead of running the tests",
    )
    parser.addini(
        "expected_tests_root",
        "Directory holding the test modules and data/expected, relative to the rootdir (default: tests/).",
        default="",
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
    ledger = config.stash[_LEDGER] = _Ledger(config)
    config.pluginmanager.register(_LedgerPlugin(ledger), "expected-ledger")
    config.pluginmanager.register(SlowTestOrdering(), "slow-test-ordering")


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


def select_test_shard(
    config: pytest.Config, items: list[pytest.Item], durations: dict[str, int], shard: str
) -> list[pytest.Item]:
    """Keep one ``INDEX/TOTAL`` shard of the items, split heaviest-first like the CI file shards.

    Tests without a measured duration weigh the median of the measured ones, and the rest are deselected.
    """
    from scripts.select_ci_test_shard import median_weight, select_shard

    index, total = map(int, shard.split("/"))
    measured = {item.nodeid: durations[item.nodeid] for item in items if item.nodeid in durations}
    fallback = median_weight(measured)
    selected = set(select_shard({item.nodeid: measured.get(item.nodeid, fallback) for item in items}, index, total))
    config.hook.pytest_deselected(items=[item for item in items if item.nodeid not in selected])
    return [item for item in items if item.nodeid in selected]


class SlowTestOrdering:
    """Select the requested test shard, then reorder it so every xdist worker chunk starts with measured slow tests."""

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, config: pytest.Config, items: list[pytest.Item]) -> None:
        """Run after marker and keyword deselection so shards and chunk boundaries match what xdist distributes."""
        durations = slow_test_durations()
        if (shard := config.getoption("--test-shard")) is not None:
            items[:] = select_test_shard(config, items, durations, shard)
        workers = getattr(config, "workerinput", {}).get("workercount", 1)
        items[:] = order_slow_tests_first(items, durations, workers)


def pytest_collection_modifyitems(
    session: pytest.Session,  # noqa: ARG001
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Skip the target tests on Python 3.10 and collect CLI doc metadata from tests with cli_doc marker.

    The server and client targets need Python 3.11 or later, so their tests and those of the wire codecs they share
    are skipped on older Pythons, including when a CI shard names their files. CLI doc metadata is always collected
    for test_cli_doc_coverage.py; its markers are validated, and their expected paths resolved through the index, only
    when --collect-cli-docs is used.
    """
    if sys.version_info < TARGET_PYTHON:
        skip = pytest.mark.skip(reason="The server and client targets need Python 3.11 or later")
        for item in items:
            if any(item.path.is_relative_to(root) for root in TARGET_TEST_ROOTS):
                item.add_marker(skip)
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
            "marker_kwargs": _stored_cli_doc_paths(config, item, marker.kwargs) if collect_cli_docs else marker.kwargs,
            "option_description": option_description,
        })

    if validation_errors:  # pragma: no cover
        error_msg = "CLI doc marker validation errors:\n"
        for node_id, errors in validation_errors:
            error_msg += f"\n  {node_id}:\n"
            error_msg += "\n".join(f"    - {e}" for e in errors)
        pytest.fail(error_msg, pytrace=False)


def pytest_runtestloop(session: pytest.Session) -> bool | None:
    """Skip test execution when --collect-cli-docs or --expected-apply is used."""
    if session.config.getoption("--collect-cli-docs") or session.config.getoption("--expected-apply"):
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


def _format_diff(expected: str, actual: str, expected_path: Path | str) -> str:  # pragma: no cover
    """Format a unified diff between expected and actual content, in colour when pytest's ``--color`` allows it.

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
    color = _color_output()
    console = Console(file=output, force_terminal=color, no_color=not color, width=200, soft_wrap=False)

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


EXPECTED_FIX_PREFIX = "dcg-expected-fix: "
"""Prefix of the line a CI failure prints so ``scripts/fix_expected.py --from-ci`` can apply CI's own output."""
EXPECTED_PAYLOAD_LIMIT = 32 * 1024
SIBLING_RERUN_SECONDS = 5.0
_REFS_ITEM = "expected_refs"
_CURRENT_ITEMS: list[pytest.Item] = []
_LEDGER = pytest.StashKey["_Ledger"]()
_UPDATE_CATEGORIES = frozenset({"create", "fix", "fix-assert", "trim", "update"})
_PLATFORMS = ("linux", "darwin", "win32")


def expected_environment_facts() -> dict[str, str]:
    """Return the facts of this environment that expected-output overrides (``@black=22``) can select."""
    from importlib.metadata import distributions

    builtin = os.environ.get(TEST_DEFAULT_FORMATTER_ENV) == BUILTIN_FORMATTER_VALUE
    return {
        "python": f"{sys.version_info[0]}.{sys.version_info[1]}",
        "formatter": BUILTIN_FORMATTER_VALUE if builtin else "external",
        **{tool: next((found.version for found in distributions(name=tool)), "") for tool in ("black", "isort")},
        "platform": next((name for name in _PLATFORMS if sys.platform.startswith(name)), sys.platform),
    }


class CiEnvironment(NamedTuple):
    """How a run relates to CI: the variable that marks it as CI, and the GitHub Actions run it belongs to."""

    variable: str | None
    github_actions: bool
    run_id: str | None


def _ci_environment() -> CiEnvironment:
    """Return how this run relates to CI; every CI decision about expected output goes through this function.

    CI runs never create or fix expected output, even with an explicit ``--inline-snapshot=create`` or ``fix``, and
    their failures print the payload that applies CI's output locally. tox passes the variables on (``pass_env``).
    """
    from inline_snapshot._snapshot_session import is_ci_run

    github = bool(os.environ.get("GITHUB_ACTIONS"))
    return CiEnvironment(is_ci_run() or None, github, os.environ.get("GITHUB_RUN_ID") if github else None)


def _item_key(item: pytest.Item) -> tuple[str, str]:
    """Return the function (with its class) and the parameter id of an item."""
    module = item.getparent(pytest.Module)
    key = item.nodeid[len(module.nodeid) + 2 :] if module is not None else item.name
    function, _, param = key.partition("[")
    return function, param[:-1]


def _applicable(lines: Sequence[RefLine], facts: Mapping[str, str]) -> RefLine | None:
    """Return the line that applies in this environment: the most specific matching override, else the base line."""
    matching = [line for line in lines if predicate_matches(line.predicate, facts)]
    return max(matching, key=lambda line: len(line.predicate), default=None)


def _quote(argument: str) -> str:
    return shlex.quote(argument)


def _describe_facts(facts: Mapping[str, str]) -> str:
    formatter = facts["formatter"]
    tools = (
        "builtin formatter"
        if formatter == BUILTIN_FORMATTER_VALUE
        else f"black {facts['black']}, isort {facts['isort']}"
    )
    return f"python {facts['python']}, {tools}"


def _git_tracked(path: Path, root: Path) -> bool:
    """Return whether git tracks a path; outside a repository nothing is tracked."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", str(path)], cwd=root, capture_output=True, check=False
        )
    except OSError:  # pragma: no cover
        return False
    return result.returncode == 0


def _color_output() -> bool:
    """Return whether failure diffs may use colour: pytest's ``--color`` decides for the running test."""
    config = _CURRENT_ITEMS[-1].config if _CURRENT_ITEMS else None
    return (
        config is not None
        and config.pluginmanager.has_plugin("terminalreporter")
        and config.get_terminal_writer().hasmarkup
    )


@dataclass
class _Observation:
    sidecar: Path
    function: str
    param: str
    nodeid: str
    label: str
    value: object
    line: RefLine | None
    status: str

    @cached_property
    def digest(self) -> str:
        """The digest of the observed value, computed only for changes and observations."""
        return unit_digest(self.label, self.value)


@dataclass
class _Sidecar:
    """A module's sidecar as a session uses it: lines by item, problems, and lines a merge left for one label."""

    path: Path
    items: dict[tuple[str, str], list[RefLine]]
    problems: list[Problem]
    flagged: dict[RefLine, str]

    @property
    def broken(self) -> list[Problem]:
        """The lines that do not parse."""
        return [problem for problem in self.problems if problem.kind == BROKEN]


class _Ledger:
    """Per-session state of the expected-output comparisons: the index, what the run observed, and what to write."""

    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        root = config.getini("expected_tests_root")
        self.tests_root = (config.rootpath / root).resolve() if root else Path(__file__).parent
        self.expected_root = self.tests_root / "data" / "expected"
        self.expected_prefix = f"{self.expected_root}{os.sep}"
        self.index = RefsIndex(self.tests_root)
        self.facts = expected_environment_facts()
        self.ci = _ci_environment()
        self.observe_dir: str | None = config.getoption("--expected-observe")
        self.apply_file: str | None = config.getoption("--expected-apply")
        self.apply_data: dict[str, Any] = (
            json.loads(Path(self.apply_file).read_text(encoding="utf-8")) if self.apply_file else {}
        )
        self.observations = [
            _load_observations(Path(directory)) for directory in self.apply_data.get("observations", [])
        ]
        self.can_create = False
        self.can_fix = False
        self.disabled_reason: str | None = None
        self.states: dict[Path, _Sidecar] = {}
        self.changes: list[_Observation] = []
        self.compared: dict[str, dict[str, object]] = {}
        self.unnamed: dict[str, int] = {}
        self.default_outputs: dict[tuple[str, str], int] = {}
        self.observed_items: dict[str, list[list[object]]] = {}
        self.outcomes: dict[str, str] = {}
        self.drops: dict[tuple[Path, str, str], set[str]] = {}
        self.repairs: dict[tuple[Path, str, str], set[RefLine]] = {}
        self.stale: dict[Path, set[tuple[str, str]]] = {}
        self.orphans: set[Path] = set()
        self.module_tests: dict[Path, set[str]] = {}
        self.keys: dict[str, tuple[str, str]] = {}
        self.collected: list[str] = []
        self.selected: set[str] | None = None
        self.named: set[str] = set()
        self.candidates: list[pytest.Item] = []
        self.ran: set[str] = set()
        self.rerun: list[str] = []
        self.report: list[str] = []
        self.refused = False
        self.check_apply()

    @property
    def observing(self) -> bool:
        return self.observe_dir is not None

    def relative(self, path: Path) -> str:
        return Path(os.path.relpath(path, self.config.rootpath)).as_posix()

    @property
    def stage(self) -> str:
        """The command staging all a writing run may change: expected files and the sidecars of every module."""
        return f"git add -A {self.relative(self.tests_root)}"

    def check_apply(self) -> None:
        """Refuse an apply session whose payloads or observations name anything but this suite's tests and labels.

        With ``skip_rejected`` (``--from-ci``), refused payloads are reported and the others applied.
        """
        if self.apply_file is None:
            return
        if getattr(self.config.option, "numprocesses", None) and not hasattr(self.config, "workerinput"):
            msg = "--expected-apply writes expected files and runs in one process; run it without -n."
            raise pytest.UsageError(msg)
        payloads = self.apply_data.get("payloads", [])
        refused = {
            number: problem
            for number, payload in enumerate(payloads, start=1)
            if (problem := payload_problem(payload, self.config.rootpath, self.index) or self.own_payload(payload))
        }
        if refused and self.apply_data.get("skip_rejected"):
            self.apply_data["payloads"] = [
                payload for number, payload in enumerate(payloads, start=1) if number not in refused
            ]
            self.report += [f"expected: skipped a payload: {problem}" for problem in refused.values()]
            refused = {}
        problems = [f"payload {number}: {problem}" for number, problem in refused.items()]
        problems += [
            f"{directory}: its environment is malformed"
            for directory, observations in zip(self.apply_data.get("observations", []), self.observations, strict=True)
            if observations["items"] and not facts_fit(observations["facts"])
        ]
        listings: dict[Path, set[str]] = {}
        problems += [
            problem
            for nodeid in dict.fromkeys(
                str(entry["item"]) for observations in self.observations for entry in observations["items"]
            )
            if (problem := item_problem(nodeid, self.config.rootpath, self.tests_root, listings))
        ]
        problems += [
            f"{entry['item']}: {problem}"
            for observations in self.observations
            for entry in observations["items"]
            for label, _, storage, _ in entry["compared"]
            for name in dict.fromkeys((label, storage))
            if (problem := label_problem(name))
        ]
        if problems:
            msg = "--expected-apply rejected its input; nothing was written:\n" + "\n".join(problems)
            raise pytest.UsageError(msg)

    def own_payload(self, payload: Mapping[str, Any]) -> str | None:
        """Refuse a payload no override predicate tells apart from this environment: only a local run changes it."""
        if payload_predicate(payload["facts"], self.facts) is not None:
            return None
        target = payload["item"].partition("::")[0] if payload.get("stale") else payload["item"]
        return (
            "it comes from this environment, so there is no other environment to add an override for; "
            f"this environment is yours, run: pytest {_quote(target)} --inline-snapshot=fix && {self.stage}"
        )

    def label(self, path: Path, pattern: str | None) -> str | None:
        """Return the label of an expected path, or None for paths outside the tests root (compared directly)."""
        absolute = os.path.abspath(path)  # noqa: PTH100
        if absolute.startswith(self.expected_prefix):
            name = absolute[len(self.expected_prefix) :].replace(os.sep, "/")
        elif Path(absolute).is_relative_to(self.tests_root):
            name = Path(os.path.relpath(absolute, self.expected_root)).as_posix()
        else:
            return None
        return name if pattern is None else tree_label(name, pattern)

    def key(self, item: pytest.Item) -> tuple[str, str]:
        if (found := self.keys.get(item.nodeid)) is None:
            found = self.keys[item.nodeid] = _item_key(item)
        return found

    def sidecar(self, path: Path) -> _Sidecar:
        """Return a module's sidecar; local runs first rewrite one that parses into the form pytest writes.

        A rewrite that ``write_files`` refuses is reported and fails the session.
        """
        if (state := self.states.get(path)) is not None:
            return state
        lines, problems = self.index.load(path)
        if self.ci.variable is None and not any(problem.kind == BROKEN for problem in problems):
            if any(problem.kind == ORDER for problem in problems):
                try:
                    write_files(self.tests_root, {path: format_refs(lines)})
                except UnsafeWriteError as error:
                    self.refuse(error)
                else:
                    self.report.append(f"expected: rewrote {self.relative(path)} in the form pytest writes")
            problems = [problem for problem in problems if problem.kind != ORDER]
        items = self.index.item_lines(path)
        flagged = {
            line: CONFLICT
            for found in items.values()
            for line in found
            if sum(other.key == line.key for other in found) > 1
        }
        state = self.states[path] = _Sidecar(path, items, problems, flagged)
        return state

    def start(self) -> None:
        state = inline_snapshot_state()
        if self.ci.variable is not None:
            from inline_snapshot._flags import Flags

            state.update_flags = Flags()
            state.flags -= _UPDATE_CATEGORIES
        explicit = self.apply_file is not None
        self.can_fix = explicit or bool(state.active and state.update_flags.fix)
        self.can_create = self.can_fix or bool(state.active and state.update_flags.create)
        self.disabled_reason = None if state.active else state.disable_reason
        if self.can_fix and not explicit:
            self.named = self.named_nodes()
            self.widen()

    def named_nodes(self) -> set[str]:
        """Return the nodeids the command line names (``module::test``), as the root directory sees them."""
        invocation = self.config.invocation_params.dir
        return {
            f"{self.relative(Path(os.path.abspath(invocation / path)))}::{rest}"  # noqa: PTH100
            for path, separator, rest in (argument.partition("::") for argument in self.config.args)
            if separator
        }

    def is_named(self, nodeid: str) -> bool:
        """Return whether the command line names an item, or the test or class it belongs to."""
        return any(nodeid == name or nodeid.startswith((f"{name}[", f"{name}::")) for name in self.named)

    def widen(self) -> None:
        """Collect every parameter of a test selected by one parameter, so its siblings can be re-run after a fix."""
        invocation = self.config.invocation_params.dir
        selected: set[str] = set()
        widened: list[str] = []
        for argument in self.config.args:
            path, separator, rest = argument.partition("::")
            if not separator or "[" not in rest:
                widened.append(argument)
                continue
            module = Path(os.path.abspath(invocation / path))  # noqa: PTH100
            selected.add(f"{self.relative(module)}::{rest}")
            if (function := f"{path}::{rest.partition('[')[0]}") not in widened:
                widened.append(function)
        if selected:
            self.selected = selected
            self.config.args[:] = widened

    def collect(self, items: list[pytest.Item]) -> None:
        """Keep widened siblings aside, find stale lines of collected modules, and record the collection.

        A line is stale when its test function no longer exists, or, in a module collected completely, when its
        unconditional item was not collected.
        """
        if self.selected is not None:
            keep = [item for item in items if item.nodeid in self.selected or not self._widened(item)]
            self.candidates = [item for item in items if item not in keep]
            if self.candidates:
                self.config.hook.pytest_deselected(items=self.candidates)
            items[:] = keep
        full = self._fully_collected([*items, *self.candidates])
        if self.observing and os.environ.get("PYTEST_XDIST_WORKER", "gw0") == "gw0":
            self.collected = [item.nodeid for item in items if item.path in full]
        by_module: dict[Path, tuple[set[tuple[str, str]], object]] = {}
        for item in (*items, *self.candidates):
            keys, _ = by_module.setdefault(item.path.with_suffix(REFS_SUFFIX), (set(), getattr(item, "module", None)))
            keys.add(self.key(item))
        for path, (keys, module) in by_module.items():
            lines = self.index.load(path)[0]
            names = self.module_tests.setdefault(path.with_suffix(".py"), _test_names(module) if module else set())
            stale = {(line.function, line.param) for line in lines if module and line.function not in names}
            if path.with_suffix(".py") in full:
                stale |= {(line.function, line.param) for line in lines if not line.conditional} - keys
            if stale:
                self.stale[path] = stale

    def _widened(self, item: pytest.Item) -> bool:
        assert self.selected is not None
        return any(item.nodeid.partition("[")[0] == nodeid.partition("[")[0] for nodeid in self.selected)

    def _fully_collected(self, items: Sequence[pytest.Item]) -> set[Path]:
        """Return the modules a run collected completely, so missing items mean renamed or deleted tests."""
        if self.config.getoption("lf", False) or self.selected is not None:
            return set()
        invocation = self.config.invocation_params.dir
        roots = [
            Path(os.path.abspath(invocation / argument))  # noqa: PTH100
            for argument in self.config.args
            if "::" not in argument
        ]
        return {
            module
            for module in {item.path for item in items}
            if any(module == root or module.is_relative_to(root) for root in roots)
        }

    def orphan(self, path: Path) -> str | None:
        """Note a sidecar whose module no longer exists; return the failure when this run cannot remove it."""
        self.orphans.add(path)
        if self.can_create:
            return None
        refs = self.relative(path)
        return (
            f"{refs} has lines for {self.relative(path.with_suffix('.py'))}, which no longer exists.\n"
            f"Fix: pytest {refs} --collect-only -q && {self.stage}\n"
            "If its tests moved to another module, run that module too."
        )

    def refs_items(self, items: list[pytest.Item]) -> None:
        """Append one failing item per module whose sidecar pytest would not have written, or names missing tests."""
        if self.observing or self.apply_file is not None:
            return
        modules: dict[Path, pytest.Module] = {}
        for item in items:
            if (module := item.getparent(pytest.Module)) is not None:
                modules.setdefault(item.path.with_suffix(REFS_SUFFIX), module)
        for path, module in modules.items():
            if messages := self.sidecar_messages(self.sidecar(path)):
                items.append(_RefsItem.from_parent(module, name=_REFS_ITEM, message="\n\n".join(messages)))

    def sidecar_messages(self, state: _Sidecar) -> list[str]:
        """Describe what is wrong with a sidecar, each with the command that repairs it.

        Lines that do not parse always fail. Everything else a local run that creates expected output repairs by
        itself: it sorts the file, re-records lines pytest would not have written, and drops stale lines.
        """
        refs, module = self.relative(state.path), self.relative(state.path.with_suffix(".py"))
        if broken := state.broken:
            return [self.hand_edit_message(state.path, broken[0])]
        if self.can_create:
            return []
        messages = [
            f"{refs} is not in the form pytest writes (line {problem.number}: {problem.reason}); "
            "a merge may have joined both sides.\n"
            f"Fix: pytest {module} --collect-only -q && {self.stage}"
            for problem in state.problems
            if problem.kind == ORDER
        ]
        if flagged := sorted({line.item for line in state.flagged}):
            reasons = "\n".join(f"  {item} lists one label twice (a merge kept both sides)" for item in flagged)
            messages.append(
                f"{refs} has lines pytest would not have written:\n{reasons}\n"
                f"Fix: pytest {' '.join(_quote(f'{module}::{item}') for item in flagged)} && {self.stage}"
            )
        messages += [self.missing_storage_message(state, line) for line in self.missing_storages(state)][:1]
        if state.path in self.stale:
            messages.append(self.stale_message(state.path))
        return messages

    def missing_storages(self, state: _Sidecar) -> list[RefLine]:
        """Return a line for each stored file the sidecar names that does not exist, listing each directory once."""
        files = {
            line.stored: line for found in state.items.values() for line in found if split_label(line.stored)[1] is None
        }
        listed: dict[Path, set[str]] = {}
        missing = []
        for storage, line in files.items():
            path = label_path(storage, self.expected_root)
            if path.parent not in listed:
                listed[path.parent] = {entry.name for entry in path.parent.iterdir()} if path.parent.is_dir() else set()
            if path.name not in listed[path.parent]:
                missing.append(line)
        return missing

    def restore_lines(self, path: Path, nodeid: str) -> list[str]:
        """Return how to bring back a missing stored file: from git when it tracks it, else from this machine."""
        relative = self.relative(path)
        if _git_tracked(path, self.config.rootpath):
            return [
                f"Fix: git checkout HEAD -- {relative}",
                f"Or, to record the current output again: pytest {nodeid} && {self.stage}",
            ]
        return [
            f"Fix, if pytest created it on your machine: {self.stage}",
            f"Or, to record the current output again: pytest {nodeid} && {self.stage}",
        ]

    def missing_storage_message(self, state: _Sidecar, line: RefLine) -> str:
        path = label_path(line.stored, self.expected_root)
        refs, module = self.relative(state.path), self.relative(state.path.with_suffix(".py"))
        return "\n".join([
            f"{refs} stores {line.item} at {line.stored}, but {self.relative(path)} does not exist.",
            *self.restore_lines(path, _quote(f"{module}::{line.item}")),
        ])

    def hand_edit_message(self, path: Path, problem: Problem) -> str:
        refs = self.relative(path)
        return (
            f"{refs} was edited by hand (line {problem.number}: {problem.reason}); pytest writes this file.\n"
            f"Fix: python scripts/fix_expected.py --repair {refs} && {self.stage}"
        )

    def stale_message(self, path: Path) -> str:
        refs, module = self.relative(path), self.relative(path.with_suffix(".py"))
        nodeids = [
            f"{module}::{function}[{param}]" if param else f"{module}::{function}"
            for function, param in sorted(self.stale[path])
        ]
        names = ", ".join(nodeids)
        command = f"pytest {module} --collect-only -q && {self.stage}"
        if self.ci.variable is None:
            verb = "which no longer exists" if len(nodeids) == 1 else "which no longer exist"
            return f"{refs} has lines for {names}, {verb}.\nFix: {command}"
        encoded = [self.encode({"item": nodeid, "stale": True}) for nodeid in nodeids]
        fix = (
            [f"Fix: {self.apply_command(encoded)}", "It keeps them for the environments that have them, or drops them."]
            if None not in encoded and sum(map(len, encoded)) <= EXPECTED_PAYLOAD_LIMIT
            else [f"Fix, if they were renamed or deleted: {command}"]
        )
        return "\n".join([
            f"{refs} has lines for {names}, which this environment ({_describe_facts(self.facts)}) does not collect.",
            *fix,
            *(f"{EXPECTED_FIX_PREFIX}{payload}" for payload in encoded if payload is not None),
        ])

    def derived_path(self, item: pytest.Item, *, role: str = "", suffix: str = "") -> Path:
        """Name the output of a test that names no expected path: ``<module dir>/<module>/<function>[<id>]<role>``.

        The ``<module>_`` prefix of the function is dropped unless that collides with another test of its class or
        module, so the name never depends on which tests ran first.
        """
        module_path = item.path
        stem = module_path.stem.removeprefix("test_")
        function, param = self.key(item)
        owner, _, name = function.rpartition("::")
        short = name.removeprefix("test_")
        if short.startswith(f"{stem}_"):
            stripped = short[len(stem) + 1 :]
            siblings = {
                test.rpartition("::")[2].removeprefix("test_")
                for test in self.module_tests.get(module_path, set())
                if test.rpartition("::")[0] == owner
            }
            short = short if stripped in siblings else stripped
        base = f"{owner.replace('::', '.')}.{short}" if owner else short
        identifier = param
        if len(f"{base}[{identifier}]{role}{suffix}") > 120:
            identifier = f"{param[:80]}~{hashlib.sha1(param.encode()).hexdigest()[:8]}"
        file_name = derived_name(f"{base}[{identifier}]" if param else base) + role + suffix
        directory = self.expected_root / module_path.parent.relative_to(self.tests_root) / stem
        return directory / file_name

    def unnamed_output(self, item: pytest.Item) -> None:
        count = self.unnamed[item.nodeid] = self.unnamed.get(item.nodeid, 0) + 1
        if count > 1:
            pytest.fail(
                f"{item.nodeid} compares several outputs; name each one after the first: "
                'assert_output(text, name="warnings")',
                pytrace=False,
            )

    def choose(self, state: _Sidecar, key: tuple[str, str], lines: list[RefLine], value: object) -> RefLine | None:
        """Return the line a comparison uses; a creating run sets aside lines pytest would not have written.

        Of two lines a merge left for one label, the one whose stored content matches the output is kept.
        """
        applicable = [line for line in lines if predicate_matches(line.predicate, self.facts)]
        if self.observing or not self.can_create or not any(line in state.flagged for line in applicable):
            return _applicable(applicable, self.facts)
        matching = next(
            (
                line
                for line in applicable
                if state.flagged.get(line) == CONFLICT
                and read_unit(label_path(line.stored, self.expected_root), split_label(line.stored)[1]) == value
            ),
            None,
        )
        dropped = {line for line in applicable if line in state.flagged and line is not matching}
        self.repairs.setdefault((state.path, *key), set()).update(dropped)
        return _applicable([line for line in applicable if line not in dropped], self.facts)

    def compare(self, item: pytest.Item, label: str, value: object, hint: str) -> None:
        """Compare a value with the stored unit this item's line for the label resolves to, recording the result."""
        __tracebackhide__ = True
        state = self.sidecar(item.path.with_suffix(REFS_SUFFIX))
        function, param = key = self.key(item)
        if state.broken and not self.observing:
            pytest.fail(self.hand_edit_message(state.path, state.broken[0]), pytrace=False)
        seen = self.compared.setdefault(item.nodeid, {})
        if label in seen and (previous := seen[label]) != value:
            pytest.fail(
                f"{item.nodeid} compares two different outputs against {label} (diff below).\n"
                f"If they should be equal, fix the code; {hint}\n{_unit_diff(previous, value, label)}",
                pytrace=False,
            )
        seen[label] = value
        line = self.choose(state, key, [line for line in state.items.get(key, ()) if line.label == label], value)
        storage = line.stored if line is not None else label
        name, pattern = split_label(storage)
        stored = read_unit(label_path(name, self.expected_root), pattern)
        observation = _Observation(state.path, function, param, item.nodeid, label, value, line, "ok")
        status = self._status(observation, stored)
        observation.status = status
        if self.observing:
            self.observed_items.setdefault(item.nodeid, []).append([
                label,
                observation.digest,
                storage,
                None if status in {"ok", "home"} else value,
            ])
        if status != "ok":
            self.changes.append(observation)

    def _status(self, observation: _Observation, stored: object | None) -> str:
        """Decide what a comparison means: equal (``ok``/``home``), ``changed`` or ``new``, or fail with the fix."""
        __tracebackhide__ = True
        equal = stored == observation.value
        present = stored not in (None, {})
        if observation.line is not None:
            if equal:
                return "ok"
            if self.observing or (self.can_fix if present else self.can_create):
                return "changed"
            self.fail_mismatch(observation, stored)
        if observation.label.startswith("../"):
            pytest.fail(
                "Expected outputs live under tests/data/expected. "
                "Fix: remove the path argument (pytest names the file)",
                pytrace=False,
            )
        if self.observing:
            return "home" if equal else "new"
        if not self.can_create:
            self.fail_missing(observation, present)
        if equal:
            return "home"
        if present and not self.can_fix:
            self.fail_mismatch(observation, stored)
        return "new"

    def encode(self, payload: dict[str, object]) -> str | None:
        """Encode what this lane compared, or None when the payload would be too large to print."""
        return encode_payload({"facts": self.facts, **payload}, EXPECTED_PAYLOAD_LIMIT)

    def apply_command(self, encoded: Sequence[str | None]) -> str:
        """Return the command applying this lane's payloads locally."""
        run = f"  (or --from-ci {self.ci.run_id})" if self.ci.run_id else ""
        payloads = " ".join(f"'{payload}'" for payload in encoded)
        return f"python scripts/fix_expected.py --apply {payloads} && {self.stage}{run}"

    def lane_lines(self, nodeid: str, encoded: str | None) -> list[str]:
        """Return the payload line of a CI failure, or the lane to run locally when the output is too large."""
        if encoded is not None:
            return [f"{EXPECTED_FIX_PREFIX}{encoded}"]
        lane = os.environ.get("TOX_ENV_NAME", "<this lane>")
        return [
            (
                "The output is too large for a payload; run this lane locally: "
                f"tox run -e {lane} -- {_quote(nodeid)} --inline-snapshot=fix && {self.stage}"
            )
        ]

    def fail_missing(self, observation: _Observation, home_exists: bool) -> NoReturn:
        __tracebackhide__ = True
        nodeid, refs = observation.nodeid, self.relative(observation.sidecar)
        command = f"pytest {_quote(nodeid)}"
        record = f"{command} && {self.stage}"
        recorded = bool(self.sidecar(observation.sidecar).items.get((observation.function, observation.param)))
        ci = self.ci
        encoded = (
            self.encode({
                "item": nodeid,
                "label": observation.label,
                "digest": observation.digest,
                "value": observation.value,
            })
            if ci.variable is not None
            else None
        )
        if self.disabled_reason == "xdist" and ci.variable is None:
            lines = [f"Expected output for {nodeid} was not created because pytest ran with -n.", f"Fix: {record}"]
        elif ci.variable is not None and not ci.github_actions:
            lines = [
                f"No expected output recorded for {nodeid}; expected files are not written while {ci.variable} is set.",
                f"Fix: {ci.variable}= {record}",
            ]
        elif recorded and ci.variable is not None and encoded is not None:
            lines = [
                (
                    f"This environment ({_describe_facts(self.facts)}) compares {observation.label}, "
                    f"which {refs} does not record for {nodeid}."
                ),
                f"Fix: {self.apply_command([encoded])}",
                f"Or, if {nodeid} is new: {record}",
            ]
        elif home_exists:
            lines = [f"{refs} has no line for {nodeid}.", f"Fix: {record}"]
        else:
            lines = [f"No expected output recorded for {nodeid}.", f"Fix: {record}"]
        if ci.variable is not None:
            lines += self.lane_lines(nodeid, encoded)
        pytest.fail("\n".join(lines), pytrace=False)

    def sharers(self, storage: str) -> int:
        return sum(line.stored == storage for lines in self.index.all_lines().values() for line in lines)

    def fail_mismatch(self, observation: _Observation, stored: object | None) -> NoReturn:
        __tracebackhide__ = True
        storage = observation.line.stored if observation.line is not None else observation.label
        path = label_path(storage, self.expected_root)
        relative, nodeid = self.relative(path), _quote(observation.nodeid)
        if stored is None:
            pytest.fail(
                "\n".join([
                    f"Expected output for {observation.nodeid} is missing: {relative}.",
                    *self.restore_lines(path, nodeid),
                ]),
                pytrace=False,
            )
        count = self.sharers(storage)
        used = f" (used by {count} tests)" if count > 1 else ""
        lines = [f"Content mismatch: {observation.nodeid} expects {storage}{used}."]
        fix = f"pytest {nodeid} --inline-snapshot=fix && {self.stage}"
        if self.ci.variable is None:
            lines += [f"If the new output is intended: {fix}", f"Otherwise: git checkout HEAD -- {relative}"]
        else:
            encoded = self.encode({
                "item": observation.nodeid,
                "label": observation.label,
                "digest": observation.digest,
                "value": observation.value,
            })
            lines.append(
                f"This environment ({_describe_facts(self.facts)}) produces different output than the stored one."
            )
            if encoded is not None:
                kept = "their stored files" if observation.line is not None and observation.line.predicate else storage
                lines.append(
                    f"Fix, if pytest {nodeid} passes on your machine (adds an override for this environment only; "
                    f"other environments keep {kept}): {self.apply_command([encoded])}"
                )
            lines += [
                f"Or, if the new output is intended everywhere: {fix}",
                f"Or, to restore the stored file: git checkout HEAD -- {relative}",
                *self.lane_lines(observation.nodeid, encoded),
            ]
        pytest.fail("\n".join([*lines, _unit_diff(stored, observation.value, relative)]), pytrace=False)

    def check_exact(self, item: pytest.Item) -> None:
        """Fail an item that passed without comparing every line its sidecar records for this environment.

        An item that compared nothing fails even when its lines are conditional (``?``). A fix run drops the lines
        an item did not compare when the item compared something here, or when the command line names it: other
        environments may compare the lines of an item that compares nothing here, so a run over a module keeps them.
        """
        if self.observing or self.apply_file is not None:
            return
        state = self.sidecar(item.path.with_suffix(REFS_SUFFIX))
        function, param = key = self.key(item)
        compared = self.compared.get(item.nodeid, {})
        applicable = [line for line in state.items.get(key, ()) if predicate_matches(line.predicate, self.facts)]
        if self.can_create and (
            flagged := {line for line in applicable if line in state.flagged and line.label not in compared}
        ):
            self.repairs.setdefault((state.path, *key), set()).update(flagged)
            applicable = [line for line in applicable if line not in flagged]
        if not applicable:
            return
        missing = sorted({
            line.label for line in applicable if (not line.conditional or not compared) and line.label not in compared
        })
        if not missing:
            return
        if self.can_fix and (compared or self.is_named(item.nodeid)):
            self.drops.setdefault((state.path, function, param), set()).update(missing)
            return
        refs, nodeid = self.relative(state.path), _quote(item.nodeid)
        fix = f"pytest {nodeid} --inline-snapshot=fix && {self.stage}"
        if not compared and all(line.conditional for line in applicable):
            pytest.fail(
                f"{item.nodeid} passed without comparing any expected output, but {refs} records "
                f"{', '.join(missing)} for it in some environments.\n"
                f"Fix: make the test compare its output here too; if it should no longer compare anything: {fix}",
                pytrace=False,
            )
        if self.ci.variable is None:
            message = [
                f"{item.nodeid} did not compare {', '.join(missing)}, which {refs} records for it.",
                f"If it no longer should: {fix}",
            ]
        else:
            encoded = [self.encode({"item": item.nodeid, "label": label, "missing": True}) for label in missing]
            message = [
                (
                    f"This environment ({_describe_facts(self.facts)}) does not compare {', '.join(missing)}, "
                    f"which {refs} records for {item.nodeid}."
                ),
                f"Fix, if pytest {nodeid} compares them on your machine: {self.apply_command(encoded)}",
                f"Or, if it should no longer compare them: {fix}",
                *(f"{EXPECTED_FIX_PREFIX}{payload}" for payload in encoded),
            ]
        pytest.fail("\n".join(message), pytrace=False)

    def begin(self, item: pytest.Item) -> None:
        self.ran.add(item.nodeid)
        if self.observing:
            self.observed_items.setdefault(item.nodeid, [])
        self.end(item)
        self.unnamed.pop(item.nodeid, None)

    def end(self, item: pytest.Item) -> None:
        self.default_outputs = {key: count for key, count in self.default_outputs.items() if key[0] != item.nodeid}
        self.compared.pop(item.nodeid, None)

    def outcome(self, report: pytest.TestReport) -> None:
        if not self.observing or report.nodeid not in self.observed_items:
            return
        if report.failed:
            self.outcomes[report.nodeid] = "failed"
        elif report.when == "call" or (report.skipped and report.when == "setup"):
            self.outcomes.setdefault(report.nodeid, "passed" if report.passed else "skipped")

    def rerun_siblings(self, session: pytest.Session) -> None:
        """Re-run deselected parameters of a fixed test that share a changed unit, for at most a few seconds."""
        if not self.can_fix or not self.candidates or session.shouldfail or session.shouldstop:
            return
        changed = {observation.line.stored for observation in self.changes if observation.line is not None}
        functions = {(observation.sidecar, observation.function) for observation in self.changes}
        queue = [
            item
            for item in self.candidates
            if (item.path.with_suffix(REFS_SUFFIX), self.key(item)[0]) in functions
            and any(
                line.stored in changed
                for line in self.sidecar(item.path.with_suffix(REFS_SUFFIX)).items.get(self.key(item), ())
            )
        ]
        started = time.monotonic()
        for index, item in enumerate(queue):
            if time.monotonic() - started > SIBLING_RERUN_SECONDS:
                break
            item.ihook.pytest_runtest_protocol(item=item, nextitem=queue[index + 1] if index + 1 < len(queue) else None)
            self.rerun.append(item.nodeid)

    def updates(self) -> dict[Path, dict[tuple[str, str], list[Desired]]]:
        """Turn this run's observations, dropped and repaired lines, stale items and orphans into wanted lines."""
        updates: dict[Path, dict[tuple[str, str], list[Desired]]] = {}
        changed: dict[tuple[Path, str, str], list[_Observation]] = {}
        for observation in self.changes:
            changed.setdefault((observation.sidecar, observation.function, observation.param), []).append(observation)
        for key in sorted(changed.keys() | self.drops.keys() | self.repairs.keys()):
            path, function, param = key
            lines = self.sidecar(path).items.get((function, param), [])
            dropped, repaired = self.drops.get(key, set()), self.repairs.get(key, set())
            by_line = {id(observation.line): observation for observation in changed.get(key, []) if observation.line}
            desired = [
                Desired(line.label, line.conditional, line.predicate, observation.value, observation.digest, line)
                if (observation := by_line.get(id(line))) is not None
                else Desired(line.label, line.conditional, line.predicate, current=line)
                for line in lines
                if line.label not in dropped and line not in repaired
            ]
            desired += [
                Desired(
                    observation.label,
                    value=AT_HOME if observation.status == "home" else observation.value,
                    digest=observation.digest,
                )
                for observation in changed.get(key, [])
                if observation.line is None
            ]
            updates.setdefault(path, {})[function, param] = desired
        for path, keys in self.stale.items():
            for key in keys:
                updates.setdefault(path, {}).setdefault(key, [])
        for path in self.orphans:
            for key in self.index.item_lines(path):
                updates.setdefault(path, {})[key] = []
        return updates

    def refuse(self, error: UnsafeWriteError) -> None:
        """Report a refused write; the session fails when it finishes."""
        self.report.append(f"expected: {error}")
        self.refused = True

    def finish(self, session: pytest.Session) -> None:
        self.write_updates(session)
        if self.refused:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def write_updates(self, session: pytest.Session) -> None:
        """Write what the session observed, applied, or created."""
        if self.observing:
            self.export()
            return
        if self.apply_file is not None:
            updates = self.apply_updates(session)
        elif self.can_create:
            updates = self.updates()
        else:
            return
        if not updates:
            return
        plan = resolve(self.index.all_lines(), updates, self.expected_root)
        sidecars = {path: text for path, text in plan.sidecars.items() if text}
        try:
            write_files(self.tests_root, plan.writes | sidecars, [*plan.deletes, *plan.sidecars.keys() - sidecars])
        except UnsafeWriteError as error:
            self.refuse(error)
            return
        self.describe(plan, updates)

    def nodeid(self, sidecar: Path, item: str) -> str:
        """Return the nodeid of an item of a sidecar's module."""
        return f"{self.relative(sidecar.with_suffix('.py'))}::{item}"

    def describe(self, plan: Plan, updates: Mapping[Path, Mapping[tuple[str, str], list[Desired]]]) -> None:
        """Report what the run wrote, with the command that updates sharers it did not run."""
        adopted: dict[Path, list[str]] = {}
        for event in plan.events:
            if event.kind == "adopted":
                adopted.setdefault(event.items[0][0], []).append(f"{event.items[0][1]} compares {event.storage}")
                continue
            others = sorted(set(starmap(self.nodeid, event.others)))
            self.report.append(self.event_line(event, len(others)))
            if not_run := [nodeid for nodeid in others if nodeid not in self.ran]:
                self.report.append(f"If they should change too: {self.fix_command(not_run)}")
        for sidecar, found in adopted.items():
            refs = self.relative(sidecar)
            if len(found) > 2:
                self.report.append(f"expected: recorded {len(found)} tests in {refs} (their files already existed)")
            else:
                self.report.extend(f"expected: {text}; recorded in {refs}" for text in found)
        self.report.extend(
            f"expected: removed {self.relative(path)}; {self.relative(path.with_suffix('.py'))} no longer exists"
            for path in sorted(self.orphans)
        )
        self.report.extend(
            f"expected: removed the lines of {self.nodeid(sidecar, f'{function}[{param}]' if param else function)} "
            f"from {self.relative(sidecar)}"
            for sidecar, items in updates.items()
            if sidecar not in self.orphans
            for (function, param), desired in items.items()
            if not desired and self.index.item_lines(sidecar).get((function, param))
        )
        if self.rerun:
            self.report.append(f"expected: re-ran {len(self.rerun)} tests that share a changed file")
        if changed := sorted({self.relative(path) for path in (*plan.writes, *plan.deletes, *plan.sidecars)}):
            self.report.append(f"expected: changed {', '.join(changed)}; stage them all: {self.stage}")

    def event_line(self, event: Event, others: int) -> str:
        """Describe one thing a resolution did."""
        refs = ", ".join(sorted({self.relative(sidecar) for sidecar, _ in event.items}))
        items = ", ".join(item for _, item in event.items)
        text = f"deleted {event.storage} (no test uses it)"
        match event.kind:
            case "created":
                text = f"created {event.storage}; recorded in {refs}"
            case "deduplicated":
                text = f"{items} has the same output as {event.storage}; no new file; recorded in {refs}"
            case "fixed":
                text = f"fixed {event.storage} in place ({len(event.items)} tests)"
            case "merged":
                text = f"{items} now has the same output as {event.target}; it uses that file"
            case "moved":
                text = f"moved {event.storage} to {event.target}, the label of every test that uses it"
            case "split":
                unchanged = "" if event.rewritten else " (their files are unchanged)"
                text = (
                    f"{items} split from {others} tests that share {event.storage}{unchanged}; stored at {event.target}"
                )
        return f"expected: {text}"

    def fix_command(self, nodeids: list[str]) -> str:
        """Return one pytest command that fixes the given items, by function or module when there are many."""
        selection = sorted({nodeid.partition("[")[0] for nodeid in nodeids}) if len(nodeids) > 5 else nodeids
        if len(selection) > 5:
            selection = sorted({nodeid.partition("::")[0] for nodeid in nodeids})
        return f"pytest {' '.join(map(_quote, selection))} --inline-snapshot=fix && {self.stage}"

    def export(self) -> None:
        directory = Path(self.observe_dir or ".")
        directory.mkdir(parents=True, exist_ok=True)
        worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
        lines = [json.dumps({"facts": self.facts, "collected": self.collected})]
        lines += [
            json.dumps({"item": nodeid, "outcome": outcome, "compared": self.observed_items.get(nodeid, [])})
            for nodeid, outcome in self.outcomes.items()
        ]
        (directory / f"observed-{worker}-{os.getpid()}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def apply_updates(self, session: pytest.Session) -> dict[Path, dict[tuple[str, str], list[Desired]]]:
        """Merge observations from other environments or CI payloads into the lines of the items they name.

        With ``drop_uncollected``, the lines of recorded items that no observed environment collects, although one of
        them collected their whole module, go too; ``merge`` false applies only that.
        """
        rootpath = self.config.rootpath
        local = {nodeid_key(rootpath, item.nodeid) for item in session.items}
        updates: dict[Path, dict[tuple[str, str], list[Desired]]] = {}
        if self.observations and self.apply_data.get("merge", True):
            for nodeid, desired in merge_environments(self.observations, self.expected_root, self.facts).items():
                sidecar, function, param = nodeid_key(rootpath, nodeid)
                updates.setdefault(sidecar, {})[function, param] = desired
        if self.apply_data.get("drop_uncollected"):
            recorded = [
                self.nodeid(sidecar, f"{function}[{param}]" if param else function)
                for sidecar in self.index.sidecars()
                for function, param in self.index.item_lines(sidecar)
            ]
            for nodeid in uncollected_items(self.observations, recorded):
                sidecar, function, param = nodeid_key(rootpath, nodeid)
                updates.setdefault(sidecar, {})[function, param] = []
        for payload in self.apply_data.get("payloads", []):
            sidecar, function, param = nodeid_key(rootpath, payload["item"])
            current = updates.get(sidecar, {}).get((function, param))
            lines = self.index.item_lines(sidecar).get((function, param), [])
            base = (
                current
                if current is not None
                else [Desired(line.label, line.conditional, line.predicate) for line in lines]
            )
            updates.setdefault(sidecar, {})[function, param] = apply_payload(
                base, lines, payload, self.facts, exists=(sidecar, function, param) in local
            )
        return updates


def _test_names(namespace: object, prefix: str = "") -> set[str]:
    """Return the test functions of a module or class, with their classes, as they appear in nodeids.

    Classes include inherited test methods.
    """
    members = (
        vars(namespace).items()
        if isinstance(namespace, ModuleType)
        else ((name, getattr(namespace, name, None)) for name in dir(namespace))
    )
    names: set[str] = set()
    for name, value in members:
        if isinstance(value, type) and name.startswith("Test"):
            names |= _test_names(value, f"{prefix}{name}::")
        elif name.startswith("test") and callable(value):
            names.add(f"{prefix}{name}")
    return names


def _load_observations(directory: Path) -> Observations:
    """Load what one environment observed (``--expected-observe``): its facts, collection, and items."""
    facts: dict[str, str] = {}
    collected: set[str] = set()
    items: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("observed-*.jsonl")):
        header, *entries = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line)
        facts = header["facts"]
        collected.update(header["collected"])
        for entry in entries:
            known = items.setdefault(entry["item"], entry)
            known["compared"] = known["compared"] or entry["compared"]
            known["outcome"] = "failed" if "failed" in {known["outcome"], entry["outcome"]} else known["outcome"]
    return cast("Observations", {"facts": facts, "collected": sorted(collected), "items": list(items.values())})


def _unit_diff(stored: object | None, value: object, path: str) -> str:  # pragma: no cover
    """Show how a value differs from a stored unit: file diffs, or a tree's missing, extra, and changed files."""
    if not isinstance(value, dict) or not isinstance(stored, (dict, type(None))):
        old = stored if isinstance(stored, str) else json.dumps(stored, indent=2, ensure_ascii=False)
        new = value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False)
        return _format_diff(old, new, path)
    stored = stored or {}
    parts = [f"missing: {relative}" for relative in sorted(set(stored) - set(value))]
    parts += [f"new: {relative}" for relative in sorted(set(value) - set(stored))]
    parts += [
        f"{relative}:\n{_format_diff(stored[relative], value[relative], relative)}"
        for relative in sorted(set(stored) & set(value))
        if stored[relative] != value[relative]
    ]
    return "\n".join(parts)


class _RefsItem(pytest.Item):
    """A failing item reporting a sidecar pytest would not have written, or one whose tests no longer exist."""

    def __init__(self, *, message: str | None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.message = message

    def runtest(self) -> None:
        if self.message is not None:
            pytest.fail(self.message, pytrace=False)

    def reportinfo(self) -> tuple[Path, int, str]:
        return self.path, 0, _REFS_ITEM


class _OrphanRefs(pytest.File):
    """A sidecar whose test module no longer exists: a creating run removes it, other runs fail its item."""

    def collect(self) -> Iterable[pytest.Item]:
        yield _RefsItem.from_parent(self, name=_REFS_ITEM, message=self.config.stash[_LEDGER].orphan(self.path))


class _LedgerPlugin:
    """Hooks that track the running item, check every comparison it made, and write expected output at the end."""

    def __init__(self, ledger: _Ledger) -> None:
        self.ledger = ledger

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionstart(self, session: pytest.Session) -> None:  # noqa: ARG002
        self.ledger.start()

    def pytest_ignore_collect(self, collection_path: Path) -> bool | None:  # noqa: ARG002
        """Collect nothing when applying only observations, which name every item they need."""
        return True if self.ledger.apply_file is not None and not self.ledger.apply_data.get("payloads") else None

    def pytest_collect_file(self, file_path: Path, parent: pytest.Collector) -> pytest.Collector | None:
        """Collect a sidecar whose module is gone, outside observing and applying sessions."""
        ledger = self.ledger
        if (
            file_path.suffix != REFS_SUFFIX
            or file_path.with_suffix(".py").exists()
            or ledger.observing
            or ledger.apply_file is not None
            or not file_path.is_relative_to(ledger.tests_root)
        ):
            return None
        return _OrphanRefs.from_parent(parent, path=file_path)

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        self.ledger.collect(items)

    @pytest.hookimpl(trylast=True, specname="pytest_collection_modifyitems")
    def pytest_append_refs_items(self, items: list[pytest.Item]) -> None:
        self.ledger.refs_items(items)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_protocol(self, item: pytest.Item) -> Generator[None, object, object]:
        _CURRENT_ITEMS.append(item)
        self.ledger.begin(item)
        try:
            return (yield)
        finally:
            _CURRENT_ITEMS.remove(item)
            self.ledger.end(item)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_call(self, item: pytest.Item) -> Generator[None, object, object]:
        result = yield
        self.ledger.check_exact(item)
        return result

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.ledger.outcome(report)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtestloop(self, session: pytest.Session) -> Generator[None, object, object]:
        result = yield
        self.ledger.rerun_siblings(session)
        return result

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        self.ledger.finish(session)

    def pytest_terminal_summary(self, terminalreporter: Any) -> None:
        if self.ledger.report:
            terminalreporter.write_sep("-", "expected output")
            for line in self.ledger.report:
                terminalreporter.write_line(line)


def _stored_cli_doc_paths(config: pytest.Config, item: pytest.Item, kwargs: Mapping[str, Any]) -> Mapping[str, Any]:
    """Point the expected paths of a cli_doc marker at their storage when the index stores them elsewhere.

    A path is relative to ``expected/main`` or else to ``expected``, as the docs builder reads it. The item's own
    lines decide where its labels are stored; any other label, tree directory or file in a tree resolves through the
    whole index, so a path another test compares follows that test's storage.
    """
    if (ledger := config.stash.get(_LEDGER, None)) is None:  # pragma: no cover
        return kwargs

    def resolve(path: str) -> str:
        for name in (f"main/{path}".rstrip("/"), path.rstrip("/")):
            if (storage := _own_storage(ledger, item, name) or ledger.index.storage(name)) is not None:
                return path if storage == name else storage
        return path

    def resolve_value(key: str, value: Any) -> Any:
        if key in {"golden_output", "expected_stdout", "comparison_output"} and isinstance(value, str):
            value = resolve(value)
        elif key in {"version_outputs", "model_outputs"} and isinstance(value, dict):
            value = {name: resolve(path) for name, path in value.items()}
        elif key == "extra_outputs" and isinstance(value, list):
            value = [{**output, "path": resolve(output["path"])} for output in value]
        return value

    return {key: resolve_value(key, value) for key, value in kwargs.items()}


def _ledger() -> _Ledger | None:
    """Return the ledger of the running item's session, or None outside a test."""
    return _CURRENT_ITEMS[-1].config.stash.get(_LEDGER, None) if _CURRENT_ITEMS else None


def _own_storage(ledger: _Ledger, item: pytest.Item, name: str) -> str | None:
    """Return where the item's own line stores a label path, or a file one of its tree labels selects, or None.

    Of an item's lines for one label, the one that applies in this environment decides, as in its comparison.
    """
    lines: dict[str, list[RefLine]] = {}
    for line in ledger.index.item_lines(item.path.with_suffix(REFS_SUFFIX)).get(_item_key(item), []):
        lines.setdefault(line.label, []).append(line)
    relative = PurePosixPath(name)
    for label, candidates in sorted(lines.items()):
        path, pattern = split_label(label)
        if not relative.is_relative_to(path):
            continue
        inside = relative.relative_to(path)
        selected = inside == PurePosixPath() or (pattern is not None and inside.match(pattern))
        if selected and (line := _applicable(candidates, ledger.facts)) is not None:
            return (PurePosixPath(split_label(line.stored)[0]) / inside).as_posix()
    return None


@cache
def _tests_index() -> RefsIndex:
    """Return the index of this repository's tests, for expected files read outside a running test."""
    return RefsIndex(Path(__file__).parent)


def stored_expected(path: Path) -> Path:
    """Return the file that stores the expected output at a path below the expected root.

    Tests with equal output share one stored file, so a test that reads an expected file itself (as input, or to
    check another test's output) reads it through this. The running test's own ``.refs`` lines decide first, then
    those of any test; a path the index does not name, or outside the expected root, is returned unchanged.
    """
    ledger = _ledger()
    index = _tests_index() if ledger is None else ledger.index
    if not path.is_relative_to(index.expected_root):
        return path
    name = path.relative_to(index.expected_root).as_posix()
    own = None if ledger is None else _own_storage(ledger, _CURRENT_ITEMS[-1], name)
    storage = own or index.storage(name)
    return path if storage is None else index.expected_root / storage


_OWN_FILE_HINT = "otherwise compare the second against its own expected file"


def _compare_expected(
    expected_path: Path, value: object, *, pattern: str | None = None, hint: str = _OWN_FILE_HINT
) -> None:
    """Compare a file value, or a tree's files when ``pattern`` is given, with the expected output stored at a path.

    Paths under the tests root go through the ``.refs`` index of the running item; other paths, such as a second
    generated tree, are compared directly. ``hint`` says how to keep two different outputs apart.
    """
    __tracebackhide__ = True
    if pattern is not None and isinstance(value, dict):
        # The stored tree holds only the files the pattern selects; compare the same files of the output.
        value = {name: text for name, text in value.items() if PurePosixPath(name).match(pattern)}
    ledger = _ledger()
    label = ledger.label(expected_path, pattern) if ledger is not None else None
    if ledger is None or label is None:
        _compare_direct(expected_path, value, pattern)
        return
    ledger.compare(_CURRENT_ITEMS[-1], label, value, hint)


def _compare_derived(
    value: object, *, role: str = "", suffix: str = "", tree: bool = False, hint: str = _OWN_FILE_HINT
) -> None:
    """Compare output a test gives without an expected path against the one stored under its derived label."""
    __tracebackhide__ = True
    ledger = _ledger()
    if ledger is None:  # pragma: no cover
        pytest.fail("Expected output without a path can only be compared inside a test", pytrace=False)
    path = ledger.derived_path(_CURRENT_ITEMS[-1], role=role, suffix=suffix)
    _compare_expected(path, value, pattern="*" if tree else None, hint=hint)


def expected_root() -> Path:
    """Return the directory expected outputs live in for the running test."""
    return ledger.expected_root if (ledger := _ledger()) is not None else Path(__file__).parent / "data" / "expected"


def default_output_count(role: str = "") -> int:
    """Count the unnamed outputs of one role the running test compared so far, including this one."""
    if (ledger := _ledger()) is None:  # pragma: no cover
        return 1
    key = (_CURRENT_ITEMS[-1].nodeid, role)
    ledger.default_outputs[key] = count = ledger.default_outputs.get(key, 0) + 1
    return count


def _compare_direct(expected_path: Path, value: object, pattern: str | None) -> None:
    __tracebackhide__ = True
    stored = read_unit(expected_path, pattern)
    if stored is None:
        pytest.fail(f"Expected file not found: {expected_path}", pytrace=False)
    if stored != value:
        pytest.fail(
            f"Content mismatch for {expected_path}\n{_unit_diff(stored, value, str(expected_path))}", pytrace=False
        )


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

        content = output_file.read_text(encoding=encoding)
        if transform is not None:
            content = transform(content)
        _compare_expected(base_path / expected_name, _normalize_line_endings(content))

    return _assert_file_content


def assert_output(
    output: object,
    expected_path: Path | None = None,
    *,
    name: str | None = None,
) -> None:
    """Assert that output matches the expected output stored for it.

    Args:
        output: The output string to compare (e.g., captured.out, parser.parse()), or a JSON value for a .json path.
        expected_path: Path to the expected file. Without it, pytest names the file after the test item.
        name: Distinguishes several outputs of one test that pass no path, e.g. ``name="warnings"``.

    Usage:
        assert_output(captured.out, EXPECTED_PATH / "output.py")
        assert_output(parser.parse())
        assert_output(captured.err, name="warnings")
    """
    __tracebackhide__ = True
    is_json_value = expected_path is not None and expected_path.suffix == ".json" and not isinstance(output, str)
    if not isinstance(output, str) and not is_json_value:  # pragma: no cover
        pytest.fail(f"Expected generated output to be str, got {type(output).__name__}")
    value = output if is_json_value else _normalize_line_endings(cast("str", output))
    if expected_path is not None:
        _compare_expected(expected_path, value)
        return
    if name is None and (ledger := _ledger()) is not None:
        ledger.unnamed_output(_CURRENT_ITEMS[-1])
    _compare_derived(
        value,
        role=f".{name}" if name else "",
        suffix=".txt",
        hint='otherwise name the second: assert_output(text, name="second")',
    )


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


def _output_tree(output_dir: Path, pattern: str, encoding: str = "utf-8") -> dict[str, str]:
    """Read the files of an output directory that match a pattern, keyed by their POSIX path."""
    return {
        path.relative_to(output_dir).as_posix(): _normalize_line_endings(path.read_text(encoding=encoding))
        for path in sorted(output_dir.rglob(pattern))
        if tree_member(path, output_dir)
    }


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
    _compare_expected(expected_dir, _output_tree(output_dir, pattern, encoding), pattern=pattern)


def assert_exact_directory_content(
    output_dir: Path,
    expected_dir: Path,
    pattern: str = "*.py",
    encoding: str = "utf-8",
) -> None:
    """Assert all files in output_dir match expected_dir exactly, with no file missing or extra on either side."""
    __tracebackhide__ = True
    _compare_expected(expected_dir, _output_tree(output_dir, pattern, encoding), pattern=pattern)


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
    tree = {Path(key).as_posix(): _normalize_line_endings(_get_full_body(result)) for key, result in results.items()}
    _compare_expected(expected_dir, tree, pattern=pattern)


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
    assert_generated_modules_output(modules, expected_dir)


def assert_generated_modules_output(
    modules: Mapping[tuple[str, ...], Any],
    expected_dir: Path,
    transform: Callable[[str], str] | None = None,
    pattern: str = "*.py",
) -> None:
    """Assert generate(output=None) module output matches expected files exactly.

    ``pattern`` selects the stored files compared; ``"*"`` compares the whole stored tree.
    """
    __tracebackhide__ = True
    tree = {}
    for paths, result in modules.items():
        content = _get_full_body(result)
        tree[Path(*paths).as_posix()] = _normalize_line_endings(transform(content) if transform else content)
    _compare_expected(expected_dir, tree, pattern=pattern)


def write_generated_modules(output_dir: Path, modules: dict[tuple[str, ...], Any]) -> None:
    """Write parser-generated module output to a package directory."""
    for module_path, result in modules.items():
        file_path = output_dir.joinpath(*module_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(_get_full_body(result).encode("utf-8"))


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
