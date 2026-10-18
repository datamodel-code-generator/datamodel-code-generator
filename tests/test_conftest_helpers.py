"""Tests for shared assertion helpers in tests.conftest."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import textwrap
import zlib
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import call

import pytest

from datamodel_code_generator.__main__ import Exit
from datamodel_code_generator.format import Formatter
from tests import conftest as shared_conftest
from tests import units
from tests.conftest import (
    _infer_expected_file,
    assert_exact_directory_content,
    assert_inputs_not_mutated,
    assert_output,
    assert_parser_modules,
    assert_parser_results,
    slow_test_durations,
    worksteal_chunk_sizes,
)
from tests.main import _builtin_parity
from tests.main import conftest as main_conftest

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest_mock import MockerFixture

HEADER = units.REFS_HEADER
SLOWEST_NODEID = (
    "tests/test_format.py::test_apply_builtin_formatter_matches_black_isort_for_normalized_expected_files[0]"
)


@pytest.mark.parametrize(
    ("function_name", "expected_file"),
    [
        ("test_main_pet", "pet.py"),
        ("test_pet", "pet.py"),
        ("helper", "helper.py"),
    ],
)
def test_infer_expected_file(function_name: str, expected_file: str) -> None:
    """Expected filenames are inferred consistently from test function names."""
    assert _infer_expected_file(function_name) == expected_file


def test_assert_exact_directory_content_reports_diff(tmp_path: Path) -> None:
    """Test exact directory comparison reports the mismatched file path."""
    output_dir = tmp_path / "output"
    expected_dir = tmp_path / "expected"
    output_dir.mkdir()
    expected_dir.mkdir()

    (output_dir / "sample.py").write_text("value = 1\n", encoding="utf-8")
    (expected_dir / "sample.py").write_text("value = 2\n", encoding="utf-8")

    with pytest.raises(pytest.fail.Exception, match="Content mismatch") as exc_info:
        assert_exact_directory_content(output_dir, expected_dir)

    assert "sample.py" in str(exc_info.value)


def test_assert_parser_results_rejects_unexpected_result(tmp_path: Path) -> None:
    """Parser result assertions fail when generated output has no expected file."""
    expected_dir = tmp_path / "expected"
    expected_dir.mkdir()
    (expected_dir / "sample.py").write_text("value = 1\n", encoding="utf-8")

    with pytest.raises(pytest.fail.Exception, match=r"new: extra\.py"):
        assert_parser_results({"sample.py": "value = 1\n", "extra.py": "value = 2\n"}, expected_dir)


def test_assert_parser_results_rejects_missing_result(tmp_path: Path) -> None:
    """Parser result assertions fail when an expected file is not generated."""
    expected_dir = tmp_path / "expected"
    expected_dir.mkdir()
    (expected_dir / "sample.py").write_text("value = 1\n", encoding="utf-8")
    (expected_dir / "missing.py").write_text("value = 2\n", encoding="utf-8")

    with pytest.raises(pytest.fail.Exception, match=r"missing: missing\.py"):
        assert_parser_results({"sample.py": "value = 1\n"}, expected_dir)


def test_assert_parser_modules_rejects_missing_expected_module(tmp_path: Path) -> None:
    """Parser module assertions fail when an expected module is not generated."""
    expected_dir = tmp_path / "expected"
    expected_dir.mkdir()
    (expected_dir / "sample.py").write_text("value = 1\n", encoding="utf-8")

    with pytest.raises(pytest.fail.Exception, match=r"missing: sample\.py"):
        assert_parser_modules({}, expected_dir)


def test_assert_parser_modules_rejects_unexpected_module(tmp_path: Path) -> None:
    """Parser module assertions fail when generated output has no expected file."""
    expected_dir = tmp_path / "expected"
    expected_dir.mkdir()

    with pytest.raises(pytest.fail.Exception, match=r"new: sample\.py"):
        assert_parser_modules({("sample.py",): "value = 1\n"}, expected_dir.parent / "absent")


def test_assert_inputs_not_mutated_allows_unchanged_nested_values() -> None:
    """Mutation guard accepts unchanged dict/list inputs and ignores immutable labels."""
    schema = {"properties": {"name": {"type": "string"}}, "required": ["name"]}

    with assert_inputs_not_mutated({"schema": schema, "description": "ignored"}):
        assert schema["properties"]["name"]["type"] == "string"


def test_assert_inputs_not_mutated_reports_nested_mutation() -> None:
    """Mutation guard reports the label for nested dict/list mutations."""
    schema = {"properties": {"name": {"type": "string"}}, "required": ["name"]}

    with (
        pytest.raises(pytest.fail.Exception, match="schema was mutated"),
        assert_inputs_not_mutated({"schema": schema}),
    ):
        schema["required"].append("age")


def test_assert_path_cache_evicts_lru_entries_reports_first_mismatch(tmp_path: Path) -> None:
    """LRU helper fails when the first path value is unstable."""
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    first_path.write_text("first", encoding="utf-8")
    second_path.write_text("second", encoding="utf-8")
    calls = 0

    def load_unstable_first(path: Path, encoding: str) -> object:  # noqa: ARG001
        nonlocal calls
        if path == first_path:
            calls += 1
            return calls
        return "stable"

    assert load_unstable_first(second_path, "utf-8") == "stable"
    with pytest.raises(pytest.fail.Exception, match=r"Expected cached value .* to stay stable"):
        main_conftest.assert_path_cache_evicts_lru_entries(load_unstable_first, first_path, second_path)


def test_assert_path_cache_evicts_lru_entries_reports_second_mismatch(tmp_path: Path) -> None:
    """LRU helper fails when the second path value is unstable."""
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    first_path.write_text("first", encoding="utf-8")
    second_path.write_text("second", encoding="utf-8")
    calls = 0

    def load_unstable_second(path: Path, encoding: str) -> object:  # noqa: ARG001
        nonlocal calls
        if path == second_path:
            calls += 1
            return calls
        return "stable"

    with pytest.raises(pytest.fail.Exception, match=r"Expected cached value .* to stay stable"):
        main_conftest.assert_path_cache_evicts_lru_entries(load_unstable_second, first_path, second_path)


def test_builtin_parity_mock_call_preservation(mocker: MockerFixture) -> None:
    """Mock call history is restored after parity-only calls."""
    mocked_callable = mocker.Mock()
    mocked_callable("before")

    with _builtin_parity._preserve_mock_calls([mocked_callable, object()]):
        mocked_callable("during")

    assert mocked_callable.call_args == call("before")
    assert mocked_callable.call_args_list == [call("before")]
    assert mocked_callable.mock_calls == [call("before")]
    assert mocked_callable.call_count == 1


def test_builtin_parity_prance_mock_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prance parser mocks are preserved only when prance is imported."""
    monkeypatch.delitem(sys.modules, "prance", raising=False)
    assert _builtin_parity._parity_mocked_callables_to_preserve() == []

    base_parser = object()
    monkeypatch.setitem(sys.modules, "prance", SimpleNamespace(BaseParser=base_parser))

    assert _builtin_parity._parity_mocked_callables_to_preserve() == [base_parser]


def test_builtin_parity_cli_formatter_helpers(tmp_path: Path) -> None:
    """Pure CLI formatter parity helpers keep default formatter semantics."""
    assert _builtin_parity._extract_cli_formatters(None) is None
    assert _builtin_parity._extract_cli_formatters(["--formatters", "black", "isort", "--check"]) == [
        "black",
        "isort",
    ]
    assert _builtin_parity._uses_default_cli_formatters(None)
    assert _builtin_parity._uses_default_cli_formatters(["--formatters", "black", "isort"])
    assert not _builtin_parity._uses_default_cli_formatters(["--custom-formatters", "tests.custom"])
    assert not _builtin_parity._uses_default_cli_formatters(["--formatters", "builtin"])
    assert _builtin_parity._uses_virtual_output_mode(["--check"])
    assert _builtin_parity._uses_virtual_output_mode(["--diff-against", "previous.json"])
    assert not _builtin_parity._uses_virtual_output_mode(None)
    assert _builtin_parity._uses_default_api_formatters({})
    assert _builtin_parity._uses_default_api_formatters({"formatters": [Formatter.BLACK, Formatter.ISORT]})
    assert not _builtin_parity._uses_default_api_formatters({"formatters": [Formatter.BUILTIN]})
    assert not _builtin_parity._uses_default_api_formatters({"custom_formatters": ["tests.custom"]})
    assert _builtin_parity._builtin_formatter_extra_args(None) == ["--formatters", "builtin"]
    assert _builtin_parity._builtin_formatter_extra_args(["--disable-timestamp"]) == [
        "--disable-timestamp",
        "--formatters",
        "builtin",
    ]

    output_dir = tmp_path / "model"
    output_dir.mkdir()
    assert _builtin_parity._builtin_formatter_parity_output_path(output_dir) == tmp_path / "model_builtin_parity"
    output_file = tmp_path / "output.py"
    assert _builtin_parity._builtin_formatter_parity_output_path(output_file) == tmp_path / "output.builtin-parity.py"


def test_builtin_parity_clear_output_path(tmp_path: Path) -> None:
    """Stale parity output paths are removed for files and directories."""
    stale_file = tmp_path / "output.py"
    stale_file.write_text("stale\n", encoding="utf-8")
    _builtin_parity._clear_builtin_formatter_parity_output(stale_file)
    assert not stale_file.exists()

    stale_dir = tmp_path / "model"
    stale_dir.mkdir()
    (stale_dir / "output.py").write_text("stale\n", encoding="utf-8")
    _builtin_parity._clear_builtin_formatter_parity_output(stale_dir)
    assert not stale_dir.exists()


def test_default_formatter_cli_args(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Builtin formatter defaults apply only to generation commands without formatter settings."""
    monkeypatch.delenv("DATAMODEL_CODE_GENERATOR_TEST_DEFAULT_FORMATTER", raising=False)
    assert main_conftest._default_formatter_cli_args(["--input", "schema.json"]) == ["--input", "schema.json"]

    monkeypatch.setenv("DATAMODEL_CODE_GENERATOR_TEST_DEFAULT_FORMATTER", "builtin")
    assert main_conftest._default_formatter_cli_args(["--input", "schema.json"]) == [
        "--input",
        "schema.json",
        "--formatters",
        "builtin",
    ]
    assert main_conftest._default_formatter_cli_args(
        ["--input", "schema.json"],
        output_path=tmp_path / "output.py",
    ) == [
        "--input",
        "schema.json",
        "--formatters",
        "builtin",
    ]
    assert not (tmp_path / "pyproject.toml").exists()
    assert main_conftest._default_formatter_cli_args(["--input", "schema.json", "--formatters=isort"]) == [
        "--input",
        "schema.json",
        "--formatters=isort",
    ]
    assert main_conftest._default_formatter_cli_args(["--input", "schema.json"], is_generation_command=False) == [
        "--input",
        "schema.json",
    ]
    assert main_conftest._default_formatter_cli_args(
        ["--input", "schema.json"],
        copy_files=[(tmp_path / "source.toml", tmp_path / "pyproject.toml")],
        output_path=tmp_path / "output.py",
    ) == ["--input", "schema.json"]
    (tmp_path / "pyproject.toml").write_text("[tool.black]\nline-length = 60\n", encoding="utf-8")
    assert main_conftest._default_formatter_cli_args(
        ["--input", "schema.json"],
        output_path=tmp_path / "output.py",
    ) == ["--input", "schema.json"]


def test_builtin_default_formatter_config(tmp_path: Path) -> None:
    """Builtin formatter config is present only while the generation helper runs."""
    output_file = tmp_path / "output.py"
    with main_conftest._builtin_default_formatter_config(output_file, enabled=True):
        assert (tmp_path / "pyproject.toml").read_text(encoding="utf-8") == (
            "[tool.datamodel-codegen]\nbuiltin-format-line-length = 88\n"
        )
    assert not (tmp_path / "pyproject.toml").exists()

    with main_conftest._builtin_default_formatter_config(tmp_path, enabled=True):
        assert (tmp_path / "pyproject.toml").is_file()
    assert not (tmp_path / "pyproject.toml").exists()

    existing_config = "[tool.black]\nline-length = 60\n"
    (tmp_path / "pyproject.toml").write_text(existing_config, encoding="utf-8")
    with main_conftest._builtin_default_formatter_config(output_file, enabled=True):
        assert (tmp_path / "pyproject.toml").read_text(encoding="utf-8") == existing_config
    assert (tmp_path / "pyproject.toml").read_text(encoding="utf-8") == existing_config


def test_default_formatter_main_generation_detection() -> None:
    """Raw main helpers identify only generation commands for builtin defaults."""
    assert main_conftest._is_main_generation_command(["--input", "schema.json"])
    assert main_conftest._is_main_generation_command(["--url=https://example.com/schema.json"])
    assert not main_conftest._is_main_generation_command(["--input", "schema.json", "--generate-prompt"])
    assert not main_conftest._is_main_generation_command(["--input", "schema.json", "--output-format=json"])
    assert not main_conftest._is_main_generation_command(["--version"])
    assert main_conftest._get_cli_output_path(["--output", "model.py"]) == Path("model.py")
    assert main_conftest._get_cli_output_path(["--output=model.py"]) == Path("model.py")
    assert main_conftest._get_cli_output_path(["--input", "schema.json"]) is None


def test_default_formatter_generate_options(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """API generation helpers add builtin only when formatter settings are absent."""
    generate_kwargs: dict[str, object] = {}
    monkeypatch.delenv("DATAMODEL_CODE_GENERATOR_TEST_DEFAULT_FORMATTER", raising=False)
    assert main_conftest._default_formatter_generate_options(generate_kwargs) == generate_kwargs

    monkeypatch.setenv("DATAMODEL_CODE_GENERATOR_TEST_DEFAULT_FORMATTER", "builtin")
    assert main_conftest._default_formatter_generate_options(generate_kwargs) == {
        "formatters": [Formatter.BUILTIN],
        "builtin_format_line_length": 88,
    }

    explicit_kwargs = {"formatters": [Formatter.BLACK]}
    assert main_conftest._default_formatter_generate_options(explicit_kwargs) == explicit_kwargs

    (tmp_path / "pyproject.toml").write_text("[tool.black]\nline-length = 60\n", encoding="utf-8")
    assert main_conftest._default_formatter_generate_options({}, output_path=tmp_path / "output.py") == {}


def test_builtin_parity_generated_python_comparison(tmp_path: Path) -> None:
    """Generated Python comparison ignores command header differences."""
    expected_file = tmp_path / "expected.py"
    actual_file = tmp_path / "actual.py"
    expected_file.write_text("#   command:   datamodel-codegen expected\nvalue = 1\n", encoding="utf-8")
    actual_file.write_text("#   command:   datamodel-codegen actual\nvalue = 1\n", encoding="utf-8")

    _builtin_parity._assert_same_generated_python(expected_file, actual_file)

    expected_dir = tmp_path / "expected"
    actual_dir = tmp_path / "actual"
    expected_dir.mkdir()
    actual_dir.mkdir()
    (expected_dir / "model.py").write_text("#   command:   datamodel-codegen expected\nvalue = 1\n", encoding="utf-8")
    (actual_dir / "model.py").write_text("#   command:   datamodel-codegen actual\nvalue = 1\n", encoding="utf-8")

    _builtin_parity._assert_same_generated_python(expected_dir, actual_dir)


def test_builtin_cli_formatter_parity_file_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """CLI parity reruns file input with the builtin formatter."""
    monkeypatch.setenv("DATAMODEL_CODE_GENERATOR_CHECK_BUILTIN_FORMATTER_PARITY", "1")
    output_path = tmp_path / "output.py"
    output_path.write_text("#   command:   datamodel-codegen expected\nvalue = 1\n", encoding="utf-8")

    def fake_run_main(
        input_path: Path,
        builtin_output_path: Path,
        input_file_type: str | None,
        *,
        extra_args: list[str] | None = None,
        copy_files: list[tuple[Path, Path]] | None = None,
    ) -> Exit:
        assert input_path == tmp_path / "schema.json"
        assert input_file_type is None
        assert extra_args == ["--formatters", "builtin"]
        assert copy_files is None
        builtin_output_path.write_text("#   command:   datamodel-codegen actual\nvalue = 1\n", encoding="utf-8")
        return Exit.OK

    monkeypatch.setattr(main_conftest, "_run_main", fake_run_main)

    _builtin_parity._assert_builtin_cli_formatter_parity(
        input_path=tmp_path / "schema.json",
        output_path=output_path,
        input_file_type=None,
        extra_args=None,
        copy_files=None,
        stdin_path=None,
        monkeypatch=None,
        context=main_conftest._builtin_cli_formatter_parity_context(),
    )

    assert not (tmp_path / "output.builtin-parity.py").exists()


def test_builtin_cli_formatter_parity_stdin_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """CLI parity supports stdin-based tests without changing output."""
    monkeypatch.setenv("DATAMODEL_CODE_GENERATOR_CHECK_BUILTIN_FORMATTER_PARITY", "1")
    stdin_path = tmp_path / "schema.json"
    stdin_path.write_text("{}\n", encoding="utf-8")
    output_path = tmp_path / "output.py"
    output_path.write_text("#   command:   datamodel-codegen expected\nvalue = 1\n", encoding="utf-8")

    def fake_main(args: list[str]) -> Exit:
        output_index = args.index("--output") + 1
        Path(args[output_index]).write_text("#   command:   datamodel-codegen actual\nvalue = 1\n", encoding="utf-8")
        return Exit.OK

    monkeypatch.setattr(_builtin_parity, "main", fake_main)

    _builtin_parity._assert_builtin_cli_formatter_parity(
        input_path=None,
        output_path=output_path,
        input_file_type=None,
        extra_args=None,
        copy_files=None,
        stdin_path=stdin_path,
        monkeypatch=monkeypatch,
        context=main_conftest._builtin_cli_formatter_parity_context(),
    )

    assert not (tmp_path / "output.builtin-parity.py").exists()


def test_builtin_generate_formatter_parity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Generate parity reruns API output with the builtin formatter."""
    monkeypatch.setenv("DATAMODEL_CODE_GENERATOR_CHECK_BUILTIN_FORMATTER_PARITY", "1")
    input_path = tmp_path / "schema.json"
    output_path = tmp_path / "output.py"
    output_path.write_text("#   command:   datamodel-codegen expected\nvalue = 1\n", encoding="utf-8")

    def fake_generate(input_: Path, **options: object) -> None:
        assert input_ == input_path
        assert options["formatters"] == [Formatter.BUILTIN]
        output = options["output"]
        assert isinstance(output, Path)
        output.write_text("#   command:   datamodel-codegen actual\nvalue = 1\n", encoding="utf-8")

    monkeypatch.setattr(_builtin_parity, "generate", fake_generate)

    _builtin_parity._assert_builtin_generate_formatter_parity(
        input_=input_path,
        output_path=output_path,
        generate_options={"output": output_path},
    )

    assert not (tmp_path / "output.builtin-parity.py").exists()


def test_builtin_generate_formatter_parity_preserves_warnings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Generate parity preserves expected warning assertions."""
    monkeypatch.setenv("DATAMODEL_CODE_GENERATOR_CHECK_BUILTIN_FORMATTER_PARITY", "1")
    input_path = tmp_path / "schema.json"
    output_path = tmp_path / "output.py"
    output_path.write_text("#   command:   datamodel-codegen expected\nvalue = 1\n", encoding="utf-8")

    def fake_generate(input_: Path, **options: object) -> None:
        import warnings

        assert input_ == input_path
        output = options["output"]
        assert isinstance(output, Path)
        output.write_text("#   command:   datamodel-codegen actual\nvalue = 1\n", encoding="utf-8")
        warnings.warn("expected warning", UserWarning, stacklevel=2)

    monkeypatch.setattr(_builtin_parity, "generate", fake_generate)

    _builtin_parity._assert_builtin_generate_formatter_parity(
        input_=input_path,
        output_path=output_path,
        generate_options={"output": output_path},
        expected_warnings=["expected warning"],
    )

    assert not (tmp_path / "output.builtin-parity.py").exists()


def test_collection_runs_measured_slow_tests_first(request: pytest.FixtureRequest) -> None:
    """Every initial xdist chunk of the session starts with its measured slow tests, longest first."""
    durations = slow_test_durations()
    items = request.session.items
    workers = getattr(request.config, "workerinput", {}).get("workercount", 1)
    chunks: list[list[int]] = []
    start = 0
    for size in worksteal_chunk_sizes(len(items), workers):
        chunks.append([durations.get(item.nodeid, 0) for item in items[start : start + size]])
        start += size
    assert_output(
        json.dumps(
            {
                "chunks_cover_session": start == len(items),
                "measured_files_exist": all(Path(nodeid.partition("::")[0]).is_file() for nodeid in durations),
                "parity_measured": SLOWEST_NODEID in durations,
                "slow_first_per_chunk": all(chunk == sorted(chunk, reverse=True) for chunk in chunks),
            },
            sort_keys=True,
        )
        + "\n",
        Path(__file__).parent / "data/expected/ci_shards/slow-first.txt",
    )


pytest_plugins = ("pytester",)

_ISOLATED_VARIABLES = (
    "CI",
    "DATAMODEL_CODE_GENERATOR_TEST_DEFAULT_FORMATTER",
    "bamboo.buildKey",
    "BUILD_ID",
    "BUILD_NUMBER",
    "BUILDKITE",
    "CIRCLECI",
    "CONTINUOUS_INTEGRATION",
    "GITHUB_ACTIONS",
    "GITHUB_RUN_ID",
    "HUDSON_URL",
    "INLINE_SNAPSHOT_DEFAULT_FLAGS",
    "JENKINS_URL",
    "PYCHARM_HOSTED",
    "PYTEST_ADDOPTS",
    "PYTEST_XDIST_WORKER",
    "TEAMCITY_VERSION",
    "TOX_ENV_NAME",
    "TRAVIS",
)


@dataclass
class LedgerProject:
    """A test project in a temporary directory whose suite uses the shared helpers and their ``.refs`` index."""

    pytester: pytest.Pytester
    monkeypatch: pytest.MonkeyPatch

    @property
    def suite(self) -> Path:
        """The project's tests root."""
        return self.pytester.path / "suite"

    @property
    def expected(self) -> Path:
        """The project's expected output directory."""
        return self.suite / "data" / "expected"

    def write(self, name: str, source: str) -> Path:
        """Write a module or file of the suite, dropping stale bytecode."""
        path = self.suite / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
        shutil.rmtree(path.parent / "__pycache__", ignore_errors=True)
        return path

    def store(self, label: str, text: str) -> Path:
        """Write a stored expected file by hand."""
        path = self.expected / label
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode())
        return path

    def refs(self, module: str = "test_m") -> str:
        """Return a module's sidecar text, or an empty string."""
        path = self.suite / f"{module}.refs"
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def files(self) -> dict[str, str]:
        """Return every stored expected file by label."""
        return {
            path.relative_to(self.expected).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(self.expected.rglob("*"))
            if path.is_file()
        }

    def run(self, *args: str, env: dict[str, str] | None = None) -> pytest.RunResult:
        """Run pytest in the project with extra environment variables."""
        with self.monkeypatch.context() as context:
            for name, value in (env or {}).items():
                context.setenv(name, value)
            return self.pytester.runpytest_inprocess("-p", "no:cacheprovider", "-p", "no:randomly", *args)


@pytest.fixture
def ledger(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> LedgerProject:
    """Return an empty project whose conftest is the shared one and whose tests live in suite/."""
    for name in _ISOLATED_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    pytester.makepyprojecttoml(
        """
        [tool.pytest.ini_options]
        expected_tests_root = "suite"

        [tool.inline-snapshot]
        default-flags = ["short-report", "create"]
        default-flags-tui = ["short-report", "create"]
        """
    )
    pytester.makeconftest("from tests.conftest import *  # noqa: F403\n")
    (pytester.path / "suite" / "data" / "expected").mkdir(parents=True)
    return LedgerProject(pytester, monkeypatch)


SHARED = """
import pytest
from pathlib import Path

from tests.conftest import assert_output

EXPECTED = Path(__file__).parent / "data" / "expected"


@pytest.mark.parametrize("case", ["a", "b"])
def test_y(case):
    assert_output(OUTPUTS.get(case, "old\\n"), EXPECTED / "m" / "y.txt")


def test_z():
    assert_output(OUTPUTS.get("z", "old\\n"), EXPECTED / "m" / "y.txt")
"""


def _shared(ledger: LedgerProject, **outputs: str) -> None:
    ledger.write("test_m.py", f"OUTPUTS = {outputs!r}\n{SHARED}")


def test_ledger_creates_derived_output_and_records_it(ledger: LedgerProject) -> None:
    """A local run stores output under the test's name and records it; later runs only compare it."""
    ledger.write(
        "test_m.py",
        """
        from tests.conftest import assert_output


        def test_m_new():
            assert_output("hello\\n")
            assert_output("warn\\n", name="warnings")
        """,
    )
    result = ledger.run("suite")
    result.assert_outcomes(passed=1)
    assert "expected: created m/new.txt; recorded in suite/test_m.refs" in result.stdout.str()
    assert ledger.files() == {"m/new.txt": "hello\n", "m/new.warnings.txt": "warn\n"}
    assert ledger.refs() == HEADER + "[test_m_new]\n\tm/new.txt\n\tm/new.warnings.txt\n"
    again = ledger.run("suite")
    again.assert_outcomes(passed=1)
    assert "expected output" not in again.stdout.str()


@pytest.mark.parametrize(
    ("env", "message"),
    [
        pytest.param(
            {"PYTEST_XDIST_WORKER": "gw0"},
            "Expected output for suite/test_m.py::test_new was not created because pytest ran with -n.\n"
            "Fix: pytest suite/test_m.py::test_new",
            id="xdist",
        ),
        pytest.param(
            {"CI": "1"},
            "No expected output recorded for suite/test_m.py::test_new; expected files are not written while CI is "
            "set.\nFix: CI= pytest suite/test_m.py::test_new",
            id="ci-mode",
        ),
        pytest.param(
            {"CI": "true", "GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "42"},
            "No expected output recorded for suite/test_m.py::test_new.\n"
            "Fix: pytest suite/test_m.py::test_new && git add -A suite\n"
            "dcg-expected-fix: ",
            id="github",
        ),
    ],
)
def test_ledger_reports_unrecorded_output(ledger: LedgerProject, env: dict[str, str], message: str) -> None:
    """Without create, an unrecorded comparison fails with one command that records it; CI adds its payload."""
    ledger.write(
        "test_m.py", "from tests.conftest import assert_output\n\n\ndef test_new():\n    assert_output('x\\n')\n"
    )
    result = ledger.run("suite", env=env)
    result.assert_outcomes(failed=1)
    output = result.stdout.str()
    assert message in output
    assert ledger.files() == {}
    assert ("dcg-expected-fix: " in output) is ("CI" in env)
    assert "\nOr" not in output


def test_ledger_reports_missing_line_for_existing_file(ledger: LedgerProject) -> None:
    """A committed expected file without its .refs line names the sidecar to add."""
    _shared(ledger)
    ledger.store("m/y.txt", "old\n")
    result = ledger.run("suite/test_m.py::test_z", "--inline-snapshot=disable")
    result.assert_outcomes(failed=1)
    assert "suite/test_m.refs has no line for suite/test_m.py::test_z.\n" in result.stdout.str()


def test_ledger_payload_round_trip(ledger: LedgerProject, tmp_path: Path) -> None:
    """A CI payload applied locally records CI's output for the environment that produced it."""
    ledger.write(
        "test_m.py", "from tests.conftest import assert_output\n\n\ndef test_new():\n    assert_output('x\\n')\n"
    )
    failure = ledger.run("suite", env={"CI": "true", "GITHUB_ACTIONS": "true"}).stdout.str()
    payload = next(line for line in failure.splitlines() if line.startswith("dcg-expected-fix: "))
    decoded = units.decode_payload(payload)
    assert (decoded["item"], decoded["label"], decoded["value"]) == ("suite/test_m.py::test_new", "m/new.txt", "x\n")
    apply_file = tmp_path / "apply.json"
    apply_file.write_text(
        json.dumps({"payloads": [{**decoded, "facts": _other_platform(decoded["facts"])}]}), encoding="utf-8"
    )
    ledger.run(f"--expected-apply={apply_file}", "suite").assert_outcomes()
    assert ledger.refs() == HEADER + "[test_new]\n\tm/new.txt\n"
    assert ledger.files() == {"m/new.txt": "x\n"}
    ledger.run("suite", env={"CI": "true"}).assert_outcomes(passed=1)


def test_ledger_shares_equal_output_and_reports_mismatch(ledger: LedgerProject) -> None:
    """Equal outputs share one file, and a mismatch fails with the fix and restore commands and the diff."""
    _shared(ledger)
    ledger.store("m/y.txt", "old\n")
    created = ledger.run("suite")
    created.assert_outcomes(passed=3)
    assert "expected: recorded 3 tests in suite/test_m.refs (their files already existed)" in created.stdout.str()
    ledger.write(
        "test_n.py",
        "from tests.conftest import assert_output\n\n\ndef test_same():\n    assert_output('old\\n')\n",
    )
    same = ledger.run("suite/test_n.py")
    assert (
        "expected: test_same has the same output as m/y.txt; no new file; recorded in suite/test_n.refs"
        in same.stdout.str()
    )
    assert ledger.refs("test_n") == HEADER + "[test_same]\n\tn/same.txt\t= m/y.txt\n"
    _shared(ledger, a="new\n")
    result = ledger.run("suite/test_m.py")
    result.assert_outcomes(failed=1, passed=2)
    output = result.stdout.str()
    assert "Content mismatch: suite/test_m.py::test_y[a] expects m/y.txt (used by 4 tests).\n" in output
    assert (
        "If the new output is intended: pytest 'suite/test_m.py::test_y[a]' --inline-snapshot=fix && git add -A suite\n"
        in output
    )
    assert "Otherwise: git checkout HEAD -- suite/data/expected/m/y.txt\n" in output


def test_ledger_fix_splits_then_merges_back(ledger: LedgerProject) -> None:
    """Fixing one sharer splits it off; fixing the others merges them back onto the shared label."""
    _shared(ledger)
    ledger.store("m/y.txt", "old\n")
    ledger.run("suite").assert_outcomes(passed=3)
    _shared(ledger, z="new\n", a="new\n", b="new\n")
    split = ledger.run("suite/test_m.py::test_z", "--inline-snapshot=fix")
    split.assert_outcomes(passed=1)
    assert (
        "expected: test_z split from 2 tests that share m/y.txt (their files are unchanged); stored at m/y~z.txt"
        in split.stdout.str()
    )
    assert "If they should change too: pytest 'suite/test_m.py::test_y[a]' 'suite/test_m.py::test_y[b]'" in (
        split.stdout.str()
    )
    assert ledger.files() == {"m/y.txt": "old\n", "m/y~z.txt": "new\n"}
    assert units.RefsIndex(ledger.suite).stored_path(ledger.expected / "m" / "y.txt") == ledger.expected / "m" / "y.txt"
    merged = ledger.run("suite/test_m.py::test_y", "--inline-snapshot=fix")
    merged.assert_outcomes(passed=2)
    assert "expected: deleted m/y~z.txt (no test uses it)" in merged.stdout.str()
    assert ledger.files() == {"m/y.txt": "new\n"}
    assert ledger.refs() == HEADER + "[test_y]\na\tm/y.txt\nb\tm/y.txt\n[test_z]\n\tm/y.txt\n"


def test_ledger_fix_reruns_sharing_siblings(ledger: LedgerProject) -> None:
    """Fixing one parameter re-runs the parameters that share its file, so the file changes in place."""
    _shared(ledger)
    ledger.store("m/y.txt", "old\n")
    ledger.run("suite/test_m.py::test_y").assert_outcomes(passed=2)
    _shared(ledger, a="new\n", b="new\n")
    result = ledger.run("suite/test_m.py::test_y[a]", "--inline-snapshot=fix")
    result.assert_outcomes(passed=2, deselected=1)
    assert "expected: fixed m/y.txt in place (2 tests)" in result.stdout.str()
    assert "expected: re-ran 1 tests that share a changed file" in result.stdout.str()
    assert ledger.files() == {"m/y.txt": "new\n"}


def test_ledger_drops_lines_of_renamed_tests(ledger: LedgerProject) -> None:
    """Lines of a renamed test fail CI with the module command, which records the new name and drops the old."""
    ledger.write(
        "test_m.py", "from tests.conftest import assert_output\n\n\ndef test_old():\n    assert_output('x\\n')\n"
    )
    ledger.run("suite").assert_outcomes(passed=1)
    ledger.write(
        "test_m.py", "from tests.conftest import assert_output\n\n\ndef test_new():\n    assert_output('x\\n')\n"
    )
    failing = ledger.run("suite", "--inline-snapshot=disable")
    failing.assert_outcomes(failed=2)
    assert (
        "suite/test_m.refs has lines for suite/test_m.py::test_old, which no longer exists.\n"
        "Fix: pytest suite/test_m.py --collect-only -q && git add -A suite"
    ) in failing.stdout.str()
    fixed = ledger.run("suite/test_m.py::test_new")
    fixed.assert_outcomes(passed=1)
    assert "expected: removed the lines of suite/test_m.py::test_old from suite/test_m.refs" in fixed.stdout.str()
    assert "expected: deleted m/old.txt (no test uses it)" in fixed.stdout.str()
    assert ledger.refs() == HEADER + "[test_new]\n\tm/new.txt\n"
    assert ledger.files() == {"m/new.txt": "x\n"}


def test_ledger_rejects_hand_edited_refs(ledger: LedgerProject, monkeypatch: pytest.MonkeyPatch) -> None:
    """A sidecar that differs from what pytest writes fails its module with the restore command."""
    ledger.write(
        "test_m.py", "from tests.conftest import assert_output\n\n\ndef test_a():\n    assert_output('x\\n')\n"
    )
    ledger.run("suite").assert_outcomes(passed=1)
    (ledger.suite / "test_m.refs").write_bytes((HEADER + "[test_a]\n\tm/a.txt\n").replace("\n", "\r\n").encode())
    ledger.run("suite").assert_outcomes(passed=1)
    (ledger.suite / "test_m.refs").write_text(HEADER + "[test_a]\n\tm/a.txt\t!\n", encoding="utf-8")
    result = ledger.run("suite")
    result.assert_outcomes(failed=2)
    assert (
        "suite/test_m.refs was edited by hand (line 4: unexpected column '!'); pytest writes this file.\n"
        "Fix: python scripts/fix_expected.py --repair suite/test_m.refs"
    ) in result.stdout.str()
    assert _fix_expected(ledger, monkeypatch, "--repair", "suite/test_m.refs") == 0
    assert ledger.refs() == HEADER + "[test_a]\n\tm/a.txt\n"


UNSORTED = "lines are not sorted, or the file differs from what pytest writes"


def test_stored_path_of_another_drive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A path on another Windows drive than the expected root is not in the index and stays as it is."""

    def other_drive(*_: object) -> str:
        msg = "path is on mount 'C:', start on mount 'D:'"
        raise ValueError(msg)

    monkeypatch.setattr(units.os.path, "relpath", other_drive)
    assert units.RefsIndex(tmp_path).stored_path(tmp_path / "x.txt") == tmp_path / "x.txt"


def test_ledger_repairs_a_label_listed_twice_that_is_no_longer_compared(ledger: LedgerProject) -> None:
    """A creating run drops both lines a merge left for a label the test no longer compares."""
    ledger.write("test_m.py", ONE_TEST)
    ledger.store("m/a.txt", "x\n")
    ledger.store("m/old.txt", "o\n")
    ledger.store("m/b.txt", "o\n")
    (ledger.suite / "test_m.refs").write_text(
        HEADER + "[test_a]\n\tm/a.txt\n\tm/old.txt\n\tm/old.txt\t= m/b.txt\n", encoding="utf-8"
    )
    ledger.run("suite").assert_outcomes(passed=1)
    assert ledger.refs() == HEADER + "[test_a]\n\tm/a.txt\n"


def test_ledger_tree_compares_only_the_files_its_pattern_selects(ledger: LedgerProject) -> None:
    """Generated files outside the pattern are neither compared nor stored, so a second run passes."""
    ledger.write(
        "test_m.py",
        "from pathlib import Path\n\nfrom tests.conftest import assert_generated_modules_output\n\n\n"
        "def test_g():\n"
        "    modules = {('pkg', '__init__.py'): 'a = 1\\n', ('pkg', 'py.typed'): ''}\n"
        "    assert_generated_modules_output(modules, Path(__file__).parent / 'data' / 'expected' / 'g')\n",
    )
    ledger.run("suite").assert_outcomes(passed=1)
    assert ledger.files() == {"g/pkg/__init__.py": "a = 1\n"}
    ledger.run("suite").assert_outcomes(passed=1)


def test_format_refs_writes_each_line_once() -> None:
    """A test comparing one label several times records one line for it."""
    line = units.RefLine("test_p", "case", "m/shared.txt")
    assert units.format_refs([line, line, line]) == HEADER + "[test_p]\ncase\tm/shared.txt\n"


@pytest.mark.parametrize(
    ("text", "reason", "kind"),
    [
        ("\tm/a.txt\n", "line 1: the line is outside a [test] section", units.BROKEN),
        ("[]\n", "line 1: a section names no test", units.BROKEN),
        ("[test_a]\n\n", "line 2: expected a parameter id and a label separated by a tab", units.BROKEN),
        ("[test_a]\n\tm/a.txt\t= m/a.txt\n", "line 2: the storage repeats the label", units.BROKEN),
        (
            "[test_a]\n\tm/a/\t= m/b.txt\n",
            "line 2: a file label needs file storage and a tree label tree storage",
            units.BROKEN,
        ),
        ("[test_a]\n\tm/a.txt\t?\t?\n", "line 2: unexpected column '?'", units.BROKEN),
        (
            "[test_a]\n\tm/a.txt\t@os=nt\n",
            "line 2: unknown predicate 'os=nt'; facts are python, formatter, black, isort, platform",
            units.BROKEN,
        ),
        (
            "[test_a]\n\t/etc/a.txt\n",
            "line 2: '/etc/a.txt' is not a relative path below tests/data/expected",
            units.BROKEN,
        ),
        (
            "[test_a]\n\tm/a.txt\t= ../a.txt\n",
            "line 2: '../a.txt' is not a relative path below tests/data/expected",
            units.BROKEN,
        ),
        ("[test_a]\n\t../a.txt\n", "line 2: '../a.txt' is not a relative path below tests/data/expected", units.BROKEN),
        ("[test_a]\n\tm//\n", "line 2: 'm//' is not a relative path below tests/data/expected", units.BROKEN),
        pytest.param(
            HEADER + "[test_a]\n\tm/a.txt\n\tm/a.txt\t= m/b.txt\n",
            "test_a[] lists m/a.txt twice",
            units.CONFLICT,
            id="conflict",
        ),
        pytest.param(HEADER + "[test_a]\n\tm/a.txt\n\tm/a.txt\n", f"line 5: {UNSORTED}", units.ORDER, id="repeated"),
        pytest.param(HEADER + "[test_a]\n\tm/b.txt\n\tm/a.txt\n", f"line 4: {UNSORTED}", units.ORDER, id="unsorted"),
        pytest.param(HEADER + "[test_a]\n\tm/a.txt", f"line 5: {UNSORTED}", units.ORDER, id="no-final-newline"),
        pytest.param("[test_a]\n\tm/a.txt\n", f"line 1: {UNSORTED}", units.ORDER, id="no-header"),
        pytest.param(
            HEADER.replace("Do not edit by hand", "Edit freely") + "[test_a]\n\tm/a.txt\n",
            f"line 1: {UNSORTED}",
            units.ORDER,
            id="edited-header",
        ),
        pytest.param(HEADER + HEADER + "[test_a]\n\tm/a.txt\n", f"line 3: {UNSORTED}", units.ORDER, id="header-twice"),
        pytest.param(HEADER, f"line 1: {UNSORTED}", units.ORDER, id="header-only"),
        pytest.param(
            HEADER + "[test_a]\n# note\n\tm/a.txt\n",
            "line 4: expected a parameter id and a label separated by a tab",
            units.BROKEN,
            id="comment-in-section",
        ),
        pytest.param(
            HEADER + "[test_a]\n\tm/a.txt\u2028[test_b]\n",
            "line 4: 'm/a.txt\\u2028[test_b]' is not a relative path below tests/data/expected",
            units.BROKEN,
            id="line-separator",
        ),
        pytest.param(
            HEADER + "[test_a]\n\tm/a\x85.txt\n",
            "line 4: 'm/a\\x85.txt' is not a relative path below tests/data/expected",
            units.BROKEN,
            id="next-line",
        ),
        pytest.param(
            HEADER + f"[test_a]\n\tm/{'x' * 252}.txt\n",
            f"line 4: 'm/{'x' * 252}.txt' is not a relative path below tests/data/expected",
            units.BROKEN,
            id="long-name",
        ),
    ],
)
def test_ledger_refs_parse_problems(text: str, reason: str, kind: str) -> None:
    """Every sidecar that pytest would not write reports where and why, and whether a local run can repair it."""
    lines, problems = units.parse_refs(text)
    assert [
        (f"line {problem.number}: {problem.reason}" if problem.number else problem.reason) for problem in problems
    ] == [reason]
    assert [problem.kind for problem in problems] == [kind]
    assert isinstance(lines, list)


def test_ledger_missing_storage(ledger: LedgerProject) -> None:
    """A deleted stored file fails runs that cannot write, and a local run stores the output again."""
    ledger.write(
        "test_m.py", "from tests.conftest import assert_output\n\n\ndef test_a():\n    assert_output('x\\n')\n"
    )
    ledger.run("suite").assert_outcomes(passed=1)
    (ledger.expected / "m" / "a.txt").unlink()
    failing = ledger.run("suite", "--inline-snapshot=disable")
    failing.assert_outcomes(failed=2)
    output = failing.stdout.str()
    assert "suite/test_m.refs stores test_a at m/a.txt, but suite/data/expected/m/a.txt does not exist.\n" in output
    assert "Expected output for suite/test_m.py::test_a is missing: suite/data/expected/m/a.txt.\n" in output
    ledger.run("suite").assert_outcomes(passed=1)
    assert ledger.files() == {"m/a.txt": "x\n"}


def test_ledger_requires_every_recorded_comparison(ledger: LedgerProject) -> None:
    """A test that stops comparing a recorded label fails; a fix run drops the line and the unused file."""
    ledger.write(
        "test_m.py",
        """
        from tests.conftest import assert_output


        def test_a():
            assert_output("x\\n")
            assert_output("w\\n", name="warnings")
        """,
    )
    ledger.run("suite").assert_outcomes(passed=1)
    ledger.write(
        "test_m.py", "from tests.conftest import assert_output\n\n\ndef test_a():\n    assert_output('x\\n')\n"
    )
    local = ledger.run("suite", "--inline-snapshot=disable")
    local.assert_outcomes(failed=1)
    assert (
        "suite/test_m.py::test_a did not compare m/a.warnings.txt, which suite/test_m.refs records for it.\n"
        "If it no longer should: pytest suite/test_m.py::test_a --inline-snapshot=fix"
    ) in local.stdout.str()
    failing = ledger.run("suite", env={"CI": "true", "GITHUB_ACTIONS": "true"})
    failing.assert_outcomes(failed=1)
    output = failing.stdout.str()
    assert (
        "does not compare m/a.warnings.txt, which suite/test_m.refs records for suite/test_m.py::test_a.\n"
        "Fix, if pytest suite/test_m.py::test_a compares them on your machine: python scripts/fix_expected.py --apply"
    ) in output
    assert "Or, if it should no longer compare them: pytest suite/test_m.py::test_a --inline-snapshot=fix" in output
    payload = next(line for line in output.splitlines() if line.startswith("dcg-expected-fix: "))
    assert units.decode_payload(payload)["missing"] is True
    fixed = ledger.run("suite", "--inline-snapshot=fix")
    fixed.assert_outcomes(passed=1)
    assert "expected: deleted m/a.warnings.txt (no test uses it)" in fixed.stdout.str()
    assert ledger.refs() == HEADER + "[test_a]\n\tm/a.txt\n"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        pytest.param(
            'assert_output("x\\n", EXPECTED / "x.txt")\n    assert_output("y\\n", EXPECTED / "x.txt")',
            "suite/test_m.py::test_a compares two different outputs against x.txt (diff below).\n"
            "If they should be equal, fix the code; otherwise compare the second against its own expected file",
            id="conflict",
        ),
        pytest.param(
            'assert_output("x\\n", name="n")\n    assert_output("y\\n", name="n")',
            "suite/test_m.py::test_a compares two different outputs against m/a.n.txt (diff below).\n"
            'If they should be equal, fix the code; otherwise name the second: assert_output(text, name="second")',
            id="conflict-named",
        ),
        pytest.param(
            'assert_output("x\\n")\n    assert_output("y\\n")',
            "suite/test_m.py::test_a compares several outputs; name each one after the first: "
            'assert_output(text, name="warnings")',
            id="unnamed",
        ),
        pytest.param(
            'assert_output("x\\n", EXPECTED.parent / "outside.txt")',
            "Expected outputs live under tests/data/expected. Fix: remove the path argument (pytest names the file)",
            id="outside",
        ),
    ],
)
def test_ledger_rejects_ambiguous_outputs(ledger: LedgerProject, body: str, message: str) -> None:
    """Two outputs for one label, several unnamed outputs, and new paths outside the expected root fail clearly."""
    ledger.write(
        "test_m.py",
        "from pathlib import Path\n\nfrom tests.conftest import assert_output\n\n"
        f'EXPECTED = Path(__file__).parent / "data" / "expected"\n\n\ndef test_a():\n    {body}\n',
    )
    result = ledger.run("suite")
    result.assert_outcomes(failed=1)
    assert message in result.stdout.str()


def test_ledger_compares_trees(ledger: LedgerProject) -> None:
    """Tree helpers store directories, compare patterns or whole trees, and treat a missing directory as empty."""
    ledger.write(
        "test_m.py",
        """
        from pathlib import Path

        from tests.conftest import (
            assert_directory_content,
            assert_exact_directory_content,
            assert_generated_modules_output,
        )

        EXPECTED = Path(__file__).parent / "data" / "expected"
        MODULES = {("pkg", "__init__.py"): "", ("pkg", "a.py"): "A = 1\\n", ("README.md",): "# pkg\\n"}


        def test_pattern(tmp_path):
            files = {"a.py": "A = 1\\n", ".b.py": "B = 1\\n", "notes.txt": "skip\\n"}
            files |= {".cache/c.py": "x", "__pycache__/a.pyc": "x"}
            for name, text in files.items():
                (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
                (tmp_path / name).write_text(text)
            assert_directory_content(tmp_path, EXPECTED / "tree")


        def test_whole():
            assert_generated_modules_output(MODULES, EXPECTED / "whole", pattern="*")


        def test_empty(tmp_path):
            assert_exact_directory_content(tmp_path, EXPECTED / "empty", "*.md")
        """,
    )
    ledger.run("suite").assert_outcomes(passed=3)
    assert ledger.files() == {
        "tree/.b.py": "B = 1\n",
        "tree/a.py": "A = 1\n",
        "whole/README.md": "# pkg\n",
        "whole/pkg/__init__.py": "",
        "whole/pkg/a.py": "A = 1\n",
    }
    assert (
        ledger.refs() == HEADER + "[test_empty]\n\tempty//*.md\n[test_pattern]\n\ttree//*.py\n[test_whole]\n\twhole/\n"
    )
    ledger.store("whole/pkg/b.py", "B = 2\n")
    result = ledger.run("suite")
    result.assert_outcomes(failed=1, passed=2)
    assert "missing: pkg/b.py" in result.stdout.str()
    fixed = ledger.run("suite", "--inline-snapshot=fix")
    fixed.assert_outcomes(passed=3)
    assert "whole/pkg/b.py" not in ledger.files()


def test_ledger_json_values_and_direct_comparisons(ledger: LedgerProject, tmp_path: Path) -> None:
    """JSON files hold encoded values; paths outside the tests root are compared directly, never written."""
    other = tmp_path / "other"
    other.mkdir()
    (other / "a.py").write_text("A = 1\n", encoding="utf-8")
    ledger.write(
        "test_m.py",
        f"""
        from pathlib import Path

        import pytest

        from tests.conftest import assert_directory_content, assert_output

        EXPECTED = Path(__file__).parent / "data" / "expected"
        OTHER = Path({str(other)!r})


        def test_json():
            assert_output({{"b": [1, "x"]}}, EXPECTED / "value.json")
            assert_output("text\\r\\n", EXPECTED / "text.json")


        def test_direct():
            assert_directory_content(OTHER, OTHER)
            with pytest.raises(pytest.fail.Exception, match="Expected file not found"):
                assert_output("x\\n", OTHER / "missing.txt")
            with pytest.raises(pytest.fail.Exception, match="Content mismatch for"):
                assert_output("B = 2\\n", OTHER / "a.py")
        """,
    )
    ledger.run("suite").assert_outcomes(passed=2)
    assert ledger.files() == {"text.json": '"text\\n"', "value.json": '{\n  "b": [\n    1,\n    "x"\n  ]\n}'}
    ledger.run("suite").assert_outcomes(passed=2)


ENVIRONMENT_TESTS = """
import os
import pytest

from tests.conftest import assert_output

FORMATTER = os.environ.get("DATAMODEL_CODE_GENERATOR_TEST_DEFAULT_FORMATTER", "external")


def test_same():
    assert_output("same\\n")


def test_label():
    assert_output("x\\n", name=FORMATTER)


def test_content():
    assert_output(f"{FORMATTER}\\n")


@pytest.mark.parametrize("case", ["both", *(["builtin-only"] if FORMATTER == "builtin" else [])])
def test_present(case):
    assert_output("p\\n")


def test_skipped():
    if FORMATTER == "builtin":
        pytest.skip("only checked with the external formatter")
    assert_output("s\\n")
"""


def test_ledger_merges_observations_from_environments(ledger: LedgerProject, tmp_path: Path) -> None:
    """Observations of several environments give conditional lines for their labels and overrides for content."""
    ledger.write("test_m.py", ENVIRONMENT_TESTS)
    external, builtin = tmp_path / "external", tmp_path / "builtin"
    ledger.run(f"--expected-observe={external}", "suite").assert_outcomes(passed=5)
    builtin_env = {"DATAMODEL_CODE_GENERATOR_TEST_DEFAULT_FORMATTER": "builtin"}
    ledger.run(f"--expected-observe={builtin}", "suite", env=builtin_env).assert_outcomes(passed=5, skipped=1)
    apply_file = tmp_path / "apply.json"
    apply_file.write_text(json.dumps({"observations": [str(external), str(builtin)]}), encoding="utf-8")
    ledger.run(f"--expected-apply={apply_file}").assert_outcomes()
    assert ledger.refs() == HEADER + (
        "[test_content]\n"
        "\tm/content.txt\n"
        "\tm/content.txt\t@formatter=builtin\t= m/content@builtin.txt\n"
        "[test_label]\n"
        "\tm/label.builtin.txt\t?\n"
        "\tm/label.external.txt\t?\t= m/label.builtin.txt\n"
        "[test_present]\n"
        "both\tm/present[both].txt\n"
        "builtin-only\tm/present[builtin-only].txt\t?\t= m/present[both].txt\n"
        "[test_same]\n"
        "\tm/same.txt\n"
        "[test_skipped]\n"
        "\tm/skipped.txt\n"
    )
    assert ledger.files()["m/content@builtin.txt"] == "builtin\n"
    ledger.run("suite", "--inline-snapshot=disable").assert_outcomes(passed=5)
    ledger.run("suite", "--inline-snapshot=disable", env=builtin_env).assert_outcomes(passed=5, skipped=1)


UNCOLLECTED_TESTS = """
import pytest

from tests.conftest import assert_output


@pytest.mark.parametrize("case", ["kept"])
def test_case(case):
    assert_output(f"{case}\\n")
"""


def test_ledger_drops_items_no_environment_collects(ledger: LedgerProject, tmp_path: Path) -> None:
    """Lines of an item no observed environment collects go, conditional ones too, with the files only they used.

    Only modules some environment collected count: the lines of ``test_o.py``, which no observation collected, stay,
    and so do the lines of an item an environment collected.
    """
    ledger.write("test_m.py", UNCOLLECTED_TESTS)
    ledger.write("test_o.py", UNCOLLECTED_TESTS)
    ledger.run("suite").assert_outcomes(passed=2)
    kept = {module: ledger.refs(module) for module in ("test_m", "test_o")}
    for module, prefix in (("test_m", "m"), ("test_o", "o")):
        gone = [
            units.RefLine("test_case", "gone", f"{prefix}/gone@{python}.txt", conditional=True)
            for python in ("py310", "py314")
        ]
        for line in gone:
            ledger.store(line.label, "gone\n")
        lines = [*units.parse_refs(kept[module])[0], *gone]
        (ledger.suite / f"{module}.refs").write_text(units.format_refs(lines), encoding="utf-8")
    other = ledger.refs("test_o")
    first, second = tmp_path / "first", tmp_path / "second"
    for directory in (first, second):
        ledger.run(f"--expected-observe={directory}", "suite/test_m.py").assert_outcomes(passed=1)
    apply_file = tmp_path / "apply.json"
    apply_file.write_text(
        json.dumps({"observations": [str(first), str(second)], "merge": False, "drop_uncollected": True}),
        encoding="utf-8",
    )
    result = ledger.run(f"--expected-apply={apply_file}")
    result.assert_outcomes()
    assert "expected: removed the lines of suite/test_m.py::test_case[gone] from suite/test_m.refs" in (
        result.stdout.str()
    )
    assert (ledger.refs(), ledger.refs("test_o")) == (kept["test_m"], other)
    assert sorted(ledger.files()) == ["m/case[kept].txt", "o/gone@py310.txt", "o/gone@py314.txt"]
    ledger.run("suite/test_m.py", env=GITHUB).assert_outcomes(passed=1)


def test_ledger_applies_foreign_payloads(ledger: LedgerProject, tmp_path: Path) -> None:
    """A payload from another environment adds an override for different content and marks labels it skips."""
    ledger.write(
        "test_m.py",
        """
        from tests.conftest import assert_output


        def test_a():
            assert_output("x\\n")
            assert_output("y\\n", name="extra")
        """,
    )
    ledger.run("suite").assert_outcomes(passed=1)
    facts = {"python": "3.10", "formatter": "external", "black": "22.1.0", "isort": "5.13.2", "platform": "linux"}
    item = "suite/test_m.py::test_a"
    payloads = [
        {
            "facts": facts,
            "item": item,
            "label": "m/a.txt",
            "digest": units.unit_digest("m/a.txt", "x22\n"),
            "value": "x22\n",
        },
        {"facts": facts, "item": item, "label": "m/a.extra.txt", "missing": True},
        {
            "facts": facts,
            "item": item,
            "label": "m/a.only.txt",
            "digest": units.unit_digest("m/a.only.txt", "o\n"),
            "value": "o\n",
        },
    ]
    apply_file = tmp_path / "apply.json"
    apply_file.write_text(json.dumps({"payloads": payloads}), encoding="utf-8")
    ledger.run(f"--expected-apply={apply_file}", "suite").assert_outcomes()
    assert ledger.refs() == HEADER + (
        "[test_a]\n\tm/a.extra.txt\t?\n\tm/a.only.txt\t?\n\tm/a.txt\n\tm/a.txt\t@black=22\t= m/a@black22.txt\n"
    )
    assert ledger.files()["m/a@black22.txt"] == "x22\n"
    payloads[0] |= {"value": "x\n", "digest": units.unit_digest("m/a.txt", "x\n")}
    apply_file.write_text(json.dumps({"payloads": payloads[:1]}), encoding="utf-8")
    ledger.run(f"--expected-apply={apply_file}", "suite").assert_outcomes()
    assert "@" not in ledger.refs()
    assert "m/a@black22.txt" not in ledger.files()
    ledger.run("suite").assert_outcomes(passed=1)


def test_ledger_payload_too_large(ledger: LedgerProject) -> None:
    """Output too large for a payload names the lane to run locally instead."""
    ledger.write(
        "test_m.py",
        "import random\nimport string\nfrom tests.conftest import assert_output\n\n\ndef test_big():\n"
        "    assert_output(''.join(random.Random(1).choices(string.ascii_letters, k=100_000)))\n",
    )
    result = ledger.run("suite", env={"CI": "true", "GITHUB_ACTIONS": "true", "TOX_ENV_NAME": "py312-black22-parallel"})
    result.assert_outcomes(failed=1)
    assert (
        "The output is too large for a payload; run this lane locally: "
        "tox run -e py312-black22-parallel -- suite/test_m.py::test_big --inline-snapshot=fix"
    ) in result.stdout.str()


def test_ledger_resolves_cli_doc_paths(ledger: LedgerProject) -> None:
    """CLI documentation collection reads expected output from where the index stores it.

    The item's own labels follow its lines; a label or tree directory of another test follows the whole index, and
    paths stored where they are named, or not named at all, stay as written.
    """
    ledger.write(
        "test_m.py",
        """
        import pytest
        from pathlib import Path

        from tests.conftest import assert_generated_modules_output, assert_output

        EXPECTED = Path(__file__).parent / "data" / "expected"


        @pytest.mark.cli_doc(
            options=["--x"], option_description="x", cli_args=["--x"], golden_output="doc.txt",
            version_outputs={"3.10": "doc.txt", "3.11": "tree", "3.12": "tree/a.py"},
            extra_outputs=[{"title": "t", "path": "doc.txt"}], comparison_output="copy", input_schema="s.json",
            model_outputs={"dataclass": "other.txt", "msgspec": "missing.txt"},
        )
        def test_doc():
            assert_output("doc\\n", EXPECTED / "main" / "doc.txt")


        def test_same():
            assert_output("doc\\n", EXPECTED / "other.txt")


        def test_tree():
            assert_generated_modules_output({("a.py",): "A = 1\\n"}, EXPECTED / "main" / "tree")


        def test_copy():
            assert_generated_modules_output({("a.py",): "A = 1\\n"}, EXPECTED / "main" / "copy")
        """,
    )
    ledger.store("other.txt", "doc\n")
    ledger.run("suite/test_m.py::test_same").assert_outcomes(passed=1)
    ledger.run("suite/test_m.py::test_tree").assert_outcomes(passed=1)
    ledger.run("suite").assert_outcomes(passed=4)
    assert "[test_copy]\n\tmain/copy//*.py\t= main/tree//*.py\n" in ledger.refs()
    config = ledger.pytester.parseconfigure("suite", "--collect-cli-docs")
    session = pytest.Session.from_config(config)
    config.hook.pytest_sessionstart(session=session)
    session.perform_collect()
    kwargs = next(item["marker_kwargs"] for item in config._cli_doc_items if item["node_id"].endswith("test_doc"))
    assert kwargs | {"options": None, "option_description": None, "cli_args": None} == {
        "options": None,
        "option_description": None,
        "cli_args": None,
        "input_schema": "s.json",
        "golden_output": "other.txt",
        "version_outputs": {"3.10": "other.txt", "3.11": "tree", "3.12": "tree/a.py"},
        "extra_outputs": [{"title": "t", "path": "other.txt"}],
        "comparison_output": "main/tree",
        "model_outputs": {"dataclass": "other.txt", "msgspec": "missing.txt"},
    }
    config._ensure_unconfigure()


def test_fix_expected_script(
    ledger: LedgerProject, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The script applies payloads printed in CI logs and shows where an item's expected output lives."""
    from scripts import fix_expected

    ledger.write(
        "test_m.py", "from tests.conftest import assert_output\n\n\ndef test_new():\n    assert_output('x\\n')\n"
    )
    log = ledger.run("suite", env={"CI": "true", "GITHUB_ACTIONS": "true"}).stdout.str()
    payloads = fix_expected.payloads_in(f"{log}\n{log}")
    assert len(payloads) == 1
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).parents[1]))
    assert fix_expected.main(["--root", str(ledger.pytester.path), "--apply", _foreign(payloads[0])]) == 0
    assert ledger.refs() == HEADER + "[test_new]\n\tm/new.txt\n"
    assert fix_expected.main(["--root", str(ledger.pytester.path), "--where", "suite/test_m.py::test_new"]) == 0
    assert "m/new.txt -> suite/data/expected/m/new.txt" in capsys.readouterr().out
    assert fix_expected.main(["--root", str(ledger.pytester.path), "--where", "suite/test_m.py::test_other"]) == 1
    assert (
        "suite/test_m.py::test_other has no expected output recorded. "
        "Run: pytest 'suite/test_m.py::test_other' && git add -A suite"
    ) in capsys.readouterr().out


def test_ledger_fix_selection_shapes(ledger: LedgerProject) -> None:
    """Fix runs that select several or all parameters of a test run exactly those, with nothing to re-run."""
    _shared(ledger)
    ledger.store("m/y.txt", "old\n")
    ledger.run("suite").assert_outcomes(passed=3)
    _shared(ledger, a="new\n", b="new\n")
    both = ledger.run("suite/test_m.py::test_y[a]", "suite/test_m.py::test_y[b]", "--inline-snapshot=fix")
    both.assert_outcomes(passed=2)
    assert "re-ran" not in both.stdout.str()
    assert ledger.files() == {"m/y.txt": "old\n", "m/y~y[a].txt": "new\n"}


def test_ledger_skips_rerun_beyond_budget(ledger: LedgerProject, monkeypatch: pytest.MonkeyPatch) -> None:
    """Siblings are re-run only within the time budget; past it, the fixed test splits off instead."""
    monkeypatch.setattr(shared_conftest, "SIBLING_RERUN_SECONDS", -1)
    _shared(ledger)
    ledger.store("m/y.txt", "old\n")
    ledger.run("suite").assert_outcomes(passed=3)
    _shared(ledger, a="new\n", b="new\n")
    result = ledger.run("suite/test_m.py::test_y[a]", "--inline-snapshot=fix")
    result.assert_outcomes(passed=1, deselected=1)
    assert "expected: test_y[a] split from 2 tests that share m/y.txt" in result.stdout.str()


def test_ledger_unreferenced_file_at_label(ledger: LedgerProject) -> None:
    """A file at a new label that no test records is compared; only a fix run overwrites it."""
    ledger.write(
        "test_m.py", "from tests.conftest import assert_output\n\n\ndef test_a():\n    assert_output('x\\n')\n"
    )
    ledger.store("m/a.txt", "stale\n")
    failing = ledger.run("suite")
    failing.assert_outcomes(failed=1)
    assert "Content mismatch: suite/test_m.py::test_a expects m/a.txt." in failing.stdout.str()
    fixed = ledger.run("suite", "--inline-snapshot=fix")
    fixed.assert_outcomes(passed=1)
    assert "expected: fixed m/a.txt in place (1 tests)" in fixed.stdout.str()
    assert ledger.files() == {"m/a.txt": "x\n"}


def test_ledger_drops_empty_sidecar(ledger: LedgerProject) -> None:
    """When a fix run drops a module's last line, its sidecar and stored file are deleted."""
    ledger.write(
        "test_m.py", "from tests.conftest import assert_output\n\n\ndef test_a():\n    assert_output('x\\n')\n"
    )
    ledger.run("suite").assert_outcomes(passed=1)
    ledger.write("test_m.py", "def test_a():\n    pass\n")
    ledger.run("suite/test_m.py::test_a", "--inline-snapshot=fix").assert_outcomes(passed=1)
    assert not (ledger.suite / "test_m.refs").exists()
    assert ledger.files() == {}
    assert not (ledger.expected / "m").exists()


def test_ledger_groups_many_sharers_in_commands(ledger: LedgerProject, monkeypatch: pytest.MonkeyPatch) -> None:
    """The command for sharers that did not run names their tests, or functions and modules when there are many."""
    functions = "".join(f"\n\ndef test_f{n}():\n    assert_output('old\\n', EXPECTED / 'y.txt')\n" for n in range(6))
    source = (
        "import pytest\nfrom pathlib import Path\n\nfrom tests.conftest import assert_output\n\n"
        'EXPECTED = Path(__file__).parent / "data" / "expected"\nNEW = set()\n\n\n'
        '@pytest.mark.parametrize("case", [str(number) for number in range(8)])\n'
        "def test_y(case):\n"
        '    assert_output("new\\n" if case in NEW else "old\\n", EXPECTED / "y.txt")\n'
        f"{functions}"
    )
    ledger.write("test_m.py", source)
    ledger.store("y.txt", "old\n")
    ledger.run("suite").assert_outcomes(passed=14)
    ledger.write("test_m.py", source.replace("NEW = set()", "NEW = {'0'}"))
    split = ledger.run("suite/test_m.py::test_y[0]", "--inline-snapshot=fix", env={"PYTHONHASHSEED": "0"})
    assert "If they should change too: pytest suite/test_m.py --inline-snapshot=fix" in split.stdout.str()
    ledger.write("test_m.py", source.replace("NEW = set()", "NEW = {'0', '1'}").replace(functions, ""))
    with monkeypatch.context() as context:
        context.setattr(shared_conftest, "SIBLING_RERUN_SECONDS", -1)
        second = ledger.run("suite/test_m.py::test_y[1]", "--inline-snapshot=fix", env={"PYTHONHASHSEED": "0"})
    assert "If they should change too: pytest suite/test_m.py::test_y --inline-snapshot=fix" in second.stdout.str()
    ledger.write("test_m.py", source.replace("NEW = set()", "NEW = {'0', '1', '2'}").replace(functions, ""))
    third = ledger.run("suite/test_m.py::test_y[2]", "--inline-snapshot=fix", env={"PYTHONHASHSEED": "0"})
    assert "expected: re-ran 5 tests that share a changed file" in third.stdout.str()
    assert "If they should change too" not in third.stdout.str()


def test_ledger_class_tests_and_long_names(ledger: LedgerProject) -> None:
    """Class tests and long parameter ids get short derived names, and removed methods drop their lines."""
    long_id = "x" * 150
    ledger.write(
        "test_m.py",
        f"""
        import pytest

        from tests.conftest import assert_output


        class TestGroup:
            def test_one(self):
                assert_output("1\\n")

            @pytest.mark.parametrize("case", [{long_id!r}])
            def test_long(self, case):
                assert_output("2\\n")


        def test_{"n" * 130}():
            assert_output("3\\n")
        """,
    )
    ledger.run("suite").assert_outcomes(passed=3)
    names = sorted(ledger.files())
    assert names[1] == "m/TestGroup.one.txt"
    assert names[0].startswith("m/TestGroup.long[" + "x" * 80 + "~")
    assert names[2].startswith("m/" + "n" * 80 + "~")
    assert all(len(Path(name).name) <= 125 for name in names)
    source = (ledger.suite / "test_m.py").read_text(encoding="utf-8")
    ledger.write("test_m.py", source.replace("def test_one(self):", "def test_two(self):"))
    result = ledger.run("suite/test_m.py::TestGroup::test_two")
    assert "expected: removed the lines of suite/test_m.py::TestGroup::test_one from suite/test_m.refs" in (
        result.stdout.str()
    )


def test_ledger_file_inside_shared_tree(ledger: LedgerProject) -> None:
    """A unit inside another test's stored tree is split off outside that tree, and is not deleted while covered."""
    ledger.write(
        "test_m.py",
        """
        from pathlib import Path

        from tests.conftest import assert_generated_modules_output, assert_output

        EXPECTED = Path(__file__).parent / "data" / "expected"
        X = "x\\n"


        def test_tree():
            assert_generated_modules_output({("x.txt",): "x\\n", ("y.txt",): "y\\n"}, EXPECTED / "t", pattern="*")


        def test_file():
            assert_output(X, EXPECTED / "t" / "x.txt")
        """,
    )
    ledger.run("suite/test_m.py::test_tree").assert_outcomes(passed=1)
    ledger.run("suite/test_m.py::test_file").assert_outcomes(passed=1)
    assert ledger.refs() == HEADER + "[test_file]\n\tt/x.txt\n[test_tree]\n\tt/\n"
    source = (ledger.suite / "test_m.py").read_text(encoding="utf-8")
    ledger.write("test_m.py", source.replace('X = "x\\n"', 'X = "x2\\n"'))
    split = ledger.run("suite/test_m.py::test_file", "--inline-snapshot=fix")
    assert "stored at t~file/x.txt" in split.stdout.str()
    assert ledger.files() == {"t/x.txt": "x\n", "t/y.txt": "y\n", "t~file/x.txt": "x2\n"}
    ledger.write("test_m.py", source.replace('X = "x\\n"', 'X = "x\\n"'))
    ledger.run("suite/test_m.py::test_file", "--inline-snapshot=fix").assert_outcomes(passed=1)
    assert ledger.files() == {"t/x.txt": "x\n", "t/y.txt": "y\n", "t~file/x.txt": "x\n"}
    ledger.write("test_m.py", source.replace('{("x.txt",): "x\\n", ("y.txt",): "y\\n"}', '{("y.txt",): "y\\n"}'))
    ledger.run("suite/test_m.py::test_tree", "--inline-snapshot=fix").assert_outcomes(passed=1)
    assert ledger.files() == {"t/y.txt": "y\n", "t~file/x.txt": "x\n"}
    ledger.run("suite", env=GITHUB).assert_outcomes(passed=2)


def test_ledger_tree_pattern_and_taken_names(ledger: LedgerProject) -> None:
    """A whole tree sharing a pattern tree splits when it gains other files; taken names get a number."""
    ledger.write(
        "test_m.py",
        """
        from pathlib import Path

        from tests.conftest import assert_generated_modules_output

        EXPECTED = Path(__file__).parent / "data" / "expected"
        MODULES = {("a.py",): "A = 1\\n"}


        def test_py():
            assert_generated_modules_output({("a.py",): "A = 1\\n"}, EXPECTED / "pkg")


        def test_whole():
            assert_generated_modules_output(MODULES, EXPECTED / "whole", pattern="*")
        """,
    )
    ledger.run("suite").assert_outcomes(passed=2)
    assert ledger.refs() == HEADER + "[test_py]\n\tpkg//*.py\n[test_whole]\n\twhole/\t= pkg//*.py\n"
    source = (ledger.suite / "test_m.py").read_text(encoding="utf-8")
    without_py = source.replace(
        'def test_py():\n    assert_generated_modules_output({("a.py",): "A = 1\\n"}, EXPECTED / "pkg")\n', ""
    )
    ledger.write(
        "test_m.py",
        without_py.replace(
            'MODULES = {("a.py",): "A = 1\\n"}', 'MODULES = {("a.py",): "A = 1\\n", ("README.md",): "r\\n"}'
        ),
    )
    fixed = ledger.run("suite", "--inline-snapshot=fix")
    fixed.assert_outcomes(passed=1)
    assert ledger.refs() == HEADER + "[test_whole]\n\twhole/\n"
    assert ledger.files() == {"whole/README.md": "r\n", "whole/a.py": "A = 1\n"}


def test_ledger_numbers_taken_derived_names(ledger: LedgerProject) -> None:
    """A derived name that another file already takes gets a number."""
    _shared(ledger)
    ledger.store("m/y.txt", "old\n")
    ledger.run("suite").assert_outcomes(passed=3)
    ledger.store("m/y~z.txt", "someone else's\n")
    _shared(ledger, z="new\n")
    ledger.run("suite/test_m.py::test_z", "--inline-snapshot=fix").assert_outcomes(passed=1)
    assert ledger.files()["m/y~z-2.txt"] == "new\n"


def test_ledger_other_items_and_nested_modules(ledger: LedgerProject) -> None:
    """Items that are not Python tests are left alone, and sidecars in subdirectories are found."""
    ledger.write(
        "conftest.py",
        """
        import pytest


        class CheckItem(pytest.Item):
            def runtest(self):
                pass


        class CheckFile(pytest.File):
            def collect(self):
                yield CheckItem.from_parent(self, name="check")


        def pytest_collect_file(file_path, parent):
            return CheckFile.from_parent(parent, path=file_path) if file_path.suffix == ".check" else None
        """,
    )
    (ledger.suite / "data.check").write_text("", encoding="utf-8")
    (ledger.suite / ".hidden").mkdir()
    (ledger.suite / ".hidden" / "test_h.refs").write_text("", encoding="utf-8")
    ledger.write(
        "sub/test_s.py", "from tests.conftest import assert_output\n\n\ndef test_a():\n    assert_output('x\\n')\n"
    )
    ledger.run("suite").assert_outcomes(passed=2)
    assert ledger.files() == {"sub/s/a.txt": "x\n"}
    index = units.RefsIndex(ledger.suite)
    assert index.sidecars() == index.sidecars() == [ledger.suite / "sub" / "test_s.refs"]
    assert index.stored_path(ledger.expected / "sub" / "s" / "a.txt") == ledger.expected / "sub" / "s" / "a.txt"
    assert index.stored_path(ledger.suite / "input.json") == ledger.suite / "input.json"


def test_ledger_reapplies_observations_and_payloads(ledger: LedgerProject, tmp_path: Path) -> None:
    """Applying observations or a payload again over existing lines changes nothing."""
    ledger.write("test_m.py", ENVIRONMENT_TESTS)
    external, builtin = tmp_path / "external", tmp_path / "builtin"
    builtin_env = {"DATAMODEL_CODE_GENERATOR_TEST_DEFAULT_FORMATTER": "builtin"}
    apply_file = tmp_path / "apply.json"
    apply_file.write_text(json.dumps({"observations": [str(external), str(builtin)]}), encoding="utf-8")
    for _ in range(2):
        ledger.run(f"--expected-observe={external}", "suite")
        ledger.run(f"--expected-observe={builtin}", "suite", env=builtin_env)
        applied = ledger.run(f"--expected-apply={apply_file}")
    assert "expected output" not in applied.stdout.str()
    refs = ledger.refs()
    payload = {
        "facts": _other_platform(shared_conftest.expected_environment_facts()),
        "item": "suite/test_m.py::test_same",
        "label": "m/same.txt",
        "digest": units.unit_digest("m/same.txt", "same\n"),
        "value": "same\n",
    }
    apply_file.write_text(json.dumps({"payloads": [payload]}), encoding="utf-8")
    ledger.run(f"--expected-apply={apply_file}", "suite")
    assert ledger.refs() == refs


def test_ledger_merge_and_payload_rules() -> None:
    """Overrides take the smallest separating predicate; payloads from this environment are refused.

    The platform joins a predicate only when nothing else tells the environments apart.
    """
    py310 = {"python": "3.10", "formatter": "builtin", "black": "26.1.0", "isort": "7.0.0", "platform": "linux"}
    py311 = {**py310, "python": "3.11"}
    black22 = {"python": "3.12", "formatter": "external", "black": "22.1.0", "isort": "7.0.0", "platform": "linux"}
    windows = {**py310, "platform": "win32"}
    assert units._distinguishing([py310], [py311, black22]) == (("python", "3.10"),)
    assert units._distinguishing([py310, py311], [black22]) == (("formatter", "builtin"),)
    assert units._distinguishing([windows], [py310, py311]) == (("platform", "win32"),)
    with pytest.raises(ValueError, match="no predicate separates"):
        units._distinguishing([py310], [py310])
    base = [units.Desired("m/a.txt")]
    payload = {"facts": py310, "item": "t.py::test_a", "label": "m/a.txt", "digest": "d", "value": "v"}
    assert [
        units._fact_value("black", "26.1a1"),
        units._fact_value("isort", "dev"),
        units._fact_value("python", "3.10"),
    ] == [
        "26",
        "dev",
        "3.10",
    ]
    with pytest.raises(ValueError, match="run its test here"):
        units.apply_payload(base, [], payload, py310, exists=True)
    assert units.apply_payload(base, [], {**payload, "facts": windows}, py310, exists=True) == [
        *base,
        units.Desired(
            "m/a.txt",
            predicate=(("formatter", "builtin"), ("platform", "win32"), ("python", "3.10")),
            value="v",
            digest="d",
        ),
    ]
    assert units.apply_payload([], [], payload, py310, exists=True) == [units.Desired("m/a.txt", value="v", digest="d")]
    assert units.apply_payload([], [], payload, py310, exists=False)[0].conditional
    assert units.payload_predicate(py310, {**py310, "black": "26.1a1"}) is None
    assert units.facts_fit({**py310, "black": "26.1.0.post1+local.7", "isort": "7.0.0.dev3"})
    assert not units.facts_fit({**py310, "black": "26.1+a/b"})
    assert units.payload_predicate({**py310, "black": "22.1.0"}, py310) == (("black", "22"),)
    assert units.payload_predicate({**black22, "isort": "5.13.2"}, {**black22, "black": "22.3.0"}) == (("isort", "5"),)
    assert units.payload_predicate(py311, py310) == (("formatter", "builtin"), ("python", "3.11"))
    assert units.payload_predicate(black22, {**black22, "black": "22.3.0", "python": "3.14"}) == (
        ("black", "22"),
        ("formatter", "external"),
        ("isort", "7"),
        ("python", "3.12"),
    )
    py312 = units.Desired("m/a.txt", predicate=(("python", "3.12"),))
    black_payload = {**payload, "facts": black22}
    assert units.apply_payload([py312], [], black_payload, py310, exists=True)[-1].predicate == (
        ("black", "22"),
        ("python", "3.12"),
    )
    stale = {"facts": py311, "item": "t.py::test_a", "stale": True}
    assert units.apply_payload(base, [], stale, py310, exists=True) == [units.Desired("m/a.txt", conditional=True)]
    assert units.apply_payload(base, [], stale, py310, exists=False) == []


GENERATION_TESTS = """
from pathlib import Path

from datamodel_code_generator import InputFileType
from tests.conftest import create_assert_file_content
from tests.main.conftest import run_generate_and_assert, run_generate_file_and_assert, run_main_and_assert

SCHEMA = Path(__file__).parent / "schema.json"
ARGS = ["--disable-timestamp", "--input-file-type", "jsonschema"]


def test_file(tmp_path):
    run_main_and_assert(input_path=SCHEMA, output_path=tmp_path / "model.py", extra_args=ARGS)
    run_main_and_assert(input_path=SCHEMA, output_path=tmp_path / "model.py", extra_args=[*ARGS, "--class-name", "B"])


def test_directory(tmp_path):
    run_main_and_assert(
        input_path=MODULAR, output_path=tmp_path / "out", extra_args=["--disable-timestamp"], transform=str.upper
    )


def test_named(tmp_path):
    run_main_and_assert(
        input_path=SCHEMA, output_path=tmp_path / "model.py", extra_args=ARGS, expected_file="named.py"
    )
    run_main_and_assert(
        input_path=MODULAR, output_path=tmp_path / "out", extra_args=["--disable-timestamp"], expected_file="tree"
    )
    run_main_and_assert(input_path=SCHEMA, output_path=tmp_path / "unwritten.py", extra_args=["--generate-prompt"])


def test_stdout():
    run_main_and_assert(input_path=SCHEMA, extra_args=ARGS)


def test_stdout_capsys(capsys):
    run_main_and_assert(input_path=SCHEMA, extra_args=ARGS, capsys=capsys)


def test_generate():
    run_generate_and_assert(input_=SCHEMA, input_file_type=InputFileType.JsonSchema, disable_timestamp=True)


def test_generate_twice():
    options = {"input_file_type": InputFileType.JsonSchema, "disable_timestamp": True}
    run_generate_and_assert(input_=SCHEMA, **options)
    run_generate_and_assert(input_=SCHEMA, class_name="B", **options)


def test_stdout_twice(capsys):
    run_main_and_assert(input_path=SCHEMA, extra_args=ARGS, capsys=capsys)
    run_main_and_assert(input_path=SCHEMA, extra_args=[*ARGS, "--class-name", "B"], capsys=capsys)


def test_explicit(tmp_path):
    expected = Path(__file__).parent / "data" / "expected"
    options = {"input_file_type": InputFileType.JsonSchema, "disable_timestamp": True}
    run_generate_file_and_assert(
        input_path=SCHEMA,
        output_path=tmp_path / "m.py",
        assert_func=create_assert_file_content(expected),
        expected_file="e.py",
        **options,
    )
    run_generate_and_assert(input_=SCHEMA, expected_file=expected / "e2.py", **options)


def test_generate_file(tmp_path):
    run_generate_file_and_assert(
        input_path=SCHEMA,
        output_path=tmp_path / "m.py",
        input_file_type=InputFileType.JsonSchema,
        disable_timestamp=True,
    )
"""


def test_ledger_generation_helpers_name_outputs(ledger: LedgerProject) -> None:
    """Generation helpers without expected paths compare the output under the test's name, numbering later ones."""
    ledger.write("schema.json", '{"title": "A", "type": "object", "properties": {"x": {"type": "string"}}}')
    modular = Path(__file__).parent / "data" / "openapi" / "modular.yaml"
    ledger.write("test_main.py", GENERATION_TESTS.replace("SCHEMA = ", f"MODULAR = Path({str(modular)!r})\nSCHEMA = "))
    ledger.store("named.py", "stale\n")
    first = ledger.run("suite", "--inline-snapshot=fix")
    first.assert_outcomes(passed=10)
    files = ledger.files()
    assert "tree/models.py" in files
    assert sorted(name for name in files if not name.startswith(("main/directory/", "tree/"))) == [
        "e.py",
        "e2.py",
        "main/file.2.py",
        "main/generate_twice.2.py",
        "main/stdout.stdout.txt",
        "main/stdout_twice.2.stdout.txt",
    ]
    refs = ledger.refs("test_main")
    assert "[test_file]\n\tmain/file.2.py\n\tmain/file.py\t= e.py\n" in refs
    assert "[test_generate]\n\tmain/generate.py\t= e2.py\n" in refs
    assert "[test_generate_file]\n\tmain/generate_file.py\t= e.py\n" in refs
    assert "[test_generate_twice]\n\tmain/generate_twice.2.py\n\tmain/generate_twice.py\t= e2.py\n" in refs
    assert "[test_named]\n\tnamed.py\t= e.py\n\ttree/\n" in refs
    assert "[test_stdout_capsys]\n\tmain/stdout_capsys.stdout.txt\t= main/stdout.stdout.txt\n" in refs
    assert "\tmain/stdout_twice.2.stdout.txt\n\tmain/stdout_twice.stdout.txt\t= main/stdout.stdout.txt\n" in refs
    assert "expected: test_named now has the same output as e.py; it uses that file" in first.stdout.str()
    assert "CLASS PET(BASEMODEL)" in files["main/directory/models.py"]
    ledger.run("suite", "--inline-snapshot=disable").assert_outcomes(passed=10)


def test_ledger_observe_ignores_failures(ledger: LedgerProject, tmp_path: Path) -> None:
    """Observations of failing tests record nothing for them."""
    ledger.write(
        "test_m.py",
        "from tests.conftest import assert_output\n\n\n"
        "def test_a():\n    assert_output('x\\n')\n    raise ValueError\n",
    )
    ledger.run(f"--expected-observe={tmp_path / 'env'}", "suite").assert_outcomes(failed=1)
    apply_file = tmp_path / "apply.json"
    apply_file.write_text(json.dumps({"observations": [str(tmp_path / "env")]}), encoding="utf-8")
    ledger.run(f"--expected-apply={apply_file}")
    assert not ledger.refs()


def test_ledger_merges_and_moves_shared_units(ledger: LedgerProject) -> None:
    """A changed copy equal to another unit uses that unit; a unit left to one label moves to that label's path."""
    _shared(ledger)
    ledger.store("m/y.txt", "old\n")
    ledger.store("other.txt", "new\n")
    ledger.write(
        "test_n.py",
        """
        from pathlib import Path

        from tests.conftest import assert_output

        EXPECTED = Path(__file__).parent / "data" / "expected"
        SAME = "old\\n"


        def test_other():
            assert_output("new\\n", EXPECTED / "other.txt")


        def test_same():
            assert_output(SAME)
        """,
    )
    ledger.run("suite").assert_outcomes(passed=5)
    assert "[test_same]\n\tn/same.txt\t= m/y.txt\n" in ledger.refs("test_n")
    source = (ledger.suite / "test_n.py").read_text(encoding="utf-8").replace('SAME = "old\\n"', 'SAME = "new\\n"')
    ledger.write("test_m.py", "def test_none():\n    pass\n")
    ledger.write("test_n.py", source)
    merged = ledger.run("suite", "--inline-snapshot=fix")
    assert "expected: test_same now has the same output as other.txt; it uses that file" in merged.stdout.str()
    assert "[test_same]\n\tn/same.txt\t= other.txt\n" in ledger.refs("test_n")
    assert "m/y.txt" not in ledger.files()
    without_other = source.replace('def test_other():\n    assert_output("new\\n", EXPECTED / "other.txt")\n', "")
    ledger.store("n/same.txt", "orphan\n")
    ledger.write("test_n.py", without_other)
    ledger.run("suite").assert_outcomes(passed=2)
    assert "[test_same]\n\tn/same.txt\t= other.txt\n" in ledger.refs("test_n")
    (ledger.expected / "n" / "same.txt").unlink()
    ledger.write("test_n.py", without_other + "\n\ndef test_more():\n    assert_output('more\\n')\n")
    ledger.run("suite").assert_outcomes(passed=3)
    assert "[test_same]\n\tn/same.txt\n" in ledger.refs("test_n")
    assert ledger.files()["n/same.txt"] == "new\n"
    assert "other.txt" not in ledger.files()


def test_fix_expected_from_ci_and_all_environments(
    ledger: LedgerProject, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Payloads are collected from a pull request's failed runs, and observations from each lane's environment.

    gh and tox are external tools: the fake answers gh's queries with a CI log and creates tox environments whose
    Python is this one; pytest itself runs for real.
    """
    import subprocess

    from scripts import fix_expected

    ledger.write(
        "test_m.py", "from tests.conftest import assert_output\n\n\ndef test_new():\n    assert_output('x\\n')\n"
    )
    failure = ledger.run("suite", env={"CI": "true", "GITHUB_ACTIONS": "true"}).stdout.str()
    log = f"dcg-expected-fix: {_foreign(fix_expected.payloads_in(failure)[0])}\n"
    in_progress = "run 9 is still in progress; logs will be available when it is complete"
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).parents[1]))
    real_run = subprocess.run
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(list(command))
        result: tuple[int, str] | None = None
        match command:
            case [_, "pr", "view", *_]:
                result = (0, "abc123\n")
            case [_, "run", "list", *_]:
                result = (
                    0,
                    json.dumps([{"databaseId": 7, "conclusion": "failure"}, {"databaseId": 8, "conclusion": "ok"}]),
                )
            case [_, "run", "view", "9", "--log-failed"]:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr=f"{in_progress}\n")
            case [_, "run", "view", run_id, "--log-failed"]:
                result = (0, log if run_id == "7" else "no payloads here\n")
            case ["tox", "run", "-q", "-e", environment, "--notest"]:
                result = (int(environment == "pydantic212"), "")
            case ["tox", "run", "-e", _]:
                addopts = str(kwargs["env"]["PYTEST_ADDOPTS"])  # type: ignore[index]
                command = [sys.executable, "-m", "pytest", *addopts.split(), "suite"]
            case _:
                command = [sys.executable, *command[1:]]
        if result is None:
            return real_run(command, **kwargs)
        return subprocess.CompletedProcess(command, result[0], stdout=result[1])

    tox_command = fix_expected.tox_command
    monkeypatch.setattr(fix_expected.subprocess, "run", fake_run)
    monkeypatch.setattr(fix_expected, "tox_command", lambda: ["tox"])
    root = ["--root", str(ledger.pytester.path)]
    assert fix_expected.main([*root, "--from-ci", "#42"]) == 0
    assert ledger.refs() == HEADER + "[test_new]\n\tm/new.txt\n"
    assert any(command[1:4] == ["run", "view", "7"] for command in commands)
    assert fix_expected.main([*root, "--from-ci", "99"]) == 1
    assert "No dcg-expected-fix payloads to apply." in capsys.readouterr().out
    assert fix_expected.main([*root, "--from-ci", "9"]) == 2
    assert capsys.readouterr().out == (
        f"gh could not read the failed-job logs of 9: {in_progress}\n"
        "If the run is still in progress, wait for it to finish and run this command again, or paste the "
        "dcg-expected-fix: lines of its log into: python scripts/fix_expected.py --apply '<payload>' ...\n"
    )
    assert ledger.refs() == HEADER + "[test_new]\n\tm/new.txt\n"
    output = ledger.pytester.path / "observed"
    environments = ["--environment", "py314", "--environment", "pydantic214", "--environment", "pydantic212"]
    assert (
        fix_expected.main([
            *root,
            "--all-environments",
            *environments,
            "--observe-only",
            "--output",
            str(output),
            "--workers",
            "0",
            "suite",
        ])
        == 0
    )
    assert "pydantic212: tox could not create pydantic212; skipped" in capsys.readouterr().out
    assert sorted(path.name for path in output.iterdir()) == ["py314", "pydantic214"]
    lane = ["--environment", "pydantic214", "--observe-only", "--output", str(output / "lane")]
    assert fix_expected.main([*root, "--all-environments", *lane]) == 0
    assert list((output / "lane" / "pydantic214").glob("observed-*.jsonl"))
    (ledger.suite / "test_m.refs").unlink()
    assert fix_expected.main([*root, "--observations", str(output / "py314")]) == 0
    assert ledger.refs() == HEADER + "[test_new]\n\tm/new.txt\n"
    (ledger.suite / "test_m.refs").unlink()
    assert (
        fix_expected.main([
            *root,
            "--all-environments",
            "--environment",
            "py314",
            "--output",
            str(output),
            "--workers",
            "0",
            "suite",
        ])
        == 0
    )
    assert ledger.refs() == HEADER + "[test_new]\n\tm/new.txt\n"
    monkeypatch.setattr(fix_expected.shutil, "which", lambda name: f"/bin/{name}" if name == "tox" else None)
    assert tox_command() == ["/bin/tox"]
    monkeypatch.setattr(fix_expected.shutil, "which", lambda _name: None)
    assert tox_command()[:3] == ["uvx", "--from", "tox>=4.32"]


def test_fix_expected_drops_tests_no_environment_collects(
    ledger: LedgerProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run over every environment drops the lines of tests none collects; ``--drop-uncollected`` does only that.

    tox is an external tool: the fake creates environments whose Python is this one, and pytest runs for real.
    """
    import subprocess

    from scripts import fix_expected

    ledger.write("test_m.py", UNCOLLECTED_TESTS)
    ledger.run("suite").assert_outcomes(passed=1)
    recorded = ledger.refs()
    gone = [
        units.RefLine("test_case", "gone", f"m/gone@{python}.txt", conditional=True) for python in ("py310", "py314")
    ]

    def record_gone() -> None:
        for line in gone:
            ledger.store(line.label, "gone\n")
        lines = [*units.parse_refs(recorded)[0], *gone]
        (ledger.suite / "test_m.refs").write_text(units.format_refs(lines), encoding="utf-8")

    real_run = subprocess.run

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:2] == ["tox", "run"]:
            return subprocess.CompletedProcess(command, 0, stdout="")
        return real_run([sys.executable, *command[1:]], **kwargs)

    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).parents[1]))
    monkeypatch.setattr(fix_expected.subprocess, "run", fake_run)
    monkeypatch.setattr(fix_expected, "tox_command", lambda: ["tox"])
    monkeypatch.setattr(fix_expected, "ENVIRONMENTS", {"py314": fix_expected.ENVIRONMENTS["py314"]})
    root, output = ["--root", str(ledger.pytester.path)], ledger.pytester.path / "observed"
    record_gone()
    assert fix_expected.main([*root, "--all-environments", "--workers", "0", "suite"]) == 0
    assert (ledger.refs(), sorted(ledger.files())) == (recorded, ["m/case[kept].txt"])
    record_gone()
    observe = ["--all-environments", "--observe-only", "--output", str(output), "--workers", "0", "suite"]
    assert fix_expected.main([*root, *observe]) == 0
    assert fix_expected.main([*root, "--drop-uncollected", str(output / "py314")]) == 0
    assert (ledger.refs(), sorted(ledger.files())) == (recorded, ["m/case[kept].txt"])


ONE_TEST = "from tests.conftest import assert_output\n\nOUT = 'x\\n'\n\n\ndef test_a():\n    assert_output(OUT)\n"
GITHUB = {"CI": "true", "GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "7"}
LANE = {"python": "3.10", "formatter": "external", "black": "22.1.0", "isort": "5.13.2", "platform": "linux"}


def _fix_expected(ledger: LedgerProject, monkeypatch: pytest.MonkeyPatch, *arguments: str) -> int:
    """Run scripts/fix_expected.py in the project, the way a contributor pastes it."""
    from scripts import fix_expected

    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).parents[1]))
    return fix_expected.main(["--root", str(ledger.pytester.path), *arguments])


def _message(output: str, start: str) -> list[str]:
    """Return the lines of the first failure message that starts with ``start``, up to its payload line."""
    lines = output.splitlines()
    first = next(index for index, line in enumerate(lines) if line.startswith(start))
    return lines[first : next(index for index in range(first, len(lines)) if lines[index].startswith("dcg-"))]


@pytest.mark.parametrize("flag", ["create", "fix"])
def test_ledger_ci_never_writes_even_with_explicit_flags(ledger: LedgerProject, flag: str) -> None:
    """CI neither creates nor fixes expected output or inline snapshots, even when the flag is passed explicitly."""
    ledger.write(
        "test_m.py",
        """
        from inline_snapshot import snapshot

        from tests.conftest import assert_output


        def test_a():
            assert_output("x\\n")


        def test_snapshot():
            assert 1 == snapshot(2)
        """,
    )
    source = (ledger.suite / "test_m.py").read_text(encoding="utf-8")
    result = ledger.run("suite", f"--inline-snapshot={flag}", env=GITHUB)
    assert result.parseoutcomes()["failed"] == 2
    assert "dcg-expected-fix: " in result.stdout.str()
    assert (ledger.files(), ledger.refs()) == ({}, "")
    assert (ledger.suite / "test_m.py").read_text(encoding="utf-8") == source
    assert shared_conftest._ci_environment() == shared_conftest.CiEnvironment(None, False, None)


def test_ledger_ci_mismatch_leads_with_the_lane_fix(ledger: LedgerProject) -> None:
    """A CI lane's content mismatch names its environment and puts the payload command first; colour follows --color."""
    ledger.write("test_m.py", ONE_TEST)
    ledger.run("suite").assert_outcomes(passed=1)
    ledger.write("test_m.py", ONE_TEST.replace("OUT = 'x\\n'", "OUT = 'y\\n'"))
    result = ledger.run("suite", "--color=no", env=GITHUB)
    result.assert_outcomes(failed=1)
    output = result.stdout.str()
    facts = shared_conftest._describe_facts(shared_conftest.expected_environment_facts())
    lines = _message(output, "Content mismatch")
    assert lines[:2] == [
        "Content mismatch: suite/test_m.py::test_a expects m/a.txt.",
        f"This environment ({facts}) produces different output than the stored one.",
    ]
    assert lines[2].startswith(
        "Fix, if pytest suite/test_m.py::test_a passes on your machine (adds an override for this environment only; "
        "other environments keep m/a.txt): python scripts/fix_expected.py --apply '"
    )
    assert lines[2].endswith("' && git add -A suite  (or --from-ci 7)")
    assert lines[3:] == [
        (
            "Or, if the new output is intended everywhere: pytest suite/test_m.py::test_a --inline-snapshot=fix "
            "&& git add -A suite"
        ),
        "Or, to restore the stored file: git checkout HEAD -- suite/data/expected/m/a.txt",
    ]
    assert "-x" in output
    assert "\x1b[" not in output
    assert "\x1b[" in ledger.run("suite", "--color=yes").stdout.str()
    python = shared_conftest.expected_environment_facts()["python"]
    override = f"m/a@python{python}.txt"
    ledger.store(override, "z\n")
    (ledger.suite / "test_m.refs").write_text(
        f"{HEADER}[test_a]\n\tm/a.txt\n\tm/a.txt\t@python={python}\t= {override}\n", encoding="utf-8"
    )
    lines = _message(ledger.run("suite", "--color=no", env=GITHUB).stdout.str(), "Content mismatch")
    assert lines[0] == f"Content mismatch: suite/test_m.py::test_a expects {override}."
    assert lines[2].startswith(
        "Fix, if pytest suite/test_m.py::test_a passes on your machine (adds an override for this environment only; "
        "other environments keep their stored files): python scripts/fix_expected.py --apply '"
    )


def _other_platform(facts: dict[str, str]) -> dict[str, str]:
    """Return an environment's facts on another platform, as a Windows lane with the same tools reports them."""
    return {**facts, "platform": "linux" if facts["platform"] == "win32" else "win32"}


def _foreign(line: str) -> str:
    """Return a payload CI printed here as the lane on the other platform prints it."""
    payload = units.decode_payload(line)
    return units.encode_payload({**payload, "facts": _other_platform(payload["facts"])}, 1 << 20) or ""


def _encoded(fields: object) -> str:
    return base64.b64encode(zlib.compress(json.dumps(fields).encode())).decode()


def _payload(**fields: object) -> dict[str, object]:
    payload: dict[str, object] = {"facts": LANE, "item": "suite/test_m.py::test_a", "label": "m/a.txt", "value": "y\n"}
    payload |= fields
    if isinstance(payload.get("label"), str) and "value" in payload and "digest" not in payload:
        payload["digest"] = units.unit_digest(str(payload["label"]), payload["value"])
    return {key: value for key, value in payload.items() if value is not None}


BOMB = base64.b64encode(zlib.compress(b'{"value": "' + b"a" * (5 * 1024 * 1024) + b'"}')).decode()


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        pytest.param(_encoded(_payload(label="../../outside.txt")), "is not a relative path below", id="up"),
        pytest.param(_encoded(_payload(label="/etc/a.txt")), "is not a relative path below", id="absolute-label"),
        pytest.param(_encoded(_payload(item="suite/../suite/test_m.py::test_a")), "is not an item", id="up-item"),
        pytest.param(_encoded(_payload(item="/suite/test_m.py::test_a")), "is not an item", id="absolute-item"),
        pytest.param(_encoded(_payload(item="suite/helper.py::test_a")), "is not an item", id="not-a-test"),
        pytest.param(_encoded(_payload(item="suite/test_gone.py::test_a")), "is not an item", id="no-module"),
        pytest.param(_encoded(_payload(item="suite/test_m.py::test_a[\n]")), "is not an item", id="control"),
        pytest.param(_encoded(_payload(digest="0")), "its digest does not match its value", id="digest"),
        pytest.param(_encoded(_payload(label="m/a.json", value={"k": 1}, digest="0")), "its digest", id="json"),
        pytest.param(_encoded(_payload(value=1)), "its value does not fit its label", id="text"),
        pytest.param(_encoded(_payload(label="m/t/", value={"../a.py": ""})), "does not fit its label", id="tree"),
        pytest.param(_encoded(_payload(label=5, digest="0")), "it names no label", id="label-type"),
        pytest.param(_encoded(_payload(path="x")), "it is not a dcg-expected-fix payload", id="keys"),
        pytest.param(_encoded(_payload(item="suite/./test_m.py::test_a")), "is not an item", id="dot-item"),
        pytest.param(_encoded(_payload(item="suite/test_M.py::test_a")), "is not an item", id="case-variant"),
        pytest.param(_encoded(_payload(item="SUITE/test_m.py::test_a")), "is not an item", id="case-variant-dir"),
        pytest.param(_encoded(_payload(item="suite/data/test_d.py::test_a")), "is not an item", id="data-module"),
        pytest.param(_encoded(_payload(item="suite/test_m.py/test_m.py::test_a")), "is not an item", id="file-as-dir"),
        pytest.param(_encoded(_payload(item="suite/test_m.py::test_a[\u2029]")), "is not an item", id="separator-item"),
        pytest.param(_encoded(_payload(item=f"suite/test_{'x' * 250}.py::test_a")), "is not an item", id="long-item"),
        pytest.param(_encoded(_payload(label="m/a\u2028b.txt")), "is not a relative path below", id="separator"),
        pytest.param(_encoded(_payload(label="m/a\x85.txt")), "is not a relative path below", id="next-line"),
        pytest.param(_encoded(_payload(label="m/a\x1e.txt")), "is not a relative path below", id="record-separator"),
        pytest.param(_encoded(_payload(label="m/a\x7f.txt")), "is not a relative path below", id="delete"),
        pytest.param(_encoded(_payload(label=f"m/{'x' * 252}.txt")), "is not a relative path below", id="long-label"),
        pytest.param(
            _encoded(_payload(item="suite//test_m.py::test_a", label=None, value=None, stale=True)),
            "is not an item",
            id="stale-empty-part",
        ),
        pytest.param(_encoded(_payload(facts={"python": "3.10"})), "are malformed", id="facts"),
        pytest.param(_encoded(_payload(facts={**LANE, "python": "3.10/../../x"})), "are malformed", id="python"),
        pytest.param(_encoded(_payload(facts={**LANE, "formatter": "ruff"})), "are malformed", id="formatter"),
        pytest.param(_encoded(_payload(facts={**LANE, "black": "22/../x"})), "are malformed", id="black"),
        pytest.param(_encoded(_payload(facts={**LANE, "platform": "nt"})), "are malformed", id="platform"),
        pytest.param(_encoded(_payload(facts={**LANE, "os": "nt"})), "are malformed", id="unknown-fact"),
        pytest.param(_encoded(_payload(value=None, digest=None, missing="yes")), "are malformed", id="flags"),
        pytest.param(_encoded([1]), "it is not a dcg-expected-fix payload", id="list"),
        pytest.param("!!", "it is not a dcg-expected-fix payload (", id="base64"),
        pytest.param(BOMB, "decodes to more than", id="bomb"),
        pytest.param(_encoded(_payload())[:-12], "it is truncated", id="truncated"),
    ],
)
def test_fix_expected_rejects_untrusted_payloads(
    ledger: LedgerProject,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    text: str,
    reason: str,
) -> None:
    """A payload naming anything but this suite's test items and expected labels writes nothing and exits 2.

    Items name modules as spelled on disk, outside the data directory; names hold no line separators.
    """
    ledger.write("test_m.py", ONE_TEST)
    ledger.write("helper.py", "def test_a():\n    pass\n")
    ledger.write("data/test_d.py", "")
    ledger.run("suite").assert_outcomes(passed=1)
    before = (ledger.files(), ledger.refs())
    capsys.readouterr()
    assert _fix_expected(ledger, monkeypatch, "--apply", text) == 2
    output = capsys.readouterr().out
    assert output.startswith("Rejected; nothing was written:\npayload 1: ")
    assert reason in output
    assert (ledger.files(), ledger.refs()) == before


def test_ledger_apply_session_checks_its_input(ledger: LedgerProject, tmp_path: Path) -> None:
    """An apply session refuses untrusted payloads and observations, and runs only in one process."""
    ledger.write("test_m.py", ONE_TEST)
    apply_file = tmp_path / "apply.json"
    apply_file.write_text(json.dumps({"payloads": [_payload(label="../x.txt")]}), encoding="utf-8")
    rejected = ledger.run(f"--expected-apply={apply_file}", "suite")
    assert rejected.ret == pytest.ExitCode.USAGE_ERROR
    assert (
        "--expected-apply rejected its input; nothing was written:\n"
        "payload 1: '../x.txt' is not a relative path below tests/data/expected"
    ) in rejected.stderr.str()
    observed = tmp_path / "observed"
    observed.mkdir()
    entry = {"item": "../test_x.py::test_a", "outcome": "passed", "compared": []}
    (observed / "observed-main-1.jsonl").write_text(
        f"{json.dumps({'facts': LANE, 'collected': []})}\n{json.dumps(entry)}\n", encoding="utf-8"
    )
    apply_file.write_text(json.dumps({"observations": [str(observed)]}), encoding="utf-8")
    assert "'../test_x.py::test_a' is not an item" in ledger.run(f"--expected-apply={apply_file}").stderr.str()
    entry["item"] = "suite/test_m.py::test_a"
    (observed / "observed-main-1.jsonl").write_text(
        f"{json.dumps({'facts': {**LANE, 'python': '../3'}, 'collected': []})}\n{json.dumps(entry)}\n",
        encoding="utf-8",
    )
    assert f"{observed}: its environment is malformed" in ledger.run(f"--expected-apply={apply_file}").stderr.str()
    entry["compared"] = [["../outside.txt", "0" * 64, "../outside.txt", None]]
    (observed / "observed-main-1.jsonl").write_text(
        f"{json.dumps({'facts': LANE, 'collected': []})}\n{json.dumps(entry)}\n", encoding="utf-8"
    )
    assert "suite/test_m.py::test_a: '../outside.txt' is not a relative path below tests/data/expected" in (
        ledger.run(f"--expected-apply={apply_file}").stderr.str()
    )
    apply_file.write_text(json.dumps({"payloads": [_payload()]}), encoding="utf-8")
    parallel = ledger.run(f"--expected-apply={apply_file}", "-n", "2", "suite")
    assert "--expected-apply writes expected files and runs in one process; run it without -n." in (
        parallel.stderr.str()
    )
    assert ledger.files() == {}


def test_ledger_merges_equal_outputs_fixed_in_one_run(ledger: LedgerProject) -> None:
    """Two stored files fixed to the same content in one run become one file; the other test points at it."""
    source = (
        "from pathlib import Path\n\nfrom tests.conftest import assert_output\n\n"
        'EXPECTED = Path(__file__).parent / "data" / "expected"\nA, B = "1\\n", "2\\n"\n\n\n'
        'def test_a():\n    assert_output(A, EXPECTED / "a.txt")\n\n\n'
        'def test_b():\n    assert_output(B, EXPECTED / "b.txt")\n'
    )
    ledger.write("test_m.py", source)
    ledger.run("suite").assert_outcomes(passed=2)
    ledger.write("test_m.py", source.replace('A, B = "1\\n", "2\\n"', 'A, B = "3\\n", "3\\n"'))
    fixed = ledger.run("suite", "--inline-snapshot=fix")
    fixed.assert_outcomes(passed=2)
    assert ledger.files() == {"a.txt": "3\n"}
    assert ledger.refs() == HEADER + "[test_a]\n\ta.txt\n[test_b]\n\tb.txt\t= a.txt\n"
    assert "expected: test_b now has the same output as a.txt; it uses that file" in fixed.stdout.str()
    assert "expected: deleted b.txt (no test uses it)" in fixed.stdout.str()


def test_ledger_merges_hand_copied_expected_file(ledger: LedgerProject) -> None:
    """A hand-written copy of a stored unit fails CI, and the local run records the test on the original instead."""
    _shared(ledger)
    ledger.store("m/y.txt", "old\n")
    ledger.run("suite").assert_outcomes(passed=3)
    ledger.store("copy.txt", "old\n")
    ledger.write(
        "test_n.py",
        "from pathlib import Path\n\nfrom tests.conftest import assert_output\n\n\ndef test_copy():\n"
        '    assert_output("old\\n", Path(__file__).parent / "data" / "expected" / "copy.txt")\n',
    )
    ci = ledger.run("suite/test_n.py", env=GITHUB)
    ci.assert_outcomes(failed=1)
    assert _message(ci.stdout.str(), "suite/test_n.refs has no line") == [
        "suite/test_n.refs has no line for suite/test_n.py::test_copy.",
        "Fix: pytest suite/test_n.py::test_copy && git add -A suite",
    ]
    local = ledger.run("suite/test_n.py")
    local.assert_outcomes(passed=1)
    assert "expected: test_copy has the same output as m/y.txt; no new file" in local.stdout.str()
    assert "expected: deleted copy.txt (no test uses it)" in local.stdout.str()
    assert ledger.refs("test_n") == HEADER + "[test_copy]\n\tcopy.txt\t= m/y.txt\n"
    assert ledger.files() == {"m/y.txt": "old\n"}


def test_ledger_leaves_equal_units_outside_the_run(ledger: LedgerProject) -> None:
    """A test first recorded on an existing file equal to two older duplicates moves to one; they stay as they are."""
    two = (
        "from tests.conftest import assert_output\n\n\n"
        "def test_a():\n    assert_output('same\\n')\n\n\n"
        "def test_b():\n    assert_output('same\\n', name='b')\n"
    )
    ledger.write("test_m.py", two)
    ledger.store("m/a.txt", "same\n")
    ledger.store("m/b.b.txt", "same\n")
    (ledger.suite / "test_m.refs").write_text(HEADER + "[test_a]\n\tm/a.txt\n[test_b]\n\tm/b.b.txt\n", encoding="utf-8")
    before = ledger.refs()
    ledger.store("n/c.txt", "same\n")
    ledger.write(
        "test_n.py", "from tests.conftest import assert_output\n\n\ndef test_c():\n    assert_output('same\\n')\n"
    )
    ledger.run("suite/test_n.py").assert_outcomes(passed=1)
    assert ledger.refs() == before
    assert ledger.files() == {"m/a.txt": "same\n", "m/b.b.txt": "same\n"}
    assert ledger.refs("test_n") == HEADER + "[test_c]\n\tn/c.txt\t= m/a.txt\n"


def test_ledger_derived_names_do_not_depend_on_run_order(ledger: LedgerProject) -> None:
    """The module prefix is dropped per class or module, whichever test ran first."""
    ledger.write(
        "test_m.py",
        """
        from tests.conftest import assert_output


        def test_m_a():
            assert_output("1\\n")


        def test_a():
            assert_output("2\\n")


        class TestK:
            def test_m_a(self):
                assert_output("3\\n")


        class TestChild(TestK):
            pass
        """,
    )
    ledger.run("suite/test_m.py::TestK").assert_outcomes(passed=1)
    ledger.run("suite").assert_outcomes(passed=4)
    assert ledger.files() == {"m/TestK.a.txt": "3\n", "m/a.txt": "2\n", "m/m_a.txt": "1\n"}
    again = ledger.run("suite")
    again.assert_outcomes(passed=4)
    assert "expected output" not in again.stdout.str()
    assert "[TestChild::test_m_a]\n\tm/TestChild.a.txt\t= m/TestK.a.txt\n" in ledger.refs()


def test_ledger_canonicalises_merged_sidecars(ledger: LedgerProject) -> None:
    """Local runs rewrite a merged sidecar in the form pytest writes; CI fails it with a fast command that does."""
    ledger.write("test_m.py", ONE_TEST + "\n\ndef test_b():\n    assert_output('y\\n')\n")
    ledger.run("suite").assert_outcomes(passed=2)
    canonical = ledger.refs()
    (ledger.suite / "test_m.refs").write_text(
        HEADER + "[test_b]\n\tm/b.txt\n[test_a]\n\tm/a.txt\n[test_b]\n\tm/b.txt\n", encoding="utf-8"
    )
    ci = ledger.run("suite", env=GITHUB)
    ci.assert_outcomes(failed=1, passed=2)
    assert (
        "suite/test_m.refs is not in the form pytest writes (line 3: lines are not sorted, or the file differs from "
        "what pytest writes); a merge may have joined both sides.\n"
        "Fix: pytest suite/test_m.py --collect-only -q && git add -A suite"
    ) in ci.stdout.str()
    repaired = ledger.run("suite/test_m.py", "--collect-only", "-q")
    assert repaired.ret == 0
    assert "expected: rewrote suite/test_m.refs in the form pytest writes" in repaired.stdout.str()
    assert ledger.refs() == canonical
    ledger.store("m/other.txt", "other\n")
    (ledger.suite / "test_m.refs").write_text(
        canonical.replace("\tm/a.txt\n", "\tm/a.txt\n\tm/a.txt\t= m/other.txt\n"), encoding="utf-8"
    )
    conflict = ledger.run("suite", env=GITHUB)
    conflict.assert_outcomes(failed=1, passed=2)
    assert "suite/test_m.refs has lines pytest would not have written:\n" in conflict.stdout.str()
    assert "  test_a lists one label twice (a merge kept both sides)\n" in conflict.stdout.str()
    assert "Fix: pytest suite/test_m.py::test_a && git add -A suite" in (conflict.stdout.str())
    ledger.run("suite/test_m.py::test_a").assert_outcomes(passed=1)
    assert ledger.refs() == canonical
    assert "m/other.txt" not in ledger.files()


@pytest.mark.parametrize(
    "edit",
    [
        pytest.param(lambda text: text.removeprefix(HEADER), id="removed"),
        pytest.param(lambda text: text.replace("Do not edit by hand", "Edit freely"), id="edited"),
    ],
)
def test_ledger_sidecar_header(ledger: LedgerProject, edit: Callable[[str], str]) -> None:
    """Sidecars start with a header saying pytest writes them; local runs restore it, CI fails with the command."""
    ledger.write("test_m.py", ONE_TEST)
    ledger.run("suite").assert_outcomes(passed=1)
    canonical = ledger.refs()
    assert canonical.splitlines() == [
        "# Generated by pytest. Do not edit by hand: run the tests instead (pytest <test>), then git add -A tests.",
        (
            "# Each line records the expected output a test compares; to accept changed output add "
            "--inline-snapshot=fix (see AGENTS.md)."
        ),
        "[test_a]",
        "\tm/a.txt",
    ]
    (ledger.suite / "test_m.refs").write_text(edit(canonical), encoding="utf-8")
    ci = ledger.run("suite", env=GITHUB)
    ci.assert_outcomes(failed=1, passed=1)
    assert (
        "suite/test_m.refs is not in the form pytest writes (line 1: lines are not sorted, or the file differs from "
        "what pytest writes); a merge may have joined both sides.\n"
        "Fix: pytest suite/test_m.py --collect-only -q && git add -A suite"
    ) in ci.stdout.str()
    assert ledger.run("suite/test_m.py", "--collect-only", "-q").ret == 0
    assert ledger.refs() == canonical
    ledger.run("suite", env=GITHUB).assert_outcomes(passed=1)
    (ledger.suite / "test_m.refs").write_text(edit(canonical), encoding="utf-8")
    local = ledger.run("suite")
    local.assert_outcomes(passed=1)
    assert "expected: rewrote suite/test_m.refs in the form pytest writes" in local.stdout.str()
    assert ledger.refs() == canonical


SHARING = """
from pathlib import Path

from tests.conftest import assert_output

EXPECTED = Path(__file__).parent / "data" / "expected" / "m"
OUT = "same\\n"


def test_s():
    assert_output(OUT, EXPECTED / "s.txt")


def test_w1():
    assert_output("same\\n", EXPECTED / "w1.txt")


def test_w2():
    assert_output("same\\n", EXPECTED / "w2.txt")
"""


def test_ledger_storage_shared_by_other_labels_passes_ci(ledger: LedgerProject) -> None:
    """Storage left to other labels, by a partial fix or a deleted test, is what pytest writes; CI passes."""
    ledger.write("test_m.py", SHARING)
    ledger.run("suite").assert_outcomes(passed=3)
    assert (
        ledger.refs()
        == HEADER + "[test_s]\n\tm/s.txt\n[test_w1]\n\tm/w1.txt\t= m/s.txt\n[test_w2]\n\tm/w2.txt\t= m/s.txt\n"
    )
    ledger.write("test_m.py", SHARING.replace('OUT = "same\\n"', 'OUT = "new\\n"'))
    ledger.run("suite/test_m.py::test_s", "--inline-snapshot=fix").assert_outcomes(passed=1)
    assert ledger.files() == {"m/s.txt": "same\n", "m/s~s.txt": "new\n"}
    ledger.run("suite", env=GITHUB).assert_outcomes(passed=3)
    ledger.write("test_m.py", SHARING.replace('def test_s():\n    assert_output(OUT, EXPECTED / "s.txt")\n', ""))
    ledger.run("suite").assert_outcomes(passed=2)
    assert ledger.refs() == HEADER + "[test_w1]\n\tm/w1.txt\t= m/s.txt\n[test_w2]\n\tm/w2.txt\t= m/s.txt\n"
    ledger.run("suite", env=GITHUB).assert_outcomes(passed=2)


def test_ledger_case_only_label_difference_passes_ci(ledger: LedgerProject) -> None:
    """A label differing from another only by case gets a derived name, which CI accepts on every file system."""
    ledger.write(
        "test_m.py",
        """
        from pathlib import Path

        from tests.conftest import assert_output

        EXPECTED = Path(__file__).parent / "data" / "expected" / "m"


        def test_a():
            assert_output("upper\\n", EXPECTED / "A.txt")


        def test_b():
            assert_output("lower\\n", EXPECTED / "a.txt")
        """,
    )
    ledger.run("suite").assert_outcomes(passed=2)
    assert ledger.refs() == HEADER + "[test_a]\n\tm/A.txt\n[test_b]\n\tm/a.txt\t= m/a~b.txt\n"
    ledger.run("suite", env=GITHUB).assert_outcomes(passed=2)
    ledger.run("suite").assert_outcomes(passed=2)
    assert ledger.files() == {"m/A.txt": "upper\n", "m/a~b.txt": "lower\n"}


def test_ledger_fix_over_a_module_keeps_lines_of_other_environments(ledger: LedgerProject) -> None:
    """A fix run over a module keeps the lines of a test that compares nothing here; it still fails that test."""
    other = "\n\ndef test_b():\n    pass\n"
    ledger.write("test_m.py", ONE_TEST + other)
    ledger.run("suite").assert_outcomes(passed=2)
    ledger.store("m/b.txt", "other environments\n")
    refs = HEADER + "[test_a]\n\tm/a.txt\n[test_b]\n\tm/b.txt\t?\n"
    (ledger.suite / "test_m.refs").write_text(refs, encoding="utf-8")
    ledger.write("test_m.py", ONE_TEST.replace("OUT = 'x\\n'", "OUT = 'y\\n'") + other)
    fixed = ledger.run("suite", "--inline-snapshot=fix")
    fixed.assert_outcomes(passed=1, failed=1)
    assert "suite/test_m.py::test_b passed without comparing any expected output" in fixed.stdout.str()
    assert (ledger.refs(), ledger.files()) == (refs, {"m/a.txt": "y\n", "m/b.txt": "other environments\n"})


def test_ledger_payload_from_this_environment_is_refused(
    ledger: LedgerProject, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A payload with this environment's facts is refused; the same tools on another platform add an override."""
    from scripts import fix_expected

    ledger.write("test_m.py", ONE_TEST)
    ledger.run("suite").assert_outcomes(passed=1)
    ledger.write("test_m.py", ONE_TEST.replace("OUT = 'x\\n'", "OUT = 'y\\n'"))
    payload = fix_expected.payloads_in(ledger.run("suite", env=GITHUB).stdout.str())[0]
    ledger.write("test_m.py", ONE_TEST)
    capfd.readouterr()
    assert _fix_expected(ledger, monkeypatch, "--apply", payload) == pytest.ExitCode.USAGE_ERROR
    assert (
        "payload 1: it comes from this environment, so there is no other environment to add an override for; "
        "this environment is yours, run: pytest suite/test_m.py::test_a --inline-snapshot=fix && git add -A suite"
    ) in capfd.readouterr().err
    apply_file = tmp_path / "apply.json"
    local = shared_conftest.expected_environment_facts()
    apply_file.write_text(json.dumps({"payloads": [_payload(facts=local, label=None, value=None, stale=True)]}))
    assert "this environment is yours, run: pytest suite/test_m.py --inline-snapshot=fix" in (
        ledger.run(f"--expected-apply={apply_file}", "suite").stderr.str()
    )
    assert (ledger.refs(), ledger.files()) == (HEADER + "[test_a]\n\tm/a.txt\n", {"m/a.txt": "x\n"})
    skipping = json.loads(apply_file.read_text()) | {"skip_rejected": True}
    apply_file.write_text(json.dumps(skipping))
    skipped = ledger.run(f"--expected-apply={apply_file}", "suite").stdout.str()
    assert "expected: skipped a payload: it comes from this environment" in skipped
    assert (ledger.refs(), ledger.files()) == (HEADER + "[test_a]\n\tm/a.txt\n", {"m/a.txt": "x\n"})
    assert _fix_expected(ledger, monkeypatch, "--apply", _foreign(payload)) == 0
    predicate = units.payload_predicate(foreign := _other_platform(local), local)
    assert ("platform", foreign["platform"]) in predicate
    storage = f"m/a@{units._environment_slug(predicate)}.txt"
    assert (
        ledger.refs() == HEADER + f"[test_a]\n\tm/a.txt\n\tm/a.txt\t@{units.predicate_text(predicate)}\t= {storage}\n"
    )
    assert ledger.files() == {"m/a.txt": "x\n", storage: "y\n"}
    ledger.run("suite", env=GITHUB).assert_outcomes(passed=1)


def test_ledger_refuses_writes_outside_its_files(ledger: LedgerProject) -> None:
    """A run whose plan writes outside the expected files and sidecars of the tests root writes nothing and fails."""
    (ledger.pytester.path / "test_out.py").write_text(
        "from pathlib import Path\n\nfrom tests.conftest import assert_output\n\n\ndef test_out():\n"
        "    assert_output('o\\n', Path(__file__).parent / 'suite' / 'data' / 'expected' / 'out.txt')\n",
        encoding="utf-8",
    )
    result = ledger.run("test_out.py")
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    output = result.stdout.str()
    assert "expected: refused to write expected output; nothing was written:" in output
    assert "test_out.refs: it resolves to" in output
    assert (ledger.files(), (ledger.pytester.path / "test_out.refs").exists()) == ({}, False)


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        pytest.param("data/expected/../escape.txt", "outside", id="up"),
        pytest.param("data/expected", "outside", id="expected-root"),
        pytest.param("test_m.py", "outside", id="not-a-sidecar"),
        pytest.param(f"data/expected/m/{'x' * 252}.txt", "longer than 255 bytes", id="long-name"),
        pytest.param("data/expected/file.txt/inner.txt", "in its way", id="file-in-the-way"),
        pytest.param("data/expected/tree", "in its way", id="directory-in-the-way"),
    ],
)
def test_write_files_checks_every_path_first(tmp_path: Path, name: str, reason: str) -> None:
    """One refused path stops the whole plan before any delete; unit-tested since e2e runs never plan such paths."""
    root = tmp_path / "suite"
    expected = root / "data" / "expected"
    (expected / "tree").mkdir(parents=True)
    (expected / "tree" / "a.txt").write_text("a\n", encoding="utf-8")
    (expected / "file.txt").write_text("f\n", encoding="utf-8")
    keep = expected / "keep" / "keep.txt"
    keep.parent.mkdir()
    keep.write_text("kept\n", encoding="utf-8")
    with pytest.raises(units.UnsafeWriteError, match=reason):
        units.write_files(root, {root / name: "x\n", root / "test_m.refs": "refs\n"}, [keep])
    assert (keep.read_text(encoding="utf-8"), (root / "test_m.refs").exists()) == ("kept\n", False)
    units.write_files(root, {expected / "file.txt" / "inner.txt": "x\n"}, [expected / "file.txt", keep])
    assert ((expected / "file.txt" / "inner.txt").read_text(encoding="utf-8"), keep.parent.exists()) == ("x\n", False)


DUPLICATED_M = """
from pathlib import Path

import pytest

from tests.conftest import assert_generated_modules_output, assert_output

EXPECTED = Path(__file__).parent / "data" / "expected"
SAME = "same\\n"
X = {("x.py",): "X = 1\\n"}
README = {("r.md",): "r\\n"}


def test_a():
    assert_output(SAME, EXPECTED / "m" / "a.txt")


def test_b():
    assert_output(SAME, EXPECTED / "m" / "b.txt")


def test_x():
    assert_output(SAME, EXPECTED / "m" / "x.txt")


def test_y():
    assert_output(SAME, EXPECTED / "m" / "y.txt")


def test_z():
    assert_output(SAME, EXPECTED / "m" / "z.txt")


def test_tree1():
    assert_generated_modules_output(X, EXPECTED / "t1")


def test_tree2():
    assert_generated_modules_output(X, EXPECTED / "t2")


def test_file():
    assert_output("X = 1\\n", EXPECTED / "f" / "x.py")


def test_inner():
    assert_output("X = 1\\n", EXPECTED / "t1" / "x.py")


def test_empty1():
    assert_generated_modules_output(README, EXPECTED / "e1")


def test_empty2():
    assert_generated_modules_output(README, EXPECTED / "e2")


@pytest.mark.parametrize("case", ["one", "two"])
def test_p(case):
    assert_output(SAME, EXPECTED / "m" / "p.txt")


def test_crlf():
    assert_output(SAME, EXPECTED / "m" / "crlf.txt")
"""
DUPLICATED_N = """
from pathlib import Path

from tests.conftest import assert_output

EXPECTED = Path(__file__).parent / "data" / "expected"


def test_d():
    assert_output("same\\n", EXPECTED / "n" / "d.txt")


def test_e():
    assert_output("same\\n", EXPECTED / "n" / "e.txt")
"""


def _duplicated(ledger: LedgerProject, capsys: pytest.CaptureFixture[str]) -> None:
    """Store equal outputs several times, as the index held them before every content was stored once."""
    ledger.write("test_m.py", DUPLICATED_M)
    ledger.write("test_n.py", DUPLICATED_N)
    for label in ("m/a.txt", "m/b.txt", "m/p.txt", "m/shared~x.txt", "n/e.txt"):
        ledger.store(label, "same\n")
    ledger.store("m/crlf.txt", "").write_bytes(b"same\r\n")
    for label in ("t1/x.py", "t2/x.py", "f/x.py"):
        ledger.store(label, "X = 1\n")
    line = units.RefLine
    (ledger.suite / "test_m.refs").write_text(
        units.format_refs([
            line("test_a", "", "m/a.txt"),
            line("test_b", "", "m/b.txt"),
            *(line(f"test_{name}", "", f"m/{name}.txt", storage="m/shared~x.txt") for name in "xyz"),
            line("test_tree1", "", "t1//*.py"),
            line("test_tree2", "", "t2//*.py"),
            line("test_file", "", "f/x.py"),
            line("test_inner", "", "t1/x.py"),
            line("test_empty1", "", "e1//*.py"),
            line("test_empty2", "", "e2//*.py"),
            line("test_p", "one", "m/p.txt"),
            line("test_p", "two", "m/p.txt"),
            line("test_crlf", "", "m/crlf.txt"),
        ]),
        encoding="utf-8",
    )
    (ledger.suite / "test_n.refs").write_text(
        units.format_refs([line("test_d", "", "n/d.txt", storage="m/b.txt"), line("test_e", "", "n/e.txt")]),
        encoding="utf-8",
    )
    ledger.run("suite", env=GITHUB).assert_outcomes(passed=16)
    capsys.readouterr()


def test_fix_expected_deduplicates_stored_units(
    ledger: LedgerProject, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Equal units are stored once, area by area, and every test still compares the same content.

    The survivor is stored at a label of its own (``m/shared~x.txt`` has more references but is no test's label),
    then has the most references, then the shortest and first name. A file unit inside a surviving tree stays as part
    of the tree, empty trees store nothing to merge, and ``m/crlf.txt`` compares equal but stores other bytes.
    """
    _duplicated(ledger, capsys)
    assert _fix_expected(ledger, monkeypatch, "--deduplicate", "suite/test_m.py::test_p[one]") == 0
    assert capsys.readouterr().out == (
        "Deduplicated: 1 references in 1 sidecars now use 1 stored units; deleted 0 stored files. "
        "Every line compares the same bytes as before.\nRemaining duplicates in all sidecars: 5 files, 1 trees.\n"
    )
    assert "[test_p]\none\tm/p.txt\t= m/b.txt\ntwo\tm/p.txt\n" in ledger.refs()
    assert _fix_expected(ledger, monkeypatch, "suite/test_m.py", "--deduplicate") == 0
    assert capsys.readouterr().out == (
        "Deduplicated: 7 references in 1 sidecars now use 3 stored units; deleted 4 stored files. "
        "Every line compares the same bytes as before.\nRemaining duplicates in all sidecars: 1 files, 0 trees.\n"
    )
    assert ledger.refs() == HEADER + (
        "[test_a]\n\tm/a.txt\t= m/b.txt\n[test_b]\n\tm/b.txt\n[test_crlf]\n\tm/crlf.txt\n[test_empty1]\n\te1//*.py\n"
        "[test_empty2]\n\te2//*.py\n[test_file]\n\tf/x.py\n[test_inner]\n\tt1/x.py\t= f/x.py\n"
        "[test_p]\none\tm/p.txt\t= m/b.txt\ntwo\tm/p.txt\t= m/b.txt\n[test_tree1]\n\tt1//*.py\n"
        "[test_tree2]\n\tt2//*.py\t= t1//*.py\n[test_x]\n\tm/x.txt\t= m/b.txt\n[test_y]\n\tm/y.txt\t= m/b.txt\n"
        "[test_z]\n\tm/z.txt\t= m/b.txt\n"
    )
    assert ledger.refs("test_n") == HEADER + "[test_d]\n\tn/d.txt\t= m/b.txt\n[test_e]\n\tn/e.txt\n"
    assert sorted(ledger.files()) == ["f/x.py", "m/b.txt", "m/crlf.txt", "n/e.txt", "t1/x.py"]
    assert _fix_expected(ledger, monkeypatch, "--deduplicate", "suite") == 0
    assert capsys.readouterr().out.endswith(
        "deleted 1 stored files. Every line compares the same bytes as before.\n"
        "Remaining duplicates in all sidecars: 0 files, 0 trees.\n"
    )
    assert ledger.refs("test_n") == HEADER + "[test_d]\n\tn/d.txt\t= m/b.txt\n[test_e]\n\tn/e.txt\t= m/b.txt\n"
    assert sorted(ledger.files()) == ["f/x.py", "m/b.txt", "m/crlf.txt", "t1/x.py"]
    ledger.run("suite", env=GITHUB).assert_outcomes(passed=16)


STORED_READS = """
from pathlib import Path

import pytest

from tests.conftest import assert_generated_modules_output, assert_output, stored_expected

EXPECTED = Path(__file__).parent / "data" / "expected"
OUTSIDE_A_TEST = stored_expected(EXPECTED / "m" / "a.txt")


def test_a():
    assert_output("x\\n", EXPECTED / "m" / "a.txt")
    assert stored_expected(EXPECTED / "m" / "a.txt").read_text() == "x\\n"


def test_b():
    assert_output("y\\n", EXPECTED / "m" / "a.txt")
    assert stored_expected(EXPECTED / "m" / "a.txt").read_text() == "y\\n"


def test_tree():
    assert_generated_modules_output({("t.py",): "T = 1\\n"}, EXPECTED / "tree")


def test_reader(tmp_path):
    assert stored_expected(EXPECTED / "m" / "a.txt").read_text() == "x\\n"
    assert stored_expected(EXPECTED / "tree" / "t.py") == EXPECTED / "copy" / "t.py"
    assert stored_expected(EXPECTED / "tree" / "notes.txt") == EXPECTED / "tree" / "notes.txt"
    assert stored_expected(EXPECTED / "unknown.txt") == EXPECTED / "unknown.txt"
    assert stored_expected(tmp_path) == tmp_path
    assert OUTSIDE_A_TEST == EXPECTED / "m" / "a.txt"
    assert stored_expected(EXPECTED / "o.txt").read_text() == "base\\n"
    assert stored_expected(EXPECTED / "mixed" / "r.md") == EXPECTED / "mixed" / "r.md"
    assert stored_expected(EXPECTED / "mixed" / "a.py") == EXPECTED / "pyfiles" / "a.py"
    assert stored_expected(EXPECTED / "pair" / "p.py") == EXPECTED / "pair-one" / "p.py"


def test_override():
    assert_output("here\\n", EXPECTED / "o.txt")
    assert stored_expected(EXPECTED / "o.txt").read_text() == "here\\n"


def test_mixed():
    modules = {("a.py",): "A = 1\\n", ("r.md",): "r\\n"}
    assert_generated_modules_output(modules, EXPECTED / "mixed")
    assert_generated_modules_output(modules, EXPECTED / "mixed", pattern="*.md")
    assert stored_expected(EXPECTED / "mixed" / "a.py") == EXPECTED / "pyfiles" / "a.py"


@pytest.mark.parametrize("name", ["one", "two"])
def test_pair(name):
    assert_generated_modules_output({("p.py",): f"P = '{name}'\\n"}, EXPECTED / "pair")
    assert stored_expected(EXPECTED / "pair" / "p.py").read_text() == f"P = '{name}'\\n"
"""


def test_stored_expected_follows_the_index(ledger: LedgerProject) -> None:
    """A test reads an expected file where the index stores it: its own applicable line first, then any test's line.

    Its own line is the one that applies here (``@python`` overrides) and covers files its tree labels select
    (``pair``); other tests read a label's base line, stored at the label first (``o.txt``), and each pattern of a
    directory (``mixed``). A file of a tree label's directory that the pattern does not select, an unrecorded path, a
    path outside the expected root, and a read at import time (no running test; this project is not the
    repository's) stay as named.
    """
    ledger.write("test_r.py", STORED_READS)
    ledger.store("m/a.txt", "x\n")
    ledger.store("m/a~b.txt", "y\n")
    ledger.store("copy/t.py", "T = 1\n")
    ledger.store("tree/notes.txt", "n\n")
    for label, text in {
        "o.txt": "base\n",
        "o@here.txt": "here\n",
        "o@old.txt": "old\n",
        "mixed/r.md": "r\n",
        "pyfiles/a.py": "A = 1\n",
        "pair-one/p.py": "P = 'one'\n",
        "pair-two/p.py": "P = 'two'\n",
    }.items():
        ledger.store(label, text)
    line = units.RefLine
    python = (("python", shared_conftest.expected_environment_facts()["python"]),)
    (ledger.suite / "test_r.refs").write_text(
        units.format_refs([
            line("test_a", "", "m/a.txt"),
            line("test_b", "", "m/a.txt", storage="m/a~b.txt"),
            line("test_tree", "", "tree//*.py", storage="copy//*.py"),
            line("test_override", "", "o.txt"),
            line("test_override", "", "o.txt", predicate=python, storage="o@here.txt"),
            line("test_override", "", "o.txt", predicate=(("python", "2.7"),), storage="o@old.txt"),
            line("test_mixed", "", "mixed//*.py", storage="pyfiles//*.py"),
            line("test_mixed", "", "mixed//*.md"),
            line("test_pair", "one", "pair//*.py", storage="pair-one//*.py"),
            line("test_pair", "two", "pair//*.py", storage="pair-two//*.py"),
        ]),
        encoding="utf-8",
    )
    ledger.run("suite", env=GITHUB).assert_outcomes(passed=8)


READ_BY_NAME = """
from pathlib import Path

from tests.conftest import assert_output

EXPECTED = Path(__file__).parent / "data" / "expected"


def test_q1():
    assert_output("x\\n", EXPECTED / "q" / "l.txt")


def test_q2():
    assert_output("y\\n", EXPECTED / "q" / "l.txt")


def test_q3():
    assert_output("x\\n", EXPECTED / "c.txt")
"""
READ_BY_NAME_TOO = """
from pathlib import Path

from tests.conftest import assert_output

EXPECTED = Path(__file__).parent / "data" / "expected"


def test_r1():
    assert_output("x\\n", EXPECTED / "r" / "l.txt")


def test_r2():
    assert_output("y\\n", EXPECTED / "r" / "l.txt")
"""


def test_fix_expected_deduplicate_keeps_what_labels_read_by_name_return(
    ledger: LedgerProject, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Units of a label whose references differ stay, so reading the label by name returns what it did.

    ``q/l.txt`` reads as its own copy (``x``); the shorter ``c.txt`` would survive otherwise, and ``q/l.txt`` would
    then read as ``b/q2.txt`` (``y``). A plan that does that anyway is refused although every line compares the same.
    ``r/l.txt`` and ``b/r2.txt`` repeat ``q/l.txt`` and ``b/q2.txt`` but stay for the same reason, and are reported.
    """
    from scripts import fix_expected

    ledger.write("test_q.py", READ_BY_NAME)
    ledger.write("test_r.py", READ_BY_NAME_TOO)
    stored = {"q/l.txt": "x\n", "b/q2.txt": "y\n", "c.txt": "x\n", "r/l.txt": "x\n", "b/r2.txt": "y\n"}
    for label, text in stored.items():
        ledger.store(label, text)
    line = units.RefLine
    lines = [line("test_q1", "", "q/l.txt"), line("test_q2", "", "q/l.txt", storage="b/q2.txt")]
    (ledger.suite / "test_q.refs").write_text(
        units.format_refs([*lines, line("test_q3", "", "c.txt")]), encoding="utf-8"
    )
    (ledger.suite / "test_r.refs").write_text(
        units.format_refs([line("test_r1", "", "r/l.txt"), line("test_r2", "", "r/l.txt", storage="b/r2.txt")]),
        encoding="utf-8",
    )
    capsys.readouterr()
    refs = (ledger.suite / "test_q.refs").read_text(encoding="utf-8")
    bad = units.Plan(
        sidecars={
            ledger.suite / "test_q.refs": units.format_refs([
                line("test_q1", "", "q/l.txt", storage="c.txt"),
                lines[1],
                line("test_q3", "", "c.txt"),
            ])
        },
        deletes={ledger.expected / "q" / "l.txt"},
    )
    with monkeypatch.context() as patched:
        patched.setattr(fix_expected, "deduplicate", lambda *_: bad)
        assert _fix_expected(ledger, monkeypatch, "--deduplicate", "suite") == 1
    assert capsys.readouterr().out == (
        "Refused: these would compare or read other bytes; nothing was written:\n  q/l.txt, read by name\n"
    )
    assert ledger.refs("test_q") == refs
    assert _fix_expected(ledger, monkeypatch, "--deduplicate", "suite") == 0
    assert capsys.readouterr().out == (
        "Deduplicated: 1 references in 1 sidecars now use 1 stored units; deleted 1 stored files. "
        "Every line compares the same bytes as before.\nRemaining duplicates in all sidecars: 2 files, 0 trees.\n"
        "Kept in all sidecars, because their label's references differ:\n"
        "  b/q2.txt\n  b/r2.txt\n  q/l.txt\n  r/l.txt\n"
    )
    assert ledger.refs("test_q") == HEADER + (
        "[test_q1]\n\tq/l.txt\n[test_q2]\n\tq/l.txt\t= b/q2.txt\n[test_q3]\n\tc.txt\t= q/l.txt\n"
    )
    assert sorted(ledger.files()) == ["b/q2.txt", "b/r2.txt", "q/l.txt", "r/l.txt"]
    ledger.run("suite", env=GITHUB).assert_outcomes(passed=5)


def test_fix_expected_deduplicate_refuses(
    ledger: LedgerProject, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Deduplication writes nothing for unknown paths, sidecars pytest cannot read, or a plan that changes content."""
    from scripts import fix_expected

    _duplicated(ledger, capsys)
    files, refs = ledger.files(), ledger.refs()
    assert _fix_expected(ledger, monkeypatch, "--deduplicate", "suite/missing") == 2
    assert capsys.readouterr().out == "suite/missing is not a test directory, module or sidecar below suite/.\n"
    monkeypatch.setattr(fix_expected, "deduplicate", lambda *_: units.Plan(deletes={ledger.expected / "m" / "b.txt"}))
    assert _fix_expected(ledger, monkeypatch, "--deduplicate", "suite") == 1
    assert capsys.readouterr().out == (
        "Refused: these would compare or read other bytes; nothing was written:\n"
        "  suite/test_m.refs: test_b m/b.txt\n  suite/test_n.refs: test_d n/d.txt\n"
        "  m/b.txt, read by name\n  n/d.txt, read by name\n"
    )
    assert (ledger.files(), ledger.refs()) == (files, refs)
    assert _fix_expected(ledger, monkeypatch, "--deduplicate", "suite/test_m.py::test_p[three]") == 2
    assert capsys.readouterr().out == (
        "suite/test_m.py::test_p[three] names no test that compares recorded expected output.\n"
    )
    for arguments, error in {
        ("--where", "suite/test_m.py::test_a", "extra"): (
            "error: a pytest selection only goes with --all-environments, and paths with --deduplicate"
        ),
        ("--deduplicate", "suite", "--workers", "2"): (
            "error: --environment, --observe-only, --output and --workers only go with --all-environments"
        ),
    }.items():
        with pytest.raises(SystemExit) as stopped:
            fix_expected.main(["--root", str(ledger.pytester.path), *arguments])
        assert (stopped.value.code, capsys.readouterr().err.splitlines()[-1].partition(": ")[2]) == (2, error)
    (ledger.suite / "test_m.refs").write_text(HEADER + "[test_a]\n\tm/a.txt\t!\n", encoding="utf-8")
    assert _fix_expected(ledger, monkeypatch, "--deduplicate", "suite/test_n.py") == 2
    assert capsys.readouterr().out == (
        "suite/test_m.refs is not in the form pytest writes. "
        "Fix: python scripts/fix_expected.py --repair suite/test_m.refs\n"
    )


def test_stored_expected_outputs_are_unique() -> None:
    """Every content below tests/data/expected is stored once: tests with equal output share one stored unit.

    A file no test records, outside the stored trees, counts as a unit of its own.
    """
    index = units.RefsIndex(Path(__file__).parent)
    lines = index.all_lines()
    unrecorded = units.unrecorded_files(lines, index.expected_root)
    if groups := units.duplicates(lines, index.expected_root, unrecorded):
        pytest.fail(units.duplicate_storages_message(groups, unrecorded), pytrace=False)  # pragma: no cover


def test_expected_files_are_recorded() -> None:
    """Every file below tests/data/expected is stored by a ``.refs`` line or belongs to a stored tree."""
    index = units.RefsIndex(Path(__file__).parent)
    if unrecorded := units.unrecorded_files(index.all_lines(), index.expected_root):
        pytest.fail(units.unrecorded_files_message(unrecorded), pytrace=False)  # pragma: no cover


def test_test_inputs_are_unique() -> None:
    """Every input below tests/data is stored once: a file, or a tree that tests read as a whole."""
    if groups := units.input_duplicates(Path(__file__).parent / "data"):
        pytest.fail(units.duplicate_inputs_message(groups), pytrace=False)  # pragma: no cover


def test_input_duplicates_compare_files_and_trees(tmp_path: Path) -> None:
    """Files compare by suffix and content with CRLF read as LF, and trees compare as a whole.

    A registered directory or a Python package below ``python/`` is one unit from its outermost root, so its files may
    repeat files elsewhere; expected output, caches, hidden directories, bytecode and the root ``__init__.py`` are not
    inputs.
    """
    for name, data in {
        "a/x.json": b"{}\r\n",
        "b/y.json": b"{}\n",
        "c/x.yaml": b"{}\n",
        "t1/s.json": b"{}\n",
        "t1/inner/u.json": b"1",
        "t2/s.json": b"{}\n",
        "t2/inner/u.json": b"1",
        "python/pkg/__init__.py": b"",
        "python/pkg/sub/__init__.py": b"",
        "python/pkg2/__init__.py": b"",
        "python/pkg2/sub/__init__.py": b"",
        "python/loose.py": b"",
        "expected/z.json": b"{}\n",
        "d/__pycache__/m.json": b"{}\n",
        "d/.hidden/h.json": b"{}\n",
        "e/m.pyc": b"",
        "__init__.py": b"",
    }.items():
        (path := tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    groups = units.input_duplicates(tmp_path, ("t1", "t2", "t1/inner"))
    assert sorted(groups) == [["a/x.json", "b/y.json"], ["python/pkg/", "python/pkg2/"], ["t1/", "t2/"]]
    assert units.input_duplicates(tmp_path / "t1", ()) == []


def test_duplicate_messages_name_the_fix() -> None:
    """Failures name every copy with the command that removes it, and the command that stores expected output once."""
    assert units.duplicate_inputs_message([["jsonschema/b.json", "jsonschema/a.json", "c.json"], ["t2/", "t1/"]]) == (
        "tests/data/jsonschema/a.json has the same content as tests/data/c.json.\n"
        "Fix: use tests/data/c.json in your test, then git rm tests/data/jsonschema/a.json\n"
        "tests/data/jsonschema/b.json has the same content as tests/data/c.json.\n"
        "Fix: use tests/data/c.json in your test, then git rm tests/data/jsonschema/b.json\n"
        "tests/data/t2/ has the same content as tests/data/t1/.\n"
        "Fix: use tests/data/t1 in your test, then git rm -r tests/data/t2\n"
        "If a directory is read as a whole (a multi-file schema, an input directory, a copied project or templates), "
        "add it to INPUT_TREES in tests/units.py: its files then form one unit."
    )
    assert units.duplicate_storages_message([["main/a.py", "main/b.py"]]) == (
        "tests/data/expected/main/a.py and tests/data/expected/main/b.py store the same content.\n"
        "Fix: python scripts/fix_expected.py --deduplicate && git add -A tests"
    )
    assert units.duplicate_storages_message([["c.txt", "d.txt"], ["e.txt", "f.txt", "g.txt"]], {"c.txt", "f.txt"}) == (
        "tests/data/expected/c.txt and tests/data/expected/d.txt store the same content.\n"
        "tests/data/expected/e.txt and tests/data/expected/f.txt and tests/data/expected/g.txt "
        "store the same content.\n"
        "Fix: no test records tests/data/expected/c.txt; read tests/data/expected/d.txt where it is read, "
        "then git rm tests/data/expected/c.txt\n"
        "Fix: no test records tests/data/expected/f.txt; read tests/data/expected/e.txt where it is read, "
        "then git rm tests/data/expected/f.txt\n"
        "Fix: python scripts/fix_expected.py --deduplicate && git add -A tests"
    )
    assert units.duplicate_storages_message([["h.txt", "i.txt"]], {"h.txt", "i.txt"}) == (
        "tests/data/expected/h.txt and tests/data/expected/i.txt store the same content.\n"
        "Fix: no test records tests/data/expected/i.txt; read tests/data/expected/h.txt where it is read, "
        "then git rm tests/data/expected/i.txt"
    )


def test_unrecorded_expected_files_are_units(tmp_path: Path) -> None:
    """A file no line stores and no stored tree holds is a unit of its own, compared with the stored units.

    Files a stored tree's pattern selects belong to that tree, and caches and bytecode are not output.
    """
    expected = tmp_path / "expected"
    for name, text in {
        "m/a.txt": "same\n",
        "m/copy.txt": "same\n",
        "tree/x.py": "X = 1\n",
        "tree/sub/y.py": "same\n",
        "tree/notes.txt": "same\n",
        "lone.py": "X = 1\n",
        "m/__pycache__/c.txt": "same\n",
        "m/.cache/d.txt": "same\n",
        "m/e.pyc": "",
    }.items():
        (path := expected / name).parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    lines = {
        tmp_path / "test_m.refs": [
            units.RefLine("test_a", "", "m/a.txt"),
            units.RefLine("test_tree", "", "tree//*.py"),
        ]
    }
    unrecorded = units.unrecorded_files(lines, expected)
    assert unrecorded == ["lone.py", "m/copy.txt", "tree/notes.txt"]
    assert units.duplicates(lines, expected, unrecorded) == [["m/a.txt", "m/copy.txt", "tree/notes.txt"]]
    assert units.unrecorded_files_message(unrecorded) == (
        "tests/data/expected/lone.py is not used by any test: no .refs line records it.\n"
        "tests/data/expected/m/copy.txt is not used by any test: no .refs line records it.\n"
        "tests/data/expected/tree/notes.txt is not used by any test: no .refs line records it.\n"
        "Fix: if a test compares it, run that test (pytest '<test file>::<test>') and git add -A tests; otherwise "
        "git rm tests/data/expected/lone.py tests/data/expected/m/copy.txt tests/data/expected/tree/notes.txt"
    )


@pytest.mark.parametrize("option", ["--apply", "--from-ci", "--repair", "--deduplicate"])
def test_fix_expected_refuses_to_write_in_ci(
    ledger: LedgerProject, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], option: str
) -> None:
    """The commands that write expected files refuse to run while a CI variable is set."""
    monkeypatch.setenv("CI", "true")
    assert _fix_expected(ledger, monkeypatch, option, "x") == 2
    assert f"CI is set: {option} writes expected files, which CI never does. Run it on your machine." in (
        capsys.readouterr().out
    )


def test_ledger_fails_tests_that_compare_nothing(ledger: LedgerProject) -> None:
    """A test that passes without comparing anything fails even when its lines are marked '?'; fix drops them."""
    ledger.write("test_m.py", ONE_TEST)
    ledger.run("suite").assert_outcomes(passed=1)
    (ledger.suite / "test_m.refs").write_text(HEADER + "[test_a]\n\tm/a.txt\t?\n", encoding="utf-8")
    ledger.write("test_m.py", ONE_TEST.replace("    assert_output(OUT)\n", "    pass\n"))
    result = ledger.run("suite")
    result.assert_outcomes(failed=1)
    assert (
        "suite/test_m.py::test_a passed without comparing any expected output, but suite/test_m.refs records "
        "m/a.txt for it in some environments.\n"
        "Fix: make the test compare its output here too; if it should no longer compare anything: "
        "pytest suite/test_m.py::test_a --inline-snapshot=fix"
    ) in result.stdout.str()
    ledger.run("suite/test_m.py::test_a", "--inline-snapshot=fix").assert_outcomes(passed=1)
    assert (ledger.refs(), ledger.files()) == ("", {})


def test_ledger_stale_lines_of_other_environments(
    ledger: LedgerProject, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A lane lacking a test fails with a payload that keeps its lines for the environments that have the test.

    Lines of a deleted test, '?' or not, are dropped by a local run and by the same payload.
    """
    ledger.write(
        "test_m.py",
        """
        import os

        import pytest

        from tests.conftest import assert_output


        @pytest.mark.parametrize("case", ["a", *(["b"] if os.environ.get("WITH_B") else [])])
        def test_p(case):
            assert_output(f"{case}\\n")


        def test_gone():
            assert_output("g\\n")
        """,
    )
    monkeypatch.setenv("WITH_B", "1")
    ledger.run("suite").assert_outcomes(passed=3)
    monkeypatch.delenv("WITH_B")
    ci = ledger.run("suite", env=GITHUB)
    ci.assert_outcomes(failed=1, passed=2)
    facts = shared_conftest._describe_facts(shared_conftest.expected_environment_facts())
    lines = _message(ci.stdout.str(), "suite/test_m.refs has lines")
    assert (
        lines[0]
        == f"suite/test_m.refs has lines for suite/test_m.py::test_p[b], which this environment ({facts}) does not "
        "collect."
    )
    assert lines[1].startswith("Fix: python scripts/fix_expected.py --apply '")
    payload = _foreign(next(line for line in ci.stdout.str().splitlines() if line.startswith("dcg-expected-fix: ")))
    monkeypatch.setenv("WITH_B", "1")
    assert _fix_expected(ledger, monkeypatch, "--apply", payload) == 0
    assert "no tests ran" not in capsys.readouterr().out
    assert "[test_p]\na\tm/p[a].txt\nb\tm/p[b].txt\t?\n" in ledger.refs()
    monkeypatch.delenv("WITH_B")
    ledger.run("suite", env=GITHUB).assert_outcomes(passed=2)
    source = (ledger.suite / "test_m.py").read_text(encoding="utf-8")
    ledger.write("test_m.py", source.replace('def test_gone():\n    assert_output("g\\n")\n', ""))
    (ledger.suite / "test_m.refs").write_text(ledger.refs().replace("\tm/gone.txt\n", "\tm/gone.txt\t?\n"))
    flagged = ledger.run("suite", "--inline-snapshot=disable")
    assert (
        "suite/test_m.refs has lines for suite/test_m.py::test_gone, which no longer exists.\n" in flagged.stdout.str()
    )
    assert "Fix: pytest suite/test_m.py --collect-only -q && git add -A" in flagged.stdout.str()
    assert ledger.run("suite/test_m.py", "--collect-only", "-q").ret == 0
    assert "test_gone" not in ledger.refs()
    assert "m/gone.txt" not in ledger.files()


def test_ledger_orphan_sidecar_of_renamed_module(ledger: LedgerProject) -> None:
    """A sidecar whose module is gone fails runs that cannot write; its pasted command or a local run removes it."""
    ledger.write("test_old.py", ONE_TEST)
    ledger.run("suite").assert_outcomes(passed=1)
    (ledger.suite / "test_old.py").rename(ledger.suite / "test_new.py")
    failing = ledger.run("suite", "--inline-snapshot=disable")
    failing.assert_outcomes(failed=2)
    assert (
        "suite/test_old.refs has lines for suite/test_old.py, which no longer exists.\n"
        "Fix: pytest suite/test_old.refs --collect-only -q && git add -A suite"
    ) in failing.stdout.str()
    assert ledger.run("suite/test_old.refs", "--collect-only", "-q").ret == 0
    assert not (ledger.suite / "test_old.refs").exists()
    assert ledger.files() == {}
    (ledger.suite / "test_new.py").rename(ledger.suite / "test_old.py")
    ledger.run("suite").assert_outcomes(passed=1)
    (ledger.suite / "test_old.py").rename(ledger.suite / "test_new.py")
    renamed = ledger.run("suite")
    renamed.assert_outcomes(passed=2)
    assert "expected: removed suite/test_old.refs; suite/test_old.py no longer exists" in renamed.stdout.str()
    assert (ledger.refs("test_old"), ledger.refs("test_new")) == ("", HEADER + "[test_a]\n\tnew/a.txt\n")
    assert ledger.files() == {"new/a.txt": "x\n"}


def test_ledger_moves_units_to_free_labels_only(ledger: LedgerProject) -> None:
    """A unit left to one label moves to it in the next writing run, unless a name differing only by case holds files.

    The run reports every file it changed, other modules' sidecars included, with one command that stages them all.
    """
    _shared(ledger)
    ledger.store("m/y.txt", "old\n")
    ledger.write(
        "test_n.py", "from tests.conftest import assert_output\n\n\ndef test_same():\n    assert_output('old\\n')\n"
    )
    ledger.run("suite").assert_outcomes(passed=4)
    assert ledger.refs("test_n") == HEADER + "[test_same]\n\tn/same.txt\t= m/y.txt\n"
    ledger.store("n/SAME.txt", "unrelated\n")
    ledger.write("test_m.py", "def test_none():\n    pass\n")
    ledger.run("suite").assert_outcomes(passed=2)
    assert ledger.refs("test_n") == HEADER + "[test_same]\n\tn/same.txt\t= m/y.txt\n"
    (ledger.expected / "n" / "SAME.txt").unlink()
    ledger.run("suite").assert_outcomes(passed=2)
    assert ledger.refs("test_n") == HEADER + "[test_same]\n\tn/same.txt\t= m/y.txt\n"
    ledger.write("test_o.py", ONE_TEST)
    moved = ledger.run("suite/test_o.py")
    assert "expected: moved m/y.txt to n/same.txt, the label of every test that uses it" in moved.stdout.str()
    assert (
        "expected: changed suite/data/expected/m/y.txt, suite/data/expected/n/same.txt, suite/data/expected/o/a.txt, "
        "suite/test_n.refs, suite/test_o.refs; stage them all: git add -A suite"
    ) in moved.stdout.str()
    assert (ledger.refs("test_n"), ledger.files()) == (
        HEADER + "[test_same]\n\tn/same.txt\n",
        {"n/same.txt": "old\n", "o/a.txt": "x\n"},
    )


def test_ledger_outside_labels_fail(ledger: LedgerProject) -> None:
    """Labels outside the expected root fail when compared, and a sidecar line naming one is a hand edit."""
    outside = ledger.suite / "data" / "outside.txt"
    outside.write_text("in\n", encoding="utf-8")
    ledger.write(
        "test_m.py",
        "from pathlib import Path\n\nfrom tests.conftest import assert_output\n\n\n"
        "def test_new():\n    assert_output('in\\n', Path(__file__).parent / 'data' / 'outside.txt')\n",
    )
    result = ledger.run("suite")
    result.assert_outcomes(failed=1)
    assert "Expected outputs live under tests/data/expected. Fix: remove the path argument" in result.stdout.str()
    observed = ledger.run(f"--expected-observe={ledger.pytester.path / 'observed'}", "suite")
    observed.assert_outcomes(failed=1)
    assert "Expected outputs live under tests/data/expected" in observed.stdout.str()
    (ledger.suite / "test_m.refs").write_text(HEADER + "[test_new]\n\t../outside.txt\n", encoding="utf-8")
    edited = ledger.run("suite")
    edited.assert_outcomes(failed=2)
    assert (
        "suite/test_m.refs was edited by hand (line 4: '../outside.txt' is not a relative path below "
        "tests/data/expected); pytest writes this file."
    ) in edited.stdout.str()
    assert (ledger.files(), outside.read_text(encoding="utf-8")) == ({}, "in\n")


def test_ledger_missing_file_names_git_add(ledger: LedgerProject) -> None:
    """A stored file missing from the commit is restored with git checkout when git tracks it, else added."""
    ledger.write("test_m.py", ONE_TEST)
    ledger.run("suite").assert_outcomes(passed=1)
    path = ledger.expected / "m" / "a.txt"
    path.unlink()
    untracked = ledger.run("suite", env=GITHUB).stdout.str()
    assert (
        "Fix, if pytest created it on your machine: git add -A suite\n"
        "Or, to record the current output again: pytest suite/test_m.py::test_a && git add -A suite"
    ) in untracked
    ledger.store("m/a.txt", "x\n")
    subprocess.run(["git", "init", "-q"], cwd=ledger.pytester.path, check=True)
    subprocess.run(["git", "add", str(path)], cwd=ledger.pytester.path, check=True)
    path.unlink()
    tracked = ledger.run("suite", env=GITHUB).stdout.str()
    assert (
        "Fix: git checkout HEAD -- suite/data/expected/m/a.txt\n"
        "Or, to record the current output again: pytest suite/test_m.py::test_a && git add -A suite"
    ) in tracked


def test_ledger_fix_restores_overwritten_shared_file(ledger: LedgerProject) -> None:
    """When every sharer of a hand-overwritten file runs, the largest group gets it back; the rest split off."""
    _shared(ledger)
    ledger.store("m/y.txt", "old\n")
    ledger.run("suite").assert_outcomes(passed=3)
    ledger.store("m/y.txt", "garbage\n")
    _shared(ledger, a="new\n")
    fixed = ledger.run("suite", "--inline-snapshot=fix")
    fixed.assert_outcomes(passed=3)
    output = fixed.stdout.str()
    assert "expected: fixed m/y.txt in place (2 tests)" in output
    assert "expected: test_y[a] split from 2 tests that share m/y.txt; stored at m/y~y[a].txt" in output
    assert "If they should change too" not in output
    assert ledger.files() == {"m/y.txt": "old\n", "m/y~y[a].txt": "new\n"}


def test_ledger_empty_directory_is_free(ledger: LedgerProject) -> None:
    """An empty leftover directory does not push a tree to a derived name; quoted commands escape quotes."""
    (ledger.expected / "lib" / "empty").mkdir(parents=True)
    ledger.write(
        "test_m.py",
        """
        from pathlib import Path

        import pytest

        from tests.conftest import assert_generated_modules_output, assert_output


        def test_tree():
            expected = Path(__file__).parent / "data" / "expected"
            assert_generated_modules_output({("a.py",): "A = 1\\n"}, expected / "lib")


        @pytest.mark.parametrize("case", ["it's"])
        def test_quote(case):
            assert_output("q\\n")
        """,
    )
    ledger.run("suite/test_m.py::test_tree").assert_outcomes(passed=1)
    assert ledger.refs() == HEADER + "[test_tree]\n\tlib//*.py\n"
    quoted = ledger.run("suite", "--inline-snapshot=disable").stdout.str()
    assert "Fix: pytest 'suite/test_m.py::test_quote[it'\"'\"'s]' && git add -A" in quoted


@pytest.mark.parametrize(
    ("refs", "message"),
    [
        pytest.param(HEADER + "[test_a]\n\tm/a.txt\n[test_b]\n\tm/b.txt\t!\n", "1 passed", id="section"),
        pytest.param(HEADER + "\tm/x.txt\n[test_a]\n\tm/a.txt\n", "2 passed", id="module"),
        pytest.param(
            HEADER + "[test_b]\n\tm/b.txt\n[test_a]\n\tm/a.txt\n",
            "suite/test_m.refs is in the form pytest writes.",
            id="order",
        ),
        pytest.param(
            HEADER + "[test_a]\n\tm/a.txt\n\tm/a.txt\t= m/b.txt\n[test_b]\n\tm/b.txt\n", "1 passed", id="conflict"
        ),
        pytest.param("[test_a]\n\tm/a.txt\n[test_b]\n\tm/b.txt\t!\n", "1 passed", id="section-without-header"),
        pytest.param(
            "[test_a]\n\tm/a.txt\n[test_b]\n\tm/b.txt\n",
            "suite/test_m.refs is in the form pytest writes.",
            id="no-header",
        ),
    ],
)
def test_fix_expected_repairs_sidecars(
    ledger: LedgerProject,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    refs: str,
    message: str,
) -> None:
    """--repair drops unreadable lines and re-records only the tests they were in, or the module when unknown.

    The repaired sidecar starts with the header pytest writes, whether or not the edited one kept it.
    """
    ledger.write("test_m.py", ONE_TEST + "\n\ndef test_b():\n    assert_output('y\\n')\n")
    ledger.run("suite").assert_outcomes(passed=2)
    (ledger.suite / "test_m.refs").write_text(refs, encoding="utf-8")
    capfd.readouterr()
    assert _fix_expected(ledger, monkeypatch, "--repair", "suite/test_m.refs") == 0
    assert message in capfd.readouterr().out
    assert ledger.refs() == HEADER + "[test_a]\n\tm/a.txt\n[test_b]\n\tm/b.txt\n"
    assert _fix_expected(ledger, monkeypatch, "--repair", "suite/test_m.py") == 2
    assert "suite/test_m.py is not the .refs sidecar of a test module." in capfd.readouterr().out
    (ledger.pytester.path / "test_out.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    (ledger.pytester.path / "test_out.refs").write_text(refs, encoding="utf-8")
    assert _fix_expected(ledger, monkeypatch, "--repair", "test_out.refs") == 2
    assert "refused to write expected output; nothing was written:" in capfd.readouterr().out
    assert (ledger.pytester.path / "test_out.refs").read_text(encoding="utf-8") == refs


def test_ledger_merges_a_copy_into_its_label(ledger: LedgerProject) -> None:
    """A derived copy whose label stores the same content is merged into that label by the next writing run."""
    ledger.write("test_m.py", ONE_TEST + "\n\ndef test_b():\n    assert_output(OUT, EXPECTED)\n")
    source = (ledger.suite / "test_m.py").read_text(encoding="utf-8")
    ledger.write(
        "test_m.py",
        source.replace(
            "OUT = ",
            "from pathlib import Path\nEXPECTED = Path(__file__).parent / 'data' / 'expected' / 'm' / 'a.txt'\nOUT = ",
        ),
    )
    ledger.run("suite").assert_outcomes(passed=2)
    ledger.store("m/a~b.txt", "x\n")
    (ledger.suite / "test_m.refs").write_text(
        HEADER + "[test_a]\n\tm/a.txt\n[test_b]\n\tm/a.txt\t= m/a~b.txt\n", encoding="utf-8"
    )
    ledger.write("test_n.py", ONE_TEST.replace("x", "n"))
    merged = ledger.run("suite")
    merged.assert_outcomes(passed=3)
    assert "expected: deleted m/a~b.txt (no test uses it)" in merged.stdout.str()
    assert ledger.refs() == HEADER + "[test_a]\n\tm/a.txt\n[test_b]\n\tm/a.txt\n"


def test_ledger_refuses_payloads_no_predicate_tells_apart(
    ledger: LedgerProject, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A payload differing from here only in what override predicates ignore (minor tool versions) is refused.

    --apply then writes nothing; --from-ci reports it and applies the run's other payloads, whose tool versions may
    carry PEP 440 suffixes.
    """
    from scripts import fix_expected

    ledger.write("test_m.py", ONE_TEST)
    ledger.run("suite").assert_outcomes(passed=1)
    ledger.write("test_m.py", ONE_TEST.replace("OUT = 'x\\n'", "OUT = 'y\\n'"))
    payload = units.decode_payload(fix_expected.payloads_in(ledger.run("suite", env=GITHUB).stdout.str())[0])
    ledger.write("test_m.py", ONE_TEST)
    local = payload["facts"]
    minor = {tool: f"{local[tool]}.post1+ci" if local[tool] else "" for tool in ("black", "isort")}
    near = units.encode_payload({**payload, "facts": {**local, **minor}}, 1 << 20) or ""
    foreign = {**_other_platform(local), **minor}
    far = units.encode_payload({**payload, "facts": foreign}, 1 << 20) or ""
    refusal = (
        "it comes from this environment, so there is no other environment to add an override for; "
        "this environment is yours, run: pytest suite/test_m.py::test_a --inline-snapshot=fix && git add -A suite"
    )
    capfd.readouterr()
    assert _fix_expected(ledger, monkeypatch, "--apply", near, far) == pytest.ExitCode.USAGE_ERROR
    assert f"payload 1: {refusal}" in capfd.readouterr().err
    assert (ledger.refs(), ledger.files()) == (HEADER + "[test_a]\n\tm/a.txt\n", {"m/a.txt": "x\n"})
    monkeypatch.setattr(fix_expected, "ci_logs", lambda _run: f"dcg-expected-fix: {near}\ndcg-expected-fix: {far}\n")
    assert _fix_expected(ledger, monkeypatch, "--from-ci", "7") == 0
    assert f"expected: skipped a payload: {refusal}" in capfd.readouterr().out
    predicate = units.payload_predicate(foreign, local) or ()
    storage = f"m/a@{units._environment_slug(predicate)}.txt"
    assert (
        ledger.refs() == HEADER + f"[test_a]\n\tm/a.txt\n\tm/a.txt\t@{units.predicate_text(predicate)}\t= {storage}\n"
    )
    assert ledger.files() == {"m/a.txt": "x\n", storage: "y\n"}


def test_fix_expected_from_ci_skips_rejected_payloads(
    ledger: LedgerProject, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--from-ci reports payloads it cannot use instead of stopping; with none left it applies nothing."""
    from scripts import fix_expected

    ledger.write("test_m.py", ONE_TEST)
    ledger.run("suite").assert_outcomes(passed=1)
    monkeypatch.setattr(
        fix_expected, "ci_logs", lambda _run: f"dcg-expected-fix: !!\ndcg-expected-fix: {_encoded([1])}\n"
    )
    capsys.readouterr()
    assert _fix_expected(ledger, monkeypatch, "--from-ci", "7") == 1
    output = capsys.readouterr().out
    assert output.startswith("Skipped:\npayload 1: it is not a dcg-expected-fix payload (")
    assert "payload 2: it is not a dcg-expected-fix payload\nNo dcg-expected-fix payloads to apply.\n" in output


def test_ledger_leaves_a_missing_storage_in_place(ledger: LedgerProject) -> None:
    """A writing run never moves a unit whose stored file is missing to its label; its test still names the gap."""
    ledger.write(
        "test_n.py", "from tests.conftest import assert_output\n\n\ndef test_same():\n    assert_output('old\\n')\n"
    )
    refs = HEADER + "[test_same]\n\tn/same.txt\t= m/gone.txt\n"
    (ledger.suite / "test_n.refs").write_text(refs, encoding="utf-8")
    ledger.write("test_o.py", ONE_TEST)
    ledger.run("suite/test_o.py").assert_outcomes(passed=1)
    assert (ledger.refs("test_n"), ledger.files()) == (refs, {"o/a.txt": "x\n"})
    missing = ledger.run("suite/test_n.py", "--inline-snapshot=disable").stdout.str()
    assert "suite/test_n.refs stores test_same at m/gone.txt, but suite/data/expected/m/gone.txt does not exist." in (
        missing
    )


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation requires elevated privileges")
def test_ledger_reports_a_refused_sidecar_rewrite(ledger: LedgerProject) -> None:
    """A sidecar resolving outside the tests root is not rewritten into the form pytest writes; the run fails."""
    ledger.write("test_m.py", ONE_TEST)
    ledger.store("m/a.txt", "x\n")
    outside = ledger.pytester.path / "outside.refs"
    outside.write_text("[test_a]\n\tm/a.txt\n", encoding="utf-8")
    (ledger.suite / "test_m.refs").symlink_to(outside)
    result = ledger.run("suite")
    result.assert_outcomes(passed=1)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    output = result.stdout.str()
    assert "expected: refused to write expected output; nothing was written:\n" in output
    assert "suite/test_m.refs: it resolves to" in output
    assert outside.read_text(encoding="utf-8") == "[test_a]\n\tm/a.txt\n"
