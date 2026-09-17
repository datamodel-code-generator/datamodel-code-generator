"""Tests for shared assertion helpers in tests.conftest."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import call

import pytest
from inline_snapshot._flags import Flags
from inline_snapshot._global_state import state as inline_snapshot_state

from datamodel_code_generator.__main__ import Exit
from datamodel_code_generator.format import Formatter
from tests import conftest as shared_conftest
from tests.conftest import (
    _infer_expected_file,
    assert_exact_directory_content,
    assert_inputs_not_mutated,
    assert_output,
    assert_parser_modules,
    assert_parser_results,
    create_assert_file_content,
    slow_test_durations,
    worksteal_chunk_sizes,
)
from tests.main import _builtin_parity
from tests.main import conftest as main_conftest

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

SLOWEST_NODEID = "tests/test_format.py::test_apply_builtin_formatter_matches_black_isort_for_normalized_expected_files"


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

    with pytest.raises(AssertionError, match="Content mismatch") as exc_info:
        assert_exact_directory_content(output_dir, expected_dir)

    assert "sample.py" in str(exc_info.value)


def test_assert_file_content_matches_inline_snapshot_external_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Generated output helper compares external files through inline-snapshot."""
    state = inline_snapshot_state()
    monkeypatch.setattr(state, "active", True)
    monkeypatch.setattr(state, "update_flags", Flags(set()))
    monkeypatch.setattr(state, "snapshots", {})
    expected_dir = tmp_path / "expected"
    expected_dir.mkdir()
    output_file = tmp_path / "output.py"
    expected_file = expected_dir / "sample.py"

    expected_file.write_bytes(b"value = 1\r\n")
    output_file.write_bytes(b"value = 1\n")

    assert_file_content = create_assert_file_content(expected_dir)

    assert_file_content(output_file, "sample.py")


def test_assert_file_content_matches_inactive_external_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Generated output helper still supports inactive inline-snapshot file loading."""
    monkeypatch.setattr(inline_snapshot_state(), "active", False)
    expected_dir = tmp_path / "expected"
    expected_dir.mkdir()
    output_file = tmp_path / "output.py"
    expected_file = expected_dir / "sample.py"

    expected_file.write_bytes(b"value = 1\r\n")
    output_file.write_bytes(b"value = 1\n")

    assert_file_content = create_assert_file_content(expected_dir)

    assert_file_content(output_file, "sample.py")


def test_assert_file_content_rejects_mismatched_inactive_external_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Generated output helper reports inactive inline-snapshot file mismatches."""
    monkeypatch.setattr(inline_snapshot_state(), "active", False)
    expected_dir = tmp_path / "expected"
    expected_dir.mkdir()
    output_file = tmp_path / "output.py"
    expected_file = expected_dir / "sample.py"

    expected_file.write_text("value = 1\n", encoding="utf-8")
    output_file.write_text("value = 2\n", encoding="utf-8")

    assert_file_content = create_assert_file_content(expected_dir)

    with pytest.raises(AssertionError, match="Content mismatch"):
        assert_file_content(output_file, "sample.py")


@pytest.mark.parametrize(("create", "raises"), [(False, True), (True, False)])
def test_assert_file_content_missing_inline_snapshot_external_file(
    create: bool, raises: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Generated output helper defers missing external file creation to inline-snapshot."""
    state = inline_snapshot_state()
    monkeypatch.setattr(state, "active", True)
    monkeypatch.setattr(state, "update_flags", Flags({"create"} if create else set()))
    monkeypatch.setattr(state, "missing_values", 0)
    monkeypatch.setattr(state, "snapshots", {})
    expected_dir = tmp_path / "expected"
    expected_dir.mkdir()
    output_file = tmp_path / "output.py"
    output_file.write_text("value = 1\n", encoding="utf-8")

    assert_file_content = create_assert_file_content(expected_dir)

    if raises:
        with pytest.raises(AssertionError, match="Expected file not found"):
            assert_file_content(output_file, "missing.py")
        return

    assert_file_content(output_file, "missing.py")
    state.missing_values = 0
    state.snapshots.clear()


def test_assert_file_content_handles_storage_lookup_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Generated output helper treats snapshot storage misses as missing external files."""
    state = inline_snapshot_state()
    monkeypatch.setattr(state, "active", True)
    monkeypatch.setattr(state, "update_flags", Flags(set()))
    monkeypatch.setattr(state, "snapshots", {})
    expected_dir = tmp_path / "expected"
    expected_dir.mkdir()
    output_file = tmp_path / "output.py"
    output_file.write_text("value = 1\n", encoding="utf-8")

    def raise_storage_lookup_error(_expected: object, _which: str) -> object:
        msg = "missing"
        raise shared_conftest.StorageLookupError(msg, files=[])

    monkeypatch.setattr(shared_conftest, "get_snapshot_value", raise_storage_lookup_error)
    assert_file_content = create_assert_file_content(expected_dir)

    with pytest.raises(AssertionError, match="Expected file not found"):
        assert_file_content(output_file, "missing.py")


def test_assert_file_content_reports_failed_inline_snapshot_update(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Generated output helper reports abnormal inline-snapshot update failures."""

    class UnrecordedSnapshot:
        __hash__ = object.__hash__

        def __init__(self) -> None:
            self.recorded = False

        def __eq__(self, other: object) -> bool:
            self.recorded = other is not self
            return False

    state = inline_snapshot_state()
    monkeypatch.setattr(state, "active", True)
    monkeypatch.setattr(state, "update_flags", Flags({"create"}))
    expected = UnrecordedSnapshot()
    expected_dir = tmp_path / "expected"
    expected_dir.mkdir()
    output_file = tmp_path / "output.py"
    output_file.write_text("value = 1\n", encoding="utf-8")

    monkeypatch.setattr(shared_conftest, "external_file", lambda _expected_path: expected)
    monkeypatch.setattr(shared_conftest, "get_snapshot_value", lambda _expected, _which: Ellipsis)
    assert_file_content = create_assert_file_content(expected_dir)

    with pytest.raises(AssertionError, match="inline-snapshot did not record"):
        assert_file_content(output_file, "missing.py")
    assert expected.recorded is True


@pytest.mark.parametrize(("fix", "raises"), [(False, True), (True, False)])
def test_assert_file_content_mismatched_inline_snapshot_external_file(
    fix: bool, raises: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Generated output helper defers mismatched external file updates to inline-snapshot."""
    state = inline_snapshot_state()
    monkeypatch.setattr(state, "active", True)
    monkeypatch.setattr(state, "update_flags", Flags({"fix"} if fix else set()))
    monkeypatch.setattr(state, "incorrect_values", 0)
    monkeypatch.setattr(state, "snapshots", {})
    expected_dir = tmp_path / "expected"
    expected_dir.mkdir()
    output_file = tmp_path / "output.py"
    expected_file = expected_dir / "sample.py"

    expected_file.write_text("value = 1\n", encoding="utf-8")
    output_file.write_text("value = 2\n", encoding="utf-8")

    assert_file_content = create_assert_file_content(expected_dir)

    if raises:
        with pytest.raises(AssertionError, match="Content mismatch"):
            assert_file_content(output_file, "sample.py")
        return

    assert_file_content(output_file, "sample.py")
    state.incorrect_values = 0
    state.snapshots.clear()


def test_assert_parser_results_rejects_unexpected_result(tmp_path: Path) -> None:
    """Parser result assertions fail when generated output has no expected file."""
    expected_dir = tmp_path / "expected"
    expected_dir.mkdir()
    (expected_dir / "sample.py").write_text("value = 1\n", encoding="utf-8")

    with pytest.raises(AssertionError, match=r"extra\.py"):
        assert_parser_results({"sample.py": "value = 1\n", "extra.py": "value = 2\n"}, expected_dir)


def test_assert_parser_results_rejects_missing_result(tmp_path: Path) -> None:
    """Parser result assertions fail when an expected file is not generated."""
    expected_dir = tmp_path / "expected"
    expected_dir.mkdir()
    (expected_dir / "sample.py").write_text("value = 1\n", encoding="utf-8")
    (expected_dir / "missing.py").write_text("value = 2\n", encoding="utf-8")

    with pytest.raises(AssertionError, match=r"missing\.py"):
        assert_parser_results({"sample.py": "value = 1\n"}, expected_dir)


def test_assert_parser_modules_rejects_missing_expected_module(tmp_path: Path) -> None:
    """Parser module assertions fail when an expected module is not generated."""
    expected_dir = tmp_path / "expected"
    expected_dir.mkdir()
    (expected_dir / "sample.py").write_text("value = 1\n", encoding="utf-8")

    with pytest.raises(AssertionError, match="Expected files not in parser modules"):
        assert_parser_modules({}, expected_dir)


def test_assert_parser_modules_rejects_unexpected_module(tmp_path: Path) -> None:
    """Parser module assertions fail when generated output has no expected file."""
    expected_dir = tmp_path / "expected"
    expected_dir.mkdir()

    with pytest.raises(AssertionError, match="Parser modules not in expected files"):
        assert_parser_modules({("sample.py",): "value = 1\n"}, expected_dir)


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
