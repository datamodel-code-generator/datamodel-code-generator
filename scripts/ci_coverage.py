"""Record and validate this run's complete CI coverage input before combining it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SHARD_COUNTS = {"py314": 3, "py313": 4, "py312": 4, "py311": 4, "py310": 4}
COVERAGE_ENVS = (
    "migration-legacy",
    "migration-boundary",
    "pydantic200",
    "pydantic20",
    "pydantic25",
    "pydantic213",
    "py312-black-latest",
    "py312-black22",
    "httpx-e2e",
    "httpx2-min-e2e",
)
EXPECTED_NAMES = frozenset({
    *(f".coverage.{env}-ubuntu-24.04" for env in COVERAGE_ENVS),
    *(
        f".coverage.{env}-parallel-shard{shard}-ubuntu-24.04"
        for env, count in SHARD_COUNTS.items()
        for shard in range(1, count + 1)
    ),
})


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


def main(argv: list[str] | None = None) -> None:
    """Run the artifact CLI, returning errors without a traceback."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("record", "prepare"))
    parser.add_argument("path", type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args(argv)
    try:
        match args.action:
            case "record":
                _record(args.path, args.run_id, args.sha)
            case _:
                _prepare(args.path, args.run_id, args.sha)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
