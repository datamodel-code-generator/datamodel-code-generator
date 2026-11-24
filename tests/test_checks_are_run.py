"""Every check script and every source rule runs on the repository tree, not on fixtures only.

A check is reached only where a command runs it on the tree with arguments that run it:

- a shell command of a workflow or action ``run:`` step (bash or sh, in the repository root) whose program is Python
  running ``scripts/check_x.py`` or ``-m scripts.check_x``, read with the shell's own word splitting (`shlex`): quoted
  text is one word, ``;``, ``&&``, ``||``, ``|`` and line ends outside quotes end a command, comments run nothing;
- a ``commands`` line of a tox environment that such a command runs (``tox run -e <env>`` without ``--notest``, the
  environment named literally, by the job's matrix or by the words of a ``for`` loop), read with ``configparser``;
- the ``entry`` of a pre-commit hook, with its ``args``;
- the ratchet, itself reached so, counting it;
- a test that asserts the result of its ``main`` directly (the call inside an ``assert``, so no stored output a fix
  run accepts stands in for it), with literal arguments, from a test function that takes no fixture but the output
  captures, changes no directory and is not skipped.

The arguments are read with the check's own parser (its ``main`` is stopped where it parses them): they run the check
when they set nothing, or when `TREE_RUNS` says how that check is run. Anything this test cannot tell, a mention (a
``paths:`` filter, an ``echo``, a hook ``files:`` pattern, a tox ``description``) and a run elsewhere (``--root``,
another directory, an option no rule names, such as ``--ruff-pin``) run nothing. The code-owned files are read, never
edited.
"""

from __future__ import annotations

import argparse
import ast
import configparser
import contextlib
import importlib.util
import inspect
import io
import re
import shlex
import sys
from collections import defaultdict
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING
from unittest import mock

import pytest
import yaml

from scripts import check_ratchets, check_source_rules
from scripts._source_model import SourceModule
from tests.conftest import assert_output

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from scripts._source_model import Scope

    RunRule = Callable[[dict[str, object]], bool]

ROOT = Path(__file__).resolve().parents[1]
RATCHET = "scripts/check_ratchets.py"
TOX = "tox.ini"
HOOKS = ".pre-commit-config.yaml"
SEPARATORS = frozenset(";&|\n")
AMBIGUOUS = re.compile(r"""(["'])[;&|]+\1|\\[;&|]|[^\s;&|]#""")
"""Text the lexer reads otherwise than the shell: a quoted or escaped separator, a ``#`` inside a word."""
SHELL_PREFIXES = frozenset({"if", "then", "else", "elif", "do", "while", "until", "!", "time", "exec"})
ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=")
UV_RUN_VALUES = frozenset({
    "--exclude-newer-package",
    "--python",
    "-p",
    "--with",
    "-w",
    "--project",
    "--directory",
    "--group",
    "--only-group",
    "--extra",
})
"""The options of ``uv run`` that take a value; a value-taking option not listed leaves the program unknown."""
PYTHON = re.compile(r"python(?:\d+(?:\.\d+)*)?")
PYTHON_OPTIONS = {"-I": 0, "-u": 0, "-B": 0, "-E": 0, "-s": 0, "-S": 0, "-O": 0, "-X": 1, "-W": 1}
"""The interpreter options before a script, with the number of values each takes."""
CHECK_SCRIPT = re.compile(r"(?:\./)?scripts/(check_\w+)\.py")
CHECK_MODULE = re.compile(r"scripts\.(check_\w+)")
TOX_RUNS = frozenset({"run", "r", "run-parallel", "p"})
EXPRESSION = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
MATRIX = re.compile(r"@matrix\.([\w-]+)@")
VARIABLE = re.compile(r"\$(?:(\w+)|\{(\w+)\})")
SUBSTITUTION = re.compile(r"\{\[testenv:([\w.-]+)\]commands\}")
POSARGS = re.compile(r"\{posargs(?::([^}]*))?\}")
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
TREE_RUNS: dict[str, RunRule] = {
    "scripts/check_ratchets.py": lambda args: (
        bool(args["base"]) and args["enforce"] is True and not args["ruff_pin"] and args["count"] is None
    ),
    "scripts/check_sdist_test_support.py": lambda args: args["dist_dir"] == Path("{env_tmp_dir}"),
}
"""How each check that needs arguments runs on the tree, from the values its parser reads.

The ratchet judges only with a base and ``--enforce``; the sdist check reads the packages its tox environment built
from the tree.
"""
UNREACHED: dict[str, str] = {}
"""Checks that nothing runs on the tree (``scripts/check_x.py`` or ``scripts/check_x.py::rule``), with the reason.

The ratchet counts the entries (``allowlist:UNREACHED``), so the table only shrinks.
"""


def _commands(text: str) -> list[list[str]]:
    """Return the words of each simple command of a shell text, split as the shell splits them; none where unsure."""
    if AMBIGUOUS.search(text):
        return []
    lexer = shlex.shlex(
        text.replace("\\\n", " ").replace("\n", "\n;"), posix=True, punctuation_chars="".join(SEPARATORS)
    )
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    commands: list[list[str]] = [[]]
    try:
        for token in lexer:
            if token and set(token) <= SEPARATORS:
                commands.append([])
            else:
                commands[-1].append(token)
    except ValueError:
        return []
    return [words for words in commands if words]


def _simple(words: list[str]) -> list[str]:
    """Return the words of a command from its first word that is neither a shell keyword nor an assignment."""
    index = 0
    while index < len(words) and (words[index] in SHELL_PREFIXES or ASSIGNMENT.match(words[index])):
        index += 1
    return words[index:]


def _program(words: list[str]) -> list[str]:
    """Return the words from the program a command runs, through ``uv run`` and ``tox exec``."""
    if words[:2] == ["uv", "run"]:
        rest = words[2:]
        while rest and rest[0].startswith("-") and rest[0] != "--":
            rest = rest[2:] if rest[0] in UV_RUN_VALUES else rest[1:]
        return rest[1:] if rest[:1] == ["--"] else rest
    if words[:1] == ["tox"] and words[1:2] in (["exec"], ["e"]) and "--" in words:
        return words[words.index("--") + 1 :]
    return words


def _check_call(words: list[str]) -> tuple[str, tuple[str, ...]] | None:
    """Return the check script a program's words run with Python and its arguments, or None."""
    if not words or not PYTHON.fullmatch(Path(words[0]).name):
        return None
    rest = words[1:]
    while rest and rest[0] in PYTHON_OPTIONS:
        rest = rest[1 + PYTHON_OPTIONS[rest[0]] :]
    if rest[:1] == ["-m"] and len(rest) > 1 and (found := CHECK_MODULE.fullmatch(rest[1])):
        return f"scripts/{found[1]}.py", tuple(rest[2:])
    if rest and (found := CHECK_SCRIPT.fullmatch(rest[0])):
        return f"scripts/{found[1]}.py", tuple(rest[1:])
    return None


def _names(value: str, loops: dict[str, list[str]], env: dict[str, str], matrix: dict[str, list[str]]) -> list[str]:
    """Return the tox environments a ``-e`` value names: literal, from a loop or variable, or the job's matrix."""
    if found := VARIABLE.fullmatch(value):
        name = found[1] or found[2]
        words = loops.get(name, [env[name]] if name in env else [])
        rest = {key: text for key, text in env.items() if key != name}
        return [environment for word in words for environment in _names(word, {}, rest, matrix)]
    if found := MATRIX.fullmatch(value):
        return [environment for item in matrix.get(found[1], []) for environment in item.split(",")]
    if any(mark in value for mark in "$@{"):
        return []
    return value.split(",")


def _tox_runs(words: list[str], resolve: Callable[[str], list[str]]) -> list[tuple[str, tuple[str, ...] | None]]:
    """Return the environments whose commands a tox command runs, each with the positional arguments it passes."""
    if Path(words[0]).name != "tox" or "--notest" in words:
        return []
    if len(words) > 1 and not words[1].startswith("-") and words[1] not in TOX_RUNS:
        return []
    options, posargs = words[1:], None
    if "--" in options:
        options, posargs = options[: options.index("--")], tuple(options[options.index("--") + 1 :])
    names = [name for index, word in enumerate(options[:-1]) if word == "-e" for name in resolve(options[index + 1])]
    return [(name, posargs) for name in names]


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


def _with_posargs(text: str, posargs: tuple[str, ...] | None) -> str:
    """Return tox commands with ``{posargs}`` replaced by the arguments a run passes, else by its default."""
    return POSARGS.sub(lambda found: shlex.join(posargs) if posargs is not None else found[1] or "", text)


def _shell_runs(
    text: str, env: dict[str, str], matrix: dict[str, list[str]], tox: dict[str, str]
) -> Iterator[tuple[str, tuple[str, ...], str]]:
    """Yield ``(script, arguments, tox environment or "")`` for each check a shell text runs, up to a ``cd``."""
    loops: dict[str, list[str]] = {}
    for words in (_simple(words) for words in _commands(text)):
        if words[:1] == ["cd"]:
            return
        if not words:
            continue
        if words[0] == "for" and words[2:3] == ["in"]:
            loops[words[1]] = words[3:]
            continue
        for name, posargs in _tox_runs(words, lambda value: _names(value, loops, env, matrix)):
            for line in _commands(_with_posargs(tox.get(name, ""), posargs)):
                if call := _check_call(_program(_simple(line))):
                    yield *call, name
        if call := _check_call(_program(words)):
            yield *call, ""


def _matrix(matrix: dict) -> dict[str, list[str]]:
    """Return the values of each key of a job's matrix, its ``include`` entries included."""
    values: defaultdict[str, list[str]] = defaultdict(list)
    for key, value in matrix.items():
        entries = value if key == "include" else [{key: item} for item in value] if isinstance(value, list) else []
        for entry in entries if key != "exclude" else []:
            for name, item in entry.items():
                values[name].append(str(item))
    return values


def _expressions(value: object) -> str:
    """Return a value with each ``${{ ... }}`` expression as a placeholder (``@matrix.tox_env@``)."""
    return EXPRESSION.sub(lambda found: f"@{found[1]}@", str(value))


def _steps(document: dict) -> Iterator[tuple[str, dict[str, str], dict[str, list[str]]]]:
    """Yield each ``run:`` text of a workflow or action with its environment and its job's matrix.

    A step that sets a working directory or a shell other than bash or sh is left out: where it runs is not told.
    """
    defaults = (document.get("defaults") or {}).get("run") or {}
    holders = [*(document.get("jobs") or {}).values(), *([document["runs"]] if "runs" in document else [])]
    for job in holders:
        matrix = _matrix((job.get("strategy") or {}).get("matrix") or {})
        for step in job.get("steps") or []:
            settings = {**defaults, **((job.get("defaults") or {}).get("run") or {}), **step}
            shell = str(settings.get("shell", "bash")).split()[0]
            if isinstance(step.get("run"), str) and shell in {"bash", "sh"} and "working-directory" not in settings:
                scopes = (document, job, step)
                env = {key: _expressions(text) for scope in scopes for key, text in (scope.get("env") or {}).items()}
                yield _expressions(step["run"]), env, matrix


def _channel_runs(root: Path) -> Iterator[tuple[str, tuple[str, ...], str]]:
    """Yield ``(script, arguments, channel)`` for each workflow command, tox command of a workflow and hook entry."""
    tox = _tox_commands(root / TOX)
    for path in sorted(path for path in (root / ".github").rglob("*.y*ml") if path.is_file()):
        rel = path.relative_to(root).as_posix()
        for text, env, matrix in _steps(yaml.safe_load(path.read_text(encoding="utf-8"))):
            for script, argv, environment in _shell_runs(text, env, matrix, tox):
                yield script, argv, f"tox {environment} (run by {rel})" if environment else f"workflow {rel}"
    config = yaml.safe_load((root / HOOKS).read_text(encoding="utf-8"))
    for hook in (hook for repository in config["repos"] for hook in repository["hooks"]):
        entry = _commands(hook.get("entry", ""))
        if len(entry) == 1 and (call := _check_call([*entry[0], *hook.get("args", [])])):
            yield *call, f"hook {HOOKS}"


class _ParsedError(Exception):
    """Ends a check's ``main`` where it parses its arguments, with the parser it built."""

    def __init__(self, parser: argparse.ArgumentParser) -> None:
        """Keep the parser."""
        super().__init__(parser.prog)
        self.parser = parser


def _stop(parser: argparse.ArgumentParser, *_arguments: object, **_keywords: object) -> argparse.Namespace:
    raise _ParsedError(parser)


def _parser(path: Path) -> argparse.ArgumentParser | None:
    """Return the parser a check script's ``main`` builds, stopped before it reads any argument; None without one."""
    name = f"_reached_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        with mock.patch.object(argparse.ArgumentParser, "parse_args", _stop):
            module.main()
    except _ParsedError as parsed:
        return parsed.parser
    finally:
        del sys.modules[name]
    return None


def _runs(parser: argparse.ArgumentParser, argv: tuple[str, ...], rule: RunRule) -> bool:
    """Return whether arguments run a check: its parser reads them as no arguments, or as the check's rule says."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            values = vars(parser.parse_args(list(argv)))
        except SystemExit:
            return False
        try:
            defaults = vars(parser.parse_args([]))
        except SystemExit:
            defaults = None
    return values == defaults or rule(values)


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


def _literal_argv(call: ast.Call) -> tuple[str, ...] | None:
    """Return the arguments of a ``main`` call: none, or one list of string literals; None for anything else."""
    if call.keywords or len(call.args) > 1:
        return None
    if not call.args:
        return ()
    argv = call.args[0]
    items = argv.elts if isinstance(argv, ast.List) else []
    values = tuple(item.value for item in items if isinstance(item, ast.Constant) and isinstance(item.value, str))
    return values if isinstance(argv, ast.List) and len(values) == len(items) else None


def _tree_tests(root: Path) -> Iterator[tuple[str, str, tuple[str, ...]]]:
    """Yield ``(test node id, script, arguments)`` for each test that asserts the result of a check's ``main``."""
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
                if called and (argv := _literal_argv(call)) is not None:
                    yield f"{rel}::{test.qualname}", f"scripts/{called[1]}.py", argv


def _reach(
    root: Path,
    rules: dict[str, tuple[str, ...]],
    ratchet: dict[str, str],
    runs: dict[str, RunRule],
    unreached: dict[str, str],
) -> str:
    """Describe what runs each check script of ``root`` and each rule of ``rules``, and what nothing runs."""
    parsers: dict[str, argparse.ArgumentParser | None] = {}

    def on_the_tree(script: str, argv: tuple[str, ...]) -> bool:
        if script not in parsers:
            parsers[script] = _parser(root / script) if (root / script).is_file() else None
        parser = parsers[script]
        return parser is not None and _runs(parser, argv, runs.get(script, lambda _: False))

    reached: defaultdict[str, set[str]] = defaultdict(set)
    for script, argv, channel in _channel_runs(root):
        if on_the_tree(script, argv):
            reached[script].add(channel)
    for test, script, argv in _tree_tests(root):
        if on_the_tree(script, argv):
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

    report = _reach(ROOT, rules, ratchet, TREE_RUNS, UNREACHED)
    gaps = [line for line in report.splitlines() if ": UNREACHED: " in line or ": listed in UNREACHED, but " in line]
    assert not gaps, "\n".join(gaps)
    assert_output(report)


PARSED_SCRIPT = """
    import argparse


    def main(argv=None):
        parser = argparse.ArgumentParser()
        parser.add_argument("--root")
        parser.add_argument("--strict", action="store_true")
        return parser.parse_args(argv)
"""
"""A check whose run on the tree passes no argument: ``--root`` names another tree, ``--strict`` no rule."""
FIXTURE_RATCHET = """
    import argparse


    def main(argv=None):
        parser = argparse.ArgumentParser()
        parser.add_argument("--base")
        parser.add_argument("--enforce", action="store_true")
        parser.add_argument("--count")
        parser.add_argument("--ruff")
        parser.add_argument("--ruff-pin", action="store_true")
        return parser.parse_args(argv)
"""
"""A ratchet with the options of the real one, which `TREE_RUNS` judges."""
FIXTURE_PLACED = """
    import argparse


    def main(argv=None):
        parser = argparse.ArgumentParser()
        parser.add_argument("target")
        return parser.parse_args(argv)
"""
"""A check that needs an argument, the build of the tree its fixture rule names."""
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
                  echo "quoted; python scripts/check_quoted.py"
                  true; python scripts/check_sequenced.py --root=elsewhere || python scripts/check_sequenced.py
                  pin="$(uv run --python 3.13 python scripts/check_ratchets.py --ruff-pin)"
                  uv run --no-project --python 3.13 python scripts/check_ratchets.py --ruff-pin
                  uv run --with "ruff==${pin}" -- \\
                    python scripts/check_ratchets.py --base "$BASE" --enforce
                  .tox/bin/python -m scripts.check_module && python -X dev scripts/check_flagged.py
                  python scripts/check_badargs.py --unknown
                  python scripts/check_placed.py elsewhere
                  python scripts/check_noparser.py
                  python -c "import scripts.check_inline" scripts/check_inline.py
                  python scripts/check_missing.py
                  cd docs
                  python scripts/check_moved.py
              - run: echo \\; python scripts/check_escaped.py
              - run: echo "open
              - run: python scripts/check_workdir.py
                working-directory: docs
              - run: python scripts/check_shell.py
                shell: python
              - uses: some/action@v1
                with:
                  script: python scripts/check_input.py
          matrix:
            env:
              TOX_ENV: ${{ matrix.tox_env }}
            strategy:
              matrix:
                os: [linux]
                python: ${{ fromJSON(inputs.pythons) }}
                include:
                  - tox_env: listed,other
                exclude:
                  - tox_env: excluded
            steps:
              - run: |
                  tox run -e "$TOX_ENV"
                  tox run -e "${{ github.event.inputs.env }}" -e "$UNSET"
          loop:
            steps:
              - run: |
                  # tox -e toxcommented
                  echo "tox -e toxechoed"
                  for tox_env in looped missing; do
                    if ! tox run -vv -e "$tox_env"; then
                      exit 1
                    fi
                  done
                  tox r --notest -e notested
                  tox list -e listing
                  tox -e named
                  tox run -e posarg -- --root elsewhere
                  tox run -e ignored,placed
                  tox exec -e named -- python scripts/check_exec.py
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
            python scripts/check_named.py {posargs}
            {[testenv:shared]commands}
        [testenv:shared]
        description = runs scripts/check_described.py nowhere
        commands = python scripts/check_shared.py
        [testenv:listed]
        commands = python -m scripts.check_listed
        [testenv:looped]
        commands = python scripts/check_looped.py
        [testenv:posarg]
        commands = python scripts/check_posarg.py {posargs:--strict}
        [testenv:ignored]
        commands = - python scripts/check_ignored.py
        [testenv:placed]
        commands = python scripts/check_placed.py {env_tmp_dir}
        [testenv:notested]
        commands = python scripts/check_notested.py
        [testenv:listing]
        commands = python scripts/check_listing.py
        [testenv:toxcommented]
        commands = python scripts/check_toxcommented.py
        [testenv:toxechoed]
        commands = python scripts/check_toxechoed.py
        [testenv:unrun]
        commands = python scripts/check_unrun.py
    """,
    HOOKS: """
        repos:
        - repo: https://github.com/astral-sh/ruff-pre-commit
          hooks:
          - id: ruff
        - repo: local
          hooks:
          - id: style
            entry: python scripts/check_style.py
            files: ^scripts/check_filesonly\\.py$
          - id: hookargs
            entry: python scripts/check_hookargs.py
            args: ["--root", "elsewhere"]
          - id: twice
            entry: python scripts/check_twice.py; python scripts/check_twice.py
    """,
    "tests/test_tools.py": """
        import os

        import pytest

        from scripts import check_chdir, check_discarded, check_fixture, check_monkeypatched, check_nested
        from scripts import check_rooted, check_skipped, check_stored, check_tree, check_varied, check_xfailed
        import scripts.check_spelled

        def test_tree(capsys):
            assert check_tree.main([]) == 0
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
            assert check_rooted.main(["--root=elsewhere"]) == 0
            assert check_rooted.main(["--ro=elsewhere"]) == 0
            assert check_varied.main(["--strict"]) == 0
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
"""A repository whose checks are run by each channel, and checks that are only mentioned or run elsewhere."""
FIXTURE_SCRIPTS = {
    **dict.fromkeys(
        (
            "action",
            "badargs",
            "chdir",
            "commented",
            "data",
            "described",
            "discarded",
            "echoed",
            "escaped",
            "exec",
            "filesonly",
            "fixture",
            "flagged",
            "hookargs",
            "ignored",
            "inline",
            "input",
            "lint",
            "listed",
            "listing",
            "looped",
            "module",
            "module_skipped",
            "monkeypatched",
            "moved",
            "named",
            "nested",
            "notested",
            "pathonly",
            "posarg",
            "quoted",
            "rooted",
            "sequenced",
            "shared",
            "shell",
            "skipped",
            "spelled",
            "stored",
            "style",
            "toxcommented",
            "toxechoed",
            "tree",
            "twice",
            "unlisted",
            "unrun",
            "varied",
            "workdir",
            "xfailed",
        ),
        PARSED_SCRIPT,
    ),
    "ratchets": FIXTURE_RATCHET,
    "placed": FIXTURE_PLACED,
    "noparser": "def main(argv=None):\n    return 0\n",
    "rules": "",
}
"""The fixture's check scripts: each parses its arguments, except one that reads none and the counted rules."""
FIXTURE_RUNS: dict[str, RunRule] = {
    **TREE_RUNS,
    "scripts/check_placed.py": lambda args: args["target"] == "{env_tmp_dir}",
}


def test_reach_report_names_every_channel_and_every_gap(tmp_path: Path) -> None:
    """Each run reaches its check; mentions, runs with other arguments and fixture runs reach nothing.

    Stale entries are named, and the ratchet's keys are reached only while a command runs the ratchet on the tree: a
    ``--ruff-pin`` lookup is no such run.
    """
    scripts = {f"scripts/check_{name}.py": text for name, text in FIXTURE_SCRIPTS.items()}
    for rel, text in {**FIXTURE, **scripts}.items():
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
    report = _reach(tmp_path, rules, ratchet, FIXTURE_RUNS, unreached)
    workflow = tmp_path / ".github/workflows/ci.yaml"
    text = workflow.read_text(encoding="utf-8").replace(
        "scripts/check_ratchets.py --base", "scripts/ratchets.py --base"
    )
    workflow.write_text(text, encoding="utf-8")

    assert_output(
        f"{report}--- only the pin lookup runs the ratchet\n{_reach(tmp_path, rules, ratchet, FIXTURE_RUNS, {})}"
    )
