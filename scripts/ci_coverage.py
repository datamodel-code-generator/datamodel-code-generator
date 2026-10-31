"""Record and validate this run's complete CI coverage input, then combine and report it."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SHARD_COUNTS = {
    "py315-parallel": 8,
    "py314-parallel": 8,
    "py313-parallel": 13,
    "py312-parallel": 17,
    "py311-parallel": 18,
    "py310-parallel": 14,
    "client-e2e": 3,
}
COVERAGE_ENVS = (
    "migration-legacy",
    "migration-boundary",
    "pydantic200",
    "pydantic20",
    "pydantic25",
    "pydantic212",
    "pydantic213",
    "pydantic214",
    "graphqlcore33",
    "py312-black-latest",
    "py312-black22",
    "httpx-e2e",
    "httpx2-min-e2e",
    "fastapi-e2e",
    "client-py311-e2e",
)
EXPECTED_NAMES = frozenset({
    *(f".coverage.{env}-ubuntu-24.04" for env in COVERAGE_ENVS),
    *(
        f".coverage.{env}-shard{shard}-ubuntu-24.04"
        for env, count in SHARD_COUNTS.items()
        for shard in range(1, count + 1)
    ),
})
COMBINE_GROUPS = 4


def _metadata(name: str, run_id: str, sha: str) -> dict[str, str]:
    return {"name": name, "run_id": run_id, "sha": sha}


def _record(path: Path, run_id: str, sha: str) -> None:
    if path.name not in EXPECTED_NAMES or not path.is_file() or not path.stat().st_size:
        msg = f"Invalid coverage file: {path.name}"
        raise ValueError(msg)
    path.with_name(f"{path.name}.json").write_text(
        json.dumps(_metadata(path.name, run_id, sha), sort_keys=True) + "\n", encoding="utf-8"
    )


def _prepare(root: Path, run_id: str, sha: str) -> None:
    if (actual := {path.name for path in root.iterdir()}) != EXPECTED_NAMES:
        msg = (
            f"Coverage artifacts differ: missing={sorted(EXPECTED_NAMES - actual)}, "
            f"unexpected={sorted(actual - EXPECTED_NAMES)}"
        )
        raise ValueError(msg)
    # Validate every artifact before moving anything into coverage combine's input directory.
    for name in sorted(EXPECTED_NAMES):
        directory = root / name
        if not directory.is_dir() or {path.name for path in directory.iterdir()} != {name, f"{name}.json"}:
            msg = f"Invalid coverage artifact contents: {name}"
            raise ValueError(msg)
        if (path := directory / name).is_symlink() or not path.is_file() or not path.stat().st_size:
            msg = f"Invalid coverage file: {name}"
            raise ValueError(msg)
        if json.loads((directory / f"{name}.json").read_text(encoding="utf-8")) != _metadata(name, run_id, sha):
            msg = f"Coverage provenance mismatch: {name}"
            raise ValueError(msg)
    if any(root.parent.glob(".coverage.*")):
        msg = "Coverage destination already contains input files"
        raise ValueError(msg)
    for name in sorted(EXPECTED_NAMES):
        (root / name / name).replace(root.parent / name)
    print(f"Validated {len(EXPECTED_NAMES)} coverage artifacts")


def _run(commands: list[list[str]]) -> tuple[int, str]:
    """Run commands in order until one fails, like `bash -e`, returning its exit code and the output."""
    output = ""
    for command in commands:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
        output += result.stdout
        if result.returncode:
            return result.returncode, output
    return 0, output


def _report(root: Path, compare_branch: str) -> int:
    """Combine the prepared inputs in parallel groups, then write the XML and diff report beside the summary gate."""
    coverage = [sys.executable, "-m", "coverage"]
    names = sorted(EXPECTED_NAMES)
    parts = [str(root / f".coverage.part{index}") for index in range(COMBINE_GROUPS)]
    data, xml = f"--data-file={root / '.coverage'}", str(root / "coverage.xml")
    stages = (
        [
            [
                [
                    *coverage,
                    "combine",
                    f"--data-file={part}",
                    *(str(root / name) for name in names[index::COMBINE_GROUPS]),
                ]
            ]
            for index, part in enumerate(parts)
        ],
        [[[*coverage, "combine", data, *parts]]],
        [
            [
                [*coverage, "xml", data, "-o", xml],
                [sys.executable, "-m", "diff_cover.diff_cover_tool", "--compare-branch", compare_branch, xml],
            ],
            [[*coverage, "report", data, "--fail-under=100", "--skip-covered", "--show-missing"]],
        ],
    )
    for stage in stages:
        with ThreadPoolExecutor(len(stage)) as pool:
            results = list(pool.map(_run, stage))
        for _, output in results:
            print(output, end="", flush=True)
        if code := next((code for code, _ in results if code), 0):
            return code
    return 0


def main(argv: list[str] | None = None) -> None:
    """Run the artifact CLI, returning errors without a traceback."""
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    for action in ("record", "prepare"):
        artifact = actions.add_parser(action)
        artifact.add_argument("path", type=Path)
        artifact.add_argument("--run-id", required=True)
        artifact.add_argument("--sha", required=True)
    report = actions.add_parser("report")
    report.add_argument("path", type=Path)
    report.add_argument("--compare-branch", required=True)
    args = parser.parse_args(argv)
    code = 0
    try:
        match args.action:
            case "record":
                _record(args.path, args.run_id, args.sha)
            case "report":
                code = _report(args.path, args.compare_branch)
            case _:
                _prepare(args.path, args.run_id, args.sha)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    if code:
        parser.exit(code)


if __name__ == "__main__":
    main()
