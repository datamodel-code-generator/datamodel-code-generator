"""Read the lanes of the CI test matrix and the environment facts each lane runs the tests with.

A lane is one tox environment of a job matrix of a workflow in ``.github/workflows``: of an ``include`` entry (an
entry that lists several, comma-separated, runs each) or of a ``tox_env`` axis, on the entry's ``os`` or the job's
``runs-on``; shards of one environment are one lane, and the same environment on the same runner in two workflows
too. A job that names its tox environment in a step, outside a matrix, runs no lane. Its facts are those of
`tests/units.py` (`FACTS`), read without running tox:

- ``python``: the environment's ``py3NN`` factor, else its ``base_python``;
- ``platform``: the runner (``ubuntu-*`` is ``linux``, ``macos-*`` ``darwin``, ``windows-*`` ``win32``);
- ``formatter``: ``builtin`` where its ``set_env`` sets `FORMATTER_ENV` to ``builtin``, else ``external``;
- ``black``, ``isort``, ``pydantic``, ``heki``: a ``deps`` pin (``pydantic==2.0.3``), else the requirement of a
  dependency group the environment installs (``black==22.1``, ``isort>=6,<7``, ``heki==0.2.4``), resolved to the
  newest version of ``uv.lock`` that satisfies it; with neither, the newest locked version where the package requires
  the tool (``[project].dependencies``, unless the environment skips installing the package), else empty: a tool only
  a group installs (heki) is not installed in an environment without that group.

tox settings are read as tox reads them for one environment: the section whose name expands (``{a,b}``) to it, else
``[testenv]``; ``{[section]key}`` substitutions; and factor-conditional lines (``py312-!nocov: value``). Environment
markers of requirements are not read, and an environment that tox installs without the lock (``uv-venv-runner``)
gets the locked version of an unpinned tool, though it may resolve a newer one. A lane is a subset lane (a fixed
list of tests, not the suite) when each of its entries lists test paths in its job or its tox environment runs no
``tests`` target; those paths are the tests it selects, and the ``exclude_tests`` every entry of it names are the
tests it leaves out (`Lane.runs`). A lane whose Python or platform cannot
be read is a problem, never a lane with guessed facts. The workflows are read line by line (the checks that read them
run without a YAML parser); `tests/test_source_rules.py` compares the entries with a YAML parse of the real ones.
"""

from __future__ import annotations

import configparser
import itertools
import operator
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.units import ORDERS, release

WORKFLOWS = ".github/workflows"
WORKFLOW = f"{WORKFLOWS}/test.yaml"
TOX = "tox.ini"
PYPROJECT = "pyproject.toml"
LOCK = "uv.lock"
FORMATTER_ENV = "DATAMODEL_CODE_GENERATOR_TEST_DEFAULT_FORMATTER"
"""The variable that makes the tests use the built-in formatter (``tests.capabilities.TEST_DEFAULT_FORMATTER_ENV``)."""
RUNNERS = (("ubuntu", "linux"), ("macos", "darwin"), ("windows", "win32"))
_JOB = re.compile(r"^  (?P<name>[A-Za-z0-9_-]+):\s*$")
_RUNS_ON = re.compile(r"^    runs-on:\s*(?P<value>.+?)\s*$")
_MATRIX = re.compile(r"^(?P<indent>\s+)matrix:\s*$")
_INCLUDE = re.compile(r"^(?P<indent>\s+)include:\s*$")
_AXIS = re.compile(r"^(?P<indent>\s+)tox_env:\s*$")
_ITEM = re.compile(r"^(?P<indent>\s+)- (?P<value>[^\s:]+)\s*$")
_ENTRY = re.compile(r"^(?P<indent>\s+)- (?P<key>[A-Za-z_]+):\s*(?P<value>.*?)\s*$")
_KEY = re.compile(r"^(?P<indent>\s+)(?P<key>[A-Za-z_]+):\s*(?P<value>.*?)\s*$")
_TEST_PATH = re.compile(r"^\s+tests/\S*(?: \\)?$")
_BRACES = re.compile(r"\{([^{}]*)\}")
_SUBSTITUTION = re.compile(r"\{\[(?P<section>[^\]]+)\](?P<key>[\w-]+)\}")
_CONDITIONAL = re.compile(r"^(?P<factors>[\w!-]+(?:,\s*[\w!-]+)*):\s*(?P<value>.*)$")
_PY_FACTOR = re.compile(r"^py3(?P<minor>\d+)$")
_BASE_PYTHON = re.compile(r"^python3\.(?P<minor>\d+)$")
_REQUIREMENT = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)(?:\[[^\]]*\])?\s*(?P<spec>[^;]*)")
_SPEC = re.compile(r"(?P<op>==|!=|>=|<=|<|>|~=)\s*(?P<version>[\w.*+-]+)")


@dataclass(frozen=True)
class Lane:
    """One tox environment of the matrix on one runner, and the facts its tests run with."""

    name: str
    facts: dict[str, str]
    tests: tuple[str, ...] | None = None
    """The test paths the lane selects (``tests/x.py``, ``tests/x.py::test_y``, a directory); None for the suite."""
    excluded: tuple[str, ...] = ()
    """The test paths the lane leaves out of what it selects."""
    entries: tuple[tuple[tuple[str, ...] | None, tuple[str, ...]], ...] = ()
    """The (selection, exclusions) pair of each workflow entry that runs the lane; empty for a lane of one entry."""

    @property
    def subset(self) -> bool:
        """The lane runs a fixed list of tests, not the suite: a skip no listed test reaches never fires there."""
        return self.tests is not None

    def runs(self, node: str) -> bool:
        """Return whether some entry of the lane runs a test at or below a node (``tests/x.py::test_y``, a module).

        An entry runs it where it selects the node, something that holds it or something below it (one parameter of
        the test), and leaves out nothing that holds the deeper of the two: leaving out one parameter still runs the
        others, and leaving out the one parameter it selects runs nothing.
        """
        return any(
            any(
                not any(_within(deeper, path) for path in excluded)
                for deeper in ((node,) if tests is None else _deeper(node, tests))
            )
            for tests, excluded in self._entries
        )

    @property
    def _entries(self) -> tuple[tuple[tuple[str, ...] | None, tuple[str, ...]], ...]:
        return self.entries or ((self.tests, self.excluded),)

    def joined(self, other: Lane) -> Lane:
        """Return the lane as two entries of its environment run it: each keeps its own selection and exclusions."""
        tests = None if self.tests is None or other.tests is None else tuple(sorted({*self.tests, *other.tests}))
        return replace(
            self,
            tests=tests,
            excluded=tuple(sorted(set(self.excluded) & set(other.excluded))),
            entries=(*self._entries, *other._entries),
        )


def _deeper(node: str, tests: tuple[str, ...]) -> tuple[str, ...]:
    """Return, for each selected path that holds a node or lies below it, the deeper of the two."""
    return tuple(node if _within(node, path) else path for path in tests if _within(node, path) or _within(path, node))


def _within(node: str, path: str) -> bool:
    """Return whether a test path holds a node: the node itself, its directory, module, class or test of a parameter."""
    return node == path or node.startswith((f"{path}/", f"{path}::", f"{path}["))


def _value(text: str) -> str:
    """Return a scalar of the workflow without its quotes and trailing comment."""
    text = text.split(" #", 1)[0].strip()
    return text[1:-1] if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'" else text


def matrix_entries(text: str) -> list[dict[str, str]]:
    """Return every entry of every job matrix of a workflow, with its ``job`` and the job's ``runs-on``.

    An entry is an ``include`` entry, or one item of a ``tox_env`` axis (as ``{"tox_env": item}``). ``selection`` is
    ``listed`` for a job whose steps list test paths, one per line (a fixed list of tests), else ``all``; ``tests``
    holds those paths, separated by spaces.
    """
    found: list[dict[str, str]] = []
    runs_on: dict[str, str] = {}
    listed: dict[str, list[str]] = {}
    job, matrix, include, axis, entry, entry_indent = "", -1, -1, -1, None, -1
    for line in text.splitlines():
        indent = len(line) - len(line.lstrip())
        if _TEST_PATH.match(line):
            listed.setdefault(job, []).append(line.strip().removesuffix("\\").strip())
        if match := _JOB.match(line):
            job, matrix, include, axis, entry = match["name"], -1, -1, -1, None
        elif match := _RUNS_ON.match(line):
            runs_on[job] = _value(match["value"])
        elif _MATRIX.match(line):
            matrix = indent
        elif match := _INCLUDE.match(line):
            include = indent
        elif matrix >= 0 and _AXIS.match(line) and indent == matrix + 2:
            axis = indent
        elif axis >= 0 and (match := _ITEM.match(line)) and indent > axis:
            found.append({"job": job, "tox_env": _value(match["value"])})
        elif include >= 0 and (match := _ENTRY.match(line)) and indent > include:
            entry, entry_indent = {"job": job, match["key"]: _value(match["value"])}, indent
            found.append(entry)
        elif entry is not None and (match := _KEY.match(line)) and indent == entry_indent + 2:
            entry[match["key"]] = _value(match["value"])
        elif line.strip() and not line.lstrip().startswith("#") and indent <= max(include, axis):
            include, axis, entry = -1, -1, None
    return [
        {
            **entry,
            "runs-on": runs_on.get(entry["job"], ""),
            "selection": "listed" if entry["job"] in listed else "all",
            "tests": " ".join(listed.get(entry["job"], [])),
        }
        for entry in found
    ]


def _expand(pattern: str) -> list[str]:
    """Return the names a tox section name stands for: every combination of its ``{a,b}`` groups."""
    groups = [[item.strip() for item in group.split(",")] for group in _BRACES.findall(pattern)]
    pieces = _BRACES.split(pattern)[::2]
    return [
        "".join(piece + choice for piece, choice in zip(pieces, (*combination, ""), strict=True))
        for combination in itertools.product(*groups)
    ]


class ToxConfig:
    """The settings of ``tox.ini`` as tox reads them for one environment."""

    def __init__(self, text: str) -> None:
        """Parse the file and index the environment sections by the names they expand to."""
        self.parser = configparser.RawConfigParser(delimiters=("=",), strict=False, interpolation=None)
        self.parser.optionxform = lambda optionstr: optionstr  # tox keeps the case of keys
        self.parser.read_string(text)
        self.sections = {
            name: section
            for section in self.parser.sections()
            if section.startswith("testenv:")
            for name in _expand(section.removeprefix("testenv:"))
        }

    def _raw(self, section: str, key: str) -> str:
        if self.parser.has_option(section, key):
            return self.parser.get(section, key)
        return self.parser.get("testenv", key) if self.parser.has_option("testenv", key) else ""

    def lines(self, env: str, key: str) -> list[str]:
        """Return the lines of a setting for ``env``: substitutions made, lines of factors it lacks dropped."""
        factors = set(env.split("-"))
        text = self._raw(self.sections.get(env, "testenv"), key)
        for _ in range(10):
            text = _SUBSTITUTION.sub(lambda match: self._raw(match["section"], match["key"]), text)
        found = []
        for line in (line.strip() for line in text.splitlines()):
            value = line
            if match := _CONDITIONAL.match(line):
                alternatives = [item.strip().split("-") for item in match["factors"].split(",")]
                if not any(
                    all((part[1:] not in factors) if part.startswith("!") else (part in factors) for part in parts)
                    for parts in alternatives
                ):
                    continue
                value = match["value"].strip()
            if value:
                found.append(value)
        return found


_COMPARISONS = {**ORDERS, "==": operator.eq, "!=": operator.ne, "~=": operator.ge}


def _satisfies(version: str, spec: str) -> bool:
    """Return whether a version satisfies every clause of a requirement's specifier (releases compared, padded)."""
    for match in _SPEC.finditer(spec):
        left, right = release(version), release(match["version"])
        width = max(len(left), len(right))
        if not _COMPARISONS[match["op"]](left + (0,) * (width - len(left)), right + (0,) * (width - len(right))):
            return False
    return True


def _is_suite_target(word: str) -> bool:
    """Return whether a command word is the ``tests`` target, bare or as tox's ``{posargs:tests}`` default."""
    return word.rstrip("}") in {"tests", "{posargs:tests"}


class Environments:
    """The facts of the tox environments of a checkout, from ``tox.ini``, ``pyproject.toml`` and ``uv.lock``."""

    def __init__(self, root: Path) -> None:
        """Read the three files; a missing one reads as empty."""
        tox = root / TOX
        self.tox = ToxConfig(tox.read_text(encoding="utf-8") if tox.is_file() else "")
        pyproject = root / PYPROJECT
        project = tomllib.loads(pyproject.read_text(encoding="utf-8")) if pyproject.is_file() else {}
        self.groups = project.get("dependency-groups", {})
        self.package_requires = {
            match["name"].lower()
            for requirement in project.get("project", {}).get("dependencies", [])
            if (match := _REQUIREMENT.match(requirement))
        }
        """The names of the tools the package requires, which every environment that installs it has."""
        lock = root / LOCK
        packages = tomllib.loads(lock.read_text(encoding="utf-8")).get("package", []) if lock.is_file() else []
        self.locked: dict[str, list[str]] = {}
        for package in packages:
            if "version" in package:
                self.locked.setdefault(package["name"], []).append(package["version"])

    def _group_requirements(self, group: str, seen: frozenset[str] = frozenset()) -> list[str]:
        found = []
        for item in self.groups.get(group, []):
            if isinstance(item, str):
                found.append(item)
            elif isinstance(item, dict) and (included := item.get("include-group")) and included not in seen:
                found += self._group_requirements(included, seen | {group})
        return found

    def _tool(self, env: str, tool: str) -> str:
        requirements = [
            requirement
            for group in self.tox.lines(env, "dependency_groups")
            for requirement in self._group_requirements(group)
        ]
        requirements += self.tox.lines(env, "deps")
        specs = [
            match["spec"].strip()
            for requirement in requirements
            if (match := _REQUIREMENT.match(requirement)) and match["name"].lower() == tool
        ]
        if not specs and (tool not in self.package_requires or self.tox.lines(env, "skip_install") == ["true"]):
            return ""
        spec = specs[-1] if specs else ""
        if (pin := re.fullmatch(r"==\s*([\w.+-]+)", spec)) is not None:
            return pin[1]
        candidates = [version for version in self.locked.get(tool, []) if _satisfies(version, spec)]
        return max(candidates, key=release, default="")

    def runs_suite(self, env: str) -> bool:
        """Return whether ``env`` runs the whole suite: no commands is read as the default, else a ``tests`` target."""
        lines = self.tox.lines(env, "commands")
        return not lines or any(_is_suite_target(word) for line in lines for word in line.split())

    def tests(self, env: str) -> tuple[str, ...] | None:
        """Return the test paths ``env`` selects: None for the suite, else the ``tests/`` words of its commands."""
        if self.runs_suite(env):
            return None
        words = (word.strip("\"'") for line in self.tox.lines(env, "commands") for word in line.split())
        return tuple(word for word in words if word.startswith("tests/"))

    def facts(self, env: str, runner: str) -> dict[str, str]:
        """Return the facts of ``env`` on ``runner``; a fact that cannot be read is empty."""
        python = next((f"3.{match['minor']}" for part in env.split("-") if (match := _PY_FACTOR.match(part))), "")
        if not python:
            python = next(
                (
                    f"3.{match['minor']}"
                    for line in self.tox.lines(env, "base_python")
                    if (match := _BASE_PYTHON.match(line))
                ),
                "",
            )
        builtin = any(re.fullmatch(rf"{FORMATTER_ENV}\s*=\s*builtin", line) for line in self.tox.lines(env, "set_env"))
        return {
            "python": python,
            "formatter": "builtin" if builtin else "external",
            "black": self._tool(env, "black"),
            "isort": self._tool(env, "isort"),
            "platform": next((platform for prefix, platform in RUNNERS if runner.startswith(prefix)), ""),
            "pydantic": self._tool(env, "pydantic"),
            "heki": self._tool(env, "heki"),
        }


def read_lanes(root: Path) -> tuple[list[Lane], list[str]]:
    """Return the lanes of the workflows of the checkout at ``root`` and the lanes whose facts cannot be read.

    A checkout without workflows has no lanes.
    """
    directory = root / WORKFLOWS
    workflows = sorted(directory.glob("*.y*ml")) if directory.is_dir() else []
    environments = Environments(root)
    lanes: dict[str, Lane] = {}
    problems: list[str] = []
    for entry in (entry for workflow in workflows for entry in matrix_entries(workflow.read_text(encoding="utf-8"))):
        runner = entry.get("os") or entry.get("runs-on", "")
        for env in filter(None, (item.strip() for item in entry.get("tox_env", "").split(","))):
            name = f"{env} on {runner}"
            tests = tuple(entry["tests"].split()) if entry["selection"] == "listed" else environments.tests(env)
            excluded = tuple(entry.get("exclude_tests", "").split())
            if name in lanes:
                lanes[name] = lanes[name].joined(Lane(name, lanes[name].facts, tests, excluded))
                continue
            facts = environments.facts(env, runner)
            lanes[name] = Lane(name, facts, tests, excluded)
            problems += [f"{name}: no {fact} can be read" for fact in ("python", "platform") if not facts[fact]]
    return list(lanes.values()), sorted(set(problems))
