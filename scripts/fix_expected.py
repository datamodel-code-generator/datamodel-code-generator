"""Apply expected output that another environment produced, or show where an item's expected output is stored.

Usage:
    python scripts/fix_expected.py --apply '<payload>' ['<payload>' ...]
    python scripts/fix_expected.py --from-ci <run id or #PR number>
    python scripts/fix_expected.py --where 'tests/m.py::test_x[a]'
    python scripts/fix_expected.py --repair tests/m.refs

A failing CI lane prints a ``dcg-expected-fix:`` payload: what that environment compared and its output. ``--apply``
writes it into ``tests/data/expected`` and the ``.refs`` sidecars through an apply-only pytest session, so no other
interpreter or formatter is needed locally. ``--from-ci`` collects every payload of a CI run with ``gh``. Payloads
are untrusted input: one that names anything but an existing test item and a label below ``tests/data/expected``,
whose digest does not match its value, or that comes from this environment (run its test here instead) is rejected.
``--apply`` then writes nothing; ``--from-ci`` reports the rejected payloads and applies the rest.
``--repair`` drops the lines of a hand-edited sidecar that pytest cannot read and re-records the tests they were in.
These three write files only inside ``tests/data/expected`` and the sidecars, and refuse to run in CI.

Maintainers regenerate the index in every environment the CI lanes use (slow; needs tox and network):
    python scripts/fix_expected.py --all-environments [--environment NAME ...] [-- PYTEST SELECTION]
    python scripts/fix_expected.py --all-environments --observe-only --output DIR [--environment NAME ...]
    python scripts/fix_expected.py --observations DIR [DIR ...]
    python scripts/fix_expected.py --drop-uncollected DIR [DIR ...]
A run over every environment also drops the lines of tests no environment collects any more, conditional lines
included; ``--drop-uncollected`` does only that, from observations of every environment that collects their modules.

Maintainers store each content once, for every sidecar or only those of some test directories, modules or sidecars:
    python scripts/fix_expected.py --deduplicate [PATH[::TEST] ...]
It points the references of equal stored units at one of them and deletes the copies left unused, after checking that
every line compares the same bytes as before; it only rewrites sidecars and deletes stored files. The units of a label
whose references compare different bytes stay, so reading that label by name returns what it did.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT)]

from tests.units import (  # noqa: E402
    BROKEN,
    REFS_SUFFIX,
    RefLine,
    RefsIndex,
    UnsafeWriteError,
    decode_payload,
    deduplicate,
    duplicates,
    format_refs,
    label_path,
    label_storages,
    parse_refs,
    payload_problem,
    predicate_text,
    refs_sections,
    split_label,
    stored_digest,
    too_long,
    tree_label,
    write_files,
)

PAYLOAD_PREFIX = "dcg-expected-fix: "
FORMATTER = "DATAMODEL_CODE_GENERATOR_TEST_DEFAULT_FORMATTER"
PARITY = "DATAMODEL_CODE_GENERATOR_CHECK_BUILTIN_FORMATTER_PARITY"
TYPING = {
    "DATAMODEL_CODE_GENERATOR_CLIENT_TYPING_E2E": "1",
    "DATAMODEL_CODE_GENERATOR_CODEC_TYPING_E2E": "1",
    "DATAMODEL_CODE_GENERATOR_FASTAPI_TYPING_E2E": "1",
}
TYPING_TESTS = (
    "tests/api_generation/test_fastapi_typing.py",
    "tests/api_generation/test_client_typing.py",
    "tests/model_codecs/test_codec_typing.py",
)
HTTP_TESTS = ("tests/test_http.py", "tests/test_http_https.py")


@dataclass(frozen=True)
class Environment:
    """A CI lane the maintainer runner reproduces: its tox environment, variables, and tests.

    Without tests, the lane runs its own tox command, which passes ``PYTEST_ADDOPTS`` on.
    """

    tox: str
    variables: dict[str, str] = field(default_factory=dict)
    tests: tuple[str, ...] | None = ("tests",)
    builtin: bool = False


ENVIRONMENTS = {
    **{
        f"py3{minor}": Environment(f"py3{minor}-nocov-parallel", builtin=True)
        for minor in ("10", "11", "12", "13", "14", "15")
    },
    "py314-external": Environment("py314-nocov-parallel"),
    "py312-blacklatest": Environment("py312-blacklatest-nocov-parallel", {PARITY: "1"}),
    **{
        f"py312-{tool}": Environment(f"py312-{tool}-nocov-parallel")
        for tool in ("black24", "black22", "isort7", "isort6")
    },
    **{
        name: Environment(name, tests=None)
        for name in (
            "pydantic200",
            "pydantic20",
            "pydantic25",
            "pydantic212",
            "pydantic213",
            "pydantic214",
            "graphqlcore33",
            "migration-legacy",
            "migration-boundary",
        )
    },
    **{
        name: Environment(name, tests=("tests/main/test_payload_validation.py", "tests/main/test_exec_validation.py"))
        for name in (
            "py311-payload-runtime-min",
            "py311-payload-runtime-latest",
            "py314-payload-runtime-min",
            "py314-payload-runtime-latest",
        )
    },
    "typing-nocov-e2e": Environment("typing-nocov-e2e", TYPING, TYPING_TESTS),
    "typing-pydantic214-nocov-e2e": Environment("typing-pydantic214-nocov-e2e", TYPING, TYPING_TESTS),
    "fastapi-install-nocov-e2e": Environment(
        "fastapi-install-nocov-e2e",
        {"DATAMODEL_CODE_GENERATOR_FASTAPI_INSTALL_E2E": "1"},
        ("tests/api_generation/test_fastapi_install.py",),
    ),
    "client-install-nocov-e2e": Environment(
        "client-install-nocov-e2e",
        {"DATAMODEL_CODE_GENERATOR_CLIENT_INSTALL_E2E": "1"},
        ("tests/api_generation/test_client_install.py",),
    ),
    "client-e2e": Environment("client-e2e", tests=("tests/api_generation/test_client_runtime.py",)),
    "client-py311-e2e": Environment("client-py311-e2e", tests=("tests/api_generation/test_client_runtime.py",)),
    "fastapi-e2e": Environment("fastapi-e2e", tests=("tests/api_generation/test_fastapi_server.py",)),
    "httpx-e2e": Environment("httpx-e2e", {"DATAMODEL_CODE_GENERATOR_EXPECTED_HTTP_BACKEND": "httpx"}, HTTP_TESTS),
    "httpx2-min-e2e": Environment(
        "httpx2-min-e2e",
        {
            "DATAMODEL_CODE_GENERATOR_EXPECTED_HTTP_BACKEND": "httpx2",
            "DATAMODEL_CODE_GENERATOR_TEST_HTTP_BACKEND": "httpx2",
        },
        HTTP_TESTS,
    ),
}


def payloads_in(log: str) -> list[str]:
    """Return every distinct payload printed in a CI log, in order."""
    seen: dict[str, None] = {}
    for match in re.finditer(rf"{re.escape(PAYLOAD_PREFIX)}(\S+)", log):
        seen.setdefault(match.group(1), None)
    return list(seen)


def run_apply(data: dict[str, object], modules: Iterable[str], root: Path = ROOT) -> int:
    """Run an apply-only pytest session that writes the data into the expected files and sidecars."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory, "apply.json")
        path.write_text(json.dumps(data), encoding="utf-8")
        selection = sorted(set(modules))
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:xdist",
            "-qq",
            f"--rootdir={root}",
        ]
        result = subprocess.run([*command, f"--expected-apply={path}", *selection], cwd=root, check=False)
    return 0 if result.returncode in {0, 5} else result.returncode


def index_for(module: str, root: Path = ROOT) -> RefsIndex:
    """Return the index of the tests root a path is or lies in: the nearest directory with ``data/expected``."""
    path = root / module
    # A name longer than the file system allows cannot hold tests; probing it raises on Python before 3.14.
    candidates = (parent for parent in (path, *path.parents) if not too_long(parent.parts))
    return RefsIndex(next((parent for parent in candidates if (parent / "data" / "expected").is_dir()), root / "tests"))


def apply_payloads(texts: Sequence[str], root: Path = ROOT, *, skip: bool = False) -> int:
    """Apply CI payloads after checking every one of them.

    A single bad payload stops all of them, unless ``skip`` is set (``--from-ci``, whose run may also hold payloads
    of this environment): then bad payloads are reported and the rest applied.
    """
    payloads, problems = [], []
    for number, text in enumerate(texts, start=1):
        try:
            payload = decode_payload(text)
        except ValueError as error:
            problems.append(f"payload {number}: {error}")
            continue
        module = str(payload.get("item", "")).partition("::")[0] if isinstance(payload, dict) else ""
        if problem := payload_problem(payload, root, index_for(module, root)):
            problems.append(f"payload {number}: {problem}")
            continue
        payloads.append(payload)
    if problems:
        print(("Skipped:\n" if skip else "Rejected; nothing was written:\n") + "\n".join(problems))
        if not skip:
            return 2
    if not payloads:
        print("No dcg-expected-fix payloads to apply.")
        return 1
    modules = (str(payload["item"]).partition("::")[0] for payload in payloads)
    return run_apply({"payloads": payloads, "skip_rejected": skip}, modules, root)


def repair(sidecar: str, root: Path = ROOT) -> int:
    """Drop the lines of a sidecar that pytest cannot read, then re-record the tests they belonged to.

    Lines in a readable ``[test]`` section re-record that test; any other broken line re-records the module. Two
    lines for one label re-record their test too, which keeps the one matching the output.
    """
    path = root / sidecar
    if path.suffix != REFS_SUFFIX or not path.is_file() or not path.with_suffix(".py").is_file():
        print(f"{sidecar} is not the .refs sidecar of a test module.")
        return 2
    module = Path(sidecar).with_suffix(".py").as_posix()
    text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    found, problems = parse_refs(text)
    lines = list(dict.fromkeys(found))
    sections = refs_sections(text)
    keys = Counter(line.key for line in lines)
    functions = {sections[problem.number - 1] for problem in problems if problem.kind == BROKEN}
    functions |= {line.function for line in lines if keys[line.key] > 1}
    try:
        write_files(index_for(module, root).tests_root, {path: format_refs(lines)})
    except UnsafeWriteError as error:
        print(error)
        return 2
    if not functions:
        print(f"{sidecar} is in the form pytest writes.")
        return 0
    selection = [module] if None in functions else [f"{module}::{function}" for function in sorted(functions)]
    command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "no:xdist", "-q", *selection]
    result = subprocess.run(command, cwd=root, check=False)
    return 0 if result.returncode in {0, 5} else result.returncode


def compared_bytes(
    lines: Mapping[Path, list[RefLine]], expected_root: Path, deleted: Collection[Path] = ()
) -> dict[tuple[Path, RefLine], str]:
    """Return a digest of the stored bytes each line compares, by sidecar and the line without its storage.

    Files in ``deleted`` count as gone, so a plan can be checked before it is written.
    """
    digests: dict[str, str] = {}

    def stored(storage: str) -> str:
        if storage not in digests:
            name, pattern = split_label(storage)
            digests[storage] = stored_digest(expected_root / name, pattern, deleted)
        return digests[storage]

    return {
        (sidecar, replace(line, storage=None)): stored(line.stored)
        for sidecar, found in lines.items()
        for line in found
    }


def resolved_bytes(
    lines: Mapping[Path, list[RefLine]], expected_root: Path, deleted: Collection[Path] = ()
) -> dict[str, str]:
    """Return a digest of the stored bytes code that reads a label by name gets (``stored_expected``, the docs).

    Files in ``deleted`` count as gone, so a plan can be checked before it is written.
    """
    return {
        tree_label(label, pattern) if pattern is not None else label: stored_digest(
            expected_root / storage, pattern, deleted
        )
        for label, storages in label_storages(lines).items()
        for pattern, storage in storages.items()
    }


def disagreeing_storages(lines: Mapping[Path, list[RefLine]], compared: Mapping[tuple[Path, RefLine], str]) -> set[str]:
    """Return the storages of labels whose references compare different bytes.

    What reading such a label by name returns depends on which of its units exist, so they all stay.
    """
    contents: dict[str, set[str]] = {}
    for sidecar, found in lines.items():
        for line in found:
            contents.setdefault(line.label, set()).add(compared[sidecar, replace(line, storage=None)])
    return {split_label(line.stored)[0] for found in lines.values() for line in found if len(contents[line.label]) > 1}


def _selects(sidecar: Path, line: RefLine, target: Path, test: str) -> bool:
    """Return whether a selection (a directory, module or sidecar, and an optional test or item) takes in a line."""
    inside = sidecar.is_relative_to(target) if target.is_dir() else sidecar == target.with_suffix(REFS_SUFFIX)
    return inside and (not test or test in {line.function, line.item} or line.function.startswith(f"{test}::"))


def deduplicate_expected(paths: Sequence[str], root: Path = ROOT) -> int:
    """Store each content once for the tests of the given directories, modules, sidecars, tests or items.

    Without paths every sidecar takes part. The plan is checked before anything is written: every line must compare
    the same bytes afterwards.
    """
    index = RefsIndex(index_for(paths[0].partition("::")[0] if paths else "tests", root).tests_root.resolve())
    selected = [((root / module).resolve(), test) for module, _, test in (path.partition("::") for path in paths)]
    if outside := [
        path
        for path, (target, _) in zip(paths, selected, strict=True)
        if not target.exists() or not target.is_relative_to(index.tests_root)
    ]:
        print(f"{outside[0]} is not a test directory, module or sidecar below {index.tests_root.name}/.")
        return 2
    if unreadable := [sidecar for sidecar in index.sidecars() if index.load(sidecar)[1]]:
        sidecar = Path(os.path.relpath(unreadable[0], root)).as_posix()
        print(f"{sidecar} is not in the form pytest writes. Fix: python scripts/fix_expected.py --repair {sidecar}")
        return 2
    lines = index.all_lines()
    references = [(sidecar, line) for sidecar, found in lines.items() for line in found]
    if unmatched := [
        path
        for path, (target, test) in zip(paths, selected, strict=True)
        if test and not any(_selects(sidecar, line, target, test) for sidecar, line in references)
    ]:
        print(f"{unmatched[0]} names no test that compares recorded expected output.")
        return 2
    before = compared_bytes(lines, index.expected_root)
    keep = disagreeing_storages(lines, before)
    plan = deduplicate(
        lines,
        index.expected_root,
        lambda sidecar, line: not selected or any(_selects(sidecar, line, *selection) for selection in selected),
        keep,
    )
    after = {
        sidecar: parse_refs(plan.sidecars[sidecar])[0] if sidecar in plan.sidecars else found
        for sidecar, found in lines.items()
    }
    planned = compared_bytes(after, index.expected_root, plan.deletes)
    named, renamed = (
        resolved_bytes(lines, index.expected_root),
        resolved_bytes(after, index.expected_root, plan.deletes),
    )
    differing = sorted(
        f"  {Path(os.path.relpath(sidecar, root)).as_posix()}: {line.item} {line.label}"
        for sidecar, line in before.keys() | planned.keys()
        if before.get((sidecar, line)) != planned.get((sidecar, line))
    ) + sorted(
        f"  {label}, read by name" for label in named.keys() | renamed.keys() if named.get(label) != renamed.get(label)
    )
    if differing:
        print("Refused: these would compare or read other bytes; nothing was written:")
        print("\n".join(differing))
        return 1
    write_files(index.tests_root, plan.sidecars, plan.deletes)
    moved = [event for event in plan.events if event.kind == "merged"]
    remaining = duplicates(after, index.expected_root)
    trees = sum(len(group) - 1 for group in remaining if split_label(group[0])[1] is not None)
    files = sum(len(group) - 1 for group in remaining) - trees
    print(
        f"Deduplicated: {sum(len(event.items) for event in moved)} references in {len(plan.sidecars)} sidecars now use "
        f"{len({event.target for event in moved})} stored units; deleted {len(plan.deletes)} stored files. "
        "Every line compares the same bytes as before.\n"
        f"Remaining duplicates in all sidecars: {files} files, {trees} trees."
    )
    if kept := [storage for group in remaining for storage in group if split_label(storage)[0] in keep]:
        print("Kept in all sidecars, because their label's references differ:")
        print("\n".join(f"  {storage}" for storage in kept))
    return 0


def ci_variable() -> str | None:
    """Return the variable that marks this run as CI, as the test suite decides it, or None."""
    from inline_snapshot._snapshot_session import is_ci_run  # noqa: PLC0415, PLC2701

    return is_ci_run() or None


class GhError(RuntimeError):
    """A gh command failed, for example because a run's logs are not available until the run finishes."""


def gh(*arguments: str) -> str:
    """Return what a gh command prints; a failing command raises ``GhError`` with what gh reported."""
    result = subprocess.run([shutil.which("gh") or "gh", *arguments], capture_output=True, text=True, check=False)
    if result.returncode:
        raise GhError(result.stderr.strip())
    return result.stdout


def ci_logs(reference: str) -> str:
    """Return the failed-job logs of a CI run, or of the latest failed runs of a pull request (``#123``)."""
    run_ids = [reference]
    if reference.startswith("#"):
        head = gh("pr", "view", reference[1:], "--json", "headRefOid", "--jq", ".headRefOid").strip()
        runs = json.loads(gh("run", "list", "--commit", head, "--json", "databaseId,conclusion"))
        run_ids = [str(run["databaseId"]) for run in runs if run["conclusion"] == "failure"]
    return "\n".join(gh("run", "view", run_id, "--log-failed") for run_id in run_ids)


def from_ci(reference: str, root: Path = ROOT) -> int:
    """Apply every payload of a CI run; when gh cannot read its logs, say how to go on instead."""
    try:
        log = ci_logs(reference)
    except GhError as error:
        print(
            f"gh could not read the failed-job logs of {reference}: {error}\n"
            "If the run is still in progress, wait for it to finish and run this command again, or paste the "
            f"{PAYLOAD_PREFIX.strip()} lines of its log into: python scripts/fix_expected.py --apply '<payload>' ..."
        )
        return 2
    return apply_payloads(payloads_in(log), root, skip=True)


def where(nodeid: str, root: Path = ROOT) -> int:
    """Print the labels an item compares and where each is stored."""
    module, _, rest = nodeid.partition("::")
    function, _, param = rest.partition("[")
    param = param.removesuffix("]")
    module_path = root / module
    tests_root = next(
        (parent for parent in module_path.parents if (parent / "data" / "expected").is_dir()), root / "tests"
    )
    index = RefsIndex(tests_root)
    lines = [
        line
        for line in index.load(module_path.with_suffix(".refs"))[0]
        if (line.function, line.param) == (function, param)
    ]
    if not lines:
        stage = Path(os.path.relpath(tests_root, root)).as_posix()
        print(f"{nodeid} has no expected output recorded. Run: pytest '{nodeid}' && git add -A {stage}")
        return 1
    for line in lines:
        flags = ", ".join(
            filter(None, ("only in some environments" if line.conditional else "", predicate_text(line.predicate)))
        )
        stored = Path(os.path.relpath(label_path(split_label(line.stored)[0], index.expected_root), root)).as_posix()
        print(f"{line.label} -> {stored}{f'  ({flags})' if flags else ''}")
    return 0


def tox_command() -> list[str]:
    """Return how to run tox: the installed one, or tox with tox-uv through uvx."""
    if tox := shutil.which("tox"):
        return [tox]
    return ["uvx", "--from", "tox>=4.32", "--with", "tox-uv", "tox"]


def observe(
    names: Sequence[str], output: Path, selection: Sequence[str], *, workers: str = "auto", root: Path = ROOT
) -> list[Path]:
    """Run each environment with ``--expected-observe`` and return the directories it recorded into."""
    directories = []
    for name in names:
        environment = ENVIRONMENTS[name]
        setup = subprocess.run([*tox_command(), "run", "-q", "-e", environment.tox, "--notest"], cwd=root, check=False)
        if setup.returncode:
            print(f"[fix_expected] {name}: tox could not create {environment.tox}; skipped", flush=True)
            continue
        scripts = "Scripts" if os.name == "nt" else "bin"
        python = root / ".tox" / environment.tox / scripts / ("python.exe" if os.name == "nt" else "python")
        variables = {key: value for key, value in os.environ.items() if key not in {FORMATTER, PARITY, "CI"}}
        variables |= environment.variables
        if environment.builtin:
            variables[FORMATTER] = "builtin"
        directory = output / name
        shutil.rmtree(directory, ignore_errors=True)
        print(f"[fix_expected] {name}: {environment.tox}", flush=True)
        if not (tests := selection or environment.tests):
            variables["PYTEST_ADDOPTS"] = f"-p no:cacheprovider --expected-observe={directory}"
            subprocess.run([*tox_command(), "run", "-e", environment.tox], cwd=root, env=variables, check=False)
        else:
            command = [str(python), "-m", "pytest", "-p", "no:cacheprovider", "-q", "-n", workers, "-m", "not perf"]
            subprocess.run(
                [*command, f"--rootdir={root}", f"--expected-observe={directory}", *tests],
                cwd=root,
                env=variables,
                check=False,
            )
        directories.append(directory)
    return directories


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--apply", nargs="+", metavar="PAYLOAD", help="apply dcg-expected-fix payloads")
    group.add_argument("--from-ci", metavar="RUN", help="apply every payload of a CI run id or a #PR number")
    group.add_argument("--where", metavar="NODEID", help="print where the expected output of an item is stored")
    group.add_argument("--repair", metavar="REFS", help="drop unreadable lines of a sidecar and re-record their tests")
    group.add_argument("--all-environments", action="store_true", help="maintainers: rebuild in every environment")
    group.add_argument("--observations", nargs="+", type=Path, metavar="DIR", help="maintainers: apply observations")
    group.add_argument(
        "--drop-uncollected",
        nargs="+",
        type=Path,
        metavar="DIR",
        help="maintainers: drop the lines of tests no observed environment collects",
    )
    group.add_argument("--deduplicate", nargs="*", metavar="PATH", help="maintainers: store each content once")
    parser.add_argument("--environment", action="append", choices=sorted(ENVIRONMENTS), help="limit the environments")
    parser.add_argument("--observe-only", action="store_true", help="record observations without applying them")
    parser.add_argument("--output", type=Path, help="where observations are recorded (default: a temporary dir)")
    parser.add_argument("--workers", default="auto", help="pytest-xdist workers per environment (default: auto)")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    parser.add_argument("selection", nargs="*", help="pytest selection for --all-environments (default: each lane's)")
    arguments = parser.parse_args(argv)
    if arguments.selection and not arguments.all_environments and arguments.deduplicate is None:
        parser.error("a pytest selection only goes with --all-environments, and paths with --deduplicate")
    if not arguments.all_environments and (
        arguments.environment or arguments.observe_only or arguments.output or arguments.workers != "auto"
    ):
        parser.error("--environment, --observe-only, --output and --workers only go with --all-environments")
    root = arguments.root.resolve()
    commands: dict[str, Callable[[Any], int]] = {
        "apply": lambda texts: apply_payloads(texts, root),
        "from_ci": lambda run: from_ci(run, root),
        "where": lambda nodeid: where(nodeid, root),
        "repair": lambda sidecar: repair(sidecar, root),
        "observations": lambda paths: run_apply({"observations": [str(path.resolve()) for path in paths]}, (), root),
        "drop_uncollected": lambda paths: run_apply(
            {"observations": [str(path.resolve()) for path in paths], "merge": False, "drop_uncollected": True},
            (),
            root,
        ),
        "deduplicate": lambda paths: deduplicate_expected([*paths, *arguments.selection], root),
    }
    if chosen := next((name for name in commands if getattr(arguments, name) is not None), None):
        if chosen in {"apply", "from_ci", "repair", "deduplicate"} and (variable := ci_variable()):
            option = f"--{chosen.replace('_', '-')}"
            print(f"{variable} is set: {option} writes expected files, which CI never does. Run it on your machine.")
            return 2
        return commands[chosen](getattr(arguments, chosen))
    output = arguments.output or Path(tempfile.mkdtemp(prefix="dcg-expected-"))
    names = arguments.environment or sorted(ENVIRONMENTS)
    directories = observe(names, output.resolve(), arguments.selection, workers=arguments.workers, root=root)
    if arguments.observe_only:
        print(f"[fix_expected] observations in {output}")
        return 0
    complete = not arguments.environment and len(directories) == len(names)
    return run_apply({"observations": [str(path) for path in directories], "drop_uncollected": complete}, (), root)


if __name__ == "__main__":
    raise SystemExit(main())
