"""Collect datamodel-code-generator release benchmarks in isolated environments.

This script is intended for GitHub Actions. It installs released PyPI packages
or the main branch in temporary virtual environments, runs CLI generation
commands, and writes a JSON artifact consumed by scripts/build_release_benchmark_docs.py.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import venv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.release_benchmark_errors import compact_benchmark_error
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from release_benchmark_errors import compact_benchmark_error

try:
    from scripts.release_benchmark_safety import MAIN_VERSION
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from release_benchmark_safety import MAIN_VERSION

ROOT = Path(__file__).resolve().parents[1]
GITHUB_PACKAGE_URL = "git+https://github.com/datamodel-code-generator/datamodel-code-generator.git"
DEFAULT_OUTPUT = ROOT / ".benchmarks" / "release-benchmarks.json"
DEFAULT_RUNS = 7
DEFAULT_WARMUPS = 1
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_INSTALL_RETRIES = 3
INSTALL_RETRY_BASE_SECONDS = 12
MAX_ERROR_LENGTH = 1800
TIMEOUT_EXIT_CODE = 124
STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_UNSUPPORTED = "unsupported"
MAIN_REF_PATTERN = re.compile(rf"^(?:{MAIN_VERSION}|[0-9a-f]{{40}})$")


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    """A CLI benchmark input case."""

    name: str
    input_type: str
    path: Path


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """A serializable benchmark result row."""

    version: str
    python_version: str
    os: str
    input_type: str
    case: str
    formatter: str
    runs: int
    median_ms: float | None
    min_ms: float | None
    max_ms: float | None
    stdev_ms: float | None
    status: str
    command: str
    error: str


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    """Benchmark execution settings."""

    runs: int
    warmups: int
    timeout: int
    install_retries: int


@dataclass(frozen=True, slots=True)
class BenchmarkContext:
    """State shared by case benchmarks for one release."""

    version: str
    python_path: Path
    work_dir: Path
    config: BenchmarkConfig


BENCHMARK_CASES = (
    BenchmarkCase("small", "jsonschema", ROOT / "tests" / "data" / "jsonschema" / "person.json"),
    BenchmarkCase("large", "jsonschema", ROOT / "tests" / "data" / "performance" / "large_models.json"),
    BenchmarkCase("small", "openapi", ROOT / "tests" / "data" / "openapi" / "api.yaml"),
    BenchmarkCase("large", "openapi", ROOT / "tests" / "data" / "performance" / "openapi_large.yaml"),
)
DEFAULT_FORMATTERS = ("default", "builtin", "ruff")


def _split_csv_words(raw: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(token for part in raw.splitlines() for token in re.split(r"[\s,]+", part) if token))


def _normalize_version(version: str) -> str:
    return version.strip().removeprefix("v")


def _venv_python(venv_path: Path) -> Path:
    if platform.system() == "Windows":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"


def _run_subprocess(command: list[str], *, timeout: int, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stderr = _process_output(exc.stderr)
        message = f"Timed out after {timeout}s"
        return subprocess.CompletedProcess(
            command,
            TIMEOUT_EXIT_CODE,
            stdout=_process_output(exc.stdout),
            stderr=f"{message}: {stderr}" if stderr else message,
        )


def _process_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def _truncate_error(text: str) -> str:
    compact = compact_benchmark_error(text)
    if len(compact) <= MAX_ERROR_LENGTH:
        return compact
    return f"{compact[: MAX_ERROR_LENGTH - 3]}..."


def _install_spec(version: str, *, main_ref: str = MAIN_VERSION) -> str:
    if (normalized := _normalize_version(version)) == MAIN_VERSION:
        return f"datamodel-code-generator[ruff] @ {GITHUB_PACKAGE_URL}@{main_ref}"
    return f"datamodel-code-generator[ruff]=={normalized}"


def _install_command(python_path: Path, version: str, *, main_ref: str) -> list[str]:
    spec = _install_spec(version, main_ref=main_ref)
    if uv_path := shutil.which("uv"):
        return [uv_path, "pip", "install", "--python", str(python_path), spec]
    return [str(python_path), "-m", "pip", "install", spec]


def _runner_os() -> str:
    return os.environ.get("OS") or os.environ.get("RUNNER_OS") or platform.platform()


def _create_venv(path: Path) -> Path:
    venv.EnvBuilder(with_pip=True, clear=True).create(path)
    return _venv_python(path)


def _install_release(python_path: Path, version: str, *, main_ref: str, timeout: int, retries: int) -> str:
    command = _install_command(python_path, version, main_ref=main_ref)
    last_error = ""
    for attempt in range(retries):
        result = _run_subprocess(command, timeout=timeout)
        if result.returncode == 0:
            return ""
        last_error = _truncate_error(result.stderr or result.stdout)
        if attempt + 1 < retries:
            time.sleep(INSTALL_RETRY_BASE_SECONDS * (attempt + 1))
    return last_error


def _formatter_args(formatter: str) -> tuple[str, ...]:
    if formatter == "default":
        return ()
    if formatter == "builtin":
        return ("--formatters", "builtin")
    if formatter == "ruff":
        return ("--formatters", "ruff-check", "ruff-format")
    msg = f"Unsupported formatter benchmark target: {formatter}"
    raise ValueError(msg)


def _command_args(python_path: Path, case: BenchmarkCase, formatter: str, output_path: Path) -> list[str]:
    return [
        str(python_path),
        "-m",
        "datamodel_code_generator",
        "--input",
        str(case.path),
        "--input-file-type",
        case.input_type,
        "--output-model-type",
        "pydantic_v2.BaseModel",
        "--output",
        str(output_path),
        *_formatter_args(formatter),
    ]


def _display_command(case: BenchmarkCase, formatter: str) -> str:
    return shlex.join([
        "python",
        "-m",
        "datamodel_code_generator",
        "--input",
        str(case.path.relative_to(ROOT)),
        "--input-file-type",
        case.input_type,
        "--output-model-type",
        "pydantic_v2.BaseModel",
        "--output",
        "model.py",
        *_formatter_args(formatter),
    ])


def _failure_status(formatter: str, output: str) -> str:
    lower = output.lower()
    if formatter == "builtin" and ("invalid choice" in lower or "unrecognized arguments" in lower):
        return STATUS_UNSUPPORTED
    if formatter == "ruff" and (
        "ruff" in lower and ("not found" in lower or "invalid choice" in lower or "unrecognized arguments" in lower)
    ):
        return STATUS_UNSUPPORTED
    return STATUS_FAILED


def _failed_result(version: str, case: BenchmarkCase, formatter: str, *, status: str, error: str) -> BenchmarkResult:
    return BenchmarkResult(
        version=version,
        python_version=platform.python_version(),
        os=_runner_os(),
        input_type=case.input_type,
        case=case.name,
        formatter=formatter,
        runs=0,
        median_ms=None,
        min_ms=None,
        max_ms=None,
        stdev_ms=None,
        status=status,
        command=_display_command(case, formatter),
        error=error,
    )


def _successful_result(
    version: str,
    case: BenchmarkCase,
    formatter: str,
    samples_ms: list[float],
) -> BenchmarkResult:
    return BenchmarkResult(
        version=version,
        python_version=platform.python_version(),
        os=_runner_os(),
        input_type=case.input_type,
        case=case.name,
        formatter=formatter,
        runs=len(samples_ms),
        median_ms=round(statistics.median(samples_ms), 3),
        min_ms=round(min(samples_ms), 3),
        max_ms=round(max(samples_ms), 3),
        stdev_ms=round(statistics.stdev(samples_ms), 3) if len(samples_ms) > 1 else 0.0,
        status=STATUS_OK,
        command=_display_command(case, formatter),
        error="",
    )


def _benchmark_case(context: BenchmarkContext, case: BenchmarkCase, formatter: str) -> BenchmarkResult:
    samples_ms: list[float] = []
    for index in range(context.config.runs + context.config.warmups):
        output_path = context.work_dir / f"{case.input_type}-{case.name}-{formatter}-{index}.py"
        command = _command_args(context.python_path, case, formatter, output_path)
        started_at = time.perf_counter()
        result = _run_subprocess(command, timeout=context.config.timeout)
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        if result.returncode != 0:
            error = _truncate_error(result.stderr or result.stdout)
            return _failed_result(
                context.version,
                case,
                formatter,
                status=_failure_status(formatter, error),
                error=error,
            )
        if index >= context.config.warmups:
            samples_ms.append(elapsed_ms)
    return _successful_result(context.version, case, formatter, samples_ms)


def _benchmark_install_failure(version: str, formatters: tuple[str, ...], error: str) -> list[BenchmarkResult]:
    return [
        _failed_result(version, case, formatter, status=STATUS_FAILED, error=error)
        for case in BENCHMARK_CASES
        for formatter in formatters
    ]


def benchmark_version(
    version: str,
    *,
    formatters: tuple[str, ...],
    config: BenchmarkConfig,
    main_ref: str = MAIN_VERSION,
) -> list[BenchmarkResult]:
    """Benchmark one released datamodel-code-generator version."""
    with tempfile.TemporaryDirectory(prefix="datamodel-code-generator-release-bench-") as tmp:
        tmp_path = Path(tmp)
        venv_python = _create_venv(tmp_path / "venv")
        if error := _install_release(
            venv_python,
            version,
            main_ref=main_ref,
            timeout=config.timeout,
            retries=config.install_retries,
        ):
            return _benchmark_install_failure(version, formatters, error)
        work_dir = tmp_path / "outputs"
        work_dir.mkdir()
        context = BenchmarkContext(version=version, python_path=venv_python, work_dir=work_dir, config=config)
        return [_benchmark_case(context, case, formatter) for case in BENCHMARK_CASES for formatter in formatters]


def _result_payload(result: BenchmarkResult | dict[str, object]) -> dict[str, object]:
    if isinstance(result, BenchmarkResult):
        return asdict(result)
    return result


def _result_version(result: BenchmarkResult | dict[str, object]) -> str:
    version = ""
    match result:
        case BenchmarkResult(version=result_version):
            version = result_version
        case dict():
            version = str(result.get("version", ""))
        case _:
            pass
    return version


def _payload(
    results: list[BenchmarkResult] | list[dict[str, object]],
    *,
    runs: int,
    warmups: int,
    main_ref: str = MAIN_VERSION,
) -> dict[str, object]:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    metadata = {
        "generated_at": generated_at,
        "workflow": os.environ.get("GITHUB_WORKFLOW", "local"),
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "collector_sha": os.environ.get("GITHUB_SHA", ""),
        "os": _runner_os(),
        "python_version": platform.python_version(),
        "runs_per_case": str(runs),
        "warmups_per_case": str(warmups),
    }
    if any(_result_version(result) == MAIN_VERSION for result in results):
        metadata.update({
            "main_snapshot_generated_at": generated_at,
            "main_snapshot_workflow_run_id": os.environ.get("GITHUB_RUN_ID", ""),
            "main_snapshot_collector_sha": main_ref if re.fullmatch(r"[0-9a-f]{40}", main_ref) else "",
        })
    return {
        "schema_version": 1,
        "metadata": metadata,
        "entries": [_result_payload(result) for result in results],
    }


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Collect datamodel-code-generator release benchmarks")
    parser.add_argument(
        "--versions",
        required=True,
        help="Comma, whitespace, or newline separated release versions/tags or main",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="JSON artifact output path")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="Measured runs per case")
    parser.add_argument("--warmups", type=int, default=DEFAULT_WARMUPS, help="Warmup runs per case")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="Subprocess timeout in seconds")
    parser.add_argument("--install-retries", type=int, default=DEFAULT_INSTALL_RETRIES, help="PyPI install retry count")
    parser.add_argument(
        "--main-ref",
        default=MAIN_VERSION,
        help="Exact 40-character commit SHA used when benchmarking main (defaults to the moving main branch)",
    )
    parser.add_argument(
        "--formatters",
        default=",".join(DEFAULT_FORMATTERS),
        help="Comma, whitespace, or newline separated formatter benchmark targets",
    )
    return parser.parse_args()


def _positive_int_error(name: str, value: int) -> str:
    if value > 0:
        return ""
    return f"{name} must be greater than 0, got {value}"


def _non_negative_int_error(name: str, value: int) -> str:
    if value >= 0:
        return ""
    return f"{name} must be greater than or equal to 0, got {value}"


def _validate_args(args: argparse.Namespace) -> str:
    errors = [
        error
        for error in (
            _positive_int_error("--runs", args.runs),
            _non_negative_int_error("--warmups", args.warmups),
            _positive_int_error("--timeout", args.timeout),
            _positive_int_error("--install-retries", args.install_retries),
        )
        if error
    ]
    if not MAIN_REF_PATTERN.fullmatch(args.main_ref):
        errors.append(f"--main-ref must be 'main' or a 40-character lowercase commit SHA, got {args.main_ref!r}")
    if not errors:
        return ""
    return "\n".join(errors)


def main() -> int:
    """Script entrypoint."""
    args = parse_args()
    if error := _validate_args(args):
        print(error, file=sys.stderr)
        return 2
    if not (versions := tuple(_normalize_version(version) for version in _split_csv_words(args.versions))):
        print("No release versions provided", file=sys.stderr)
        return 2
    if not (formatters := _split_csv_words(args.formatters)):
        print("No formatter targets provided", file=sys.stderr)
        return 2

    results: list[BenchmarkResult] = []
    config = BenchmarkConfig(
        runs=args.runs,
        warmups=args.warmups,
        timeout=args.timeout,
        install_retries=args.install_retries,
    )
    for version in versions:
        print(f"Benchmarking datamodel-code-generator {version}", file=sys.stderr)
        results.extend(
            benchmark_version(
                version,
                formatters=formatters,
                config=config,
                main_ref=args.main_ref,
            )
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(_payload(results, runs=args.runs, warmups=args.warmups, main_ref=args.main_ref), indent=2) + "\n"
    )
    print(f"Wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
