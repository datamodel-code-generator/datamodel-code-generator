"""Keep each local pre-commit hook, and the behaviour gate's workflow, triggered by the code it runs.

A local hook runs its entry script, and the script runs every module of ``scripts`` and ``tests`` it imports, at
module level or in a function, directly or through another such module (an import only type checkers run does not
count). The hook's ``files`` pattern matches each of them, or a change to an imported module skips the hook. Imports
are read through ``scripts/_source_model.py``.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import pytest
import yaml

from scripts._source_model import SourceModule
from scripts.behaviour_diff import ORACLE_FILES
from tests.conftest import assert_output

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ".pre-commit-config.yaml"
GATE_WORKFLOW = ".github/workflows/behaviour-diff.yaml"
CHECKER_PACKAGES = ("scripts", "tests")
ENTRY_SCRIPT = re.compile(r"scripts/[\w/]+\.py")
GATE_ENTRY = "scripts/behaviour_diff.py"
SPAWNED_SCRIPT = re.compile(r'with_name\("(\w+\.py)"\)|"-m", "scripts\.(\w+)"|"(scripts/\w+\.py)"')
MISSING_FAILURE_MESSAGE = (
    "These local pre-commit hooks run modules their files pattern does not match, so a change to one of them skips "
    "the hook: add each module to the hook's files pattern in .pre-commit-config.yaml."
)


def _module_file(root: Path, name: str) -> Path | None:
    """Return the file of a module of the checkers' own packages below a root, if there is one."""
    parts = name.split(".")
    if parts[0] not in CHECKER_PACKAGES:
        return None
    base = root.joinpath(*parts)
    return next((path for path in (base / "__init__.py", base.with_suffix(".py")) if path.is_file()), None)


def _imported_files(root: Path, path: Path) -> list[Path]:
    """Return the checker modules an import of a file runs: the longest module each imported name names."""
    relative = path.relative_to(root)
    module = ".".join(relative.with_suffix("").parts).removesuffix(".__init__")
    model = SourceModule(path.read_text(encoding="utf-8"), relative.as_posix(), module)
    found = []
    for item in model.imports:
        names = [name for name in (item.target, item.source) if isinstance(name, str) and name]
        for name in names if not item.typing_only else []:
            parts = name.split(".")
            files = (_module_file(root, ".".join(parts[:end])) for end in range(len(parts), 0, -1))
            found += [file] if (file := next((file for file in files if file is not None), None)) is not None else []
    return found


def runs(root: Path, entry: Path) -> list[str]:
    """Return the checker files an entry script runs below a root: itself and every module it imports, transitively."""
    seen: set[Path] = set()
    pending = [entry]
    while pending:
        path = pending.pop()
        if path not in seen:
            seen.add(path)
            pending += _imported_files(root, path)
    return sorted(path.relative_to(root).as_posix() for path in seen)


def missing_files(root: Path, config: dict) -> dict[str, list[str]]:
    """Return, for each local hook of a pre-commit configuration, the files its entry runs that its pattern misses."""
    hooks = [hook for repo in config["repos"] if repo["repo"] == "local" for hook in repo["hooks"]]
    found = {}
    for hook in hooks:
        pattern = re.compile(hook.get("files", ""))
        ran = [path for script in ENTRY_SCRIPT.findall(hook["entry"]) for path in runs(root, root / script)]
        found[hook["id"]] = sorted({path for path in ran if not pattern.search(path)})
    return found


def _config() -> dict:
    return yaml.safe_load((ROOT / CONFIG).read_text(encoding="utf-8"))


@pytest.mark.allow_direct_assert("a hook that misses a module fails on its own, not through a stored output")
def test_local_hooks_run_when_their_scripts_change() -> None:
    """A local hook's ``files`` pattern matches its entry script and every checker module that script imports."""
    found = missing_files(ROOT, _config())
    assert not {hook: paths for hook, paths in found.items() if paths}, MISSING_FAILURE_MESSAGE
    assert_output("".join(f"{hook}: {', '.join(paths) or 'none missing'}\n" for hook, paths in found.items()))


@pytest.mark.allow_direct_assert("the seeded gap must be found, not only stored")
def test_a_pattern_without_an_imported_module_misses_it() -> None:
    """A hook whose pattern lacks a module its script imports, here the source model, reports that module."""
    config = _config()
    for repo in config["repos"]:
        for hook in repo["hooks"] if repo["repo"] == "local" else []:
            hook["files"] = hook.get("files", "").replace(r"|scripts/_source_model\.py", "")
    found = {hook: paths for hook, paths in missing_files(ROOT, config).items() if paths}
    assert any("scripts/_source_model.py" in paths for paths in found.values()), found
    assert_output("".join(f"{hook}: {', '.join(paths)}\n" for hook, paths in found.items()))


def gate_misses(root: Path, workflow: dict) -> dict[str, list[str]]:
    """Return, per event of the gate's workflow, the scripts its steps run that the event's ``paths`` miss.

    The gate's own code is the steps' scripts, every ``scripts`` module they import and every script they start in a
    subprocess (`gate_files`); the harness modules under ``tests`` they import are the Test workflow's to run on a
    change, so they are not required here.
    """
    steps = [step for job in workflow["jobs"].values() for step in job.get("steps", [])]
    scripts = sorted({script for step in steps for script in ENTRY_SCRIPT.findall(step.get("run", ""))})
    ran = gate_files(root, scripts)
    events = workflow.get("on", workflow.get(True))
    return {
        event: [path for path in ran if not any(fnmatch.fnmatchcase(path, glob) for glob in events[event]["paths"])]
        for event in ("pull_request", "push")
    }


def _workflow() -> dict:
    return yaml.safe_load((ROOT / GATE_WORKFLOW).read_text(encoding="utf-8"))


@pytest.mark.allow_direct_assert("a gate that misses a module fails on its own, not through a stored output")
def test_behaviour_gate_runs_when_its_scripts_change() -> None:
    """The gate workflow's ``paths`` match every script its steps run and every checker module those scripts import."""
    found = gate_misses(ROOT, _workflow())
    assert not any(found.values()), f"add these to the paths of {GATE_WORKFLOW}: {found}"
    assert_output("".join(f"{event}: {', '.join(paths) or 'none missing'}\n" for event, paths in found.items()))


@pytest.mark.allow_direct_assert("the seeded gap must be found, not only stored")
def test_gate_paths_without_an_imported_module_miss_it() -> None:
    """Gate paths that lack a module a gate script imports, here the comparator, report that module."""
    workflow = _workflow()
    events = workflow.get("on", workflow.get(True))
    for event in ("pull_request", "push"):
        events[event]["paths"] = [glob for glob in events[event]["paths"] if glob != "scripts/compare_release.py"]
    found = gate_misses(ROOT, workflow)
    assert all("scripts/compare_release.py" in paths for paths in found.values()), found
    assert_output("".join(f"{event}: {', '.join(paths)}\n" for event, paths in found.items()))


def gate_files(root: Path, entries: list[str]) -> list[str]:
    """Return the ``scripts`` files entry scripts run: themselves, their imports and the scripts they start.

    A started script is named by a ``with_name("x.py")`` beside its starter, by ``"-m", "scripts.x"`` or
    by a ``"scripts/x.py"`` path; each runs what it imports in turn.
    """
    seen: set[str] = set()
    pending = list(entries)
    while pending:
        entry = pending.pop()
        if entry in seen:
            continue
        files = runs(root, root / entry)
        seen.update(files)
        for name in files:
            for beside, module, path in SPAWNED_SCRIPT.findall((root / name).read_text(encoding="utf-8")):
                spawned = path or (f"scripts/{beside}" if beside else f"scripts/{module}.py")
                pending += [spawned] if (root / spawned).is_file() else []
    return sorted(path for path in seen if path.startswith("scripts/") and path != "scripts/__init__.py")


@pytest.mark.allow_direct_assert(
    "a list that drifts from the gate's files fails on its own, not through a stored output"
)
def test_oracle_files_are_the_files_the_gate_runs() -> None:
    """``ORACLE_FILES``, the files whose change also runs the base's gate, are every ``scripts`` file the gate runs."""
    ran = gate_files(ROOT, [GATE_ENTRY])
    assert sorted(ORACLE_FILES) == ran, (
        f"missing from ORACLE_FILES in {GATE_ENTRY}: {sorted(set(ran) - set(ORACLE_FILES))}; "
        f"not run by the gate: {sorted(set(ORACLE_FILES) - set(ran))}"
    )
    assert_output("".join(f"{path}\n" for path in ran))


LINT_WORKFLOW = ".github/workflows/lint.yaml"
LINT_SKIP = re.compile(r"\bSKIP=([\w,-]+)")


def lint_skipped_hooks(root: Path) -> set[str]:
    """Return the hooks the Lint workflow skips: the ``SKIP=`` list of its ``run`` steps."""
    steps = yaml.safe_load((root / LINT_WORKFLOW).read_text(encoding="utf-8"))["jobs"]["lint"]["steps"]
    return {hook for step in steps for found in LINT_SKIP.findall(step.get("run", "")) for hook in found.split(",")}


@pytest.mark.allow_direct_assert("a hook that one runner skips and the other runs fails on its own")
def test_lint_workflow_skips_the_hooks_pre_commit_ci_skips() -> None:
    """The Lint workflow and pre-commit.ci skip the same hooks: those that build a tox environment neither has."""
    ci_skip = set(_config()["ci"]["skip"])
    lint_skip = lint_skipped_hooks(ROOT)
    only_ci, only_lint = sorted(ci_skip - lint_skip), sorted(lint_skip - ci_skip)
    assert ci_skip == lint_skip, f"only in ci.skip: {only_ci}; only in {LINT_WORKFLOW}: {only_lint}"
    assert_output("".join(f"{hook}\n" for hook in sorted(ci_skip)))
