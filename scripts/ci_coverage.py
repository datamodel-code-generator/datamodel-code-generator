"""Collect, record, download and validate this run's complete CI coverage input, then combine and report it.

The report ends with the lines excluded from coverage that the suite executed (`coverage_exclusions.py`); that list
never changes the exit code.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NotRequired, TypedDict

SHARD_COUNTS = {
    "py315-parallel": 16,
    "py314-parallel": 16,
    "py311-parallel": 24,
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
    "heki",
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
ARTIFACT_PREFIX = ".coverage."
DOWNLOAD_WORKERS = 32
DOWNLOAD_ATTEMPTS = 3
PAGE_SIZE = 100
TOKEN_ENV = "GH_TOKEN"


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


def _read(request: urllib.request.Request) -> bytes:
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        with error:  # An HTTP error holds its response open until it is closed.
            raise


def _attempt(request: urllib.request.Request) -> bytes | None:
    """Return the body, or `None` when the request failed."""
    try:
        return _read(request)
    except OSError:
        return None


def _fetch(url: str) -> bytes:
    """Return the body of a GitHub REST URL, the second attempt at once and the third a second later.

    The token is not sent on: an artifact's zip redirects to blob storage, which refuses a request that carries it.
    """
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    if token := os.environ.get(TOKEN_ENV):
        request.add_unredirected_header("Authorization", f"Bearer {token}")
    for attempt in range(1, DOWNLOAD_ATTEMPTS):
        if (body := _attempt(request)) is not None:
            return body
        time.sleep(attempt - 1)
    return _read(request)


class _Artifact(TypedDict):
    """The fields of a REST artifact record this script reads."""

    id: int
    name: str
    expired: bool
    digest: NotRequired[str | None]


def _artifacts(api: str, repository: str, run_id: str) -> list[_Artifact]:
    """Return the newest unexpired artifact of each coverage name of a run; a rerun lists the older attempts too."""
    newest: dict[str, _Artifact] = {}
    page = 1
    while True:
        url = f"{api}/repos/{repository}/actions/runs/{run_id}/artifacts?per_page={PAGE_SIZE}&page={page}"
        listed: list[_Artifact] = json.loads(_fetch(url))["artifacts"]
        for artifact in listed:
            name = artifact["name"]
            if (
                name.startswith(ARTIFACT_PREFIX)
                and not artifact["expired"]
                and ((current := newest.get(name)) is None or artifact["id"] > current["id"])
            ):
                newest[name] = artifact
        if len(listed) < PAGE_SIZE:
            return [newest[name] for name in sorted(newest)]
        page += 1


def _download_artifact(api: str, repository: str, artifact: _Artifact, root: Path) -> None:
    data = _fetch(f"{api}/repos/{repository}/actions/artifacts/{artifact['id']}/zip")
    if artifact.get("digest") != f"sha256:{hashlib.sha256(data).hexdigest()}":
        msg = f"Coverage artifact digest mismatch: {artifact['name']}"
        raise ValueError(msg)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        archive.extractall(root / str(artifact["name"]))


def _download(root: Path, api: str, repository: str, run_id: str) -> None:
    """Unpack each coverage artifact of a run into a directory of its name below `root`, the layout `prepare` checks.

    A process ends when its downloads do: the artifact action's process stayed up to three minutes after it had
    written every file. The digest each artifact was uploaded with must match the bytes downloaded.
    """
    artifacts = _artifacts(api, repository, run_id)
    root.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(DOWNLOAD_WORKERS) as pool:
        list(pool.map(lambda artifact: _download_artifact(api, repository, artifact, root), artifacts))
    print(f"Downloaded {len(artifacts)} coverage artifacts")


def _run(commands: list[list[str]]) -> tuple[int, str]:
    """Run commands in order until one fails, like `bash -e`, returning its exit code and the output."""
    output = ""
    for command in commands:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
        output += result.stdout
        if result.returncode:
            return result.returncode, output
    return 0, output


def _collect(data: Path, target: Path) -> None:
    """Combine the data files left beside a run's data file into it, then move it to the artifact's name.

    pytest-cov combines the files of the run's processes when the tests end, but a process that outlives its test
    writes its file when it exits: a multiprocessing forkserver or resource tracker that an xdist worker started ends
    only with that worker, after the combine. Whichever test starts such a process, its data joins the artifact here.
    """
    if target.name not in EXPECTED_NAMES or not data.is_file():
        msg = f"Invalid coverage data: {data.name} for {target.name}"
        raise ValueError(msg)
    if leftovers := sorted(data.parent.glob(f"{data.name}.*")):
        command = [sys.executable, "-m", "coverage", "combine", "--append", f"--data-file={data}"]
        code, output = _run([[*command, *map(str, leftovers)]])
        print(output, end="", flush=True)
        kept = [path.name for path in leftovers if path.exists()]
        if code or kept:
            msg = f"Could not combine the coverage data files beside {data.name}: {', '.join(kept) or code}"
            raise ValueError(msg)
        print(f"Combined {len(leftovers)} data files written after the tests ended into {data.name}")
    data.replace(target)


def _report(root: Path, compare_branch: str) -> int:
    """Combine the prepared inputs in parallel groups, then write the XML and diff report beside the summary gate.

    The executed exclusions are listed once the checks start, so only from the data file this run's combine wrote.
    """
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
    code, combined = 0, False
    for stage in stages:
        combined = stage is stages[-1]
        with ThreadPoolExecutor(len(stage)) as pool:
            results = list(pool.map(_run, stage))
        for _, output in results:
            print(output, end="", flush=True)
        if code := next((code for code, _ in results if code), 0):
            break
    if combined:
        script = Path(__file__).with_name("coverage_exclusions.py")
        print(_run([[sys.executable, str(script), str(root / ".coverage")]])[1], end="", flush=True)
    return code


def _act(args: argparse.Namespace) -> int:
    """Run the parsed action, returning the exit status of a report."""
    match args.action:
        case "record":
            _record(args.path, args.run_id, args.sha)
        case "collect":
            _collect(args.data, args.path)
        case "report":
            return _report(args.path, args.compare_branch)
        case "download":
            _download(args.path, args.api_url, args.repository, args.run_id)
        case _:
            _prepare(args.path, args.run_id, args.sha)
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
    collect = actions.add_parser("collect", help="run with the Python of the environment that ran the tests")
    collect.add_argument("data", type=Path, help="the data file the run wrote (its COVERAGE_FILE)")
    collect.add_argument("path", type=Path, help="the artifact's data file")
    download = actions.add_parser("download", help=f"read the token from {TOKEN_ENV}")
    download.add_argument("path", type=Path, help="the directory `prepare` reads")
    download.add_argument("--repository", required=True, help="OWNER/NAME")
    download.add_argument("--run-id", required=True)
    download.add_argument("--api-url", default="https://api.github.com")
    report = actions.add_parser("report")
    report.add_argument("path", type=Path)
    report.add_argument("--compare-branch", required=True)
    args = parser.parse_args(argv)
    try:
        code = _act(args)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    if code:
        parser.exit(code)


if __name__ == "__main__":
    main()
