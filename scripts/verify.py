"""Verify a change before committing: the tests it reaches and an identity comparison of the cells that run it.

Usage:
  python -m scripts.verify [--base REF] [--catalog FILE] [-j N] [--cache-dir DIR]

Run it in the checkout to verify. It judges the working tree (uncommitted and untracked files included) against the
merge base of REF (default: origin/main) and HEAD:

1. The tests the diff reaches run with pytest and line coverage: `scripts/related_tests.py` selects them (the test
   modules that mirror the changed modules and the rows of `scripts/related_tests.toml` the diff fires).
2. Where the diff changes `src/`, `scripts/behaviour_diff.py gate MERGE-BASE CHECKOUT --identity --pick CATALOG`
   compares the cells of the cell catalog that run the changed code (`scripts/_cell_catalog.py`), the merge base
   against the working tree. It prints every difference with its cell, and each changed code object no compared cell
   ran, marked where the tests of step 1 ran its lines (`--test-coverage`). Its tier
   is narrowed to no input, so the picked cells alone run: this is the check before a commit, not the gate's verdict
   on a pull request, which runs its whole tier.
3. The same comparison runs a second time beside the first, with an empty output cache so that both sides generate
   again. A cell whose two runs differ on either side is flaky: it fails the run, as its comparison proves nothing.

The catalog is the newest file `<git common dir>/verify-catalogs/<revision>.json.gz` (shared by the worktrees of the
repository) whose revision is an ancestor of the merge base. Without one the script first records one at the merge base
(`behaviour_diff.py catalog`, several minutes; later merge bases reuse it). `--catalog FILE` names a catalog instead.

The exit status is 1 when a test fails, the comparison finds a violation (a cell differs) or a cell is flaky. Changed
code no compared cell ran fails nothing: a warning lists it by side, file and lines (`WARN uncovered`).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import tomllib

from scripts import _cell_catalog, behaviour_diff, compare_release, related_tests

GATE = Path(behaviour_diff.__file__).resolve()
PACKAGE_SOURCE = f"src/{compare_release.PACKAGE}/"
NO_TESTS_COLLECTED = 5
"""pytest's exit status when the selected paths hold no test."""
NO_TIER_INPUT = "verify: the picked cells only"
"""A `--select` pattern no input label matches: the gate's tier runs no cell, and `--pick` adds the catalog's."""
CATALOGS = "verify-catalogs"
"""The catalogs' directory below the repository's git directory, which every worktree of it shares."""


def _git(root: Path, *args: str) -> str:
    """Return the output of a git command that has to succeed."""
    result = compare_release.git(root, *args)
    if result.returncode:
        msg = f"git {' '.join(args)} failed in {root}: {result.stderr.strip()}"
        raise SystemExit(msg)
    return result.stdout.strip()


def src_changed(root: Path, base: str) -> bool:
    """Whether the working tree changes anything under `src/` since `base`, untracked files included."""
    return bool(
        _git(root, "diff", "--name-only", base, "--", "src")
        or _git(root, "ls-files", "-o", "--exclude-standard", "--", "src")
    )


def newest_catalog(root: Path, directory: Path, base: str) -> Path | None:
    """Return the catalog of `directory` recorded nearest before `base`: its revision is the closest ancestor."""
    found: list[tuple[int, Path]] = []
    for path in directory.glob("*.json.gz"):
        revision = path.name.removesuffix(".json.gz")
        if not compare_release.git(root, "merge-base", "--is-ancestor", revision, base).returncode:
            found.append((int(_git(root, "rev-list", "--count", f"{revision}..{base}")), path))
    return min(found)[1] if found else None


def flaky_cells(first: dict, second: dict) -> list[str]:
    """Return the cells whose two runs (`--evidence` files) differ on either side, or ran in one run only."""
    runs = [{cell["cell"]: (cell["base"], cell["candidate"]) for cell in data["cells"]} for data in (first, second)]
    return sorted(name for name in runs[0].keys() | runs[1].keys() if runs[0].get(name) != runs[1].get(name))


def _python(root: Path) -> str:
    """Return the checkout's own interpreter where it has a `.venv`, else this one."""
    venv = root / ".venv" / "bin" / "python"
    return str(venv if venv.exists() else sys.executable)


def run_tests(root: Path, base: str, jobs: int, coverage: Path) -> bool:
    """Run the tests the diff reaches with line coverage into `coverage`; return whether one failed.

    A selection of helper modules alone collects no test (pytest's status 5), which fails nothing.
    """
    rules_file = root / "scripts" / "related_tests.toml"
    rules = tomllib.loads(rules_file.read_text("utf-8"))["rule"] if rules_file.exists() else []
    nodes = related_tests.select(root, base, None, rules)
    print(f"verify: tests the diff reaches: {len(nodes)}", flush=True)
    if not nodes:
        return False
    env = {**os.environ, "PYTHONPATH": str(root / "src"), "COVERAGE_FILE": str(coverage)}
    package = root / "src" / compare_release.PACKAGE
    options = ["-q", "-p", "no:randomly", "-n", str(jobs), f"--cov={package}", "--cov-report=", "--cov-fail-under=0"]
    command = [_python(root), "-m", "pytest", *options, *nodes]
    return subprocess.run(command, cwd=root, env=env, check=False).returncode not in {0, NO_TESTS_COLLECTED}


def _cache(cache_dir: Path | None) -> list[str]:
    """Return the `--cache-dir` arguments of the comparator: none for its default cache."""
    return [] if cache_dir is None else ["--cache-dir", str(cache_dir.absolute())]


def ensure_catalog(root: Path, base: str, ns: argparse.Namespace) -> Path:
    """Return the catalog to pick from: the one given the newest ancestor one, or one recorded at the merge base.

    A given catalog that is missing, unreadable, of another format or no ancestor of the base exits 2.
    """
    if ns.catalog is not None:
        given = ns.catalog.absolute()
        catalog, reason = _cell_catalog.loaded(given, root, base)
        if catalog is None:
            print(f"verify: --catalog refused, {reason}", file=sys.stderr)
            raise SystemExit(2)
        return given
    directory = Path(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")) / CATALOGS
    directory.mkdir(parents=True, exist_ok=True)
    if (found := newest_catalog(root, directory, base)) is not None:
        print(f"verify: catalog {found}", flush=True)
        return found
    out = directory / f"{base}.json.gz"
    print(f"verify: no catalog recorded before {base[:12]} in {directory}: recording one there (minutes)", flush=True)
    command = [sys.executable, str(GATE), "catalog", str(out), base, *_cache(ns.cache_dir)]
    if subprocess.run(command, env={**os.environ, behaviour_diff.REPOSITORY_ENV: str(root)}, check=False).returncode:
        msg = "verify: recording the catalog failed"
        raise SystemExit(msg)
    return out


def compare(root: Path, base: str, catalog: Path, ns: argparse.Namespace, work: Path) -> tuple[list[dict], list[dict]]:
    """Run the identity comparison of the picked cells twice at once; return the first run's cells and both evidences.

    The first run prints its report and may reuse cached base outputs; the second one writes its report to a file
    beside its empty cache, so both of its sides generate again. A run that wrote no evidence gives none.
    """
    jobs = str(max(1, ns.j // 2))
    tested = ["--test-coverage", str(work / "tests.coverage")] if (work / "tests.coverage").exists() else []

    def gate(cache_dir: Path | None, run: str) -> list[str]:
        return [
            *(sys.executable, str(GATE), "gate", base, str(root), "--identity", "--pick", str(catalog), *tested),
            *("--select", NO_TIER_INPUT, "--evidence", str(work / f"{run}.json"), "--json", str(work / f"{run}.jsonl")),
            *("-j", jobs, *_cache(cache_dir)),
        ]

    env = {**os.environ, behaviour_diff.REPOSITORY_ENV: str(root)}
    log = work / "second.log"
    with log.open("w", encoding="utf-8") as second_out, ThreadPoolExecutor(1) as pool:
        second = pool.submit(
            subprocess.run,
            gate(work / "cache", "second"),
            env=env,
            stdout=second_out,
            stderr=subprocess.STDOUT,
            check=False,
        )
        subprocess.run(gate(ns.cache_dir, "first"), env=env, check=False)
        second.result()
    reports = [work / "first.json", work / "second.json", work / "first.jsonl"]
    if not all(report.exists() for report in reports):
        print(f"verify: the second run:\n{log.read_text(encoding='utf-8')[-4000:]}")
        return [], []
    rows = [json.loads(line) for line in reports[2].read_text("utf-8").splitlines()]
    return rows, [json.loads(report.read_text("utf-8")) for report in reports[:2]]


def spans(lines: set[int]) -> str:
    """Return sorted line numbers as ranges: `3-5,9`."""
    ordered = sorted(lines)
    starts = [line for line in ordered if line - 1 not in lines]
    ends = [line for line in ordered if line + 1 not in lines]
    return ",".join(str(start) if start == end else f"{start}-{end}" for start, end in zip(starts, ends, strict=True))


def uncovered(units: list[dict]) -> list[str]:
    """Return a line per side and file: the lines of the changed code objects no compared cell ran, and their names."""
    files: dict[tuple[str, str], tuple[set[int], list[str]]] = {}
    for unit in units:
        if not unit["ran"]:
            lines, names = files.setdefault((unit["side"], unit["path"]), (set(), []))
            lines.update(line for where in unit["missing"] for line in compare_release.lines_of(where))
            names.append(unit["qualname"])
    return [
        f"{side} {PACKAGE_SOURCE}{path}:{spans(lines) or '-'} ({', '.join(names)})"
        for (side, path), (lines, names) in sorted(files.items())
    ]


def judge(rows: list[dict], evidences: list[dict]) -> list[str]:
    """Print what the comparison found and return why it fails: differing or flaky cells; warn of unrun changed code.

    Changed code no compared cell ran fails nothing: it is listed under a warning, by side, file and lines.
    """
    if not evidences:
        return ["the comparison wrote no evidence (see above)"]
    cells, units = evidences[0]["cells"], evidences[0]["units"]
    violations = sum(row["class"] == "violation" for row in rows)
    flaky = flaky_cells(*evidences)
    print("".join(f"verify: flaky cell, its two runs differ: {cell}\n" for cell in flaky), end="")
    unrun = sum(not unit["ran"] for unit in units)
    if unrun:
        print(f"WARN uncovered: {unrun} of {len(units)} changed code objects no compared cell ran (side, file:lines):")
        print("".join(f"WARN   {line}\n" for line in uncovered(units)), end="")
    compared = sum(cell["compared"] for cell in cells)
    print(f"verify: cells compared {compared} of {len(cells)}, violations {violations}, flaky {len(flaky)}")
    failed = [f"violations: {violations}"] if violations else []
    return failed + ([f"flaky cells: {len(flaky)}"] if flaky else [])


def main(argv: list[str] | None = None) -> int:
    """Run the tests the change reaches and the identity comparison of its cells, and say what failed."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="origin/main", help="the change is measured from the merge base with this")
    parser.add_argument("--catalog", type=Path, help="cell catalog to pick from (default: see the module docstring)")
    parser.add_argument("-j", type=int, default=max(2, (os.cpu_count() or 2) // 2), help="parallel tests and cells")
    parser.add_argument("--cache-dir", type=Path, help="the comparator's cache (default: the comparator's default)")
    ns = parser.parse_args(argv)
    root = Path(_git(Path.cwd(), "rev-parse", "--show-toplevel"))
    base = _git(root, "merge-base", ns.base, "HEAD")
    if ns.catalog is not None:
        ensure_catalog(root, base, ns)
    started = time.monotonic()
    print(f"verify: {root} (working tree) against the merge base {base[:12]} with {ns.base}", flush=True)
    with tempfile.TemporaryDirectory(prefix="verify-") as scratch:
        work = Path(scratch)
        failed = ["tests"] if run_tests(root, base, ns.j, work / "tests.coverage") else []
        print(f"verify: tests done after {time.monotonic() - started:.0f}s", flush=True)
        if src_changed(root, base):
            failed += judge(*compare(root, base, ensure_catalog(root, base, ns), ns, work))
        else:
            print("verify: src/ is unchanged since the merge base: no cell can differ")
    verdict = f"FAILED: {'; '.join(failed)}" if failed else "passed"
    print(f"verify: {verdict} ({time.monotonic() - started:.0f}s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
