"""Measure startup/import paths without adding timing gates to CI."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = REPO_ROOT / "src"
IMPORT_TOP_LIMIT = 10


class Case(NamedTuple):
    """Startup or import path to measure."""

    name: str
    args: tuple[str, ...]
    import_statement: str | None = None


CASES = (
    Case("import-package", ("-c", "import datamodel_code_generator"), "import datamodel_code_generator"),
    Case(
        "import-arguments",
        ("-c", "import datamodel_code_generator.arguments"),
        "import datamodel_code_generator.arguments",
    ),
    Case(
        "import-main",
        ("-c", "from datamodel_code_generator.__main__ import main"),
        "from datamodel_code_generator.__main__ import main",
    ),
    Case("import-config", ("-c", "import datamodel_code_generator.config"), "import datamodel_code_generator.config"),
    Case("cli-version", ("-m", "datamodel_code_generator.__main__", "--version")),
    Case("cli-help", ("-m", "datamodel_code_generator.__main__", "--help")),
    Case(
        "cli-schema-generation",
        ("-m", "datamodel_code_generator.__main__", "--output-format-json-schema=generation"),
    ),
    Case(
        "cli-schema-model-metadata",
        ("-m", "datamodel_code_generator.__main__", "--output-format-json-schema=model-metadata"),
    ),
    Case(
        "cli-schema-structured-output",
        ("-m", "datamodel_code_generator.__main__", "--output-format-json-schema=structured-output"),
    ),
)


def _env() -> dict[str, str]:
    env = os.environ.copy()
    python_path = str(SRC_PATH)
    if existing := env.get("PYTHONPATH"):
        python_path = f"{python_path}{os.pathsep}{existing}"
    env["PYTHONPATH"] = python_path
    return env


def _run(args: tuple[str, ...], *, importtime: bool = False) -> subprocess.CompletedProcess[str]:
    command = [sys.executable]
    if importtime:
        command.extend(("-X", "importtime"))
    command.extend(args)
    return subprocess.run(command, check=True, capture_output=True, text=True, cwd=REPO_ROOT, env=_env())


def _measure_elapsed(args: tuple[str, ...]) -> float:
    start = time.perf_counter()
    _run(args)
    return (time.perf_counter() - start) * 1000


def _measure_memory(import_statement: str) -> dict[str, int]:
    code = (
        "import json, tracemalloc; "
        "tracemalloc.start(); "
        f"exec({import_statement!r}); "
        "current, peak = tracemalloc.get_traced_memory(); "
        "print(json.dumps({'current_bytes': current, 'peak_bytes': peak}))"
    )
    result = _run(("-c", code))
    return json.loads(result.stdout)


def _importtime_top(args: tuple[str, ...]) -> list[dict[str, int | str]]:
    result = _run(args, importtime=True)
    rows: list[dict[str, int | str]] = []
    for line in result.stderr.splitlines():
        if not line.startswith("import time:") or "|" not in line:
            continue
        parts = line.split("|", maxsplit=2)
        try:
            self_us = int(parts[0].removeprefix("import time:").strip())
            cumulative_us = int(parts[1].strip())
        except ValueError:
            continue
        rows.append(
            {
                "self_us": self_us,
                "cumulative_us": cumulative_us,
                "module": parts[2].strip(),
            },
        )
    return sorted(rows, key=lambda row: int(row["cumulative_us"]), reverse=True)[:IMPORT_TOP_LIMIT]


def _measure_case(case: Case, runs: int) -> dict[str, object]:
    elapsed_ms = [_measure_elapsed(case.args) for _ in range(runs)]
    result: dict[str, object] = {
        "name": case.name,
        "runs": runs,
        "median_ms": statistics.median(elapsed_ms),
        "min_ms": min(elapsed_ms),
        "max_ms": max(elapsed_ms),
    }
    if case.import_statement is not None:
        result["tracemalloc"] = _measure_memory(case.import_statement)
    result["importtime_top"] = _importtime_top(case.args)
    return result


def main() -> int:
    """Run startup/import measurements."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=5, help="number of fresh subprocess runs per case")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    if args.runs < 1:
        parser.error("--runs must be at least 1")

    results = [_measure_case(case, args.runs) for case in CASES]
    if args.json:
        print(json.dumps({"cases": results}, indent=2, sort_keys=True))
        return 0

    for result in results:
        memory = result.get("tracemalloc", {})
        peak = memory.get("peak_bytes") if isinstance(memory, dict) else None
        peak_text = f", peak={peak / 1024:.1f} KiB" if isinstance(peak, int) else ""
        print(f"{result['name']}: median={result['median_ms']:.2f} ms{peak_text}")
        for row in result["importtime_top"]:
            print(f"  {row['cumulative_us']:>8} us cumulative  {row['module']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
