"""Built-in formatter parity helpers for main integration tests."""

from __future__ import annotations

import os
import re
import shutil
import sys
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from datamodel_code_generator import generate
from datamodel_code_generator.__main__ import Exit, main
from datamodel_code_generator.format import Formatter
from tests.conftest import _format_diff, _normalize_line_endings, assert_warnings_contain

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Sequence
    from pathlib import Path

_BUILTIN_FORMATTER_PARITY_ENV = "DATAMODEL_CODE_GENERATOR_CHECK_BUILTIN_FORMATTER_PARITY"
_DEFAULT_CLI_FORMATTERS = {"black", "isort"}
_DEFAULT_API_FORMATTERS = {Formatter.BLACK, Formatter.ISORT}


@dataclass(frozen=True)
class _BuiltinCliFormatterParityContext:
    run_main: Callable[..., Exit]
    extend_args: Callable[..., None]
    copy_files: Callable[[Sequence[tuple[Path, Path]] | None], None]
    assert_exit_code: Callable[[Exit, Exit, str], None]


@contextmanager
def _preserve_mock_calls(mocked_callables: Sequence[Any]) -> Generator[None, None, None]:
    snapshots: list[tuple[Any, Any, list[Any], list[Any], list[Any], int]] = []
    for mocked_callable in mocked_callables:
        if hasattr(mocked_callable, "mock_calls") and hasattr(mocked_callable, "reset_mock"):
            snapshots.append((  # noqa: PERF401
                mocked_callable,
                mocked_callable.call_args,
                list(mocked_callable.call_args_list),
                list(mocked_callable.mock_calls),
                list(mocked_callable.method_calls),
                mocked_callable.call_count,
            ))
    try:
        yield
    finally:
        for mocked_callable, call_args, call_args_list, mock_calls, method_calls, call_count in snapshots:
            mocked_callable.reset_mock()
            mocked_callable._mock_call_args = call_args
            mocked_callable._mock_call_args_list = call_args_list
            mocked_callable._mock_mock_calls = mock_calls
            mocked_callable._mock_method_calls = method_calls
            mocked_callable._mock_call_count = call_count


def _parity_mocked_callables_to_preserve() -> list[Any]:
    prance = sys.modules.get("prance")
    if prance is None:
        return []
    return [getattr(prance, "BaseParser", None)]


def _http_get_is_mocked() -> bool:
    """Return whether either lazily imported HTTP backend has a mocked get call."""
    for module_name in ("httpx2", "httpx"):
        if (module := sys.modules.get(module_name)) is not None and hasattr(getattr(module, "get", None), "mock_calls"):
            return True
    return False


def _extract_cli_formatters(extra_args: Sequence[str] | None) -> list[str] | None:
    if extra_args is None or "--formatters" not in extra_args:
        return None
    extra_args_list = list(extra_args)
    formatter_index = extra_args_list.index("--formatters")
    formatters: list[str] = []
    for item in extra_args_list[formatter_index + 1 :]:
        if item.startswith("-"):
            break
        formatters.append(item)
    return formatters


def _uses_default_cli_formatters(extra_args: Sequence[str] | None) -> bool:
    if extra_args is None:
        return True
    if "--custom-formatters" in extra_args or "--custom-formatters-kwargs" in extra_args:
        return False
    return (formatters := _extract_cli_formatters(extra_args)) is None or set(formatters) == _DEFAULT_CLI_FORMATTERS


def _uses_virtual_output_mode(extra_args: Sequence[str] | None) -> bool:
    """Return whether a command deliberately leaves its output path untouched."""
    return extra_args is not None and any(option in extra_args for option in ("--check", "--diff-against"))


def _uses_default_api_formatters(generate_options: dict[str, Any]) -> bool:
    if generate_options.get("custom_formatters") or generate_options.get(
        "custom_formatters_kwargs"
    ):  # pragma: no cover
        return False
    return (formatters := generate_options.get("formatters")) is None or set(formatters) == _DEFAULT_API_FORMATTERS


def _builtin_formatter_extra_args(extra_args: Sequence[str] | None) -> list[str]:
    if extra_args is None:
        return ["--formatters", "builtin"]
    extra_args_list = list(extra_args)
    if "--formatters" not in extra_args_list:
        return [*extra_args_list, "--formatters", "builtin"]
    formatter_index = extra_args_list.index("--formatters")  # pragma: no cover
    end_index = formatter_index + 1  # pragma: no cover
    while end_index < len(extra_args_list) and not extra_args_list[end_index].startswith("-"):  # pragma: no cover
        end_index += 1  # pragma: no cover
    return [*extra_args_list[: formatter_index + 1], "builtin", *extra_args_list[end_index:]]  # pragma: no cover


def _builtin_formatter_parity_output_path(output_path: Path) -> Path:
    if output_path.is_dir():
        return output_path.with_name(f"{output_path.name}_builtin_parity")
    if output_path.suffix:
        return output_path.with_name(f"{output_path.stem}.builtin-parity{output_path.suffix}")
    return output_path.with_name(f"{output_path.name}.builtin-parity")  # pragma: no cover


def _clear_builtin_formatter_parity_output(output_path: Path) -> None:
    if output_path.is_dir():
        shutil.rmtree(output_path)
    elif output_path.exists():
        output_path.unlink()


def _normalize_builtin_parity_content(content: str) -> str:
    return re.sub(
        r"^#   command:   datamodel-codegen .*$",
        "#   command:   datamodel-codegen [COMMAND]",
        content,
        flags=re.MULTILINE,
    )


def _assert_same_generated_python(expected_path: Path, actual_path: Path) -> None:
    if expected_path.is_file():
        expected = _normalize_builtin_parity_content(_normalize_line_endings(expected_path.read_text(encoding="utf-8")))
        actual = _normalize_builtin_parity_content(_normalize_line_endings(actual_path.read_text(encoding="utf-8")))
        if expected != actual:  # pragma: no cover
            diff = _format_diff(expected, actual, expected_path)
            pytest.fail(f"Built-in formatter output differs from black+isort for {expected_path}\n{diff}")
        return

    expected_files = {path.relative_to(expected_path) for path in expected_path.rglob("*.py")}
    actual_files = {path.relative_to(actual_path) for path in actual_path.rglob("*.py")}
    if expected_files != actual_files:  # pragma: no cover
        pytest.fail(
            "Built-in formatter output file set differs from black+isort\n"
            f"Missing: {sorted(expected_files - actual_files)}\n"
            f"Extra: {sorted(actual_files - expected_files)}"
        )
    for relative_path in sorted(expected_files):
        expected_file = expected_path / relative_path
        actual_file = actual_path / relative_path
        expected = _normalize_builtin_parity_content(_normalize_line_endings(expected_file.read_text(encoding="utf-8")))
        actual = _normalize_builtin_parity_content(_normalize_line_endings(actual_file.read_text(encoding="utf-8")))
        if expected != actual:  # pragma: no cover
            diff = _format_diff(expected, actual, expected_file)
            pytest.fail(f"Built-in formatter output differs from black+isort for {relative_path}\n{diff}")


def _assert_builtin_cli_formatter_parity(
    *,
    input_path: Path | None,
    output_path: Path | None,
    input_file_type: str | None,
    extra_args: Sequence[str] | None,
    copy_files: Sequence[tuple[Path, Path]] | None,
    stdin_path: Path | None,
    monkeypatch: pytest.MonkeyPatch | None,
    context: _BuiltinCliFormatterParityContext,
) -> None:
    if os.environ.get(_BUILTIN_FORMATTER_PARITY_ENV) != "1":
        return
    if (
        output_path is None
        or not output_path.exists()
        or _uses_virtual_output_mode(extra_args)
        or not _uses_default_cli_formatters(extra_args)
        or _http_get_is_mocked()
    ):
        return

    builtin_output_path = _builtin_formatter_parity_output_path(output_path)
    _clear_builtin_formatter_parity_output(builtin_output_path)
    builtin_extra_args = _builtin_formatter_extra_args(extra_args)

    with _preserve_mock_calls(_parity_mocked_callables_to_preserve()):
        if stdin_path is not None:
            if monkeypatch is None:  # pragma: no cover
                pytest.fail("monkeypatch is required when using stdin_path")
            context.copy_files(copy_files)
            with stdin_path.open(encoding="utf-8") as stdin:
                monkeypatch.setattr("sys.stdin", stdin)
                args: list[str] = []
                context.extend_args(
                    args,
                    output_path=builtin_output_path,
                    input_file_type=input_file_type,
                    extra_args=builtin_extra_args,
                )
                return_code = main(args)
        else:
            if input_path is None:  # pragma: no cover
                pytest.fail("input_path is required")
            return_code = context.run_main(
                input_path,
                builtin_output_path,
                input_file_type,
                extra_args=builtin_extra_args,
                copy_files=copy_files,
            )

    context.assert_exit_code(return_code, Exit.OK, f"Built-in formatter parity input: {input_path}")
    _assert_same_generated_python(output_path, builtin_output_path)
    _clear_builtin_formatter_parity_output(builtin_output_path)


def _assert_builtin_generate_formatter_parity(
    *,
    input_: Path,
    output_path: Path,
    generate_options: dict[str, Any],
    expected_warnings: Sequence[str] | None = None,
) -> None:
    if os.environ.get(_BUILTIN_FORMATTER_PARITY_ENV) != "1":
        return
    if not output_path.exists() or not _uses_default_api_formatters(generate_options):  # pragma: no cover
        return

    builtin_output_path = _builtin_formatter_parity_output_path(output_path)
    _clear_builtin_formatter_parity_output(builtin_output_path)
    builtin_options = {
        **generate_options,
        "output": builtin_output_path,
        "formatters": [Formatter.BUILTIN],
    }
    if expected_warnings is None:
        generate(input_=input_, **builtin_options)
    else:
        with warnings.catch_warnings(record=True) as warning_records:
            warnings.simplefilter("always")
            generate(input_=input_, **builtin_options)
        assert_warnings_contain(warning_records, *expected_warnings)
    _assert_same_generated_python(output_path, builtin_output_path)
    _clear_builtin_formatter_parity_output(builtin_output_path)
