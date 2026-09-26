"""Tests for watch mode functionality."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, NoReturn, TextIO

import pytest

import datamodel_code_generator
from datamodel_code_generator.__main__ import Exit
from tests.conftest import assert_output
from tests.main.conftest import (
    EXPECTED_MAIN_PATH,
    JSON_SCHEMA_DATA_PATH,
    assert_watchfiles_module,
    run_main_with_args,
    run_watch_and_assert,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from pytest_mock import MockerFixture

    from datamodel_code_generator.watch_dependencies import WatchDependencies

PROJECT_ROOT = Path(__file__).parents[2]
PACKAGE_ROOT = Path(datamodel_code_generator.__file__).parent
WATCH_DATA_PATH = PROJECT_ROOT / "tests/data/watch"
WATCH_CLI_TIMEOUT_SECONDS = 15.0
WATCH_CLI_STOP_TIMEOUT_SECONDS = 5.0
WATCH_CLI_READY_DELAY_SECONDS = 0.3
WATCH_CLI_CHANGE_RETRY_SECONDS = 0.75
WATCH_SCHEMA_INITIAL = """\
{
  "title": "WatchedPerson",
  "type": "object",
  "properties": {
    "name": {
      "type": "string"
    }
  },
  "required": [
    "name"
  ]
}
"""
WATCH_SCHEMA_CHANGED = """\
{
  "title": "WatchedPerson",
  "type": "object",
  "properties": {
    "name": {
      "type": "string"
    },
    "age": {
      "type": "integer"
    }
  },
  "required": [
    "name"
  ]
}
"""
WATCH_SCHEMA_INVALID = '{"title": "WatchedPerson",'
WATCH_GENERATION_ERROR = "generation error"


class _WatchRemoteSchemaHandler(BaseHTTPRequestHandler):
    """Serve a real remote reference to watch-mode subprocesses."""

    body = b'{"title":"Child","type":"object","properties":{"name":{"type":"string"}}}'

    def do_GET(self) -> None:
        if self.path == "/child.json":
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(self.body)))
            self.end_headers()
            self.wfile.write(self.body)
            return
        self.send_error(404)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@pytest.fixture
def watched_http_server() -> Iterator[str]:
    """Provide an actual HTTP endpoint reachable from a watch subprocess."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _WatchRemoteSchemaHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _watch_cli_prefix() -> list[str]:
    command = [sys.executable]
    coverage_file = os.environ.get("COVERAGE_FILE", "")
    if coverage_file and "-nocov" not in coverage_file:
        command.extend([
            "-m",
            "coverage",
            "run",
            "--branch",
            "--concurrency=thread",
            "--parallel-mode",
            "--source",
            str(PACKAGE_ROOT),
            "--rcfile",
            str(PROJECT_ROOT / "pyproject.toml"),
        ])
    command.extend(["-m", "datamodel_code_generator"])
    return command


def _watch_cli_command(input_path: Path, output_path: Path, extra_args: list[str] | None = None) -> list[str]:
    command = _watch_cli_prefix()
    command.extend([
        "--watch",
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--watch-delay",
        "0.1",
        "--input-file-type",
        "jsonschema",
        "--formatters",
        "builtin",
        "--disable-timestamp",
    ])
    command.extend(extra_args or ())
    return command


def _batch_watch_cli_command(extra_args: list[str] | None = None) -> list[str]:
    command = _watch_cli_prefix()
    command.extend([
        "--all-jobs",
        "--watch",
        "--watch-delay",
        "0.1",
        "--formatters",
        "builtin",
        "--disable-timestamp",
    ])
    command.extend(extra_args or ())
    return command


def _collect_stream_lines(stream: TextIO, lines: list[str]) -> None:
    lines.extend(stream)


def _start_watch_process(
    command: list[str],
    working_directory: Path,
) -> tuple[subprocess.Popen[str], list[str], list[str], threading.Thread, threading.Thread]:
    environment = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "WATCHFILES_FORCE_POLLING": os.environ.get("WATCHFILES_FORCE_POLLING", "true"),
    }
    if working_directory != PROJECT_ROOT and (coverage_file := environment.get("COVERAGE_FILE")):
        coverage_path = Path(coverage_file)
        if not coverage_path.is_absolute():
            environment["COVERAGE_FILE"] = str((PROJECT_ROOT / coverage_path).resolve())
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=working_directory,
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0,
    )
    if process.stdout is None or process.stderr is None:  # pragma: no cover
        pytest.fail("watch CLI process did not expose output streams")

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    stdout_thread = threading.Thread(
        target=_collect_stream_lines,
        args=(process.stdout, stdout_lines),
        name="watch-cli-stdout",
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_collect_stream_lines,
        args=(process.stderr, stderr_lines),
        name="watch-cli-stderr",
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    return process, stdout_lines, stderr_lines, stdout_thread, stderr_thread


def _start_watch_cli(
    input_path: Path,
    output_path: Path,
    extra_args: list[str] | None = None,
    working_directory: Path = PROJECT_ROOT,
) -> tuple[subprocess.Popen[str], list[str], list[str], threading.Thread, threading.Thread]:
    return _start_watch_process(_watch_cli_command(input_path, output_path, extra_args), working_directory)


def _stop_watch_cli(
    process: subprocess.Popen[str],
    stdout_thread: threading.Thread,
    stderr_thread: threading.Thread,
) -> None:
    if process.poll() is None:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=WATCH_CLI_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=WATCH_CLI_STOP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=WATCH_CLI_STOP_TIMEOUT_SECONDS)
    stdout_thread.join(timeout=1.0)
    stderr_thread.join(timeout=1.0)


def _watch_cli_output(stdout_lines: list[str], stderr_lines: list[str]) -> str:
    return f"stdout:\n{''.join(stdout_lines)}\n\nstderr:\n{''.join(stderr_lines)}"


def _wait_for_watch_cli(
    process: subprocess.Popen[str],
    stdout_lines: list[str],
    stderr_lines: list[str],
    condition: Callable[[], bool],
    description: str,
) -> None:
    deadline = time.monotonic() + WATCH_CLI_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if condition():
            return
        if (return_code := process.poll()) is not None:
            pytest.fail(
                f"watch CLI exited with {return_code} before {description}\n"
                f"{_watch_cli_output(stdout_lines, stderr_lines)}"
            )
        time.sleep(0.05)
    pytest.fail(f"Timed out waiting for {description}\n{_watch_cli_output(stdout_lines, stderr_lines)}")


def _write_watch_cli_input_and_wait(
    process: subprocess.Popen[str],
    stdout_lines: list[str],
    stderr_lines: list[str],
    input_file: Path,
    content: str,
    condition: Callable[[], bool],
    description: str,
) -> None:
    pending_input = input_file.with_name(f".{input_file.name}.pending")
    pending_input.write_text(content, encoding="utf-8")
    pending_input.replace(input_file)
    last_write = time.monotonic()

    def condition_after_write() -> bool:
        nonlocal last_write

        if condition():
            return True

        now = time.monotonic()
        if now - last_write >= WATCH_CLI_CHANGE_RETRY_SECONDS:
            input_file.touch()
            last_write = now
        return False

    _wait_for_watch_cli(process, stdout_lines, stderr_lines, condition_after_write, description)


def _lines_contain(lines: list[str], expected_text: str) -> bool:
    return any(expected_text in line for line in lines)


def _file_contains(path: Path, expected_text: str) -> bool:
    if not path.is_file():
        return False
    return expected_text in path.read_text(encoding="utf-8")


@dataclass
class _WatchedOutput:
    """A watched output file and the text that last satisfied a wait for it.

    The watch process may regenerate the file again after a wait succeeds and truncate it while the test reads it,
    so tests assert the text their wait read instead of reading the file a second time.
    """

    path: Path
    text: str = ""

    def contains(self, included: str, excluded: str | None = None) -> Callable[[], bool]:
        """Return a wait condition that keeps the file's text once it has the included and not the excluded text."""

        def condition() -> bool:
            text = self.path.read_text(encoding="utf-8") if self.path.is_file() else ""
            if included not in text or (excluded is not None and excluded in text):
                return False
            self.text = text
            return True

        return condition


def _batch_pyproject(jobs: list[tuple[str, Path, Path, Path | None]]) -> str:
    """Render a minimal real batch-watch project."""
    sections = ["[tool.datamodel-codegen]", 'input-file-type = "jsonschema"']
    for name, input_path, output_path, metadata_path in jobs:
        sections.extend([
            "",
            f"[tool.datamodel-codegen.jobs.{name}]",
            f'input = "{input_path.as_posix()}"',
            f'output = "{output_path.as_posix()}"',
        ])
        if metadata_path is not None:
            sections.append(f'emit-model-metadata = "{metadata_path.as_posix()}"')
    return "\n".join((*sections, ""))


def _record_failed_dependency(dependencies: WatchDependencies, path: Path) -> None:
    from datamodel_code_generator.watch_dependencies import record_local_dependency

    with dependencies.generation():
        record_local_dependency(path)
        _raise_watch_generation_error()


def _raise_watch_generation_error() -> NoReturn:
    """Raise the shared watch-generation error from a non-assert helper."""
    message = WATCH_GENERATION_ERROR
    raise RuntimeError(message)


def _start_watch_cli_until_ready(
    input_path: Path,
    output_path: Path,
    extra_args: list[str] | None = None,
    working_directory: Path = PROJECT_ROOT,
) -> tuple[subprocess.Popen[str], list[str], list[str], threading.Thread, threading.Thread]:
    if extra_args is None and working_directory == PROJECT_ROOT:
        process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli(input_path, output_path)
    else:
        process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli(
            input_path,
            output_path,
            extra_args,
            working_directory,
        )
    return _wait_for_watch_cli_ready(process, stdout_lines, stderr_lines, stdout_thread, stderr_thread)


def _wait_for_watch_cli_ready(
    process: subprocess.Popen[str],
    stdout_lines: list[str],
    stderr_lines: list[str],
    stdout_thread: threading.Thread,
    stderr_thread: threading.Thread,
) -> tuple[subprocess.Popen[str], list[str], list[str], threading.Thread, threading.Thread]:
    try:
        _wait_for_watch_cli(
            process,
            stdout_lines,
            stderr_lines,
            lambda: _lines_contain(stdout_lines, "Watching "),
            "watch mode to start",
        )
        time.sleep(WATCH_CLI_READY_DELAY_SECONDS)
    except BaseException:
        _stop_watch_cli(process, stdout_thread, stderr_thread)
        raise
    return process, stdout_lines, stderr_lines, stdout_thread, stderr_thread


def _start_batch_watch_cli_until_ready(
    working_directory: Path,
    extra_args: list[str] | None = None,
) -> tuple[subprocess.Popen[str], list[str], list[str], threading.Thread, threading.Thread]:
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_process(
        _batch_watch_cli_command(extra_args), working_directory
    )
    return _wait_for_watch_cli_ready(process, stdout_lines, stderr_lines, stdout_thread, stderr_thread)


@pytest.mark.allow_direct_assert
def test_watch_cli_command_uses_coverage_for_coverage_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test watch CLI subprocess runs under coverage for coverage-enabled tox envs."""
    monkeypatch.setenv("COVERAGE_FILE", ".tox/.coverage.py314-parallel")

    command = _watch_cli_command(tmp_path / "schema.json", tmp_path / "output.py")

    assert command[1:8] == ["-m", "coverage", "run", "--branch", "--concurrency=thread", "--parallel-mode", "--source"]
    assert command[8:11] == [str(PACKAGE_ROOT), "--rcfile", str(PROJECT_ROOT / "pyproject.toml")]


@pytest.mark.allow_direct_assert
def test_watch_cli_command_skips_coverage_for_nocov_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test watch CLI subprocess skips coverage for no-cov tox envs."""
    monkeypatch.setenv("COVERAGE_FILE", ".tox/.coverage.py314-nocov-parallel")

    command = _watch_cli_command(tmp_path / "schema.json", tmp_path / "output.py")

    assert "coverage" not in command


@pytest.mark.allow_direct_assert
@pytest.mark.skipif(
    os.name == "nt",
    reason="CTRL_BREAK_EVENT does not flush coverage.py parallel data before the helper exits the subprocess",
)
def test_watch_cli_resolves_relative_coverage_file_for_other_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test a watch subprocess keeps its coverage data at the project root."""
    project_root = tmp_path / "project"
    working_directory = tmp_path / "working-directory"
    project_root.mkdir()
    working_directory.mkdir()
    shutil.copyfile(PROJECT_ROOT / "pyproject.toml", project_root / "pyproject.toml")
    input_file = working_directory / "schema.json"
    output_file = working_directory / "output.py"
    input_file.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    coverage_file = ".coverage.watch-cli"
    monkeypatch.setattr(sys.modules[__name__], "PROJECT_ROOT", project_root)
    monkeypatch.setenv("COVERAGE_FILE", coverage_file)

    process, _stdout_lines, _stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_file,
        output_file,
        working_directory=working_directory,
    )
    _stop_watch_cli(process, stdout_thread, stderr_thread)

    assert list(project_root.glob(f"{coverage_file}.*"))
    assert not list(working_directory.glob(f"{coverage_file}.*"))


@pytest.mark.allow_direct_assert
def test_watch_cli_helpers_report_process_output(tmp_path: Path) -> None:
    """Test watch CLI helper predicates cover missing files and captured output."""
    output = _watch_cli_output(["out"], ["err"])
    assert output == "stdout:\nout\n\nstderr:\nerr"
    assert not _file_contains(tmp_path / "missing.py", "age")


def test_wait_for_watch_cli_reports_process_exit() -> None:
    """Test watch CLI waiting reports early process exits."""

    class ExitedProcess:
        def poll(self) -> int:
            return 7

    with pytest.raises(pytest.fail.Exception, match="exited with 7"):
        _wait_for_watch_cli(
            ExitedProcess(),  # ty: ignore[arg-type]
            ["started\n"],
            ["failed\n"],
            lambda: False,
            "ready",
        )


@pytest.mark.allow_direct_assert
def test_wait_for_watch_cli_reports_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test watch CLI waiting reports timeouts."""

    class RunningProcess:
        def poll(self) -> None:
            return None

    assert RunningProcess().poll() is None
    monkeypatch.setattr(sys.modules[__name__], "WATCH_CLI_TIMEOUT_SECONDS", -1.0)
    with pytest.raises(pytest.fail.Exception, match="Timed out"):
        _wait_for_watch_cli(
            RunningProcess(),  # ty: ignore[arg-type]
            [],
            [],
            lambda: False,
            "ready",
        )


@pytest.mark.allow_direct_assert
def test_stop_watch_cli_joins_threads_after_completed_process() -> None:
    """Test watch CLI cleanup joins stream reader threads after completed process."""

    class CompletedProcess:
        def poll(self) -> int:
            return 0

    class ThreadStub:
        def __init__(self) -> None:
            self.joined = False

        def join(self, *, timeout: float) -> None:
            self.joined = timeout == pytest.approx(1.0)

    stdout_thread = ThreadStub()
    stderr_thread = ThreadStub()

    _stop_watch_cli(CompletedProcess(), stdout_thread, stderr_thread)  # ty: ignore[arg-type]

    assert stdout_thread.joined
    assert stderr_thread.joined


@pytest.mark.allow_direct_assert
def test_stop_watch_cli_sends_windows_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test watch CLI cleanup sends the Windows interrupt signal on Windows."""

    class RunningProcess:
        def __init__(self) -> None:
            self.sent_signal: int | None = None

        def poll(self) -> None:
            return None

        def send_signal(self, value: int) -> None:
            self.sent_signal = value

        @pytest.mark.allow_direct_assert
        def wait(self, *, timeout: float) -> None:
            assert timeout == WATCH_CLI_STOP_TIMEOUT_SECONDS

    class ThreadStub:
        @pytest.mark.allow_direct_assert
        def join(self, *, timeout: float) -> None:
            assert timeout == pytest.approx(1.0)

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM, raising=False)
    process = RunningProcess()

    _stop_watch_cli(process, ThreadStub(), ThreadStub())  # ty: ignore[arg-type]

    assert process.sent_signal == signal.SIGTERM


@pytest.mark.allow_direct_assert
def test_stop_watch_cli_kills_after_repeated_timeouts() -> None:
    """Test watch CLI cleanup kills the subprocess when graceful stop times out."""
    expected_signal = signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT

    class RunningProcess:
        def __init__(self) -> None:
            self.wait_calls = 0
            self.terminated = False
            self.killed = False

        def poll(self) -> None:
            return None

        @pytest.mark.allow_direct_assert
        def send_signal(self, value: int) -> None:
            assert value == expected_signal

        def wait(self, *, timeout: float) -> None:
            self.wait_calls += 1
            if self.wait_calls < 3:
                raise subprocess.TimeoutExpired(cmd="watch", timeout=timeout)

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

    class ThreadStub:
        @pytest.mark.allow_direct_assert
        def join(self, *, timeout: float) -> None:
            assert timeout == pytest.approx(1.0)

    process = RunningProcess()

    _stop_watch_cli(process, ThreadStub(), ThreadStub())  # ty: ignore[arg-type]

    assert process.terminated
    assert process.killed


@pytest.mark.allow_direct_assert
def test_start_watch_cli_until_ready_stops_process_on_start_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test watch CLI startup cleanup runs when readiness wait fails."""

    class ProcessStub:
        pass

    class ThreadStub:
        pass

    stopped: list[ProcessStub] = []
    process = ProcessStub()
    stdout_thread = ThreadStub()
    stderr_thread = ThreadStub()

    @pytest.mark.allow_direct_assert
    def fake_start_watch_cli(
        input_path: Path, output_path: Path
    ) -> tuple[ProcessStub, list[str], list[str], ThreadStub, ThreadStub]:
        assert input_path.name == "schema.json"
        assert output_path.name == "output.py"
        return process, [], [], stdout_thread, stderr_thread

    @pytest.mark.allow_direct_assert
    def fake_wait_for_watch_cli(
        process_: ProcessStub,
        stdout_lines: list[str],
        stderr_lines: list[str],
        condition: Callable[[], bool],
        description: str,
    ) -> None:
        assert process_ is process
        assert not stdout_lines
        assert not stderr_lines
        assert description == "watch mode to start"
        assert not condition()
        raise KeyboardInterrupt

    @pytest.mark.allow_direct_assert
    def fake_stop_watch_cli(process_: ProcessStub, stdout_thread_: ThreadStub, stderr_thread_: ThreadStub) -> None:
        assert process_ is process
        assert stdout_thread_ is stdout_thread
        assert stderr_thread_ is stderr_thread
        stopped.append(process_)

    monkeypatch.setattr(sys.modules[__name__], "_start_watch_cli", fake_start_watch_cli)
    monkeypatch.setattr(sys.modules[__name__], "_wait_for_watch_cli", fake_wait_for_watch_cli)
    monkeypatch.setattr(sys.modules[__name__], "_stop_watch_cli", fake_stop_watch_cli)

    with pytest.raises(KeyboardInterrupt):
        _start_watch_cli_until_ready(Path("schema.json"), Path("output.py"))

    assert stopped == [process]


@pytest.mark.cli_doc(
    options=["--watch"],
    option_description="""Watch input file(s) for changes and regenerate output automatically.

The `--watch` flag enables continuous file monitoring mode. When enabled,
datamodel-codegen watches the input file or directory for changes and
automatically regenerates the output whenever changes are detected.
Press Ctrl+C to stop watching.

!!! warning "Requires extra dependency"

    The watch feature requires the `watch` extra:

    ```bash
    pip install 'datamodel-code-generator[watch]'
    ```""",
    input_schema="jsonschema/person.json",
    cli_args=["--watch", "--check"],
    expected_stdout="Error: --watch and --check cannot be used together",
    primary=True,
)
def test_watch_with_check_error(output_file: Path) -> None:
    """Watch mode cannot be used with --check mode.

    The `--watch` flag enables file watching for automatic regeneration.
    It cannot be combined with `--check` since check mode requires a single
    comparison, not continuous watching.
    """
    run_main_with_args(
        [
            "--watch",
            "--check",
            "--input",
            str(JSON_SCHEMA_DATA_PATH / "person.json"),
            "--output",
            str(output_file),
        ],
        expected_exit=Exit.ERROR,
    )


@pytest.mark.cli_doc(
    options=["--watch"],
    option_description="""Watch input file(s) for changes and regenerate output automatically.

The `--watch` flag monitors local files for changes. It requires a local file
path via `--input` and cannot be used with `--url` since remote URLs cannot
be watched for changes.""",
    cli_args=["--watch", "--url", "https://example.com/schema.json"],
    expected_stdout="Error: --watch requires --input file path",
)
def test_watch_with_url_error() -> None:
    """Watch mode requires a file path input, not a URL.

    The `--watch` flag monitors local files for changes. It cannot be used
    with `--url` since remote URLs cannot be watched for changes.
    """
    run_main_with_args(
        [
            "--watch",
            "--url",
            "https://example.com/schema.json",
        ],
        expected_exit=Exit.ERROR,
    )


def test_watch_without_input_error() -> None:
    """Watch mode requires --input file path."""
    run_main_with_args(
        ["--watch"],
        expected_exit=Exit.ERROR,
    )


def test_structured_output_rejects_overwriting_its_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Structured generation validates input/output conflicts before writing."""
    input_file = tmp_path / "schema.json"
    input_file.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")

    run_main_with_args(
        ["--input", str(input_file), "--output", str(input_file), "--output-format", "json"],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Output path must not overwrite an input path",
    )


def test_watch_without_watchfiles_installed(
    output_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test error message when watchfiles is not installed."""
    monkeypatch.setitem(sys.modules, "watchfiles", None)
    run_main_with_args(
        [
            "--watch",
            "--input",
            str(JSON_SCHEMA_DATA_PATH / "person.json"),
            "--output",
            str(output_file),
        ],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="pip install",
    )


@pytest.mark.allow_direct_assert
def test_main_watch_uses_watch_module_import_seam(
    output_file: Path, mocker: pytest.MockerFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test main resolves watch_and_regenerate through datamodel_code_generator.watch."""
    mock_generate = mocker.patch("datamodel_code_generator.__main__.run_generate_from_config", return_value=None)
    mock_watch = mocker.Mock(return_value=Exit.OK)
    watch_module = ModuleType("datamodel_code_generator.watch")
    watch_module.watch_and_regenerate = mock_watch
    monkeypatch.setitem(sys.modules, "datamodel_code_generator.watch", watch_module)

    run_main_with_args(
        [
            "--watch",
            "--input",
            str(JSON_SCHEMA_DATA_PATH / "person.json"),
            "--output",
            str(output_file),
        ],
    )

    mock_generate.assert_called_once()
    mock_watch.assert_called_once()
    config = mock_watch.call_args.args[0]
    assert config.watch is True
    assert config.input == JSON_SCHEMA_DATA_PATH / "person.json"


def test_get_watchfiles_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _get_watchfiles raises exception when watchfiles is not installed."""
    from datamodel_code_generator.watch import _get_watchfiles

    monkeypatch.setitem(sys.modules, "watchfiles", None)
    with pytest.raises(Exception, match="pip install"):
        _get_watchfiles()


def test_get_watchfiles_success() -> None:
    """Test _get_watchfiles returns watchfiles module when installed."""
    from datamodel_code_generator.watch import _get_watchfiles

    result = _get_watchfiles()
    assert_watchfiles_module(result)


@pytest.mark.allow_direct_assert
@pytest.mark.parametrize(
    ("platform", "expected"),
    [("darwin", True), ("linux", False)],
)
def test_watch_force_polling_policy(monkeypatch: pytest.MonkeyPatch, platform: str, expected: bool) -> None:
    """On macOS, watches use polling so atomic replacement cannot be missed."""
    from datamodel_code_generator.watch import _force_polling

    monkeypatch.setattr("datamodel_code_generator.watch.sys.platform", platform)

    assert _force_polling() is expected


@pytest.mark.cli_doc(
    options=["--watch", "--watch-delay"],
    option_description="""Set debounce delay in seconds for watch mode.

The `--watch-delay` option configures the debounce interval (default: 0.5 seconds)
for watch mode. This prevents multiple regenerations when files are rapidly
modified in succession. The delay ensures that after the last file change,
the generator waits the specified time before regenerating.

**Related:** [`--watch`](general-options.md#watch)""",
    input_schema="jsonschema/person.json",
    cli_args=["--watch", "--watch-delay", "1.5"],
    expected_stdout="Watching",
)
def test_watch_cli_regenerates_file_output_on_change(tmp_path: Path) -> None:
    """Watch mode regenerates file output when the input file changes.

    The `--watch` flag starts a file watcher that monitors the input file
    or directory for changes. The `--watch-delay` option sets the debounce
    delay in seconds (default: 0.5) to prevent multiple regenerations for
    rapid file changes. Press Ctrl+C to stop watching.
    """
    input_file = tmp_path / "schema.json"
    output_file = tmp_path / "output.py"
    input_file.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_file,
        output_file,
    )

    try:
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            input_file,
            WATCH_SCHEMA_CHANGED,
            lambda: _file_contains(output_file, "age: int | None = None"),
            "file output to be regenerated",
        )
        assert_output(output_file.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_file_change.py")
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


def test_watch_cli_regenerates_for_consecutive_input_changes_without_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep one watcher subscription active across consecutive dependent-file updates."""
    file_change_data_path = WATCH_DATA_PATH / "file_change"
    input_file = tmp_path / "schema.json"
    output_file = tmp_path / "output.py"
    input_file.write_text((file_change_data_path / "initial.json").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("WATCHFILES_FORCE_POLLING", "false")
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_file,
        output_file,
        ["--watch-delay", "0.05"],
    )

    try:
        time.sleep(WATCH_CLI_CHANGE_RETRY_SECONDS)
        completed_before_first_change = sum(line.strip() == "Done." for line in stdout_lines)
        first_update = tmp_path / "schema-first-update.json"
        first_update.write_text((file_change_data_path / "changed.json").read_text(encoding="utf-8"), encoding="utf-8")
        first_update.replace(input_file)
        _wait_for_watch_cli(
            process,
            stdout_lines,
            stderr_lines,
            lambda: (
                _file_contains(output_file, "age: int | None = None")
                and sum(line.strip() == "Done." for line in stdout_lines) > completed_before_first_change
            ),
            "the first consecutive input change to regenerate",
        )
        assert_output(output_file.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_file_change.py")
        completed_before_second_change = sum(line.strip() == "Done." for line in stdout_lines)
        time.sleep(0.05)
        second_update = tmp_path / "schema-second-update.json"
        second_update.write_text((file_change_data_path / "initial.json").read_text(encoding="utf-8"), encoding="utf-8")
        second_update.replace(input_file)
        _wait_for_watch_cli(
            process,
            stdout_lines,
            stderr_lines,
            lambda: (
                not _file_contains(output_file, "age: int | None = None")
                and sum(line.strip() == "Done." for line in stdout_lines) > completed_before_second_change
            ),
            "the second consecutive input change to regenerate without re-sending it",
        )
        assert_output(output_file.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_file_initial.py")
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


@pytest.mark.skipif(os.name == "nt", reason="the lexical input fixture requires POSIX symlinks")
def test_watch_cli_regenerates_when_a_symlinked_input_is_repointed(tmp_path: Path) -> None:
    """Watch both the lexical input link and its resolved schema target."""
    file_change_data_path = WATCH_DATA_PATH / "file_change"
    first_input = tmp_path / "first" / "schema.json"
    second_input = tmp_path / "second" / "schema.json"
    input_link = tmp_path / "input.json"
    output_file = tmp_path / "output.py"
    first_input.parent.mkdir()
    second_input.parent.mkdir()
    first_input.write_text((file_change_data_path / "initial.json").read_text(encoding="utf-8"), encoding="utf-8")
    second_input.write_text((file_change_data_path / "changed.json").read_text(encoding="utf-8"), encoding="utf-8")
    input_link.symlink_to(first_input.relative_to(tmp_path))
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_link,
        output_file,
    )

    try:
        time.sleep(WATCH_CLI_CHANGE_RETRY_SECONDS)
        input_link.unlink()
        input_link.symlink_to(second_input.relative_to(tmp_path))
        _wait_for_watch_cli(
            process,
            stdout_lines,
            stderr_lines,
            lambda: _file_contains(output_file, "age: int | None = None"),
            "the repointed symlink input to regenerate",
        )
        assert_output(output_file.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_file_change.py")
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


@pytest.mark.skipif(os.name == "nt", reason="the lexical input fixture requires POSIX symlinks")
def test_watch_cli_regenerates_when_a_symlinked_input_directory_is_repointed(tmp_path: Path) -> None:
    """Watch the symlink ancestor of a lexical ``link/schema.json`` input path."""
    file_change_data_path = WATCH_DATA_PATH / "file_change"
    first_directory = tmp_path / "first"
    second_directory = tmp_path / "second"
    input_link = tmp_path / "link"
    output_file = tmp_path / "output.py"
    first_directory.mkdir()
    second_directory.mkdir()
    (first_directory / "schema.json").write_text(
        (file_change_data_path / "initial.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (second_directory / "schema.json").write_text(
        (file_change_data_path / "changed.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    input_link.symlink_to(first_directory.relative_to(tmp_path), target_is_directory=True)
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_link / "schema.json",
        output_file,
    )

    try:
        time.sleep(WATCH_CLI_CHANGE_RETRY_SECONDS)
        input_link.unlink()
        input_link.symlink_to(second_directory.relative_to(tmp_path), target_is_directory=True)
        _wait_for_watch_cli(
            process,
            stdout_lines,
            stderr_lines,
            lambda: _file_contains(output_file, "age: int | None = None"),
            "the repointed symlink ancestor input to regenerate",
        )
        assert_output(output_file.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_file_change.py")
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


@pytest.mark.allow_direct_assert
def test_watch_cli_reloads_custom_formatter_module_after_edit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Editing a custom formatter reloads its actual module before the next generation."""
    file_change_data_path = WATCH_DATA_PATH / "file_change"
    project_directory = tmp_path / "project"
    formatter_directory = tmp_path / "formatter"
    input_file = project_directory / "schema.json"
    output_file = project_directory / "output.py"
    formatter_name = "reloadable_watch"
    formatter_file = formatter_directory / f"{formatter_name}.py"
    project_directory.mkdir()
    formatter_directory.mkdir()
    formatter_file.parent.mkdir(exist_ok=True)
    input_file.write_text((file_change_data_path / "initial.json").read_text(encoding="utf-8"), encoding="utf-8")
    (project_directory / "pyproject.toml").write_text(
        f'[tool.datamodel-codegen]\ncustom-formatters = "{formatter_name}"\n',
        encoding="utf-8",
    )
    shutil.copyfile(WATCH_DATA_PATH.parent / "python/custom_formatters/reloadable_watch.py", formatter_file)
    formatter_timestamp = 1_700_000_000
    os.utime(formatter_file, (formatter_timestamp, formatter_timestamp))
    previous_python_path = os.environ.get("PYTHONPATH")
    monkeypatch.setenv(
        "PYTHONPATH",
        str(formatter_directory)
        if previous_python_path is None
        else f"{formatter_directory}{os.pathsep}{previous_python_path}",
    )
    monkeypatch.setenv("WATCHFILES_FORCE_POLLING", "false")
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_file,
        output_file,
        working_directory=project_directory,
    )

    try:
        assert_output(output_file.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_custom_formatter_initial.py")
        time.sleep(WATCH_CLI_CHANGE_RETRY_SECONDS)
        formatter_update = formatter_directory / "reloadable-watch-update.py"
        shutil.copyfile(
            WATCH_DATA_PATH.parent / "python/custom_formatters/reloadable_watch_changed.py",
            formatter_update,
        )
        os.utime(formatter_file, (formatter_timestamp, formatter_timestamp))
        os.utime(formatter_update, (formatter_timestamp, formatter_timestamp))
        assert formatter_update.stat().st_size == formatter_file.stat().st_size
        formatter_update.replace(formatter_file)
        _wait_for_watch_cli(
            process,
            stdout_lines,
            stderr_lines,
            lambda: _file_contains(output_file, 'formatter_revision = "changed"'),
            "the edited custom formatter module to be reloaded",
        )
        assert_output(output_file.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_custom_formatter_changed.py")
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


@pytest.mark.allow_direct_assert
def test_watch_cli_reloads_custom_formatter_package_after_refactor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Package helpers refresh and a deleted helper can be replaced in one event window."""
    project_directory = tmp_path / "project"
    formatter_directory = tmp_path / "formatter"
    package_directory = formatter_directory / "reloadable_watch_package"
    input_file = project_directory / "schema.json"
    output_file = project_directory / "output.py"
    execution_marker = tmp_path / "formatter-executions.txt"
    helper_file = package_directory / "helper.py"
    project_directory.mkdir()
    package_directory.mkdir(parents=True)
    input_file.write_text((WATCH_DATA_PATH / "file_change/initial.json").read_text(encoding="utf-8"), encoding="utf-8")
    (project_directory / "pyproject.toml").write_text(
        '[tool.datamodel-codegen]\ncustom-formatters = "reloadable_watch_package"\n', encoding="utf-8"
    )
    shutil.copyfile(
        WATCH_DATA_PATH.parent / "python/custom_formatters/reloadable_watch_package/__init__.py",
        package_directory / "__init__.py",
    )
    shutil.copyfile(WATCH_DATA_PATH.parent / "python/custom_formatters/reloadable_watch_package/helper.py", helper_file)
    formatter_timestamp = 1_700_000_000
    os.utime(helper_file, (formatter_timestamp, formatter_timestamp))
    monkeypatch.setenv("PYTHONPATH", f"{formatter_directory}{os.pathsep}{os.environ.get('PYTHONPATH', '')}")
    monkeypatch.setenv("DATAMODEL_CODEGEN_FORMATTER_EXECUTIONS", str(execution_marker))
    monkeypatch.setenv("WATCHFILES_FORCE_POLLING", "false")
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_file,
        output_file,
        ["--watch-delay", "0.5"],
        working_directory=project_directory,
    )

    try:
        assert_output(output_file.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_custom_formatter_initial.py")
        assert_output(
            execution_marker.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_formatter_executed_once.txt"
        )
        time.sleep(WATCH_CLI_CHANGE_RETRY_SECONDS)
        helper_update = formatter_directory / "helper-update.py"
        shutil.copyfile(
            WATCH_DATA_PATH.parent / "python/custom_formatters/reloadable_watch_package/helper_changed.py",
            helper_update,
        )
        os.utime(helper_update, (formatter_timestamp, formatter_timestamp))
        assert helper_update.stat().st_size == helper_file.stat().st_size
        helper_update.replace(helper_file)
        _wait_for_watch_cli(
            process,
            stdout_lines,
            stderr_lines,
            lambda: _file_contains(output_file, 'formatter_revision = "changed"'),
            "the edited custom formatter helper to be reloaded",
        )
        assert_output(output_file.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_custom_formatter_changed.py")
        assert_output(
            execution_marker.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_formatter_executed_twice.txt"
        )
        time.sleep(WATCH_CLI_CHANGE_RETRY_SECONDS)
        shutil.copyfile(
            WATCH_DATA_PATH.parent / "python/custom_formatters/reloadable_watch_package/new_helper.py",
            package_directory / "new_helper.py",
        )
        package_update = formatter_directory / "package-update.py"
        shutil.copyfile(
            WATCH_DATA_PATH.parent / "python/custom_formatters/reloadable_watch_package/package_refactored.py",
            package_update,
        )
        package_update.replace(package_directory / "__init__.py")
        helper_file.unlink()
        _wait_for_watch_cli(
            process,
            stdout_lines,
            stderr_lines,
            lambda: _file_contains(output_file, 'formatter_revision = "refactored"'),
            "the refactored custom formatter package to be reloaded",
        )
        assert_output(
            output_file.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_custom_formatter_refactored.py"
        )
        assert_output(
            execution_marker.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_formatter_executed_thrice.txt"
        )
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


def test_watch_and_regenerate_without_input() -> None:
    """Test watch_and_regenerate returns error when input is None."""
    from datamodel_code_generator.__main__ import Config

    config = Config(input=None)
    run_watch_and_assert(config, expected_exit=Exit.ERROR)


def test_watch_and_regenerate_handles_exhausted_watcher(mocker: pytest.MockerFixture) -> None:
    """Test watch_and_regenerate exits when the watcher iterator is exhausted."""
    from datamodel_code_generator.__main__ import Config

    mock_watchfiles = mocker.Mock()
    mock_watchfiles.watch.return_value = iter(())
    config = Config(input=str(JSON_SCHEMA_DATA_PATH / "person.json"))

    mocker.patch("datamodel_code_generator.watch._get_watchfiles", return_value=mock_watchfiles)
    run_watch_and_assert(config)


@pytest.mark.allow_direct_assert
def test_watch_once_discards_a_stale_polling_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A timeout queued before regeneration cannot cause a redundant full rebuild afterward."""
    from datamodel_code_generator import watch as watch_module
    from datamodel_code_generator.__main__ import Config
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    input_file = tmp_path / "schema.json"
    input_file.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    config = Config(input=input_file)
    dependencies = WatchDependencies()
    dependencies.configure(config, config_values={})

    def publish_stale_timeout(
        _context: object,
        _watch_roots: object,
        _stop_event: object,
        condition: threading.Condition,
        state: watch_module._WatcherState,
    ) -> None:
        with condition:
            state.add_changes(set())
            state.exhausted = True
            condition.notify()

    monkeypatch.setattr(watch_module, "_watch_changes", publish_stale_timeout)
    context = watch_module._WatchContext(SimpleNamespace(), config, dependencies, lambda: Exit.OK)

    assert not watch_module._watch_once(context, dependencies.watch_roots(), catch_up=False)


@pytest.mark.allow_direct_assert
def test_watch_dependency_paths_are_limited_to_generation_inputs(tmp_path: Path) -> None:
    """Dependency collection retains paths, not parsed source content or output files."""
    from datamodel_code_generator.__main__ import Config
    from datamodel_code_generator.watch import _watch_filter
    from datamodel_code_generator.watch_dependencies import (
        WatchDependencies,
        _nearest_pyproject_toml,
        record_local_dependency,
    )

    input_file = tmp_path / "schema.json"
    config_file = tmp_path / "aliases.json"
    header_file = tmp_path / "header.txt"
    template_dir = tmp_path / "templates"
    output_dir = tmp_path / "models"
    input_file.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    config_file.write_text("{}", encoding="utf-8")
    header_file.write_text("# header", encoding="utf-8")
    template_dir.mkdir()
    output_dir.mkdir()
    referenced_file = tmp_path / "reference.json"
    referenced_file.write_text("{}", encoding="utf-8")

    dependencies = WatchDependencies()
    config = Config(
        input=input_file,
        output=output_dir,
        custom_file_header_path=header_file,
        custom_template_dir=template_dir,
    )
    dependencies.configure(config, config_values={"aliases": str(config_file)})
    with dependencies.generation():
        record_local_dependency(referenced_file)

    assert dependencies.files == frozenset({
        PROJECT_ROOT / "pyproject.toml",
        input_file,
        config_file,
        header_file,
        referenced_file,
    })
    assert dependencies.directories == frozenset({template_dir})
    watch_filter = _watch_filter(dependencies)
    assert watch_filter(None, str(referenced_file))
    assert watch_filter(None, str(template_dir / "BaseModel.jinja2"))
    assert not dependencies.accepts_event(output_dir / "model.py")
    assert not watch_filter(None, str(output_dir / "model.py"))
    assert _nearest_pyproject_toml(tmp_path) is None

    with pytest.raises(RuntimeError, match=WATCH_GENERATION_ERROR):
        _record_failed_dependency(dependencies, tmp_path / "attempted.json")

    assert referenced_file in dependencies.files
    assert tmp_path / "attempted.json" in dependencies.files


@pytest.mark.allow_direct_assert
def test_watch_dependencies_collect_module_formatter_and_protobuf_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generation collection includes module, formatter, and nested protobuf resources."""
    import py_compile
    from importlib.util import cache_from_source

    from datamodel_code_generator.format import CodeFormatter, Formatter, PythonVersionMin
    from datamodel_code_generator.parser.protobuf import ProtobufParser
    from datamodel_code_generator.watch_dependencies import (
        WatchDependencies,
        record_local_dependency,
        record_module_dependency,
    )

    source_module = tmp_path / "custom_module.py"
    source_module.write_text("value = 1\n", encoding="utf-8")
    cached_module = Path(cache_from_source(str(source_module)))
    py_compile.compile(str(source_module), cfile=str(cached_module), doraise=True)
    module = ModuleType("custom_module")
    module.__file__ = str(cached_module)
    module_dependencies = WatchDependencies()
    with module_dependencies.generation():
        record_module_dependency(module)
    assert source_module in module_dependencies.files
    assert record_module_dependency(ModuleType("built_in")) is None
    record_local_dependency(tmp_path / "inactive.json")
    assert tmp_path / "inactive.json" not in module_dependencies.files

    formatter_dependencies = WatchDependencies()
    with formatter_dependencies.generation():
        CodeFormatter(
            PythonVersionMin,
            custom_formatters=["tests.data.python.custom_formatters.add_comment"],
            formatters=[Formatter.BUILTIN],
        )
    assert PROJECT_ROOT / "tests/data/python/custom_formatters/add_comment.py" in formatter_dependencies.files

    package_name = "tests.data.python.custom_formatters.reloadable_watch_package"
    import_module(package_name)
    package_dependencies = WatchDependencies()
    with package_dependencies.generation():
        CodeFormatter(PythonVersionMin, custom_formatters=[package_name], formatters=[Formatter.BUILTIN])
        CodeFormatter(PythonVersionMin, custom_formatters=[package_name], formatters=[Formatter.BUILTIN])
    assert (
        PROJECT_ROOT / "tests/data/python/custom_formatters/reloadable_watch_package/__init__.py"
        in package_dependencies.files
    )
    assert (
        PROJECT_ROOT / "tests/data/python/custom_formatters/reloadable_watch_package/helper.py"
        in package_dependencies.files
    )
    monkeypatch.delitem(sys.modules, f"{package_name}.helper")
    recovered_package_dependencies = WatchDependencies()
    with recovered_package_dependencies.generation():
        CodeFormatter(PythonVersionMin, custom_formatters=[package_name], formatters=[Formatter.BUILTIN])
    assert (
        PROJECT_ROOT / "tests/data/python/custom_formatters/reloadable_watch_package/helper.py"
        in recovered_package_dependencies.files
    )

    proto_directory = tmp_path / "protos"
    proto_directory.mkdir()
    root_proto = proto_directory / "root.proto"
    child_proto = proto_directory / "child.proto"
    nested_proto = proto_directory / "nested.proto"
    root_proto.write_text('import "child.proto";\nimport "child.proto";\n', encoding="utf-8")
    child_proto.write_text('import "nested.proto";\n', encoding="utf-8")
    protobuf_dependencies = WatchDependencies()
    with protobuf_dependencies.generation():
        ProtobufParser._record_lexical_import_candidates(
            SimpleNamespace(config=SimpleNamespace(encoding="utf-8")),
            [root_proto],
            [tmp_path / "prepared", proto_directory],
        )
        ProtobufParser._record_descriptor_dependencies(
            SimpleNamespace(file=[SimpleNamespace(name=child_proto.name)]),
            [tmp_path / "prepared", proto_directory],
        )
    assert child_proto in protobuf_dependencies.files
    assert nested_proto in protobuf_dependencies.files


@pytest.mark.allow_direct_assert
def test_watch_formatter_package_reload_is_transactional(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing later child restores earlier children before the next generation."""
    from datamodel_code_generator.format import CodeFormatter, Formatter, PythonVersionMin
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    fixture_directory = PROJECT_ROOT / "tests/data/python/custom_formatters/transactional_watch_package"
    formatter_directory = tmp_path / "formatters"
    package_directory = formatter_directory / "transactional_watch_package"
    package_directory.mkdir(parents=True)
    for filename in ("__init__.py", "child_a.py", "child_b.py"):
        shutil.copyfile(fixture_directory / filename, package_directory / filename)
    monkeypatch.syspath_prepend(str(formatter_directory))
    package_name = package_directory.name
    try:
        dependencies = WatchDependencies()
        with dependencies.generation():
            CodeFormatter(
                PythonVersionMin,
                custom_formatters=[package_name],
                formatters=[Formatter.BUILTIN],
            )
        old_package = sys.modules[package_name]
        old_child_a = sys.modules[f"{package_name}.child_a"]
        old_child_b = sys.modules[f"{package_name}.child_b"]
        old_formatter_class = old_child_a.CodeFormatter
        original_modules = {
            loaded_name: loaded_module
            for loaded_name, loaded_module in sys.modules.copy().items()
            if loaded_name == package_name or loaded_name.startswith(f"{package_name}.")
        }
        original_namespaces = {
            old_package: old_package.__dict__.copy(),
            old_child_a: old_child_a.__dict__.copy(),
            old_child_b: old_child_b.__dict__.copy(),
        }

        shutil.copyfile(fixture_directory / "child_a_changed.py", package_directory / "child_a.py")
        shutil.copyfile(fixture_directory / "child_b_syntax_error.txt", package_directory / "child_b.py")
        with pytest.raises(SyntaxError), dependencies.generation():
            CodeFormatter(PythonVersionMin, custom_formatters=[package_name], formatters=[Formatter.BUILTIN])
        assert sys.modules[package_name] is old_package
        assert sys.modules[f"{package_name}.child_a"] is old_child_a
        assert sys.modules[f"{package_name}.child_b"] is old_child_b
        assert old_child_a.CodeFormatter is old_formatter_class
        assert old_child_b.REVISION == "initial"
        assert {
            loaded_name: loaded_module
            for loaded_name, loaded_module in sys.modules.copy().items()
            if loaded_name == package_name or loaded_name.startswith(f"{package_name}.")
        } == original_modules
        assert all(module.__dict__ == namespace for module, namespace in original_namespaces.items())

        shutil.copyfile(fixture_directory / "child_b_runtime_error.py", package_directory / "child_b.py")
        with pytest.raises(RuntimeError, match="formatter child failed"), dependencies.generation():
            CodeFormatter(PythonVersionMin, custom_formatters=[package_name], formatters=[Formatter.BUILTIN])
        assert sys.modules[package_name] is old_package
        assert sys.modules[f"{package_name}.child_a"] is old_child_a
        assert sys.modules[f"{package_name}.child_b"] is old_child_b
        assert old_child_a.CodeFormatter is old_formatter_class
        assert old_child_b.REVISION == "initial"
        assert {
            loaded_name: loaded_module
            for loaded_name, loaded_module in sys.modules.copy().items()
            if loaded_name == package_name or loaded_name.startswith(f"{package_name}.")
        } == original_modules
        assert all(module.__dict__ == namespace for module, namespace in original_namespaces.items())

        shutil.copyfile(fixture_directory / "child_b.py", package_directory / "child_b.py")
        with dependencies.generation():
            formatter = CodeFormatter(
                PythonVersionMin,
                custom_formatters=[package_name],
                formatters=[Formatter.BUILTIN],
            )
        assert_output(
            formatter.format_code("value=1\n"), EXPECTED_MAIN_PATH / "watch_transactional_formatter_changed.txt"
        )
    finally:
        for loaded_name in tuple(sys.modules.copy()):
            if loaded_name == package_name or loaded_name.startswith(f"{package_name}."):
                sys.modules.pop(loaded_name, None)


@pytest.mark.allow_direct_assert
def test_watch_formatter_package_refreshes_sibling_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A package refresh leaves sibling execution order to its current root imports."""
    from datamodel_code_generator.format import CodeFormatter, Formatter, PythonVersionMin
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    fixture_directory = PROJECT_ROOT / "tests/data/python/custom_formatters/sibling_watch_package"
    formatter_directory = tmp_path / "formatters"
    package_directory = formatter_directory / fixture_directory.name
    shutil.copytree(fixture_directory, package_directory)
    monkeypatch.syspath_prepend(str(formatter_directory))
    package_name = package_directory.name
    source_timestamp = 1_700_000_000
    sibling_source = package_directory / "a.py"
    os.utime(sibling_source, (source_timestamp, source_timestamp))
    try:
        dependencies = WatchDependencies()
        with dependencies.generation():
            formatter = CodeFormatter(
                PythonVersionMin,
                custom_formatters=[package_name],
                formatters=[Formatter.BUILTIN],
            )
        assert '# formatter_revision = "sibling_initial"' in formatter.format_code("value = 1\n")

        updated_sibling_source = formatter_directory / "a-update.py"
        shutil.copyfile(fixture_directory / "a_changed.py", updated_sibling_source)
        os.utime(updated_sibling_source, (source_timestamp, source_timestamp))
        assert updated_sibling_source.stat().st_size == sibling_source.stat().st_size
        updated_sibling_source.replace(sibling_source)
        with dependencies.generation():
            formatter = CodeFormatter(
                PythonVersionMin,
                custom_formatters=[package_name],
                formatters=[Formatter.BUILTIN],
            )
        assert '# formatter_revision = "sibling_changed"' in formatter.format_code("value = 1\n")
        assert sys.modules[f"{package_name}.a"].CodeFormatter is sys.modules[package_name].CodeFormatter
    finally:
        for loaded_name in tuple(sys.modules.copy()):
            if loaded_name == package_name or loaded_name.startswith(f"{package_name}."):
                sys.modules.pop(loaded_name, None)


@pytest.mark.allow_direct_assert
def test_watch_formatter_package_drops_unimported_invalid_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale present child cannot block a current root that no longer imports it."""
    from datamodel_code_generator.format import (
        _WATCH_FORMATTER_STATES,
        CodeFormatter,
        Formatter,
        PythonVersionMin,
    )
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    fixture_directory = PROJECT_ROOT / "tests/data/python/custom_formatters/stale_watch_package"
    formatter_directory = tmp_path / "formatters"
    package_directory = formatter_directory / fixture_directory.name
    shutil.copytree(fixture_directory, package_directory)
    monkeypatch.syspath_prepend(str(formatter_directory))
    package_name = package_directory.name
    try:
        dependencies = WatchDependencies()
        with dependencies.generation():
            formatter = CodeFormatter(
                PythonVersionMin,
                custom_formatters=[package_name],
                formatters=[Formatter.BUILTIN],
            )
        assert f"{package_name}.stale" in sys.modules
        assert '# formatter_revision = "active"' in formatter.format_code("value = 1\n")

        shutil.copyfile(fixture_directory / "package_refactored.py", package_directory / "__init__.py")
        shutil.copyfile(fixture_directory / "stale_syntax_error.txt", package_directory / "stale.py")
        with dependencies.generation():
            formatter = CodeFormatter(
                PythonVersionMin,
                custom_formatters=[package_name],
                formatters=[Formatter.BUILTIN],
            )
        assert '# formatter_revision = "active"' in formatter.format_code("value = 1\n")
        assert {
            loaded_name
            for loaded_name in sys.modules.copy()
            if loaded_name == package_name or loaded_name.startswith(f"{package_name}.")
        } == {package_name, f"{package_name}.active"}
        assert _WATCH_FORMATTER_STATES[sys.modules[package_name]].module_names == (
            package_name,
            f"{package_name}.active",
        )
        assert package_directory / "stale.py" not in dependencies.files
    finally:
        for loaded_name in tuple(sys.modules.copy()):
            if loaded_name == package_name or loaded_name.startswith(f"{package_name}."):
                sys.modules.pop(loaded_name, None)


@pytest.mark.allow_direct_assert
def test_watch_dependency_state_uses_weakrefable_slots() -> None:
    """Watch state remains slotted while allowing lifecycle weak references."""
    import weakref

    from datamodel_code_generator.watch_dependencies import WatchDependencies, collector_identity

    dependencies = WatchDependencies()
    with dependencies.generation():
        collector = collector_identity()
        assert collector is not None
        assert not hasattr(collector, "__dict__")
        assert weakref.ref(collector)() is collector
    assert not hasattr(dependencies, "__dict__")
    assert weakref.ref(dependencies)() is dependencies


@pytest.mark.allow_direct_assert
def test_watch_formatter_state_does_not_retain_generation() -> None:
    """Formatter reload state keeps neither a completed collector nor its session alive."""
    import gc
    import weakref

    from datamodel_code_generator.format import CodeFormatter, Formatter, PythonVersionMin
    from datamodel_code_generator.watch_dependencies import WatchDependencies, collector_identity

    package_name = "tests.data.python.custom_formatters.reloadable_watch_package"
    dependencies = WatchDependencies()
    with dependencies.generation():
        formatter = CodeFormatter(
            PythonVersionMin,
            custom_formatters=[package_name],
            formatters=[Formatter.BUILTIN],
        )
        collector = collector_identity()
        assert collector is not None
        collector_reference = weakref.ref(collector)
    dependencies_reference = weakref.ref(dependencies)
    del collector, dependencies, formatter
    gc.collect()
    assert collector_reference() is None
    assert dependencies_reference() is None


@pytest.mark.allow_direct_assert
def test_local_package_module_snapshot_tolerates_concurrent_imports(tmp_path: Path) -> None:
    """Normal imports cannot resize the module registry during package discovery."""
    from datamodel_code_generator.format import _local_package_modules

    package = ModuleType("watch_snapshot_package")
    package.__path__ = [str(tmp_path)]
    seeded_names = [f"watch_snapshot_seed_{index}" for index in range(10_000)]
    racing_names = [f"watch_snapshot_race_{index}" for index in range(20_000)]
    for loaded_name in seeded_names:
        sys.modules[loaded_name] = ModuleType(loaded_name)
    start = threading.Event()

    def import_modules() -> None:
        start.wait()
        for loaded_name in racing_names:
            sys.modules[loaded_name] = ModuleType(loaded_name)

    import_thread = threading.Thread(target=import_modules)
    import_thread.start()
    try:
        start.set()
        assert _local_package_modules(package, previous_modules=frozenset()) == (package.__name__,)
    finally:
        import_thread.join()
        for loaded_name in (*seeded_names, *racing_names):
            sys.modules.pop(loaded_name, None)


@pytest.mark.allow_direct_assert
def test_watch_formatter_refresh_handles_unavailable_source_and_parent_bindings() -> None:
    """Unavailable package source is retained and rollback restores either parent state."""
    from importlib.machinery import ModuleSpec

    from datamodel_code_generator.format import (
        _MISSING_PARENT_ATTRIBUTE,
        _fresh_watch_package,
        _prepare_watch_module,
        _restore_watch_package,
    )

    source_error_message = "formatter source is unavailable"

    def raise_source_error(_module_name: str) -> NoReturn:
        raise OSError(source_error_message)

    unavailable_source_package = ModuleType("watch_unavailable_source_package")
    unavailable_source_package.__spec__ = ModuleSpec(
        unavailable_source_package.__name__, SimpleNamespace(get_source=raise_source_error), is_package=True
    )
    assert _prepare_watch_module(unavailable_source_package) is None

    unavailable_package = ModuleType("watch_unavailable_package")
    unavailable_package.__spec__ = ModuleSpec(
        unavailable_package.__name__,
        SimpleNamespace(get_source=lambda _: None),
        is_package=True,
    )
    unavailable_package.__path__ = []
    sys.modules[unavailable_package.__name__] = unavailable_package
    assert _prepare_watch_module(unavailable_package) is None
    assert _fresh_watch_package(unavailable_package) is unavailable_package

    parent_module = ModuleType("watch_restore_parent")
    module_name = f"{parent_module.__name__}.child"
    original_module = ModuleType(module_name)
    namespace = original_module.__dict__.copy()
    restored_attribute = object()
    sys.modules[parent_module.__name__] = parent_module
    sys.modules[module_name] = original_module
    _restore_watch_package(
        module_name,
        {module_name: original_module},
        {original_module: namespace},
        parent_module,
        restored_attribute,
    )
    assert parent_module.child is restored_attribute
    parent_module.__dict__.pop("child")
    _restore_watch_package(
        module_name,
        {module_name: original_module},
        {original_module: namespace},
        parent_module,
        _MISSING_PARENT_ATTRIBUTE,
    )
    assert not hasattr(parent_module, "child")
    for loaded_name in (unavailable_package.__name__, module_name, parent_module.__name__):
        sys.modules.pop(loaded_name, None)


@pytest.mark.allow_direct_assert
def test_watch_source_loader_finder_handles_unavailable_and_non_source_children(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Source-first imports preserve unsupported children without inspecting unrelated imports."""
    from datamodel_code_generator.format import _WatchSourceFinder, _WatchSourceLoader

    fallback_module = ModuleType("watch_fallback_module")

    def execute_fallback(module: ModuleType) -> None:
        module.__dict__["used_fallback"] = True

    loader = _WatchSourceLoader(
        SimpleNamespace(get_source=lambda _module_name: None, exec_module=execute_fallback), None
    )
    loader.exec_module(fallback_module)
    assert fallback_module.used_fallback

    loader_without_executor = _WatchSourceLoader(SimpleNamespace(get_source=lambda _module_name: None), None)
    with pytest.raises(ImportError, match="cannot load module 'watch_loader_without_executor'"):
        loader_without_executor.exec_module(ModuleType("watch_loader_without_executor"))

    package_name = "watch_finder_package"
    package = ModuleType(package_name)
    package.__path__ = [str(tmp_path)]
    monkeypatch.setitem(sys.modules, package_name, package)
    (tmp_path / "namespace_child").mkdir()
    finder = _WatchSourceFinder(package_name)

    assert finder.find_spec("unrelated_module") is None
    assert finder.find_spec(f"{package_name}.missing_child", []) is None
    namespace_spec = finder.find_spec(f"{package_name}.namespace_child", [str(tmp_path)])
    assert namespace_spec is not None
    assert namespace_spec.loader is None


@pytest.mark.allow_direct_assert
def test_watch_formatter_cleanup_allows_a_package_to_remove_its_finder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A formatter package may alter the import hooks while its source executes."""
    from datamodel_code_generator.format import _fresh_watch_package

    package_name = "watch_finder_cleanup_package"
    formatter_directory = tmp_path / "formatters"
    package_directory = formatter_directory / package_name
    package_directory.mkdir(parents=True)
    package_source = package_directory / "__init__.py"
    package_source.write_text("pass\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(formatter_directory))
    package = import_module(package_name)
    package_source.write_text("import sys\nsys.meta_path.pop(0)\n", encoding="utf-8")
    try:
        assert _fresh_watch_package(package) is not package
    finally:
        sys.modules.pop(package_name, None)


@pytest.mark.allow_direct_assert
def test_watch_dependencies_record_regular_yaml_loads_and_direct_files(tmp_path: Path) -> None:
    """Collector bridges capture regular YAML loads while direct additions stay available."""
    from datamodel_code_generator import load_yaml_dict_from_path
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    yaml_file = tmp_path / "schema.yaml"
    yaml_file.write_text("title: WatchedPerson\n", encoding="utf-8")
    dependencies = WatchDependencies()
    with dependencies.generation():
        assert load_yaml_dict_from_path(yaml_file, "utf-8") == {"title": "WatchedPerson"}
    dependencies.record_file(yaml_file)

    assert yaml_file in dependencies.files
    assert dependencies.output is None


@pytest.mark.allow_direct_assert
def test_watch_dependencies_continue_when_symlink_inspection_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An inaccessible symlink ancestor retains the lexical dependency path."""
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    input_file = tmp_path / "schema.json"
    input_file.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    error_message = "unavailable"

    def raise_symlink_error(_path: Path) -> bool:
        raise OSError(error_message)

    monkeypatch.setattr(Path, "is_symlink", raise_symlink_error)
    dependencies = WatchDependencies()
    dependencies.add_file(input_file)

    assert dependencies.includes(input_file)


@pytest.mark.allow_direct_assert
def test_watch_custom_formatter_without_source_reuses_loaded_module() -> None:
    """Non-source custom modules keep their stable loaded object in watch collection."""
    from datamodel_code_generator.format import _fresh_watch_module, _module_source_path

    formatter_module = ModuleType("formatter_without_source")
    invalid_cached_module = ModuleType("formatter_with_invalid_cache")
    invalid_cached_module.__file__ = "formatter.pyc"

    assert _fresh_watch_module(formatter_module) is formatter_module
    assert _module_source_path(formatter_module) is None
    assert _module_source_path(invalid_cached_module) is None


@pytest.mark.allow_direct_assert
def test_protobuf_lexical_sources_exclude_prepared_inputs() -> None:
    """In-memory protobuf sources never expose temporary compiler inputs to watch."""
    from datamodel_code_generator.parser.protobuf import ProtobufParser

    assert ProtobufParser._lexical_source_files(SimpleNamespace(source="syntax = 'proto3';")) == ()


@pytest.mark.allow_direct_assert
def test_protobuf_descriptor_collection_handles_no_persistent_include_path() -> None:
    """Descriptors without persistent include roots add no watch dependency."""
    from google.protobuf import descriptor_pb2

    from datamodel_code_generator.parser.protobuf import ProtobufParser
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    descriptor_set = descriptor_pb2.FileDescriptorSet()
    descriptor_set.file.add().name = "google/protobuf/empty.proto"
    dependencies = WatchDependencies()
    with dependencies.generation():
        ProtobufParser._record_descriptor_dependencies(descriptor_set, [])

    assert not dependencies.files


@pytest.mark.allow_direct_assert
def test_protobuf_missing_import_candidates_exclude_preparer_paths(tmp_path: Path) -> None:
    """Lexical missing imports retain project paths without deleted preparer roots."""
    from datamodel_code_generator.parser.protobuf import ProtobufParser
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    schema_directory = tmp_path / "project"
    preparer_directory = tmp_path / "deleted-preparer"
    root_proto = schema_directory / "root.proto"
    schema_directory.mkdir()
    root_proto.write_text('import "nested/missing.proto";\n', encoding="utf-8")
    parser = SimpleNamespace(source=root_proto, base_path=schema_directory, config=SimpleNamespace(encoding="utf-8"))
    lexical_sources = ProtobufParser._lexical_source_files(parser)
    persistent_roots = ProtobufParser._persistent_include_paths(parser, lexical_sources)
    dependencies = WatchDependencies()
    with dependencies.generation():
        ProtobufParser._record_lexical_import_candidates(parser, lexical_sources, persistent_roots)

    assert persistent_roots == (schema_directory,)
    assert schema_directory / "nested/missing.proto" in dependencies.files
    assert not any(path.is_relative_to(preparer_directory) for path in dependencies.files)


@pytest.mark.allow_direct_assert
def test_protobuf_lexical_self_import_with_parent_traversal_terminates(tmp_path: Path) -> None:
    """A normalized self-import is collected once instead of growing the traversal path."""
    from datamodel_code_generator.parser.protobuf import ProtobufParser
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    schema_directory = tmp_path / "schemas"
    nested_directory = schema_directory / "nested"
    root_proto = schema_directory / "root.proto"
    nested_directory.mkdir(parents=True)
    root_proto.write_text('import "nested/../root.proto";\n', encoding="utf-8")
    dependencies = WatchDependencies()
    with dependencies.generation():
        ProtobufParser._record_lexical_import_candidates(
            SimpleNamespace(config=SimpleNamespace(encoding="utf-8")),
            [root_proto],
            [schema_directory],
        )

    assert dependencies.files == frozenset({root_proto})


@pytest.mark.allow_direct_assert
def test_watch_state_discards_events_after_its_diagnostic_sample_is_full() -> None:
    """A full sample preserves the dirty flag without retaining another event."""
    from datamodel_code_generator.watch import _PENDING_CHANGE_SAMPLE_LIMIT, _WatcherState

    state = _WatcherState({(None, str(index)) for index in range(_PENDING_CHANGE_SAMPLE_LIMIT)})
    state.add_changes({(None, "discarded")})

    assert state.has_pending_changes
    assert len(state.pending_change_sample) == _PENDING_CHANGE_SAMPLE_LIMIT


@pytest.mark.allow_direct_assert
def test_watch_dependencies_publish_complete_generation_graphs(tmp_path: Path) -> None:
    """Configure and generation publication never expose a temporary dependency gap."""
    from datamodel_code_generator.__main__ import Config
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    first_input = tmp_path / "first.json"
    second_input = tmp_path / "second.json"
    failed_input = tmp_path / "failed.json"
    for path in (first_input, second_input, failed_input):
        path.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    dependencies = WatchDependencies()
    dependencies.configure(Config(input=first_input), config_values={})
    with dependencies.generation():
        dependencies.record_file(first_input)
    dependencies.configure(Config(input=second_input), config_values={})

    assert dependencies.includes(first_input)
    assert dependencies.includes(second_input)
    with dependencies.generation():
        dependencies.record_file(second_input)
    assert dependencies.includes(second_input)
    assert not dependencies.includes(first_input)

    dependencies.configure(Config(input=first_input), config_values={})
    with pytest.raises(RuntimeError, match=WATCH_GENERATION_ERROR):
        _record_failed_dependency(dependencies, failed_input)

    assert dependencies.includes(first_input)
    assert dependencies.includes(second_input)
    assert dependencies.includes(failed_input)


@pytest.mark.allow_direct_assert
def test_watch_dependencies_do_not_watch_the_filesystem_root_for_system_symlink_ancestors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lexical path below ``/tmp`` never promotes the stable system link parent to ``/``."""
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    system_link = Path("/tmp")
    original_is_symlink = Path.is_symlink

    def is_system_link(path: Path) -> bool:
        return path == system_link or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", is_system_link)
    dependencies = WatchDependencies()
    dependencies.add_file(system_link / "dcg-watch-root-protection/schema.json")

    assert Path("/") not in dependencies.watch_roots()


@pytest.mark.allow_direct_assert
def test_watch_dependencies_exclude_an_unavailable_filesystem_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing filesystem root ends ancestor lookup without adding a watch root."""
    from datamodel_code_generator.watch_dependencies import WatchDependencies, _nearest_existing_directory

    root = Path(Path.cwd().anchor)
    original_is_dir = Path.is_dir

    def is_unavailable(path: Path) -> bool:
        return False if path == root else original_is_dir(path)

    monkeypatch.setattr(Path, "is_dir", is_unavailable)
    dependencies = WatchDependencies()
    dependencies.add_file(root / "unavailable" / "schema.json")

    assert _nearest_existing_directory(root) == root
    assert not dependencies.watch_roots()


@pytest.mark.allow_direct_assert
def test_watch_dependencies_collapse_nested_watch_roots(tmp_path: Path) -> None:
    """A parent root recursively covers its nested dependency roots."""
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    formatter_directory = tmp_path / "formatter"
    package_directory = formatter_directory / "package"
    package_directory.mkdir(parents=True)
    dependencies = WatchDependencies()
    dependencies.add_file(formatter_directory / "module.py")
    dependencies.add_file(package_directory / "helper.py")

    assert dependencies.watch_roots() == (formatter_directory,)


@pytest.mark.allow_direct_assert
def test_watch_dependencies_keep_configured_directory_watch_roots_at_the_parent(tmp_path: Path) -> None:
    """Configured directories can be deleted and recreated without replacing the watcher."""
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    external_directory = tmp_path / "external"
    template_directory = external_directory / "templates"
    external_directory.mkdir()
    dependencies = WatchDependencies()
    dependencies.add_directory(template_directory)

    assert dependencies.watch_roots() == (external_directory,)
    template_directory.mkdir()
    with dependencies.generation():
        pass
    assert dependencies.watch_roots() == (external_directory,)


@pytest.mark.allow_direct_assert
def test_watch_dependencies_accepts_external_parent_events_only_while_polling(tmp_path: Path) -> None:
    """Polling can handle the directory event emitted for an external dependency replacement."""
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    formatter_directory = tmp_path / "formatter"
    formatter_directory.mkdir()
    dependencies = WatchDependencies()
    dependencies.add_file(formatter_directory / "custom_formatter.py")

    assert not dependencies.accepts_event(formatter_directory)
    assert dependencies.accepts_event(formatter_directory, accept_directory_events=True)


@pytest.mark.allow_direct_assert
def test_watch_dependencies_ignore_unreadable_or_unrelated_polling_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Polling ignores a directory it cannot inspect and one without dependencies."""
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    input_file = tmp_path / "schema.json"
    unrelated_directory = tmp_path / "unrelated"
    input_file.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    unrelated_directory.mkdir()
    dependencies = WatchDependencies()
    dependencies.add_file(input_file)
    original_is_dir = Path.is_dir
    error_message = "unreadable"

    def raise_directory_error(path: Path) -> bool:
        if path == tmp_path:
            raise OSError(error_message)
        return original_is_dir(path)

    monkeypatch.setattr(Path, "is_dir", raise_directory_error)

    assert not dependencies.accepts_event(tmp_path, accept_directory_events=True)
    assert not dependencies.accepts_event(unrelated_directory, accept_directory_events=True)


@pytest.mark.allow_direct_assert
def test_watch_polling_filters_unchanged_timeouts_and_late_directory_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A second fingerprint-changing timeout reaches the regeneration hand-off."""
    from datamodel_code_generator import watch as watch_module
    from datamodel_code_generator.__main__ import Config
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    input_file = tmp_path / "schema.json"
    output_file = tmp_path / "output.py"
    input_file.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    config = Config(input=input_file, output=output_file, watch_delay=0.05)
    dependencies = WatchDependencies()
    dependencies.configure(config, config_values={})
    replacement = tmp_path / "replacement.json"
    replacement.write_text(WATCH_SCHEMA_CHANGED, encoding="utf-8")
    watch_options: dict[str, object] = {}

    class PollingEvents:
        @staticmethod
        def watch(*_paths: object, **watch_kwargs: object) -> object:
            watch_options.update(watch_kwargs)
            yield set()
            yield {(None, str(tmp_path))}
            replacement.replace(input_file)
            yield set()
            yield set()

    monkeypatch.setattr(watch_module.sys, "platform", "darwin")
    condition = threading.Condition()
    state = watch_module._WatcherState(set())
    watch_module._watch_changes(
        watch_module._WatchContext(PollingEvents(), config, dependencies, lambda: Exit.OK),
        dependencies.watch_roots(),
        threading.Event(),
        condition,
        state,
    )

    assert state.has_pending_changes
    assert not state.pending_change_sample
    assert state.exhausted
    assert watch_options["debounce"] == 50
    assert watch_options["step"] == 50
    with dependencies.generation():
        pass

    class NativeEvents:
        @staticmethod
        def watch(*_paths: object, **_kwargs: object) -> object:
            yield set()

    monkeypatch.setattr(watch_module.sys, "platform", "linux")
    native_state = watch_module._WatcherState(set())
    watch_module._watch_changes(
        watch_module._WatchContext(NativeEvents(), config, dependencies, lambda: Exit.OK),
        dependencies.watch_roots(),
        threading.Event(),
        threading.Condition(),
        native_state,
    )
    assert not native_state.has_pending_changes
    assert native_state.exhausted


@pytest.mark.allow_direct_assert
def test_watch_dependencies_rejects_polling_directory_events_containing_output(tmp_path: Path) -> None:
    """Output-parent polling events cannot trigger a regeneration loop."""
    from datamodel_code_generator.__main__ import Config
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    project_directory = tmp_path / "project"
    project_directory.mkdir()
    input_file = project_directory / "schema.json"
    output_file = project_directory / "output.py"
    input_file.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    dependencies = WatchDependencies()
    dependencies.configure(Config(input=input_file, output=output_file), config_values={})

    assert not dependencies.accepts_event(project_directory, accept_directory_events=True)


@pytest.mark.allow_direct_assert
def test_watch_dependencies_accepts_polling_parent_events_for_changed_inputs(tmp_path: Path) -> None:
    """A polling parent event is kept when an atomic input replacement changed its fingerprint."""
    from datamodel_code_generator.__main__ import Config
    from datamodel_code_generator.watch_dependencies import WatchDependencies, _path_fingerprint

    project_directory = tmp_path / "project"
    project_directory.mkdir()
    input_file = project_directory / "schema.json"
    output_file = project_directory / "output.py"
    input_file.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    dependencies = WatchDependencies()
    dependencies.configure(Config(input=input_file, output=output_file), config_values={})
    dependencies.enable_polling_fingerprints()
    dependencies.enable_polling_fingerprints()

    assert _path_fingerprint(tmp_path / "missing.json") == (-1, -1, -1)
    assert not dependencies._polling_dependencies_changed()
    assert not dependencies.accepts_event(project_directory, accept_directory_events=True)
    replacement = project_directory / "schema-replacement.json"
    replacement.write_text(WATCH_SCHEMA_CHANGED, encoding="utf-8")
    replacement.replace(input_file)
    assert dependencies._polling_dependencies_changed()
    assert dependencies.accepts_event(project_directory, accept_directory_events=True)

    with dependencies.generation():
        pass
    output_file.touch()
    assert not dependencies._polling_dependencies_changed()
    assert not dependencies.accepts_event(project_directory, accept_directory_events=True)

    dependencies.add_directory(project_directory)
    child_file = project_directory / "child.json"
    child_file.write_text(WATCH_SCHEMA_CHANGED, encoding="utf-8")
    input_file.write_text((WATCH_DATA_PATH / "file_change/reference.json").read_text(), encoding="utf-8")
    with dependencies.generation():
        run_main_with_args([
            "--input",
            str(input_file),
            "--output",
            str(output_file),
            "--input-file-type",
            "jsonschema",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ])
        assert_output(output_file.read_text(), EXPECTED_MAIN_PATH / "watch_reference_change.py")
        replacement.write_text("generated output\n", encoding="utf-8")
        replacement.replace(output_file)
    assert not dependencies._polling_dependencies_changed()
    assert not dependencies.accepts_event(output_file, accept_directory_events=True)
    child_file.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    assert dependencies._polling_dependencies_changed()
    assert dependencies.accepts_event(project_directory, accept_directory_events=True)


@pytest.mark.allow_direct_assert
def test_watch_dependencies_accept_events_with_unresolvable_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An event path whose resolver rejects it still checks lexical membership."""
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    input_file = tmp_path / "schema.json"
    input_file.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    dependencies = WatchDependencies()
    dependencies.add_file(input_file)
    original_resolve = Path.resolve

    def raise_value_error(path: Path, *args: object, **kwargs: object) -> Path:
        if path == input_file:
            msg = "unresolvable"
            raise ValueError(msg)
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", raise_value_error)

    assert dependencies.accepts_event(input_file)


@pytest.mark.allow_direct_assert
def test_watch_dependencies_exclude_staged_and_failed_generation_outputs(tmp_path: Path) -> None:
    """Output exclusions publish before a generation and retain recovery outputs after failure."""
    from datamodel_code_generator.__main__ import Config
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    input_file = tmp_path / "schema.json"
    initial_output = tmp_path / "initial.py"
    attempted_output = tmp_path / "attempted.py"
    input_file.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    dependencies = WatchDependencies()
    dependencies.configure(Config(input=input_file, output=initial_output), config_values={})
    with dependencies.generation():
        pass
    dependencies.configure(Config(input=input_file, output=attempted_output), config_values={})

    assert not dependencies.accepts_event(attempted_output)
    with pytest.raises(RuntimeError, match=WATCH_GENERATION_ERROR), dependencies.generation():
        _raise_watch_generation_error()

    assert not dependencies.accepts_event(initial_output)
    assert not dependencies.accepts_event(attempted_output)
    dependencies.configure(Config(input=input_file, output=attempted_output), config_values={})
    with dependencies.generation():
        pass
    assert dependencies.outputs == frozenset({attempted_output})


@pytest.mark.allow_direct_assert
def test_watch_dependencies_bound_repeated_failed_candidates(tmp_path: Path) -> None:
    """Repeated failures retain only the successful graph and latest recovery candidate."""
    from datamodel_code_generator.__main__ import Config
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    successful_input = tmp_path / "successful.json"
    successful_output = tmp_path / "successful.py"
    successful_input.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    dependencies = WatchDependencies()
    dependencies.configure(Config(input=successful_input, output=successful_output), config_values={})
    with dependencies.generation():
        pass

    for index in range(32):
        failed_input = tmp_path / f"failed-{index}.json"
        failed_output = tmp_path / f"failed-{index}.py"
        discovered_input = tmp_path / f"discovered-{index}.json"
        dependencies.configure(Config(input=failed_input, output=failed_output), config_values={})
        with pytest.raises(RuntimeError, match=WATCH_GENERATION_ERROR):
            _record_failed_dependency(dependencies, discovered_input)

    assert dependencies.includes(successful_input)
    assert dependencies.includes(tmp_path / "failed-31.json")
    assert dependencies.includes(tmp_path / "discovered-31.json")
    assert not dependencies.includes(tmp_path / "failed-0.json")
    assert not dependencies.includes(tmp_path / "discovered-0.json")
    assert dependencies.outputs == frozenset({successful_output, tmp_path / "failed-31.py"})


@pytest.mark.allow_direct_assert
def test_watch_dependencies_retain_failed_generation_files_during_replan(tmp_path: Path) -> None:
    """A pending replan continues to accept recovery edits from the failed generation."""
    from datamodel_code_generator.__main__ import Config
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    initial_input = tmp_path / "initial.json"
    next_input = tmp_path / "next.json"
    failed_dependency = tmp_path / "missing.json"
    initial_input.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    next_input.write_text(WATCH_SCHEMA_CHANGED, encoding="utf-8")
    dependencies = WatchDependencies()
    dependencies.configure(Config(input=initial_input), config_values={})

    with pytest.raises(RuntimeError, match=WATCH_GENERATION_ERROR):
        _record_failed_dependency(dependencies, failed_dependency)
    dependencies.configure(Config(input=next_input), config_values={})

    assert dependencies.includes(failed_dependency)
    assert dependencies.accepts_event(failed_dependency)


@pytest.mark.allow_direct_assert
def test_watch_dependencies_replace_raw_config_recovery_candidates(tmp_path: Path) -> None:
    """A newer raw config replan replaces the previous missing-file candidate."""
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    first_missing = tmp_path / "first-aliases.json"
    second_missing = tmp_path / "second-aliases.json"
    dependencies = WatchDependencies()
    dependencies.stage_raw_config({"aliases": str(first_missing)})
    dependencies.stage_raw_config({"aliases": str(second_missing)})
    dependencies.add_file(None)
    dependencies.add_directory(None)

    assert not dependencies.includes(first_missing)
    assert dependencies.includes(second_missing)


@pytest.mark.allow_direct_assert
def test_watch_dependencies_ignore_raw_paths_that_cannot_be_expanded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An invalid raw recovery path does not abort the active watch replan."""
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    invalid_path = Path("unexpandable.json")
    error_message = "unexpandable"

    def raise_expansion_error(_path: Path) -> Path:
        raise OSError(error_message)

    monkeypatch.setattr(Path, "expanduser", raise_expansion_error)
    dependencies = WatchDependencies()
    dependencies.stage_raw_config({"aliases": str(invalid_path)})

    assert not dependencies.files


@pytest.mark.allow_direct_assert
def test_watch_dependencies_keep_explicit_metadata_outputs_when_raw_output_resolution_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A malformed raw output does not hide an explicitly-file metadata recovery output."""
    from datamodel_code_generator import watch_dependencies as dependency_module
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    unreadable_output = tmp_path / "unreadable-output.py"
    metadata_output = tmp_path / "metadata"
    original_resolved_path = dependency_module._resolved_path

    def fail_unreadable_output(path: Path) -> Path:
        if path == unreadable_output:
            msg = "simulated raw output resolution failure"
            raise OSError(msg)
        return original_resolved_path(path)

    monkeypatch.setattr(dependency_module, "_resolved_path", fail_unreadable_output)
    dependencies = WatchDependencies()
    dependencies.stage_raw_config({"output": unreadable_output, "emit_model_metadata": metadata_output})

    assert unreadable_output not in dependencies.outputs
    assert metadata_output in dependencies.outputs


@pytest.mark.allow_direct_assert
def test_batch_watch_records_raw_dependencies_after_a_failed_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An invalid batch job retains its raw input as a recovery event before watch startup."""
    from datamodel_code_generator import __main__ as main_module
    from datamodel_code_generator.arguments import arg_parser
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    input_file = tmp_path / "input.json"
    input_file.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        f"""[tool.datamodel-codegen.jobs.invalid]
input = "{input_file.as_posix()}"
""",
        encoding="utf-8",
    )
    dependencies = WatchDependencies()
    monkeypatch.chdir(tmp_path)
    arg_parser.parse_args(["--all-jobs"], namespace=main_module.namespace)

    assert main_module._main(["--all-jobs"], start_watch=False, dependencies=dependencies) is Exit.ERROR
    assert dependencies.accepts_event(input_file)


@pytest.mark.allow_direct_assert
def test_batch_watch_excludes_a_shared_update_lock_from_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A successful update-mode batch makes its shared lock observable but not self-triggering."""
    from datamodel_code_generator import __main__ as main_module
    from datamodel_code_generator.arguments import arg_parser
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    input_file = JSON_SCHEMA_DATA_PATH / "person.json"
    output_path = tmp_path / "output.py"
    lockfile = tmp_path / "remote.lock"
    (tmp_path / "pyproject.toml").write_text(
        f"""[tool.datamodel-codegen]
disable-timestamp = true
input-file-type = "jsonschema"

[tool.datamodel-codegen.jobs.model]
input = "{input_file.as_posix()}"
output = "{output_path.as_posix()}"
update-lock = true
lockfile = "{lockfile.as_posix()}"
""",
        encoding="utf-8",
    )
    dependencies = WatchDependencies()
    monkeypatch.chdir(tmp_path)
    arg_parser.parse_args(["--all-jobs", "--formatters", "builtin"], namespace=main_module.namespace)
    batch_plan = main_module._plan_jobs(main_module.namespace)

    assert main_module._run_watched_jobs(["--all-jobs", "--formatters", "builtin"], batch_plan, dependencies) is Exit.OK
    assert lockfile in dependencies.files
    assert lockfile in dependencies.outputs
    assert not dependencies.accepts_event(lockfile)


@pytest.mark.skipif(find_spec("grpc_tools") is None, reason="requires the protobuf extra")
def test_regular_generation_does_not_load_watch_dependency_collector(tmp_path: Path) -> None:
    """Non-watch protobuf and custom formatter execution skip all watch collector work."""
    formatter_directory = tmp_path / "formatter"
    schema_directory = tmp_path / "schemas"
    output_file = tmp_path / "protobuf.py"
    formatter_directory.mkdir()
    shutil.copytree(WATCH_DATA_PATH / "protobuf_import", schema_directory)
    (schema_directory / "child.proto").write_text(
        (schema_directory / "child_changed.proto").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    shutil.copyfile(
        WATCH_DATA_PATH.parent / "python/custom_formatters/reloadable_watch.py",
        formatter_directory / "normal_formatter.py",
    )
    environment = {**os.environ, "PYTHONPATH": f"{formatter_directory}{os.pathsep}{PROJECT_ROOT}"}
    formatter_script = """\
import sys
from datamodel_code_generator.format import CodeFormatter, Formatter, PythonVersionMin

CodeFormatter(PythonVersionMin, custom_formatters=["normal_formatter"], formatters=[Formatter.BUILTIN])
if "datamodel_code_generator.watch_dependencies" in sys.modules:
    raise RuntimeError("custom formatter imported watch dependencies")
"""
    protobuf_args = [
        "--input",
        str(schema_directory / "root.proto"),
        "--input-file-type",
        "protobuf",
        "--output",
        str(output_file),
        "--formatters",
        "builtin",
        "--disable-timestamp",
    ]
    protobuf_script = f"""\
import sys
from datamodel_code_generator.__main__ import Exit, main

result = main({protobuf_args!r})
if result != Exit.OK:
    raise RuntimeError("protobuf generation failed")
if "datamodel_code_generator.watch_dependencies" in sys.modules:
    raise RuntimeError("protobuf generation imported watch dependencies")
"""

    subprocess.run([sys.executable, "-c", formatter_script], check=True, cwd=PROJECT_ROOT, env=environment)
    subprocess.run([sys.executable, "-c", protobuf_script], check=True, cwd=PROJECT_ROOT, env=environment)

    assert_output(output_file.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_protobuf_import_change.py")


@pytest.mark.allow_direct_assert
def test_watch_dependencies_skip_unreadable_protobuf_import(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A discovered lexical protobuf import may disappear before it can be read."""
    from datamodel_code_generator.parser.protobuf import ProtobufParser
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    root_proto = tmp_path / "root.proto"
    unreadable_proto = tmp_path / "unreadable.proto"
    root_proto.write_text('import "unreadable.proto";\n', encoding="utf-8")
    unreadable_proto.write_text('syntax = "proto3";\n', encoding="utf-8")
    original_read_text = Path.read_text
    msg = "unreadable"

    def raise_read_error(path: Path, *args: object, **kwargs: object) -> str:
        if path == unreadable_proto:
            raise OSError(msg)
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", raise_read_error)
    dependencies = WatchDependencies()
    with dependencies.generation():
        ProtobufParser._record_lexical_import_candidates(
            SimpleNamespace(config=SimpleNamespace(encoding="utf-8")),
            [root_proto],
            [tmp_path / "prepared", tmp_path],
        )

    assert unreadable_proto in dependencies.files


@pytest.mark.allow_direct_assert
def test_watch_with_no_collected_dependencies_stops_cleanly(tmp_path: Path) -> None:
    """An empty dependency graph does not leave an idle watch loop behind."""
    from datamodel_code_generator.__main__ import Config
    from datamodel_code_generator.watch import watch_and_regenerate
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    input_file = tmp_path / "schema.json"
    input_file.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")

    assert (
        watch_and_regenerate(
            Config(input=input_file),
            dependencies=WatchDependencies(),
            regenerate=lambda: Exit.OK,
        )
        == Exit.OK
    )


@pytest.mark.parametrize("layout", ["sibling", "child", "same"])
@pytest.mark.parametrize("emit_metadata", [False, True])
def test_watch_cli_regenerates_directory_output_on_change(tmp_path: Path, layout: str, emit_metadata: bool) -> None:
    """Watch mode regenerates package output when a schema directory changes."""
    input_dir = tmp_path / "schemas"
    input_dir.mkdir()
    input_file = input_dir / "schema.json"
    output_dir = {"sibling": tmp_path / "models", "child": input_dir / "models", "same": input_dir}[layout]
    output_file = output_dir / "schema.py"
    input_file.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_dir,
        output_dir,
        ["--emit-model-metadata", str(input_dir / "model_map.json")] if emit_metadata else None,
    )

    try:
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            input_file,
            WATCH_SCHEMA_CHANGED,
            lambda: _file_contains(output_file, "age: int | None = None"),
            "directory output to be regenerated",
        )
        assert_output(output_file.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_file_change.py")
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


def test_watch_cli_regenerates_on_nested_reference_change(tmp_path: Path) -> None:
    """Watch mode fully regenerates after an external local ``$ref`` changes."""
    schema_dir = tmp_path / "schemas"
    shutil.copytree(WATCH_DATA_PATH / "nested_ref", schema_dir)
    input_file = schema_dir / "root.json"
    referenced_file = schema_dir / "child.json"
    output_file = tmp_path / "output.py"
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_file,
        output_file,
    )

    try:
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            referenced_file,
            (schema_dir / "child_changed.json").read_text(encoding="utf-8"),
            lambda: _file_contains(output_file, "age: int | None = None"),
            "nested reference output to be regenerated",
        )
        assert_output(output_file.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_nested_ref_change.py")
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


def test_watch_cli_regenerates_on_xmlschema_include_change(tmp_path: Path) -> None:
    """Watch mode fully regenerates after an included XML Schema changes."""
    schema_dir = tmp_path / "schemas"
    shutil.copytree(WATCH_DATA_PATH / "xmlschema_include", schema_dir)
    input_file = schema_dir / "root.xsd"
    included_file = schema_dir / "child.xsd"
    output_file = tmp_path / "output.py"
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_file,
        output_file,
        ["--input-file-type", "xmlschema"],
    )

    try:
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            included_file,
            (schema_dir / "child_changed.xsd").read_text(encoding="utf-8"),
            lambda: _file_contains(output_file, "age: conint(ge=-2147483648, le=2147483647) | None = None"),
            "included XML Schema output to be regenerated",
        )
        assert_output(output_file.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_xmlschema_include_change.py")
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


@pytest.mark.skipif(find_spec("grpc_tools") is None, reason="requires the protobuf extra")
def test_watch_cli_regenerates_on_protobuf_import_change(tmp_path: Path) -> None:
    """Watch mode fully regenerates after an imported Protocol Buffers file changes."""
    schema_dir = tmp_path / "schemas"
    shutil.copytree(WATCH_DATA_PATH / "protobuf_import", schema_dir)
    input_file = schema_dir / "root.proto"
    imported_file = schema_dir / "child.proto"
    output_file = tmp_path / "output.py"
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_file,
        output_file,
        ["--input-file-type", "protobuf"],
    )

    try:
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            imported_file,
            (schema_dir / "child_changed.proto").read_text(encoding="utf-8"),
            lambda: _file_contains(output_file, "age: int | None = 0"),
            "imported Protocol Buffers output to be regenerated",
        )
        assert_output(output_file.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_protobuf_import_change.py")
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


@pytest.mark.skipif(find_spec("grpc_tools") is None, reason="requires the protobuf extra")
def test_watch_cli_recovers_when_an_existing_protobuf_import_adds_a_missing_nested_import(tmp_path: Path) -> None:
    """A failed protoc run retains lexical nested-import candidates for a later create event."""
    schema_dir = tmp_path / "schemas"
    shutil.copytree(WATCH_DATA_PATH / "protobuf_import", schema_dir)
    input_file = schema_dir / "root.proto"
    imported_file = schema_dir / "child.proto"
    missing_import = schema_dir / "nested" / "grandchild.proto"
    output_file = tmp_path / "output.py"
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_file,
        output_file,
        ["--input-file-type", "protobuf"],
    )

    try:
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            imported_file,
            (schema_dir / "child_missing_nested.proto").read_text(encoding="utf-8"),
            lambda: _lines_contain(stderr_lines, "Invalid Protocol Buffers schema"),
            "the missing nested protobuf import to fail",
        )
        completed_before_create = sum(line.strip() == "Done." for line in stdout_lines)
        missing_import.parent.mkdir()
        shutil.copyfile(schema_dir / "grandchild.proto", missing_import)
        _wait_for_watch_cli(
            process,
            stdout_lines,
            stderr_lines,
            lambda: sum(line.strip() == "Done." for line in stdout_lines) > completed_before_create,
            "the newly created nested protobuf import to recover generation",
        )
        assert_output(
            output_file.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_protobuf_missing_nested_recovery.py"
        )
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


def test_watch_cli_regenerates_on_custom_header_change(tmp_path: Path) -> None:
    """Watch mode reloads a configured custom header without watching generated output."""
    input_file = tmp_path / "schema.json"
    header_file = tmp_path / "header.txt"
    output_file = tmp_path / "output.py"
    input_file.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    header_file.write_text("# first header", encoding="utf-8")
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_file,
        output_file,
        ["--custom-file-header-path", str(header_file)],
    )

    try:
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            header_file,
            "# updated header",
            lambda: _file_contains(output_file, "# updated header"),
            "custom header output to be regenerated",
        )
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


def test_watch_cli_regenerates_on_top_level_custom_template_change(tmp_path: Path) -> None:
    """Watch mode clears cached custom templates before a full regeneration."""
    input_dir = tmp_path / "schemas"
    template_dir = tmp_path / "external" / "templates"
    input_file = input_dir / "schema.json"
    template_file = template_dir / "BaseModel.jinja2"
    output_file = tmp_path / "output.py"
    input_dir.mkdir()
    template_dir.mkdir(parents=True)
    input_file.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    shutil.copyfile(WATCH_DATA_PATH / "custom_templates/initial/BaseModel.jinja2", template_file)
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_file,
        output_file,
        ["--custom-template-dir", str(template_dir)],
    )

    try:
        _wait_for_watch_cli(
            process,
            stdout_lines,
            stderr_lines,
            lambda: _file_contains(output_file, 'template_revision = "initial"'),
            "initial custom template output",
        )
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            template_file,
            (WATCH_DATA_PATH / "custom_templates/changed/BaseModel.jinja2").read_text(encoding="utf-8"),
            lambda: _file_contains(output_file, 'template_revision = "changed"'),
            "custom template output to be regenerated",
        )
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


def test_watch_cli_regenerates_when_external_custom_template_directory_is_created_and_recreated(
    tmp_path: Path,
) -> None:
    """Watch mode retains missing configured directories by watching their existing parent."""
    input_dir = tmp_path / "schemas"
    external_dir = tmp_path / "external"
    template_dir = external_dir / "templates"
    input_file = input_dir / "schema.json"
    template_file = template_dir / "BaseModel.jinja2"
    output_file = tmp_path / "output.py"
    input_dir.mkdir()
    external_dir.mkdir()
    input_file.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_file,
        output_file,
        ["--custom-template-dir", str(template_dir)],
    )

    try:
        template_dir.mkdir()
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            template_file,
            (WATCH_DATA_PATH / "custom_templates/initial/BaseModel.jinja2").read_text(encoding="utf-8"),
            lambda: _file_contains(output_file, 'template_revision = "initial"'),
            "created custom template directory output to be regenerated",
        )
        shutil.rmtree(template_dir)
        _wait_for_watch_cli(
            process,
            stdout_lines,
            stderr_lines,
            lambda: output_file.is_file() and not _file_contains(output_file, 'template_revision = "initial"'),
            "deleted custom template directory output to be regenerated",
        )
        template_dir.mkdir()
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            template_file,
            (WATCH_DATA_PATH / "custom_templates/changed/BaseModel.jinja2").read_text(encoding="utf-8"),
            lambda: _file_contains(output_file, 'template_revision = "changed"'),
            "recreated custom template directory output to be regenerated",
        )
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


def test_watch_cli_regenerates_on_alias_configuration_change(tmp_path: Path) -> None:
    """Watch mode resolves a JSON-backed alias configuration again after it changes."""
    input_file = tmp_path / "schema.json"
    aliases_file = tmp_path / "aliases.json"
    output_file = tmp_path / "output.py"
    input_file.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    aliases_file.write_text('{"name": "first_name"}\n', encoding="utf-8")
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_file,
        output_file,
        ["--aliases", str(aliases_file)],
    )

    try:
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            aliases_file,
            '{"name": "updated_name"}\n',
            lambda: _file_contains(output_file, "updated_name: str"),
            "alias configuration output to be regenerated",
        )
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


def test_watch_cli_recovers_when_missing_alias_configuration_is_created(tmp_path: Path) -> None:
    """Creating the missing raw config file alone retries a failed watch replan."""
    project_directory = tmp_path / "project"
    input_file = project_directory / "schema.json"
    output_file = project_directory / "output.py"
    pyproject_file = project_directory / "pyproject.toml"
    aliases_file = project_directory / "missing-aliases.json"
    project_directory.mkdir()
    input_file.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    pyproject_file.write_text("[tool.datamodel-codegen]\n", encoding="utf-8")
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_file, output_file, working_directory=project_directory
    )

    try:
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            pyproject_file,
            '[tool.datamodel-codegen]\naliases = "missing-aliases.json"\n',
            lambda: _lines_contain(stderr_lines, "Unable to load alias mapping"),
            "the missing aliases replan to fail",
        )
        aliases_file.write_text('{"name": "first_name"}\n', encoding="utf-8")
        _wait_for_watch_cli(
            process,
            stdout_lines,
            stderr_lines,
            lambda: _file_contains(output_file, "first_name: str"),
            "the created aliases file to recover generation",
        )
        assert_output(output_file.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_alias_configuration.py")
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


def test_watch_cli_recovers_when_missing_custom_formatter_is_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Creating a missing formatter candidate alone retries failed watch generation."""
    project_directory = tmp_path / "project"
    formatter_directory = tmp_path / "formatters"
    input_file = project_directory / "schema.json"
    output_file = project_directory / "output.py"
    pyproject_file = project_directory / "pyproject.toml"
    formatter_file = formatter_directory / "future_formatter.py"
    project_directory.mkdir()
    formatter_directory.mkdir()
    input_file.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    pyproject_file.write_text("[tool.datamodel-codegen]\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", f"{formatter_directory}{os.pathsep}{os.environ.get('PYTHONPATH', '')}")
    monkeypatch.setenv("WATCHFILES_FORCE_POLLING", "false")
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_file, output_file, working_directory=project_directory
    )

    try:
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            pyproject_file,
            '[tool.datamodel-codegen]\ncustom-formatters = "future_formatter"\n',
            lambda: _lines_contain(stderr_lines, "No module named 'future_formatter'"),
            "the missing custom formatter generation to fail",
        )
        shutil.copyfile(WATCH_DATA_PATH.parent / "python/custom_formatters/reloadable_watch.py", formatter_file)
        _wait_for_watch_cli(
            process,
            stdout_lines,
            stderr_lines,
            lambda: _file_contains(output_file, 'formatter_revision = "initial"'),
            "the created custom formatter to recover generation",
        )
        assert_output(output_file.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_custom_formatter_initial.py")
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


def test_watch_cli_reloads_pyproject_configuration(tmp_path: Path) -> None:
    """Watch mode resolves the command again after its pyproject.toml changes."""
    input_file = tmp_path / "schema.json"
    output_file = tmp_path / "output.py"
    pyproject_file = tmp_path / "pyproject.toml"
    input_file.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    pyproject_file.write_text("[tool.datamodel-codegen]\nclass-name = 'FirstModel'\n", encoding="utf-8")
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_file,
        output_file,
        working_directory=tmp_path,
    )

    try:
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            pyproject_file,
            "[tool.datamodel-codegen]\nclass-name = 'UpdatedModel'\n",
            lambda: _file_contains(output_file, "class UpdatedModel(BaseModel):"),
            "pyproject configuration output to be regenerated",
        )
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


def test_watch_cli_update_lock_does_not_trigger_its_own_regeneration(
    tmp_path: Path,
    watched_http_server: str,
) -> None:
    """An update-mode lock is a watched dependency but not a self-triggering output."""
    input_file = tmp_path / "sources" / "root.json"
    output_file = tmp_path / "output.py"
    lockfile = tmp_path / "locks" / "remote.lock"
    input_file.parent.mkdir()
    input_file.write_text(
        f"""{{
  "title": "Root",
  "type": "object",
  "properties": {{"child": {{"$ref": "{watched_http_server}/child.json"}}}}
}}
""",
        encoding="utf-8",
    )
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_file,
        output_file,
        [
            "--allow-remote-refs",
            "--allow-private-network",
            "--update-lock",
            "--lockfile",
            str(lockfile),
        ],
        tmp_path,
    )

    try:
        _wait_for_watch_cli(
            process,
            stdout_lines,
            stderr_lines,
            lambda: lockfile.is_file() and output_file.is_file(),
            "the initial remote lock to be published",
        )
        assert_output(
            output_file.read_text(encoding="utf-8"), PROJECT_ROOT / "tests/data/expected/http/remote_lock_nested.py"
        )
        time.sleep(WATCH_CLI_CHANGE_RETRY_SECONDS * 2)
        done_count = sum(line.strip() == "Done." for line in stdout_lines)
        assert_output(f"done={done_count}\n", WATCH_DATA_PATH / "batch_no_cycle.txt")
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


def test_watch_initial_remote_lock_errors_discard_the_open_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Initial watch failures release an active update transaction before the loop can start."""
    input_file = tmp_path / "source.json"
    output_file = tmp_path / "output.py"
    lockfile = tmp_path / "locks" / "remote.lock"
    input_file.write_text("{", encoding="utf-8")
    common_args = [
        "--input",
        str(input_file),
        "--output",
        str(output_file),
        "--input-file-type",
        "jsonschema",
        "--disable-timestamp",
        "--lockfile",
        str(lockfile),
    ]

    monkeypatch.chdir(tmp_path)
    run_main_with_args([*common_args, "--update-lock", "--watch"], expected_exit=Exit.ERROR)

    input_file.write_text((JSON_SCHEMA_DATA_PATH / "person.json").read_text(encoding="utf-8"), encoding="utf-8")
    run_main_with_args(
        [*common_args, "--output", str(input_file), "--update-lock", "--watch"],
        expected_exit=Exit.ERROR,
    )


def test_watch_exception_after_initial_remote_lock_commit_preserves_real_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    """A watch-loop exception happens after the initial output and lock journal have committed."""
    input_file = JSON_SCHEMA_DATA_PATH / "person.json"
    output_file = tmp_path / "output.py"
    lockfile = tmp_path / "remote.lock"
    monkeypatch.chdir(tmp_path)
    mocker.patch("datamodel_code_generator.watch.watch_and_regenerate", side_effect=RuntimeError("watch loop failed"))

    run_main_with_args(
        [
            "--input",
            str(input_file),
            "--output",
            str(output_file),
            "--input-file-type",
            "jsonschema",
            "--disable-timestamp",
            "--lockfile",
            str(lockfile),
            "--update-lock",
            "--watch",
        ],
        expected_exit=Exit.ERROR,
    )

    assert_output(output_file.read_text(encoding="utf-8"), PROJECT_ROOT / "tests/data/expected/main/person.py")
    assert_output(lockfile.read_text(encoding="utf-8"), PROJECT_ROOT / "tests/data/expected/http/remote_lock_empty.txt")


def test_watch_cli_locked_lock_recovery_after_external_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    watched_http_server: str,
) -> None:
    """A locked watch observes external lock deletion and recovers when it is restored."""
    input_file = tmp_path / "sources" / "root.json"
    output_file = tmp_path / "output.py"
    lockfile = tmp_path / "locks" / "remote.lock"
    input_file.parent.mkdir()
    input_file.write_text(
        f"""{{
  "title": "Root",
  "type": "object",
  "properties": {{"child": {{"$ref": "{watched_http_server}/child.json"}}}}
}}
""",
        encoding="utf-8",
    )
    common_args = [
        "--input",
        str(input_file),
        "--output",
        str(output_file),
        "--input-file-type",
        "jsonschema",
        "--allow-remote-refs",
        "--allow-private-network",
        "--disable-timestamp",
        "--lockfile",
        str(lockfile),
    ]
    monkeypatch.chdir(tmp_path)
    run_main_with_args([*common_args, "--update-lock"])
    lock_content = lockfile.read_text(encoding="utf-8")
    assert_output(
        output_file.read_text(encoding="utf-8"), PROJECT_ROOT / "tests/data/expected/http/remote_lock_nested.py"
    )
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_file,
        output_file,
        ["--allow-remote-refs", "--allow-private-network", "--locked", "--lockfile", str(lockfile)],
        tmp_path,
    )

    try:
        deletion_error_count = len(stderr_lines)
        lockfile.unlink()
        lockfile.parent.rmdir()
        _wait_for_watch_cli(
            process,
            stdout_lines,
            stderr_lines,
            lambda: (
                len(stderr_lines) > deletion_error_count
                and _lines_contain(stderr_lines[deletion_error_count:], "Remote lock file not found")
            ),
            "the locked watch to report external lock deletion",
        )
        assert_output(
            output_file.read_text(encoding="utf-8"), PROJECT_ROOT / "tests/data/expected/http/remote_lock_nested.py"
        )
        completed_before_restore = sum(line.strip() == "Done." for line in stdout_lines)
        lockfile.parent.mkdir()
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            lockfile,
            lock_content,
            lambda: sum(line.strip() == "Done." for line in stdout_lines) > completed_before_restore,
            "the locked watch to recover after the lock is restored",
        )
        assert_output(
            output_file.read_text(encoding="utf-8"), PROJECT_ROOT / "tests/data/expected/http/remote_lock_nested.py"
        )
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


def test_watch_cli_implicit_lock_verification_recovers_after_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    watched_http_server: str,
) -> None:
    """A vanished auto-discovered lock remains required until the same path is restored."""
    input_file = tmp_path / "sources" / "root.json"
    output_file = tmp_path / "output.py"
    lockfile = tmp_path / "datamodel-codegen.lock"
    input_file.parent.mkdir()
    input_file.write_text(
        f"""{{
  "title": "Root",
  "type": "object",
  "properties": {{"child": {{"$ref": "{watched_http_server}/child.json"}}}}
}}
""",
        encoding="utf-8",
    )
    common_args = [
        "--input",
        str(input_file),
        "--output",
        str(output_file),
        "--input-file-type",
        "jsonschema",
        "--allow-remote-refs",
        "--allow-private-network",
        "--disable-timestamp",
    ]
    monkeypatch.chdir(tmp_path)
    run_main_with_args([*common_args, "--update-lock"])
    lock_content = lockfile.read_text(encoding="utf-8")
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_file,
        output_file,
        ["--allow-remote-refs", "--allow-private-network"],
        tmp_path,
    )

    try:
        deletion_error_count = len(stderr_lines)
        lockfile.unlink()
        _wait_for_watch_cli(
            process,
            stdout_lines,
            stderr_lines,
            lambda: (
                len(stderr_lines) > deletion_error_count
                and _lines_contain(stderr_lines[deletion_error_count:], "Remote lock file not found")
            ),
            "the implicit lock watch to fail closed after lock deletion",
        )
        assert_output(
            output_file.read_text(encoding="utf-8"), PROJECT_ROOT / "tests/data/expected/http/remote_lock_nested.py"
        )
        completed_before_restore = sum(line.strip() == "Done." for line in stdout_lines)
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            lockfile,
            lock_content,
            lambda: sum(line.strip() == "Done." for line in stdout_lines) > completed_before_restore,
            "the implicit lock watch to recover after lock restoration",
        )
        assert_output(
            output_file.read_text(encoding="utf-8"), PROJECT_ROOT / "tests/data/expected/http/remote_lock_nested.py"
        )
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


def test_batch_watch_implicit_lock_verification_recovers_after_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    watched_http_server: str,
) -> None:
    """A failed batch replan retains implicit verification until the lock is restored."""
    input_file = tmp_path / "sources" / "root.json"
    output_file = tmp_path / "output.py"
    lockfile = tmp_path / "datamodel-codegen.lock"
    input_file.parent.mkdir()
    input_file.write_text(
        f"""{{
  "title": "Root",
  "type": "object",
  "properties": {{"child": {{"$ref": "{watched_http_server}/child.json"}}}}
}}
""",
        encoding="utf-8",
    )

    def write_project(*, update_lock: bool) -> None:
        (tmp_path / "pyproject.toml").write_text(
            f"""[tool.datamodel-codegen]
allow-private-network = true
allow-remote-refs = true
disable-timestamp = true
input-file-type = "jsonschema"
{("update-lock = true" if update_lock else "")}

[tool.datamodel-codegen.jobs.root]
input = "{input_file.as_posix()}"
output = "{output_file.as_posix()}"
""",
            encoding="utf-8",
        )

    monkeypatch.chdir(tmp_path)
    write_project(update_lock=True)
    run_main_with_args(["--all-jobs", "--formatters", "builtin"])
    lock_content = lockfile.read_text(encoding="utf-8")
    write_project(update_lock=False)
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_batch_watch_cli_until_ready(tmp_path)

    try:
        deletion_error_count = len(stderr_lines)
        lockfile.unlink()
        _wait_for_watch_cli(
            process,
            stdout_lines,
            stderr_lines,
            lambda: (
                len(stderr_lines) > deletion_error_count
                and _lines_contain(stderr_lines[deletion_error_count:], "Remote lock file not found")
            ),
            "the implicit batch lock watch to fail closed after lock deletion",
        )
        assert_output(
            output_file.read_text(encoding="utf-8"), PROJECT_ROOT / "tests/data/expected/http/remote_lock_nested.py"
        )
        completed_before_restore = sum(line.strip() == "Done." for line in stdout_lines)
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            lockfile,
            lock_content,
            lambda: sum(line.strip() == "Done." for line in stdout_lines) > completed_before_restore,
            "the implicit batch lock watch to recover after lock restoration",
        )
        assert_output(
            output_file.read_text(encoding="utf-8"), PROJECT_ROOT / "tests/data/expected/http/remote_lock_nested.py"
        )
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


def test_watch_failed_lock_path_replan_retains_newly_verified_candidate(  # noqa: PLR0914
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    watched_http_server: str,
) -> None:
    """A failed single-job replan retains both old and newly verified lock paths."""
    input_file = tmp_path / "sources" / "root.json"
    output_file = tmp_path / "output.py"
    lockfile = tmp_path / "datamodel-codegen.lock"
    alternate_lockfile = tmp_path / "alternate.lock"
    input_file.parent.mkdir()
    valid_input = f"""{{
  "title": "Root",
  "type": "object",
  "properties": {{"child": {{"$ref": "{watched_http_server}/child.json"}}}}
}}
"""
    broken_input = valid_input.replace("/child.json", "/missing.json")
    input_file.write_text(valid_input, encoding="utf-8")

    def project_content(*, lock_path: Path | None = None) -> str:
        lockfile_config = f'lockfile = "{lock_path.as_posix()}"\n' if lock_path is not None else ""
        return f"""[tool.datamodel-codegen]
allow-private-network = true
allow-remote-refs = true
disable-timestamp = true
input-file-type = "jsonschema"
{lockfile_config}"""

    project_file = tmp_path / "pyproject.toml"
    project_file.write_text(project_content(), encoding="utf-8")
    common_args = [
        "--input",
        str(input_file),
        "--output",
        str(output_file),
        "--input-file-type",
        "jsonschema",
        "--allow-remote-refs",
        "--allow-private-network",
        "--disable-timestamp",
    ]
    monkeypatch.chdir(tmp_path)
    run_main_with_args([*common_args, "--update-lock"])
    lock_content = lockfile.read_text(encoding="utf-8")
    run_main_with_args([*common_args, "--update-lock", "--lockfile", str(alternate_lockfile)])
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_file,
        output_file,
        ["--allow-remote-refs", "--allow-private-network"],
        tmp_path,
    )

    try:
        replan_error_count = len(stderr_lines)
        lockfile.unlink()
        _wait_for_watch_cli(
            process,
            stdout_lines,
            stderr_lines,
            lambda: (
                len(stderr_lines) > replan_error_count
                and _lines_contain(stderr_lines[replan_error_count:], "Remote lock file not found")
            ),
            "the original implicit lock to fail closed after deletion",
        )
        replan_error_count = len(stderr_lines)
        input_file.write_text(broken_input, encoding="utf-8")
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            project_file,
            project_content(lock_path=alternate_lockfile),
            lambda: _lines_contain(stderr_lines[replan_error_count:], "HTTP 404 error fetching"),
            "the alternate existing lock to be verified by the failed replan",
        )
        alternate_lockfile.unlink()
        alternate_error_count = len(stderr_lines)
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            input_file,
            broken_input,
            lambda: (
                len(stderr_lines) > alternate_error_count
                and _lines_contain(stderr_lines[alternate_error_count:], "Remote lock file not found")
            ),
            "the newly verified alternate lock to remain required after deletion",
        )
        original_error_count = len(stderr_lines)
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            project_file,
            project_content(),
            lambda: (
                len(stderr_lines) > original_error_count
                and _lines_contain(stderr_lines[original_error_count:], "Remote lock file not found")
            ),
            "the earlier lock path to remain required after the failed replan",
        )
        original_error_count = len(stderr_lines)
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            input_file,
            valid_input,
            lambda: (
                len(stderr_lines) > original_error_count
                and _lines_contain(stderr_lines[original_error_count:], "Remote lock file not found")
            ),
            "the restored input to remain blocked by the missing original lock",
        )
        completed_before_restore = sum(line.strip() == "Done." for line in stdout_lines)
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            lockfile,
            lock_content,
            lambda: sum(line.strip() == "Done." for line in stdout_lines) > completed_before_restore,
            "the restored original lock to recover the watch",
        )
        assert_output(
            output_file.read_text(encoding="utf-8"), PROJECT_ROOT / "tests/data/expected/http/remote_lock_nested.py"
        )
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


def test_batch_watch_failed_lock_path_replan_retains_prior_implicit_intent(  # noqa: PLR0914
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    watched_http_server: str,
) -> None:
    """A failed replan cannot forget an auto-verified lock at the previous path."""
    input_file = tmp_path / "sources" / "root.json"
    output_file = tmp_path / "output.py"
    lockfile = tmp_path / "datamodel-codegen.lock"
    alternate_lockfile = tmp_path / "alternate.lock"
    input_file.parent.mkdir()
    valid_input = f"""{{
  "title": "Root",
  "type": "object",
  "properties": {{"child": {{"$ref": "{watched_http_server}/child.json"}}}}
}}
"""
    broken_input = valid_input.replace("/child.json", "/missing.json")
    input_file.write_text(valid_input, encoding="utf-8")

    def project_content(*, lock_path: Path | None = None) -> str:
        lockfile_config = f'lockfile = "{lock_path.as_posix()}"\n' if lock_path is not None else ""
        return f"""[tool.datamodel-codegen]
allow-private-network = true
allow-remote-refs = true
disable-timestamp = true
input-file-type = "jsonschema"
{lockfile_config}

[tool.datamodel-codegen.jobs.root]
input = "{input_file.as_posix()}"
output = "{output_file.as_posix()}"
"""

    def write_project(*, lock_path: Path | None = None) -> None:
        (tmp_path / "pyproject.toml").write_text(project_content(lock_path=lock_path), encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    write_project()
    run_main_with_args(["--all-jobs", "--update-lock", "--formatters", "builtin"])
    lock_content = lockfile.read_text(encoding="utf-8")
    write_project(lock_path=alternate_lockfile)
    run_main_with_args(["--all-jobs", "--update-lock", "--formatters", "builtin"])
    write_project()
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_batch_watch_cli_until_ready(tmp_path)

    try:
        replan_error_count = len(stderr_lines)
        lockfile.unlink()
        _wait_for_watch_cli(
            process,
            stdout_lines,
            stderr_lines,
            lambda: (
                len(stderr_lines) > replan_error_count
                and _lines_contain(stderr_lines[replan_error_count:], "Remote lock file not found")
            ),
            "the original implicit lock to fail closed after deletion",
        )
        replan_error_count = len(stderr_lines)
        input_file.write_text(broken_input, encoding="utf-8")
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            tmp_path / "pyproject.toml",
            project_content(lock_path=alternate_lockfile),
            lambda: (
                len(stderr_lines) > replan_error_count
                and _lines_contain(
                    stderr_lines[replan_error_count:], f"HTTP 404 error fetching {watched_http_server}/missing.json"
                )
            ),
            "the changed lock-path replan to fail after planning the alternate lock",
        )
        alternate_lockfile.unlink()
        alternate_error_count = len(stderr_lines)
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            input_file,
            broken_input,
            lambda: (
                len(stderr_lines) > alternate_error_count
                and _lines_contain(stderr_lines[alternate_error_count:], "Remote lock file not found")
            ),
            "the newly verified alternate lock to remain required after deletion",
        )
        restore_error_count = len(stderr_lines)
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            tmp_path / "pyproject.toml",
            project_content(),
            lambda: (
                len(stderr_lines) > restore_error_count
                and _lines_contain(stderr_lines[restore_error_count:], "Remote lock file not found")
            ),
            "the restored old lock path to remain fail-closed",
        )
        restore_error_count = len(stderr_lines)
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            input_file,
            valid_input,
            lambda: (
                len(stderr_lines) > restore_error_count
                and _lines_contain(stderr_lines[restore_error_count:], "Remote lock file not found")
            ),
            "the restored input to remain blocked by the missing original lock",
        )
        assert_output(
            output_file.read_text(encoding="utf-8"), PROJECT_ROOT / "tests/data/expected/http/remote_lock_nested.py"
        )
        completed_before_restore = sum(line.strip() == "Done." for line in stdout_lines)
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            lockfile,
            lock_content,
            lambda: sum(line.strip() == "Done." for line in stdout_lines) > completed_before_restore,
            "the restored original implicit lock to recover the batch watch",
        )
        assert_output(
            output_file.read_text(encoding="utf-8"), PROJECT_ROOT / "tests/data/expected/http/remote_lock_nested.py"
        )
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


def test_batch_watch_nested_dependency_reruns_full_batch_without_output_loop(tmp_path: Path) -> None:
    """Dependency changes republish every job without generated artifacts keeping the batch active."""
    root_file = tmp_path / "nested/root.json"
    root_file.parent.mkdir()
    child_file = root_file.parent / "child.json"
    second_input = tmp_path / "schema.json"
    first_output = tmp_path / "first.py"
    second_output = tmp_path / "second.py"
    first_metadata = tmp_path / "first.metadata.json"
    second_metadata = tmp_path / "second.metadata.json"
    second_expected = tmp_path / "second.expected.py"
    metadata_expected = tmp_path / "second.metadata.expected.txt"
    shutil.copyfile(WATCH_DATA_PATH / "nested_ref/root.json", root_file)
    shutil.copyfile(WATCH_DATA_PATH / "nested_ref/child.json", child_file)
    second_input.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        _batch_pyproject([
            ("nested", root_file, first_output, first_metadata),
            ("second", second_input, second_output, second_metadata),
        ]),
        encoding="utf-8",
    )
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_batch_watch_cli_until_ready(tmp_path)

    try:
        shutil.copyfile(second_output, second_expected)
        shutil.copyfile(second_metadata, metadata_expected)
        second_output.write_text("stale\n", encoding="utf-8")
        second_metadata.write_text("stale\n", encoding="utf-8")
        time.sleep(WATCH_CLI_CHANGE_RETRY_SECONDS * 2)
        done_count = sum(line.strip() == "Done." for line in stdout_lines)
        assert_output(f"done={done_count}\n", WATCH_DATA_PATH / "batch_no_cycle.txt")
        assert_output(
            second_output.read_text(encoding="utf-8"), PROJECT_ROOT / "tests/data/expected/main_kr/jobs/stale.py"
        )
        assert_output(
            second_metadata.read_text(encoding="utf-8"), PROJECT_ROOT / "tests/data/expected/main_kr/jobs/stale.py"
        )
        shutil.copyfile(WATCH_DATA_PATH / "nested_ref/child_changed.json", child_file.with_suffix(".pending"))
        child_file.with_suffix(".pending").replace(child_file)
        # Do not open batch destinations until their atomic publication completes. On Windows,
        # a reader can temporarily prevent replacement and make the test race with the watch CLI.
        _wait_for_watch_cli(
            process,
            stdout_lines,
            stderr_lines,
            lambda: sum(line.strip() == "Done." for line in stdout_lines) > done_count,
            "the full watched batch to be republished",
        )
        assert_output(first_output.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_nested_ref_change.py")
        assert_output(second_output.read_text(encoding="utf-8"), second_expected)
        assert_output(second_metadata.read_text(encoding="utf-8"), metadata_expected)
        # Polling can deliver file and parent-directory input notifications separately.
        # Check for output-driven cycles after that input notification burst settles.
        time.sleep(WATCH_CLI_CHANGE_RETRY_SECONDS * 2)
        done_count = sum(line.strip() == "Done." for line in stdout_lines)
        time.sleep(WATCH_CLI_CHANGE_RETRY_SECONDS * 2)
        done_count = sum(line.strip() == "Done." for line in stdout_lines) - done_count
        assert_output(f"done={done_count}\n", WATCH_DATA_PATH / "batch_no_cycle.txt")
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


def test_batch_watch_failed_cycle_preserves_outputs_and_recovers_from_new_dependency(tmp_path: Path) -> None:
    """A failed full transaction retains output and watches a newly missing reference for recovery."""
    nested_dir = tmp_path / "nested"
    nested_dir.mkdir()
    root_file = nested_dir / "root.json"
    missing_file = nested_dir / "missing.json"
    second_input = tmp_path / "second.json"
    first_output = tmp_path / "first.py"
    second_output = tmp_path / "second.py"
    first_expected = tmp_path / "first.expected.py"
    second_expected = tmp_path / "second.expected.py"
    shutil.copyfile(WATCH_DATA_PATH / "nested_ref/root.json", root_file)
    shutil.copyfile(WATCH_DATA_PATH / "nested_ref/child.json", nested_dir / "child.json")
    second_input.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        _batch_pyproject([
            ("nested", root_file, first_output, None),
            ("second", second_input, second_output, None),
        ]),
        encoding="utf-8",
    )
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_batch_watch_cli_until_ready(tmp_path)

    try:
        shutil.copyfile(first_output, first_expected)
        shutil.copyfile(second_output, second_expected)
        broken_root = root_file.read_text(encoding="utf-8").replace('"child.json"', '"missing.json"')
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            root_file,
            broken_root,
            lambda: _lines_contain(stderr_lines, "Generation failed"),
            "the failed batch cycle to be reported",
        )
        assert_output(first_output.read_text(encoding="utf-8"), first_expected)
        assert_output(second_output.read_text(encoding="utf-8"), second_expected)

        completed_before_recovery = sum(line.strip() == "Done." for line in stdout_lines)
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            missing_file,
            (WATCH_DATA_PATH / "nested_ref/child_changed.json").read_text(encoding="utf-8"),
            lambda: sum(line.strip() == "Done." for line in stdout_lines) > completed_before_recovery,
            "the failed batch to recover from its newly created dependency",
        )
        assert_output(first_output.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_missing_ref_recovery.py")
        assert_output(second_output.read_text(encoding="utf-8"), second_expected)
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


def test_batch_watch_all_jobs_replans_membership_from_pyproject(tmp_path: Path) -> None:
    """An all-jobs watcher includes newly declared jobs and reruns the existing selection."""
    first_input = tmp_path / "first.json"
    second_input = tmp_path / "schema.json"
    first_output = tmp_path / "first.py"
    second_output = tmp_path / "second.py"
    first_expected = tmp_path / "first.expected.py"
    first_input.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    second_input.write_text(WATCH_SCHEMA_CHANGED, encoding="utf-8")
    pyproject_file = tmp_path / "pyproject.toml"
    first_job = ("first", first_input, first_output, None)
    pyproject_file.write_text(_batch_pyproject([first_job]), encoding="utf-8")
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_batch_watch_cli_until_ready(tmp_path)

    try:
        shutil.copyfile(first_output, first_expected)
        first_output.write_text("stale\n", encoding="utf-8")
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            pyproject_file,
            _batch_pyproject([first_job, ("second", second_input, second_output, None)]),
            lambda: second_output.is_file() and first_output.read_text(encoding="utf-8") != "stale\n",
            "all-jobs membership to be replanned",
        )
        assert_output(first_output.read_text(encoding="utf-8"), first_expected)
        assert_output(second_output.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_file_change.py")
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


def test_watch_and_regenerate_handles_keyboard_interrupt(mocker: pytest.MockerFixture) -> None:
    """Test that watch_and_regenerate handles KeyboardInterrupt gracefully."""
    from datamodel_code_generator.__main__ import Config

    mock_watchfiles = mocker.Mock()
    mock_watchfiles.watch.side_effect = KeyboardInterrupt()
    config = Config(input=str(JSON_SCHEMA_DATA_PATH / "person.json"))

    mocker.patch("datamodel_code_generator.watch._get_watchfiles", return_value=mock_watchfiles)
    run_watch_and_assert(config)


def test_watch_and_regenerate_propagates_watcher_exception(mocker: pytest.MockerFixture) -> None:
    """Surface watcher-thread failures to the watch caller."""
    from datamodel_code_generator.__main__ import Config
    from datamodel_code_generator.watch import watch_and_regenerate

    mock_watchfiles = mocker.Mock()
    mock_watchfiles.watch.side_effect = RuntimeError("watcher failed")
    config = Config(input=str(JSON_SCHEMA_DATA_PATH / "person.json"))

    mocker.patch("datamodel_code_generator.watch._get_watchfiles", return_value=mock_watchfiles)
    with pytest.raises(RuntimeError, match="watcher failed"):
        watch_and_regenerate(config, regenerate=lambda: Exit.OK)


@pytest.mark.parametrize("add_catch_up_template_root", [False, True], ids=["unchanged-roots", "changed-roots"])
def test_watch_cli_catches_up_changes_queued_while_restarting_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    add_catch_up_template_root: bool,
) -> None:
    """A watcher restart catches up the latest inputs and configuration with a full regeneration."""
    project_directory = tmp_path / "project"
    formatter_directory = tmp_path / "formatter"
    template_directory = tmp_path / "templates"
    input_file = project_directory / "schema.json"
    output_file = project_directory / "output.py"
    pyproject_file = project_directory / "pyproject.toml"
    marker_file = tmp_path / "formatter-started"
    release_file = tmp_path / "formatter-release"
    project_directory.mkdir()
    formatter_directory.mkdir()
    template_directory.mkdir()
    input_file.write_text((WATCH_DATA_PATH / "file_change/initial.json").read_text(encoding="utf-8"), encoding="utf-8")
    pyproject_file.write_text("[tool.datamodel-codegen]\n", encoding="utf-8")
    shutil.copyfile(
        WATCH_DATA_PATH.parent / "python/custom_formatters/blocking_watch.py",
        formatter_directory / "blocking_formatter.py",
    )
    monkeypatch.setenv(
        "PYTHONPATH",
        f"{formatter_directory}{os.pathsep}{os.environ['PYTHONPATH']}"
        if "PYTHONPATH" in os.environ
        else str(formatter_directory),
    )
    monkeypatch.setenv("DATAMODEL_CODEGEN_WATCH_MARKER", str(marker_file))
    monkeypatch.setenv("DATAMODEL_CODEGEN_WATCH_RELEASE", str(release_file))
    monkeypatch.setenv("DATAMODEL_CODEGEN_WATCH_BLOCK_TIMEOUT", "5")
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_file,
        output_file,
        working_directory=project_directory,
    )

    try:
        assert_output(output_file.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_file_initial.py")
        time.sleep(WATCH_CLI_CHANGE_RETRY_SECONDS)
        pyproject_update = project_directory / "pyproject-update.toml"
        pyproject_update.write_text(
            '[tool.datamodel-codegen]\ncustom-formatters = "blocking_formatter"\n',
            encoding="utf-8",
        )
        pyproject_update.replace(pyproject_file)
        _wait_for_watch_cli(
            process,
            stdout_lines,
            stderr_lines,
            marker_file.is_file,
            "the custom formatter to begin the root-changing regeneration",
        )
        input_file.write_text(
            (WATCH_DATA_PATH / "file_change/changed.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
        if add_catch_up_template_root:
            caught_up_pyproject = project_directory / "pyproject-catchup.toml"
            caught_up_pyproject.write_text(
                "\n".join([
                    "[tool.datamodel-codegen]",
                    'custom-formatters = "blocking_formatter"',
                    f'custom-template-dir = "{template_directory.as_posix()}"',
                    "",
                ]),
                encoding="utf-8",
            )
            caught_up_pyproject.replace(pyproject_file)
        release_file.touch()
        _wait_for_watch_cli(
            process,
            stdout_lines,
            stderr_lines,
            lambda: (
                _file_contains(output_file, "age: int | None = None")
                and _lines_contain(stdout_lines, "Detected changes while restarting the watcher")
            ),
            "the queued input change to be caught up after the watcher restart",
        )
        assert_output(output_file.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_file_change.py")
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


@pytest.mark.allow_direct_assert
def test_watch_dependencies_handle_path_resolution_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Defensive path resolution falls back to the lexical path."""
    from datamodel_code_generator.watch_dependencies import _logical_working_directory, _path_variants

    unresolved_path = tmp_path / "unresolved.json"
    resolution_error = "unresolvable"

    def raise_resolution_error(_path: Path, *_args: object, **_kwargs: object) -> Path:
        raise OSError(resolution_error)

    monkeypatch.setattr(Path, "resolve", raise_resolution_error)
    assert _path_variants(unresolved_path) == frozenset({unresolved_path})

    def raise_samefile_error(_path: Path, _other: Path) -> bool:
        raise OSError(resolution_error)

    monkeypatch.setattr(Path, "samefile", raise_samefile_error)
    assert _logical_working_directory() == Path.cwd()


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX symlinks")
@pytest.mark.allow_direct_assert
def test_watch_dependencies_track_symlink_parent_events(tmp_path: Path) -> None:
    """Directory events from symlink replacement remain part of the graph."""
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    source_file = tmp_path / "source.json"
    source_directory = tmp_path / "templates-source"
    file_link = tmp_path / "schema.json"
    directory_link = tmp_path / "templates"
    source_file.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    source_directory.mkdir()
    file_link.symlink_to(source_file.name)
    directory_link.symlink_to(source_directory.name)
    dependencies = WatchDependencies()
    dependencies.add_file(file_link)
    dependencies.add_directory(directory_link)
    with dependencies.generation():
        dependencies.record_file(file_link)

    assert dependencies.includes(file_link.parent)


def test_watch_cli_reports_generation_error_after_change(tmp_path: Path) -> None:
    """Watch mode reports generation errors after a watched file change."""
    input_file = tmp_path / "schema.json"
    output_file = tmp_path / "output.py"
    input_file.write_text(WATCH_SCHEMA_INITIAL, encoding="utf-8")
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_file,
        output_file,
    )

    first_modified_time: int | None = None
    first_error_count = 0

    def error_after_retry() -> bool:
        nonlocal first_modified_time, first_error_count

        modified_time = input_file.stat().st_mtime_ns
        if first_modified_time is None:
            first_modified_time = modified_time
            return False
        error_count = sum("Error:" in line for line in stderr_lines)
        if not first_error_count:
            first_error_count = error_count
            return False
        return modified_time != first_modified_time and error_count > first_error_count

    try:
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            input_file,
            WATCH_SCHEMA_INVALID,
            error_after_retry,
            "the invalid input to be retried and its generation error reported",
        )
        assert_output(output_file.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_file_initial.py")
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


@pytest.mark.parametrize("notice", ["default", "optional"])
def test_watch_migration_notice_is_not_repeated(notice: str, tmp_path: Path) -> None:
    """Real watch regeneration reports each migration once even with an always warning filter."""
    input_file = tmp_path / "schema.json"
    output_file = tmp_path / "output.py"
    shutil.copyfile(JSON_SCHEMA_DATA_PATH / "migration_notice.json", input_file)
    command = _watch_cli_command(
        input_file,
        output_file,
        [
            "--target-python-version",
            "3.10",
            "--output-model-type",
            "dataclasses.dataclass",
        ],
    )
    formatter_index = command.index("--formatters")
    command[formatter_index : formatter_index + 2] = [] if notice == "default" else ["--formatters", "black", "isort"]
    command.insert(1, "-Walways::FutureWarning")
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _wait_for_watch_cli_ready(
        *_start_watch_process(command, tmp_path),
    )
    try:
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            input_file,
            (JSON_SCHEMA_DATA_PATH / "migration_notice_changed.json").read_text(encoding="utf-8"),
            lambda: _file_contains(output_file, "age: int | None = None"),
            "changed model output",
        )
        prefix = "Default formatters" if notice == "default" else "Black/isort"
        assert_output(
            "\n".join(
                line.partition("FutureWarning: ")[2].strip()
                for line in stderr_lines
                if f"FutureWarning: {prefix}" in line
            ),
            EXPECTED_MAIN_PATH / "migration_warnings" / f"{notice}.txt",
        )
        assert_output(output_file.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "migration_warnings" / "watched.py")
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


def test_watch_cli_keeps_input_changes_during_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An atomic input update during formatting survives publication of the dependency graph."""
    project_directory = tmp_path / "project"
    formatter_directory = project_directory
    input_file = project_directory / "schema.json"
    output_file = project_directory / "output.py"
    pyproject_file = project_directory / "pyproject.toml"
    marker_file = tmp_path / "formatter-started"
    release_file = tmp_path / "formatter-release"
    project_directory.mkdir()
    input_file.write_text((WATCH_DATA_PATH / "file_change/initial.json").read_text(encoding="utf-8"), encoding="utf-8")
    pyproject_file.write_text('[tool.datamodel-codegen]\ncustom-formatters = "blocking_formatter"\n', encoding="utf-8")
    release_file.touch()
    shutil.copyfile(
        WATCH_DATA_PATH.parent / "python/custom_formatters/blocking_watch.py",
        formatter_directory / "blocking_formatter.py",
    )
    monkeypatch.setenv(
        "PYTHONPATH",
        f"{formatter_directory}{os.pathsep}{os.environ['PYTHONPATH']}"
        if "PYTHONPATH" in os.environ
        else str(formatter_directory),
    )
    monkeypatch.setenv("DATAMODEL_CODEGEN_WATCH_MARKER", str(marker_file))
    monkeypatch.setenv("DATAMODEL_CODEGEN_WATCH_RELEASE", str(release_file))
    monkeypatch.setenv("DATAMODEL_CODEGEN_WATCH_BLOCK_TIMEOUT", "5")
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_file,
        output_file,
        working_directory=project_directory,
    )

    try:
        marker_file.unlink()
        release_file.unlink()
        assert_output(output_file.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_file_initial.py")
        time.sleep(WATCH_CLI_CHANGE_RETRY_SECONDS)
        pyproject_update = project_directory / "pyproject-update.toml"
        pyproject_update.write_text(
            '[tool.datamodel-codegen]\ncustom-formatters = "blocking_formatter"\n',
            encoding="utf-8",
        )
        pyproject_update.replace(pyproject_file)
        _wait_for_watch_cli(
            process,
            stdout_lines,
            stderr_lines,
            marker_file.is_file,
            "the custom formatter to begin regeneration",
        )
        input_update = project_directory / "input-update.json"
        input_update.write_text(
            (WATCH_DATA_PATH / "file_change/changed.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
        original_status = input_file.stat()
        os.utime(input_update, ns=(original_status.st_atime_ns, original_status.st_mtime_ns))
        input_update.replace(input_file)
        release_file.touch()
        _wait_for_watch_cli(
            process,
            stdout_lines,
            stderr_lines,
            lambda: _file_contains(output_file, "age: int | None = None"),
            "the input changed during formatting to regenerate",
        )
        assert_output(output_file.read_text(encoding="utf-8"), EXPECTED_MAIN_PATH / "watch_file_change.py")
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)


def test_watch_cli_tracks_new_reference_after_generation(tmp_path: Path) -> None:
    """Newly discovered dependencies remain observable after their first generation."""
    input_file = tmp_path / "schema.json"
    child_file = tmp_path / "child.json"
    output_file = tmp_path / "output.py"
    initial = (WATCH_DATA_PATH / "file_change/initial.json").read_text(encoding="utf-8")
    changed = (WATCH_DATA_PATH / "file_change/changed.json").read_text(encoding="utf-8")
    input_file.write_text(initial, encoding="utf-8")
    child_file.write_text(changed, encoding="utf-8")
    output = _WatchedOutput(output_file)
    process, stdout_lines, stderr_lines, stdout_thread, stderr_thread = _start_watch_cli_until_ready(
        input_file,
        output_file,
    )
    try:
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            input_file,
            (WATCH_DATA_PATH / "file_change/reference.json").read_text(encoding="utf-8"),
            output.contains("age: int | None = None"),
            "the newly referenced child to generate",
        )
        assert_output(output.text, EXPECTED_MAIN_PATH / "watch_reference_change.py")
        _write_watch_cli_input_and_wait(
            process,
            stdout_lines,
            stderr_lines,
            child_file,
            initial,
            output.contains("name: str", excluded="age:"),
            "an edit to the newly referenced child to regenerate",
        )
        assert_output(output.text, EXPECTED_MAIN_PATH / "watch_reference_initial.py")
    finally:
        _stop_watch_cli(process, stdout_thread, stderr_thread)
