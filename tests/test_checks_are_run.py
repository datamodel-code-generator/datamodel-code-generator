"""Every check script and every source rule runs on the repository tree, not on fixtures only.

A check is reached only where something runs it on the tree:

- a workflow or action ``run:`` step that executes it (``python scripts/check_x.py`` or ``python -m scripts.check_x``
  as a command, not as the text of an ``echo``);
- a ``commands`` line of a tox environment that a workflow runs (``tox ... -e <env>``, with the environment named
  literally, by a value of the job's matrix or by the words of a ``for`` loop), ``{[testenv:x]commands}`` followed;
- the ``entry`` of a pre-commit hook;
- the ratchet, itself reached, counting it;
- a test that asserts the result of its ``main`` directly (the call inside an ``assert``, so no stored output a fix
  run accepts stands in for it), with literal arguments that name no other tree (no ``--root``, no path), from a test
  function that takes no fixture but the output captures, changes no directory and is not skipped.

A mention is no run: a ``paths:`` filter, an ``echo``, a hook ``files:`` pattern, a tox ``description`` or a fixture
path. The code-owned files are read, never edited.
"""

from __future__ import annotations

import ast
import configparser
import inspect
import re
from collections import defaultdict
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING

import pytest
import yaml

from scripts import check_ratchets, check_source_rules
from scripts._source_model import SourceModule
from tests.conftest import assert_output

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from scripts._source_model import Scope

ROOT = Path(__file__).resolve().parents[1]
RATCHET = "scripts/check_ratchets.py"
TOX = "tox.ini"
HOOKS = ".pre-commit-config.yaml"
CHECK_RUN = re.compile(
    r"(?<![\w.-])python[\d.]*\s+(?:-(?!m\s)\S+\s+(?:[^\s-]\S*\s+)??)*"
    r"(?:-m\s+scripts\.(check_\w+)|scripts/(check_\w+)\.py)\b"
)
"""A Python command that executes a check script, by path or as a module, after options and their values."""
COMMAND_BREAK = re.compile(r"\n|&&|\|\||[;|`(]|\$\(")
"""What separates two shell commands of a ``run:`` text (a subshell or a command substitution starts one)."""
NOT_RUN = frozenset({"echo", "printf"})
"""Commands whose arguments are text, never executed."""
TOX_ENVIRONMENT = re.compile(r"(?<![\w-])tox\b[^\n;&|]*?\s-e\s*([^\s;]+)")
FOR_LOOP = re.compile(r"\bfor\s+(\w+)\s+in\s+([^;\n]+);")
VARIABLE = re.compile(r"\$\{?(\w+)\}?")
SUBSTITUTION = re.compile(r"\{\[testenv:([\w.-]+)\]commands\}")
TEST_CALL = re.compile(r"scripts import .*\bcheck_|scripts\.check_")
CHECK_MAIN = re.compile(r"scripts\.(check_\w+)\.main")
CAPTURES = frozenset({"capsys", "capfd", "capsysbinary", "capfdbinary"})
"""The fixtures a test on the tree may take: they capture output and move nothing."""
SKIPS = frozenset({
    "pytest.mark.skip",
    "pytest.mark.skipif",
    "pytest.mark.xfail",
    "pytest.skip",
    "pytest.xfail",
    "pytest.importorskip",
})
UNREACHED: dict[str, str] = {}
"""Checks that nothing runs on the tree (``scripts/check_x.py`` or ``scripts/check_x.py::rule``), with the reason.

The ratchet counts the entries (``allowlist:UNREACHED``), so the table only shrinks.
"""


def _executed(text: str) -> set[str]:
    """Return the check scripts a shell text executes, as ``scripts/check_<name>.py``; comment lines run nothing."""
    joined = "\n".join(line for line in text.replace("\\\n", " ").splitlines() if not line.lstrip().startswith("#"))
    return {
        f"scripts/{found[1] or found[2]}.py"
        for command in COMMAND_BREAK.split(joined)
        if (words := command.split()) and words[0] not in NOT_RUN
        for found in CHECK_RUN.finditer(command)
    }


def _strings(value: object) -> Iterator[str]:
    """Yield every string of a YAML value, nested lists and mappings included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _jobs(document: dict) -> Iterator[tuple[list[str], list[str]]]:
    """Yield the ``run:`` texts and the matrix values of each job of a workflow, or of the steps of an action."""
    holders = [*(document.get("jobs") or {}).values(), *([document["runs"]] if "runs" in document else [])]
    for job in holders:
        runs = [step["run"] for step in job.get("steps") or [] if isinstance(step.get("run"), str)]
        yield runs, list(_strings((job.get("strategy") or {}).get("matrix")))


def _tox_environments(runs: Iterable[str], matrix: list[str]) -> set[str]:
    """Return the tox environments the ``run:`` texts of one job run: named, from the matrix or from a loop's words."""
    environments: set[str] = set()
    for run in runs:
        loops = {name: words.split() for name, words in FOR_LOOP.findall(run)}
        for argument in (found.strip("\"'") for found in TOX_ENVIRONMENT.findall(run)):
            variable = VARIABLE.fullmatch(argument)
            if "$" not in argument:
                names = [argument]
            elif variable and variable[1] in loops:
                names = loops[variable[1]]
            else:
                names = matrix
            environments.update(name for value in names for name in value.split(","))
    return environments


def _tox_commands(path: Path) -> dict[str, str]:
    """Return the ``commands`` of each tox environment, with the commands of other environments it names substituted."""
    config = configparser.ConfigParser(interpolation=None, strict=False)
    config.read_string(path.read_text(encoding="utf-8"))
    written = {
        section.removeprefix("testenv:"): config[section].get("commands", "")
        for section in config.sections()
        if section.startswith("testenv:")
    }

    def expanded(text: str) -> str:
        return SUBSTITUTION.sub(lambda found: expanded(written.get(found[1], "")), text)

    return {name: expanded(text) for name, text in written.items()}


def _channel_runs(root: Path) -> Iterator[tuple[str, str]]:
    """Yield ``(script, channel)`` for each workflow step, tox environment of a workflow and hook that runs a check."""
    tox = _tox_commands(root / TOX)
    for path in sorted(path for path in (root / ".github").rglob("*.y*ml") if path.is_file()):
        rel = path.relative_to(root).as_posix()
        for runs, matrix in _jobs(yaml.safe_load(path.read_text(encoding="utf-8"))):
            for script in (script for run in runs for script in _executed(run)):
                yield script, f"workflow {rel}"
            for environment in _tox_environments(runs, matrix) & tox.keys():
                for script in _executed(tox[environment]):
                    yield script, f"tox {environment} (run by {rel})"
    config = yaml.safe_load((root / HOOKS).read_text(encoding="utf-8"))
    for hook in (hook for repository in config["repos"] for hook in repository["hooks"]):
        for script in _executed(hook.get("entry", "")):
            yield script, f"hook {HOOKS}"


def _reads(model: SourceModule) -> Iterator[tuple[str, Scope, int]]:
    """Yield the dotted path, scope and node id of every name and attribute chain the module loads.

    An attribute of an object no import names (a fixture's method) yields its attribute name.
    """
    for reference in model.references:
        if reference.context == "load":
            yield model.origin(reference.node) or "", reference.scope, id(reference.node)
    for node in ast.walk(model.tree):
        if isinstance(node, ast.Attribute):
            yield model.origin(node) or node.attr, model.scope_of(node), id(node)


def _within(scope: Scope, outer: Scope) -> bool:
    """Return whether ``scope`` is ``outer`` or nested in it."""
    while scope is not outer and scope.parent is not None:
        scope = scope.parent
    return scope is outer


def _on_the_tree(call: ast.Call, root: Path) -> bool:
    """Return whether a ``main`` call passes literal arguments that name no other tree: no ``--root``, no path."""
    if call.keywords or len(call.args) > 1:
        return False
    if not call.args:
        return True
    argv = call.args[0]
    items = argv.elts if isinstance(argv, ast.List) else [argv]
    values = [item.value for item in items if isinstance(item, ast.Constant) and isinstance(item.value, str)]
    return (
        isinstance(argv, ast.List)
        and len(values) == len(items)
        and not any(value == "--root" or (root / value).exists() for value in values)
    )


def _tree_tests(root: Path) -> Iterator[tuple[str, str]]:
    """Yield ``(test node id, script)`` for each test that asserts the result of a check's ``main`` on the tree."""
    for path in sorted((root / "tests").rglob("test_*.py")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith("tests/data/") or not TEST_CALL.search(text := path.read_text(encoding="utf-8")):
            continue
        model = SourceModule(text, rel)
        reads = list(_reads(model))
        tests = [
            scope
            for scope in model.module_scope.children
            if scope.kind == "function" and scope.qualname.startswith("test_")
        ]
        decorators = {
            id(node): scope
            for scope in tests
            for decorator in scope.node.decorator_list
            for node in ast.walk(decorator)
        }
        module_skipped = any(
            name in SKIPS and scope is model.module_scope and key not in decorators for name, scope, key in reads
        )
        for test in [] if module_skipped else tests:
            parameters = {binding.name for binding in model.bindings(scope=test) if binding.kind == "param"}
            own = [name for name, scope, key in reads if decorators.get(key) is test or _within(scope, test)]
            if parameters - CAPTURES or any(name in SKIPS or name.endswith("chdir") for name in own):
                continue
            asserts = [
                node for node in ast.walk(test.node) if isinstance(node, ast.Assert) and model.scope_of(node) is test
            ]
            for call in (
                node for statement in asserts for node in ast.walk(statement.test) if isinstance(node, ast.Call)
            ):
                called = CHECK_MAIN.fullmatch(model.origin(call.func) or "")
                if called and _on_the_tree(call, root):
                    yield f"{rel}::{test.qualname}", f"scripts/{called[1]}.py"


def _reach(root: Path, rules: dict[str, tuple[str, ...]], ratchet: dict[str, str], unreached: dict[str, str]) -> str:
    """Describe what runs each check script of ``root`` and each rule of ``rules``, and what nothing runs."""
    reached: defaultdict[str, set[str]] = defaultdict(set)
    for script, channel in _channel_runs(root):
        reached[script].add(channel)
    for test, script in _tree_tests(root):
        reached[script].add(f"test {test}")
    if reached[RATCHET]:
        for entry, label in ratchet.items():
            reached[entry].add(label)
    scripts = sorted(path.relative_to(root).as_posix() for path in (root / "scripts").glob("check_*.py"))
    entries = [*scripts, *(f"{script}::{rule}" for script, names in sorted(rules.items()) for rule in names)]
    lines = []
    for entry in entries:
        if channels := sorted(reached[entry]):
            lines.append(f"{entry}: {', '.join(channels)}")
        elif entry in unreached:
            lines.append(f"{entry}: unreached ({unreached[entry]})")
        else:
            lines.append(f"{entry}: UNREACHED: run it on the tree, or list it in UNREACHED with the reason")
    lines += [
        f"{entry}: listed in UNREACHED, but it is reached or gone: remove the entry"
        for entry in sorted(unreached)
        if reached[entry] or entry not in entries
    ]
    return "\n".join(lines) + "\n"


@pytest.mark.allow_direct_assert("a reach gate fails on its own, never through a stored output a fix run accepts")
def test_every_check_runs_on_the_tree() -> None:
    """Each check script and each source rule is run on the repository tree, or listed with the reason it is not."""
    script = Path(inspect.getfile(check_source_rules)).resolve().relative_to(ROOT).as_posix()
    ratchet = {script: "ratchet"}
    ratchet |= {f"{script}::{rule.name}": "ratchet" for rule in check_ratchets.SOURCE_RULES}
    ratchet |= {f"{script}::{rule.name}": "ratchet (report only)" for rule in check_ratchets.REPORTED_RULES}
    rules = {script: tuple(rule.name for rule in check_source_rules.RULES)}

    report = _reach(ROOT, rules, ratchet, UNREACHED)
    gaps = [line for line in report.splitlines() if ": UNREACHED: " in line or ": listed in UNREACHED, but " in line]
    assert not gaps, "\n".join(gaps)
    assert_output(report)


FIXTURE = {
    ".github/workflows/ci.yaml": """
        on:
          push:
            paths:
              - 'scripts/check_pathonly.py'
        jobs:
          lint:
            steps:
              - run: python scripts/check_lint.py
              # - run: python scripts/check_commented.py
              - run: |
                  # python scripts/check_commented.py
                  echo see python scripts/check_echoed.py
                  pin="$(uv run --python 3.13 python scripts/check_ratchets.py --ruff-pin)"
                  .tox/bin/python -m scripts.check_module --strict && python -X dev scripts/check_flagged.py
              - uses: some/action@v1
                with:
                  script: python scripts/check_input.py
          matrix:
            strategy:
              matrix:
                include:
                  - tox_env: listed,other
            steps:
              - run: tox run -e "$TOX_ENV"
          loop:
            steps:
              - run: |
                  for tox_env in looped missing; do
                    if ! tox run -vv -e "$tox_env"; then
                      exit 1
                    fi
                  done
                  tox -e named -- --check
    """,
    ".github/actions/setup/action.yml": """
        runs:
          using: composite
          steps:
            - run: python scripts/check_action.py
    """,
    TOX: """
        [testenv:named]
        commands =
            python scripts/check_named.py
            {[testenv:shared]commands}
        [testenv:shared]
        description = runs scripts/check_described.py nowhere
        commands = python scripts/check_shared.py
        [testenv:listed]
        commands = python -m scripts.check_listed
        [testenv:looped]
        commands = python scripts/check_looped.py
        [testenv:unrun]
        commands = python scripts/check_unrun.py
    """,
    HOOKS: """
        repos:
        - repo: local
          hooks:
          - id: style
            entry: python scripts/check_style.py
            files: ^scripts/check_filesonly\\.py$
    """,
    "tests/test_tools.py": """
        import os

        import pytest

        from scripts import check_chdir, check_discarded, check_fixture, check_monkeypatched, check_nested
        from scripts import check_rooted, check_skipped, check_stored, check_tree, check_varied, check_xfailed
        import scripts.check_spelled

        def test_tree(capsys):
            assert check_tree.main(["--strict"]) == 0
            assert not scripts.check_spelled.main()

        def test_discarded():
            check_discarded.main([])

        def test_stored(capsys):
            assert_output(f"exit status {check_stored.main([])}")

        def test_fixture(tmp_path):
            assert check_fixture.main([]) == 0

        def test_monkeypatched(monkeypatch, fixture_repo):
            monkeypatch.chdir(fixture_repo)
            assert check_monkeypatched.main([]) == 0

        def test_chdir():
            os.chdir("elsewhere")
            assert check_chdir.main([]) == 0

        @pytest.mark.skip(reason="later")
        def test_skipped():
            assert check_skipped.main([]) == 0

        @pytest.mark.xfail
        def test_xfailed():
            assert check_xfailed.main([]) == 0

        def test_rooted():
            assert check_rooted.main(["--root", "elsewhere"]) == 0
            assert check_varied.main(["tests/data"]) == 0
            assert check_varied.main(ARGS) == 0
            assert check_varied.main([NAME]) == 0
            assert check_varied.main([1]) == 0
            assert check_varied.main([], verbose=True) == 0
            assert check_varied.main([], []) == 0

        def helper():
            assert check_varied.main([]) == 0

        def test_nested():
            def inner():
                assert check_nested.main([]) == 0
            return [check_nested.main([]) for _ in range(1)]
    """,
    "tests/test_skipped_module.py": """
        import pytest

        from scripts import check_module_skipped

        pytest.importorskip("missing")

        def test_module_skipped():
            assert check_module_skipped.main([]) == 0
    """,
    "tests/data/test_data.py": """
        from scripts import check_data

        def test_data():
            assert check_data.main([]) == 0
    """,
    "tests/test_unrelated.py": "def test_nothing():\n    assert main([]) == 0\n",
}
"""A repository whose checks are run by each channel, and checks that are only mentioned or run on fixtures only."""
FIXTURE_SCRIPTS = (
    "lint",
    "commented",
    "echoed",
    "pathonly",
    "input",
    "ratchets",
    "module",
    "flagged",
    "action",
    "listed",
    "looped",
    "named",
    "shared",
    "described",
    "unrun",
    "style",
    "filesonly",
    "tree",
    "spelled",
    "discarded",
    "stored",
    "fixture",
    "monkeypatched",
    "chdir",
    "skipped",
    "xfailed",
    "rooted",
    "varied",
    "nested",
    "module_skipped",
    "data",
    "rules",
    "unlisted",
)


def test_reach_report_names_every_channel_and_every_gap(tmp_path: Path) -> None:
    """Each run reaches its check; mentions and fixture runs reach nothing; stale entries are named.

    The ratchet's keys are reached only while a channel runs the ratchet.
    """
    for rel, text in {**FIXTURE, **{f"scripts/check_{name}.py": "" for name in FIXTURE_SCRIPTS}}.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(text).lstrip("\n"), encoding="utf-8")
    rules = {"scripts/check_rules.py": ("counted", "listed", "missing")}
    ratchet = {"scripts/check_rules.py": "ratchet", "scripts/check_rules.py::counted": "ratchet"}
    unreached = {
        "scripts/check_unlisted.py": "a reason",
        "scripts/check_rules.py::listed": "report only",
        "scripts/check_lint.py": "stale",
        "scripts/check_gone.py": "removed",
    }
    report = _reach(tmp_path, rules, ratchet, unreached)
    workflow = tmp_path / ".github/workflows/ci.yaml"
    workflow.write_text(workflow.read_text(encoding="utf-8").replace("check_ratchets", "ratchets"), encoding="utf-8")

    assert_output(f"{report}--- no workflow runs the ratchet\n{_reach(tmp_path, rules, ratchet, {})}")
