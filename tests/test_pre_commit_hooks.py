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
from tests.conftest import assert_output

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ".pre-commit-config.yaml"
GATE_WORKFLOW = ".github/workflows/behaviour-diff.yaml"
CHECKER_PACKAGES = ("scripts", "tests")
ENTRY_SCRIPT = re.compile(r"scripts/[\w/]+\.py")
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

    The gate's own code is the steps' scripts and every ``scripts`` module they import; the harness modules under
    ``tests`` they import are the Test workflow's to run on a change, so they are not required here.
    """
    steps = [step for job in workflow["jobs"].values() for step in job.get("steps", [])]
    scripts = sorted({script for step in steps for script in ENTRY_SCRIPT.findall(step.get("run", ""))})
    ran = sorted({path for script in scripts for path in runs(root, root / script) if path.startswith("scripts/")})
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
