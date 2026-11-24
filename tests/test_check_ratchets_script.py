"""Count the escape hatches of temporary git repositories against their base commit through the script's CLI."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import check_ratchets
from tests.conftest import assert_output
from tests.units import RefLine, derived_file_name, format_refs

ROOT = Path(__file__).resolve().parents[1]
GIT = ["git", "-c", "user.name=CI", "-c", "user.email=ci@example.com", "-c", "commit.gpgsign=false"]
PYPROJECT = """[tool.ruff]
extend-exclude = ["scripts"]
lint.select = ["ALL"]
lint.ignore = ["CPY", "D", "INP"]
lint.preview = true
lint.per-file-ignores."tests/**/*.py" = ["S101"]
"""
SIZE = """

def size() -> int:
    import json  # noqa: PLC0415

    return len(json.dumps(1))
"""
NAME = """

def name() -> str:
    return "a"
"""
FUTURE = "from __future__ import annotations\n"
BASE = {
    "pyproject.toml": PYPROJECT + '\n[tool.ty]\nrules.deprecated = "error"\n',
    ".pre-commit-config.yaml": """repos:
- repo: https://github.com/astral-sh/ruff-pre-commit
  rev: 0123456789abcdef0123456789abcdef01234567  # frozen: v0.0.0
  hooks:
  - id: ruff-format
  - id: ruff
    exclude: "^src/pkg/generated/"
    args: ["--preview", "--fix", "--extend-ignore", "ERA001"]
""",
    "src/pkg/__init__.py": "",
    "src/pkg/a.py": FUTURE + SIZE + NAME,
    "src/pkg/generated/out.py": "import os  # noqa: F401\n",
    "src/pkg/broken.py": 'value = cast("int", (\n',
    "tests/test_a.py": FUTURE
    + """
import warnings


def test_size() -> None:
    warnings.simplefilter("error")
    assert getattr(warnings, "filters") is not None
""",
}
OTHER_KINDS = {
    "pyproject.toml": PYPROJECT
    + """
[tool.coverage]
run.omit = ["src/pkg/d.py"]
report.exclude_also = ["if TYPE_CHECKING:"]

[tool.ty]
rules.deprecated = "warn"

[[tool.ty.overrides]]
include = ["src"]
rules.unresolved-import = "ignore"

[tool.pytest]
ini_options.filterwarnings = ["error", "ignore::DeprecationWarning"]
""",
    "src/pkg/d.py": FUTURE
    + """
import importlib
import warnings
from typing import cast


def risky(value: object) -> int:  # pragma: no cover
    if value:  # pragma: no branch
        warnings.filterwarnings("ignore")
    warnings.filterwarnings(action="ignore", category=DeprecationWarning)
    module = importlib.import_module("json")  # ty: ignore[unresolved-import]
    secret = getattr(module, "_default_encoder", None)  # pyright: ignore[reportPrivateUsage]
    return cast("int", secret)  # type: ignore[return-value]  # pragma: win32 cover


def silenced(items: list[int]) -> None:
    items.insert(0, 1)  # heki: ignore
    items.pop(0)  # heki: ignore[OPT103, list-front-pop]
    items.sort()  # noqa: OPT110, E501
    items.reverse()  # noqa: write-owner-violation
    items.clear()  # noqa: PLC0415 - MUT201
    items.append(1)  # noqa
    items.append(2)  # heki: ignore[MUT201
    items.append(3)  # heki: ignore[]
""",
    "tests/test_d.py": FUTURE
    + """
import pytest


@pytest.mark.abnormal_path("reason")
def test_d() -> None:
    pytest.importorskip("json")
""",
    "tests/assert_helper_allowlists.py": FUTURE
    + """
from pathlib import Path

JUSTIFIED_VIOLATIONS: dict[str, tuple[str, ...]] = {"group": ("rule:tests/test_d.py",)}
FROZEN_EXEMPT_FILES = frozenset({Path("test_d.py")})
""",
    "scripts/check_architecture_boundaries.py": 'DEFAULT_ALLOWLIST = {("a", "b"): Entry("reason")}\n',
}
HEKI_BASELINE = """version = 2

[[entries]]
path = "src/pkg/a.py"
qualname = "run"
rule_code = "MUT201"
target = "Model.fields"
count = 1
"""
HEKI_ENVIRONMENT = """
[[entries]]
path = "src/pkg/a.py"
qualname = "<module>"
rule_code = "EFF201"
target = "no-environment-reads:reads-environment"
count = 1
"""
"""A baseline entry of the environment boundary, whose sites count under their own key."""
ALLOWLIST = 'FROZEN_EXEMPT_FILES = frozenset({"a.py"})\n'
HOOK_START = """repos:
- repo: https://github.com/astral-sh/ruff-pre-commit
  rev: v0.0.0
  hooks:
  - id: ruff
"""
PRAGMA_FILE = FUTURE + NAME.replace("-> str:", "-> str:  # pragma: no cover")
ANY_USES = (
    FUTURE
    + """
import typing
from typing import Any, cast


def keep(value: Any, other: "dict[str, Any]", odd: "a b") -> typing.Any:
    local: list[Any] = [value]
    return cast("dict[str, Any]", cast(typing.Any, local)), other, odd
"""
)
ANY_VALUES = (
    FUTURE
    + """
from typing import Annotated, Literal, cast


def keep(value: Literal["Any"], other: Annotated[int, "Any"], third: Annotated["Any", "Any"]) -> "Literal['Any']":
    return cast("Literal['Any'] | Annotated[int, 'Any']", value), other, third
"""
)
LOCAL_IMPORT = (
    FUTURE
    + """

def show() -> None:
    import json

    json.dumps(1)
"""
)
DODGES = (
    FUTURE
    + """
import operator
import warnings
from typing import cast as narrow
from typing import no_type_check


@no_type_check
def hidden(value: object, name: str) -> object:
    with warnings.catch_warnings(action="ignore"):
        return narrow("int", operator.attrgetter("_x")(value)), object.__getattribute__(value, f"_{name}")
"""
)
STORED_REFS, STORED_TEST = check_ratchets.STORED_LISTS[0]
STORED_NAME = STORED_TEST.removeprefix("test_")
SEED, NO_SEED = check_ratchets.REJECTED_SEED_LINE, check_ratchets.NO_SEED_LINE
GAP = "/k const {}: invalid, model accepts"
LANES = {
    ".github/workflows/test.yaml": (
        "jobs:\n  test:\n    runs-on: ubuntu-24.04\n    strategy:\n      matrix:\n        include:\n"
        "          - tox_env: py312\n          - tox_env: py313\n            os: windows-latest\n"
        f"            exclude_tests: {STORED_REFS.removesuffix('.refs')}.py\n"
    ),
    ".github/workflows/nightly.yaml": (
        "jobs:\n  payload:\n    runs-on: ubuntu-24.04\n    strategy:\n      matrix:\n        tox_env:\n"
        "          - pydantic20\n"
    ),
    "tox.ini": "[testenv:pydantic20]\nbase_python = python3.11\ndeps =\n    pydantic==2\n",
}
"""The workflow matrices that judge the stored lists: Python 3.12 without Pydantic, Python 3.11 pinning ``pydantic==2``
(a nightly ``tox_env`` axis), and Windows, which leaves the stored lists' module out."""


ITEM_EXCLUDED = {
    **LANES,
    ".github/workflows/test.yaml": LANES[".github/workflows/test.yaml"].replace(
        f"{STORED_REFS.removesuffix('.refs')}.py\n", f"{STORED_REFS.removesuffix('.refs')}.py::{STORED_TEST}[unread]\n"
    ),
}
"""`LANES` with Windows leaving out only the item ``unread``: it still runs the test's other items."""


def _stored_lists(lists: dict[str, object]) -> dict[str, str]:
    """Return a ``.refs`` file whose test stores one list per item, and the lists; a string names another's list.

    A key ``<item>@<predicate>`` is the item's override for the environments of the predicate, and ``<item>#<name>``
    another output the test stores for the item under the role ``<name>``. The tree holds the lanes of `LANES`.
    """
    records, files = [], {}
    for key, value in lists.items():
        key_item, _, other = key.partition("#")
        item, _, predicate = key_item.partition("@")
        label = f"lists/{STORED_NAME}[{item}]{f'.{other}' if other else ''}.txt"
        storage = (
            f"lists/{STORED_NAME}[{value}].txt"
            if isinstance(value, str)
            else label.replace(item, re.sub(r"[<>|]", "-", key_item), 1)
        )
        environment = f"\t@{predicate}" if predicate else ""
        shared = f"\t= {storage}" if storage != label else ""
        records.append(f"{item}\t{label}{environment}{shared}\n")
        if not isinstance(value, str):
            files[f"{check_ratchets.EXPECTED_ROOT}/{storage}"] = json.dumps(value)
    refs = f"# Generated by pytest.\n[test_other]\nx\tlists/x.txt\n[{STORED_TEST}]\n{''.join(records)}"
    return {STORED_REFS: refs, **files, **LANES}


STORED_BASE = {"one": [GAP.format(1), GAP.format(2)], "two": "empty", "empty": [], "three": [SEED]}
STORED_LANES = {
    "relabel": [GAP.format(1)],
    "relabel@pydantic=2.0": [GAP.format(1), GAP.format(2)],
    "respelled": [GAP.format(3)],
    "respelled@python=3.11": [GAP.format(3), GAP.format(4)],
    "unread": [GAP.format(5)],
    "unread@platform==win32": [GAP.format(5)],
}
"""Overrides in the pairs sidecars wrote before condition clauses: the Pydantic 2.0 lane and the Python 3.11 lane."""
STORED_RELABELED = {
    "relabel": [GAP.format(1)],
    "relabel@pydantic<2.5": [GAP.format(1), GAP.format(2)],
    "respelled": [GAP.format(3)],
    "respelled@python==3.11": [GAP.format(3), GAP.format(4)],
    "unread": [GAP.format(5)],
    "unread@platform==win32": [GAP.format(5), GAP.format(6)],
}
"""`STORED_LANES` in condition clauses, each lane reading the lines it read; only a record no lane reads gains one."""
SCRIPT = (ROOT / "scripts/check_ratchets.py").read_text(encoding="utf-8")
COUNTER = {
    "scripts/__init__.py": "",
    "scripts/check_ratchets.py": SCRIPT,
    "scripts/_source_model.py": (ROOT / "scripts/_source_model.py").read_text(encoding="utf-8"),
    "scripts/_import_cycles.py": (ROOT / "scripts/_import_cycles.py").read_text(encoding="utf-8"),
    "scripts/check_source_rules.py": (ROOT / "scripts/check_source_rules.py").read_text(encoding="utf-8"),
    "scripts/_ci_lanes.py": (ROOT / "scripts/_ci_lanes.py").read_text(encoding="utf-8"),
    "tests/units.py": (ROOT / "tests/units.py").read_text(encoding="utf-8"),
}
"""The counter and the modules it reads Python, cycles, source rules, lanes and ``.refs`` files with, for a copy."""
IMPORT_ONE_WAY = {
    "scripts/report.py": FUTURE + "\nfrom scripts import guide\n\nTITLE = guide.TITLE\n",
    "scripts/guide.py": FUTURE + '\nTITLE = "guide"\n',
}
"""Imports one way in ``scripts``: no import cycle."""
IMPORT_BACK = {
    "scripts/guide.py": FUTURE + '\nfrom scripts import report\n\nTITLE = "guide"\nLOAD = report\n',
}
"""An import back at module level in ``scripts``: every key rises."""
IMPORT_IGNORED = {
    ".gitignore": "scripts/local_*.py\n",
    "scripts/local_a.py": FUTURE + "\nfrom scripts import local_b\n\nVALUE = local_b.VALUE\n",
    "scripts/local_b.py": FUTURE + "\nfrom scripts import local_a\n\nVALUE = 1\n",
}
"""Two ignored files of ``scripts`` import each other: the ratchet reads the tree's files, so no cycle is counted."""


def _refs(*lines: tuple[str, str, str, str]) -> str:
    """Return a ``.refs`` file with the (function, parameter id, label, predicate) lines, as pytest writes it."""
    return format_refs(
        RefLine(function, param, label, predicate=(f"python=={predicate}",) if predicate else ())
        for function, param, label, predicate in lines
    )


A_MODULE = (
    FUTURE
    + """

def test_size() -> None: ...


def test_a_size() -> None: ...


def test_a_other() -> None: ...


def test_p(one) -> None: ...


def test_window(one) -> None: ...


class TestCase:
    def test_q(self) -> None: ...


class Helper:
    def test_not_collected(self) -> None: ...
"""
)
"""The tests of ``tests/test_a.py``: ``test_a_size`` keeps its module prefix because ``test_size`` exists."""
WINDOW = "p" * 111
"""An id with which ``window[<id>]`` is 119 characters: any role and suffix push the name over 120."""
SHORT_WINDOW = WINDOW[:-3]
"""An id with which ``window[<id>]`` plus ``.txt`` is exactly 120 characters; ``.warnings.txt`` pushes it over."""


def _window(param: str, role: str = "", suffix: str = ".txt") -> tuple[str, str, str, str]:
    """Return the line of a ``test_window`` output as the harness names it, with the role and suffix it appends."""
    return "test_window", param, f"a/{derived_file_name('a', 'test_window', param, frozenset(), role, suffix)}", ""


DERIVED_LINES = (
    ("test_size", "", "a/size.txt", ""),
    ("test_size", "", "a/size.txt", "3.11"),
    ("test_size", "", "a/size.warnings.txt", ""),
    ("test_size", "", "a/size/", ""),
    ("test_a_size", "", "a/a_size.txt", ""),
    ("test_a_other", "", "a/other.txt", ""),
    ("test_p", "one", "a/p[one].txt", ""),
    ("TestCase::test_q", "", "a/TestCase.q.txt", ""),
    _window(WINDOW, ".warnings"),
    _window(SHORT_WINDOW),
    _window(SHORT_WINDOW, ".warnings"),
)
"""Stored outputs that the harness names for the tests of ``tests/test_a.py``, in every form it writes them."""
NAMED_LINES = (
    ("test_size", "", "other/size.txt", ""),
    ("test_size", "", "other/size.txt", "3.11"),
    ("test_size", "", "a/shared.txt", ""),
    ("test_size", "", "a/size-extra.txt", ""),
    ("test_size", "", "a/size//*.py", ""),
    ("test_p", "one", "a/p[two].txt", ""),
    ("TestCase::test_q", "", "a/q.txt", ""),
    ("test_a_size", "", "a/size.txt", ""),
    ("test_a_other", "", "a/a_other.txt", ""),
    ("test_window", WINDOW, f"a/window[{WINDOW}].warnings.txt", ""),
)
"""Nine stored outputs that tests name by hand: outside the directory, beside or inside the derived name, in a tree
with a pattern, of another item, with the prefix the harness drops, or with the id the harness shortens."""
NO_MODULE_LINES = (("test_b_x", "", "b/x.txt", ""),)
"""An output of a module whose file is gone, which the harness still names by its test."""
DERIVED_REFS, NAMED_REFS, BOTH_REFS = _refs(*DERIVED_LINES), _refs(*NAMED_LINES), _refs(*DERIVED_LINES, *NAMED_LINES)

COMMENTS = (
    FUTURE
    + """#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os  # noqa: F401


def one() -> int:
    # for now the answer is fixed
    return 1  # pragma: no cover


def two() -> int:
    return 2  # TODO: compute it
"""
)
"""A module with a stopgap comment, a to-do comment and directives (a shebang, an encoding line, noqa, a pragma)."""
COMMENTS_MOVED = FUTURE + "\n\n" + COMMENTS.removeprefix(FUTURE)
"""The same comments with other line numbers: no key rises."""
PROSE_COMMENTS = (
    FUTURE
    + """VALUE = 1  # value type: int for now
OTHER = 2  # NOQA
THIRD = 3  # type: int
FOURTH = 4  # see mypy: the docs
"""
)
"""A comment that holds ``type:`` in prose counts as plain; a bare ``noqa`` and a ``type:`` comment are directives."""
EXPECTED_REFS = (
    "# Generated by pytest.\n[test_size]\n\ta/size.txt\n[test_p]\n"
    "one\ta/p[one].txt\t?\n"
    "one\ta/p[one].txt\t@python==3.11\t= a/p[one]@python3.11.txt\n"
    "two\ta/p[two].txt\t?\n"
)
"""A ``.refs`` file with two conditional lines and one environment override."""
SCOPE_WALK = """import ast as syntax
import symtable
import tokenize
from ast import Name

from scripts._source_model import SourceModule


def stores(tree: syntax.AST) -> list[str]:
    found = []
    for node in syntax.walk(tree):
        match node:
            case syntax.Name(ctx=syntax.Store()):
                found.append(node.id)
    return found


def locals_of(text: str) -> list[str]:
    return [name for name in symtable.symtable(text, "m", "exec").get_identifiers() if isinstance(name, Name)]


def comments(tokens: list[tokenize.TokenInfo]) -> int:
    return sum(token.type == tokenize.COMMENT for token in tokens)


def built() -> syntax.Name:
    return syntax.Name(id="x", ctx=syntax.Store())


class Visitor(syntax.NodeVisitor):
    def visit_ClassDef(self, node: syntax.ClassDef) -> None:
        self.generic_visit(node)


def bound(text: str) -> list[str]:
    return [binding.name for binding in SourceModule(text).bindings()]
"""
"""A rule module that reads bindings and comments itself, by alias and by name, and one that asks the model."""
JUSTIFIED = 'JUSTIFIED_VIOLATIONS = {"g": ("a",)}\n'
APPROVAL = """[[approved]]
key = "allowlist:JUSTIFIED_VIOLATIONS"
head = 2
pr = 7172
decision = ["#7172"]
reason = "a pure test of private cores"
"""
APPROVED_BASE = {"tests/assert_helper_allowlists.py": JUSTIFIED, "scripts/ratchet_approvals.toml": APPROVAL}
"""A base whose table approves one more justified violation, up to 2, for pull request 7172."""
ONE_MORE = {
    "tests/assert_helper_allowlists.py": JUSTIFIED.replace('("a",)', '("a", "b")'),
    "scripts/ratchet_approvals.toml": "# No pending approval.\n",
}
"""The approved rise, with the entry removed as the pull request that reaches the count does."""
TWO_MORE = {**ONE_MORE, "tests/assert_helper_allowlists.py": JUSTIFIED.replace('("a",)', '("a", "b", "c")')}
PULL_REQUEST = {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_REF": "refs/pull/7172/merge"}
MALFORMED_APPROVALS = """note = "not a table of the file"
approved = [
  1,
  {key = "allowlist:JUSTIFIED_VIOLATIONS", head = 2, extra = true},
  {key = "allowlist:X:unreadable", head = 0, pr = "7172", decision = ["7172"], reason = " "},
  {key = "allowlist:JUSTIFIED_VIOLATIONS", head = 2, pr = 7172, decision = ["#7172"], reason = "kept"},
  {key = "allowlist:JUSTIFIED_VIOLATIONS", head = 3, pr = 7172, decision = [], reason = "again"},
  {key = "allowlist:JUSTIFIED_VIOLATIONS", head = 3, pr = 7172, decision = ["#7172"], reason = "again"},
  {key = "allowlist:JUSTIFIED_VIOLATIONS", head = 2026-11-19, pr = 7173, decision = ["#7173"], reason = 07:32:00},
  {key = "source-rule:unused-private-global", head = 1, pr = 7174, decision = ["#7174"], reason = "an absolute rule"},
]
"""
PACKAGE = "src/datamodel_code_generator"
SOURCE_RULES_BASE = {
    f"{PACKAGE}/__init__.py": "",
    f"{PACKAGE}/hosts.py": FUTURE
    + """
from typing import Protocol

_LEFT = 1


class Host(Protocol):
    def size(self) -> int: ...


def helper() -> int:
    return 1
""",
    f"{PACKAGE}/user.py": FUTURE + "\nfrom datamodel_code_generator.hosts import helper\n\nVALUE = helper()\n",
}
"""A package with a stub body, an unused private global (an absolute rule) and a public function it calls."""
SOURCE_RULES_HEAD = {
    f"{PACKAGE}/hosts.py": SOURCE_RULES_BASE[f"{PACKAGE}/hosts.py"].replace(
        "def size(self) -> int: ...", "def size(self) -> int: ...\n\n    def name(self) -> str: ..."
    ),
    f"{PACKAGE}/user.py": FUTURE + "\n_STALE = 2\nVALUE = 1\n",
}
"""A second stub body, a second unused private global, and the last call of ``helper`` gone (a candidate only)."""
ABSOLUTE_APPROVAL = APPROVAL.replace("allowlist:JUSTIFIED_VIOLATIONS", "source-rule:unused-private-global")
"""An approval of a second unused private global, the key of an absolute rule, which no table may hold."""
DEMOTED_RULES = COUNTER["scripts/check_source_rules.py"].replace(
    '"delete the unused name (a move left it behind)",\n        absolute=True,',
    '"delete the unused name (a move left it behind)",',
)
"""The source rules with ``unused-private-global`` no longer absolute: only the base's rules still say it is."""
UNPARSABLE = 'try:\n    VALUE = int("1")\nexcept (ValueError, TypeError:\n    VALUE = ...\n'
"""A test input this Python cannot parse: an unclosed bracket stands for syntax newer than the interpreter."""
REFUSED = "def f():\n    nonlocal x\n    x = 1\n"
"""A test input that parses and that the source model refuses: a ``nonlocal`` name no function binds."""
CASES = {
    "unchanged": {"base": COUNTER, "head": {}},
    "approved-rise": {
        "base": {**COUNTER, **APPROVED_BASE},
        "head": ONE_MORE,
        "env": PULL_REQUEST,
        "args": ["--enforce"],
    },
    "approved-rise-exceeded": {"base": APPROVED_BASE, "head": TWO_MORE, "env": PULL_REQUEST, "args": ["--enforce"]},
    "approved-rise-other-pull-request": {
        "base": APPROVED_BASE,
        "head": ONE_MORE,
        "env": {**PULL_REQUEST, "GITHUB_REF": "refs/pull/7200/merge"},
        "args": ["--enforce"],
    },
    "approved-rise-local-run": {"base": APPROVED_BASE, "head": ONE_MORE, "args": ["--enforce"]},
    "approved-rise-merged": {
        "base": APPROVED_BASE,
        "head": ONE_MORE,
        "subject": "Check the merge cores (#7172)",
        "env": {"GITHUB_EVENT_NAME": "push"},
        "args": ["--enforce"],
    },
    "approval-stale": {
        "base": APPROVED_BASE,
        "head": {"tests/assert_helper_allowlists.py": ONE_MORE["tests/assert_helper_allowlists.py"]},
        "env": PULL_REQUEST,
        "args": ["--enforce"],
    },
    "approvals-malformed": {"head": {"scripts/ratchet_approvals.toml": MALFORMED_APPROVALS}, "args": ["--enforce"]},
    "approvals-not-a-list": {"head": {"scripts/ratchet_approvals.toml": "approved = 1\n"}, "args": ["--enforce"]},
    "approvals-not-toml": {"head": {"scripts/ratchet_approvals.toml": "[[approved]\n"}, "args": ["--enforce"]},
    "added-hatch": {"head": {"src/pkg/b.py": FUTURE + SIZE}, "args": ["--enforce"]},
    "scope-walk": {
        "base": COUNTER,
        "head": {"scripts/rule.py": SCOPE_WALK, "tests/test_rule.py": SCOPE_WALK, "src/pkg/rule.py": SCOPE_WALK},
        "args": ["--enforce"],
    },
    "named-outputs": {
        "base": {"tests/test_a.py": A_MODULE, "tests/test_a.refs": DERIVED_REFS},
        "head": {"tests/test_a.refs": BOTH_REFS},
        "args": ["--enforce"],
    },
    "named-outputs-moved": {
        "base": {"tests/test_a.py": A_MODULE, "tests/test_a.refs": BOTH_REFS},
        "head": {"tests/test_a.refs": DERIVED_REFS, "tests/test_b.refs": _refs(*NAMED_LINES, *NO_MODULE_LINES)},
        "args": ["--enforce"],
    },
    "named-outputs-fall": {
        "base": {"tests/test_a.py": A_MODULE, "tests/test_a.refs": BOTH_REFS},
        "head": {"tests/test_a.refs": DERIVED_REFS},
        "args": ["--enforce"],
    },
    "stopgap-comments": {"head": {"src/pkg/c.py": COMMENTS}, "args": ["--enforce"]},
    "stopgap-comments-moved": {
        "base": {"src/pkg/c.py": COMMENTS},
        "head": {"src/pkg/c.py": COMMENTS_MOVED},
        "args": ["--enforce"],
    },
    "stopgap-comments-prose": {"head": {"src/pkg/c.py": PROSE_COMMENTS}, "args": ["--enforce"]},
    "expected-reported-only": {"head": {"tests/test_a.refs": EXPECTED_REFS}, "args": ["--enforce"]},
    "moved-line": {"head": {"src/pkg/a.py": FUTURE + NAME, "src/pkg/b.py": FUTURE + SIZE}, "args": ["--enforce"]},
    "form-swap": {
        "head": {
            "src/pkg/a.py": FUTURE + SIZE.replace("noqa: PLC0415", "ruff: ignore[import-outside-top-level]") + NAME
        }
    },
    "hidden-by-config": {
        "head": {
            "pyproject.toml": BASE["pyproject.toml"].replace(
                "lint.per-file-ignores.",
                'lint.per-file-ignores."src/pkg/c.py" = ["import-outside-top-level"]\n'
                'lint.per-file-ignores."src/**/*.py" = ["FBT001"]\nlint.per-file-ignores.',
            ),
            ".pre-commit-config.yaml": BASE[".pre-commit-config.yaml"].replace('"ERA001"', '"ERA001,T201"'),
            "src/pkg/c.py": FUTURE
            + """

def show(flag: bool) -> None:
    import json

    print(json.dumps(flag))
""",
        },
        "args": ["--enforce"],
    },
    "other-kinds": {"head": OTHER_KINDS},
    "hook-exclude-widened": {
        "head": {
            ".pre-commit-config.yaml": BASE[".pre-commit-config.yaml"].replace(
                "^src/pkg/generated/", "^src/pkg/(generated/|b)"
            ),
            "src/pkg/b.py": PRAGMA_FILE,
        },
        "args": ["--enforce"],
    },
    "extend-exclude-swapped": {
        "head": {
            "pyproject.toml": BASE["pyproject.toml"].replace('["scripts"]', '["src/pkg/b.py"]'),
            "src/pkg/b.py": PRAGMA_FILE,
        },
        "args": ["--enforce"],
    },
    "coverage-omit-widened": {
        "base": {"pyproject.toml": PYPROJECT + '\n[tool.coverage]\nrun.omit = ["src/pkg/generated/*"]\n'},
        "head": {"pyproject.toml": PYPROJECT + '\n[tool.coverage]\nrun.omit = ["src/*"]\n'},
        "args": ["--enforce"],
    },
    "nested-ruff-config": {
        "head": {
            "src/pkg/sub/ruff.toml": 'extend = "../../../pyproject.toml"\nexclude = ["b.py"]\n',
            "src/pkg/sub/b.py": PRAGMA_FILE,
            "src/pkg/other/pyproject.toml": "[tool.ruff]\nline-length = 100\n",
            "src/pkg/third/pyproject.toml": "[project]\nname = 'third'\n",
            "tests/data/fixture/ruff.toml": "line-length = 100\n",
        },
        "args": ["--enforce"],
    },
    "exempt-files-listed": {
        "base": {
            "pyproject.toml": PYPROJECT
            + '\n[tool.pytest]\nini_options.assert_helper_direct_assert_exempt_files = ["a.py"]\n'
        },
        "head": {
            "pyproject.toml": PYPROJECT
            + '\n[tool.pytest]\nini_options.assert_helper_direct_assert_exempt_files = ["a.py", "b.py"]\n'
        },
        "args": ["--enforce"],
    },
    "heki-baseline-grows": {
        "base": {"scripts/heki_baseline.toml": HEKI_BASELINE},
        "head": {
            "scripts/heki_baseline.toml": HEKI_BASELINE.replace("count = 1", "count = 2")
            + HEKI_BASELINE.removeprefix("version = 2\n").replace('"run"', '"stop"')
        },
        "args": ["--enforce"],
    },
    "heki-baseline-environment-swap": {
        "base": {"scripts/heki_baseline.toml": HEKI_BASELINE.replace("count = 1", "count = 2") + HEKI_ENVIRONMENT},
        "head": {"scripts/heki_baseline.toml": HEKI_BASELINE + HEKI_ENVIRONMENT.replace("count = 1", "count = 2")},
        "args": ["--enforce"],
    },
    "heki-baseline-shrinks": {
        "base": {"scripts/heki_baseline.toml": HEKI_BASELINE.replace("count = 1", "count = 3")},
        "head": {"scripts/heki_baseline.toml": HEKI_BASELINE},
        "args": ["--enforce"],
    },
    "heki-baseline-unreadable": {
        "head": {"scripts/heki_baseline.toml": "version = 2\n[[entries]\n"},
        "args": ["--enforce"],
    },
    "heki-baseline-unreadable-count": {
        "head": {"scripts/heki_baseline.toml": HEKI_BASELINE.replace("count = 1", 'count = "one"')},
        "args": ["--enforce"],
    },
    "heki-baseline-unreadable-shape": {
        "head": {"scripts/heki_baseline.toml": "version = 2\nentries = 3\n"},
        "args": ["--enforce"],
    },
    "allowlist-unreadable": {
        "base": {"tests/assert_helper_allowlists.py": ALLOWLIST},
        "head": {
            "tests/assert_helper_allowlists.py": 'FROZEN_EXEMPT_FILES = frozenset(f"{n}.py" for n in ("a", "b"))\n'
            'JUSTIFIED_VIOLATIONS = {}\nJUSTIFIED_VIOLATIONS.update({"g": ("x", "y")})\n'
        },
        "args": ["--enforce"],
    },
    "allowlist-dict-value-not-written-out": {
        "base": {"tests/assert_helper_allowlists.py": 'JUSTIFIED_VIOLATIONS = {"g": ("x",)}\n'},
        "head": {"tests/assert_helper_allowlists.py": 'JUSTIFIED_VIOLATIONS = {"g": tuple(["x", "y", "z"])}\n'},
        "args": ["--enforce"],
    },
    "allowlist-dict-of-constants": {
        "base": {"tests/assert_helper_allowlists.py": 'JUSTIFIED_VIOLATIONS = {"g": ("x",)}\n'},
        "head": {"tests/assert_helper_allowlists.py": 'JUSTIFIED_VIOLATIONS = {"g": "x", "h": "y"}\n'},
        "args": ["--enforce"],
    },
    "any-uses": {"head": {"src/pkg/c.py": ANY_USES}, "args": ["--enforce"]},
    "any-values": {"head": {"src/pkg/c.py": ANY_VALUES}, "args": ["--enforce"]},
    "dodges": {"head": {"src/pkg/c.py": DODGES}, "args": ["--enforce"]},
    "fix-enabled": {
        "head": {
            "pyproject.toml": BASE["pyproject.toml"].replace("lint.select", "fix = true\nlint.select"),
            "src/pkg/b.py": "import os  # noqa: F401\n",
        },
        "args": ["--enforce"],
    },
    "extended-per-file-ignores": {
        "head": {
            "pyproject.toml": BASE["pyproject.toml"].replace(
                "lint.per-file-ignores.",
                'lint.extend-per-file-ignores."src/pkg/b.py" = ["PLC0415"]\n'
                'lint.extend-per-file-ignores."src/**/*.py" = ["FBT001"]\nextend = "base.toml"\nlint.per-file-ignores.',
            ),
            "base.toml": "line-length = 100\n",
            "src/pkg/b.py": LOCAL_IMPORT,
        },
        "args": ["--enforce"],
    },
    "top-level-extended-ignores": {
        "head": {
            "pyproject.toml": BASE["pyproject.toml"].replace(
                "lint.per-file-ignores.",
                'extend-per-file-ignores."src/pkg/b.py" = ["PLC0415"]\nlint.per-file-ignores.',
            ),
            "src/pkg/b.py": LOCAL_IMPORT,
        },
        "args": ["--enforce"],
    },
    "quoted-hook-id": {
        "head": {
            ".pre-commit-config.yaml": HOOK_START.replace("id: ruff", 'id: "ruff"')
            + '    exclude: "^src/pkg/generated/"\n    args: ["--extend-ignore", "T201"]\n'
        },
        "args": ["--enforce"],
    },
    "allowlist-subscript-writes": {
        "base": {"tests/assert_helper_allowlists.py": ALLOWLIST},
        "head": {
            "tests/assert_helper_allowlists.py": ALLOWLIST
            + 'JUSTIFIED_VIOLATIONS = {"g": ("a",)}\nJUSTIFIED_VIOLATIONS["g"] += ("b",)\n'
            'JUSTIFIED_VIOLATIONS["h"] = ("c", "d")\nFROZEN_VIOLATIONS = {"g": ["a"]}\n'
            'FROZEN_VIOLATIONS["g"].append("x")\nLEGACY_VIOLATIONS = {}\n'
            'LEGACY_VIOLATIONS["g"] += FROZEN_VIOLATIONS["g"]\n'
        },
        "args": ["--enforce"],
    },
    "explicit-preview-rules": {
        "base": {
            "src/pkg/m.py": FUTURE + "\n\nclass M:\n    def run(self) -> int:\n        return 1\n",
        },
        "head": {
            "pyproject.toml": PYPROJECT
            + 'lint.explicit-preview-rules = true\n\n[tool.ty]\nrules.deprecated = "error"\n'
        },
        "args": ["--enforce"],
    },
    "block-style-args": {
        "head": {
            ".pre-commit-config.yaml": HOOK_START
            + """    exclude: "^src/pkg/generated/"
    args:
      # kept

      - --preview
      - --fix
      - --extend-ignore
      - "ERA001,T201"
- repo: https://github.com/codespell-project/codespell
  rev: v2.4.3
  hooks:
  - id: codespell
""",
            "src/pkg/b.py": FUTURE + "\n\ndef show() -> None:\n    print(1)\n",
        },
        "args": ["--enforce"],
    },
    "unreadable-hook": {
        "head": {
            ".pre-commit-config.yaml": HOOK_START
            + """    exclude: |
      ^src/pkg/generated/
    args: ['--fix', "--preview"]
"""
        },
        "args": ["--enforce"],
    },
    "unreadable-arguments": {
        "head": {
            ".pre-commit-config.yaml": HOOK_START
            + """    args:
      - --select=E
      - --config
      - lint.preview = true
"""
        },
        "args": ["--enforce"],
    },
    "import-cycles": {"base": IMPORT_ONE_WAY, "head": IMPORT_BACK, "args": ["--enforce"]},
    "import-cycles-ignored-files": {
        "base": IMPORT_ONE_WAY,
        "head": IMPORT_IGNORED,
        "args": ["--enforce"],
    },
    "ahead-base": {
        "ahead": {"src/pkg/a.py": FUTURE + NAME},
        "base_ref": "ahead",
        "head": {"src/pkg/d.py": FUTURE + NAME},
        "args": ["--enforce"],
    },
    "unchanged-counter": {
        "base": COUNTER,
        "head": {"src/pkg/d.py": FUTURE + NAME},
        "args": ["--enforce"],
        "subprocess": True,
    },
    "unchanged-counter-other-checkout-input": {
        "base": {
            **COUNTER,
            "pyproject.toml": BASE["pyproject.toml"] + '\n[project]\nrequires-python = ">=3.9"\n',
            "tests/test_a.py": "match 1:\n    case _:\n        pass\n",
        },
        "head": {
            "pyproject.toml": BASE["pyproject.toml"] + '\n[project]\nrequires-python = "~=3.9"\n',
            "tests/test_b.py": "match 1:\n    case _:\n        pass\n",
        },
        "args": ["--enforce"],
        "subprocess": True,
    },
    "weakened-counter": {
        "base": COUNTER,
        "head": {
            "scripts/check_ratchets.py": SCRIPT.replace(
                r'PRAGMA = re.compile(r"\bpragma:', r'PRAGMA = re.compile(r"\bnever:'
            ),
            "src/pkg/a.py": FUTURE + SIZE + NAME.replace("-> str:", "-> str:  # pragma: no cover"),
        },
        "subprocess": True,
    },
    "stored-lists-grow": {
        "base": _stored_lists(STORED_BASE),
        "head": _stored_lists({
            "one": [GAP.format(1), GAP.format(3)],
            "two": [GAP.format(4)],
            "empty": [],
            "three": [SEED],
            "four": [GAP.format(5)],
            "five": [NO_SEED],
            "six": "nowhere",
            "seven": {"k": 1},
        }),
        "args": ["--enforce"],
    },
    "stored-lists-shrink": {
        "base": _stored_lists(STORED_BASE),
        "head": _stored_lists({
            "one": [GAP.format(1)],
            "two": "empty",
            "empty": [],
            "three": [GAP.format(6), GAP.format(7)],
        }),
        "args": ["--enforce"],
    },
    "stored-lists-environments": {
        "base": _stored_lists({
            "one": [GAP.format(1)],
            "one@pydantic==2.0": [],
            "two": [GAP.format(2)],
            "two@pydantic==2.0": [GAP.format(2), GAP.format(3)],
            "three": [GAP.format(4)],
            "three@pydantic==2.0": [GAP.format(4), GAP.format(5)],
            "four": [GAP.format(6)],
            "five": [GAP.format(7)],
        }),
        "head": _stored_lists({
            "one": [GAP.format(1)],
            "one@pydantic==2.0": [GAP.format(1)],
            "two": [GAP.format(2), GAP.format(3)],
            "three": [GAP.format(4)],
            "four": [GAP.format(6)],
            "four@pydantic==2.0": [GAP.format(6)],
            "five": [GAP.format(7)],
            "five@pydantic==2.0": [GAP.format(7), GAP.format(8)],
            "five#warnings": ["a new generation warning"],
        }),
        "args": ["--enforce"],
    },
    "stored-lists-relabeled": {
        "base": _stored_lists(STORED_LANES),
        "head": _stored_lists(STORED_RELABELED),
        "args": ["--enforce"],
    },
    "stored-lists-lane-gains": {
        "base": _stored_lists({
            **STORED_LANES,
            "odd": [GAP.format(8)],
            "moved": [GAP.format(10)],
            "moved@pydantic==2.0": [GAP.format(10), GAP.format(11)],
            "old": [GAP.format(12)],
            "old@pydantic=2.0": [GAP.format(12)],
        }),
        "head": _stored_lists({
            **STORED_RELABELED,
            "relabel@pydantic<2.5": [GAP.format(1), GAP.format(2), GAP.format(7)],
            "odd": [GAP.format(8)],
            "odd@pydantic~2": [GAP.format(8), GAP.format(9)],
            "moved": [GAP.format(10), GAP.format(11)],
            "moved@pydantic==2.0": [GAP.format(10)],
            "old": [GAP.format(12)],
            "old@pydantic=2.0": [GAP.format(12)],
        }),
        "args": ["--enforce"],
    },
    "stored-lists-item-excluded": {
        "base": {**_stored_lists(STORED_LANES), **ITEM_EXCLUDED},
        "head": {**_stored_lists(STORED_RELABELED), **ITEM_EXCLUDED},
        "args": ["--enforce"],
    },
    "stored-lists-without-lanes": {
        "head": {name: text for name, text in _stored_lists({"one": [GAP.format(1)]}).items() if name not in LANES},
        "args": ["--enforce"],
    },
    "stored-lists-new-test": {
        "head": _stored_lists({"one": [GAP.format(1)], "five": [NO_SEED]}),
        "args": ["--enforce"],
    },
    "source-rules": {"base": SOURCE_RULES_BASE, "head": SOURCE_RULES_HEAD, "args": ["--enforce"]},
    "source-rules-unchanged": {"base": SOURCE_RULES_BASE, "head": {}, "args": ["--enforce"]},
    "source-rules-unparsable": {"base": SOURCE_RULES_BASE, "head": {"tests/data/python/newer.py": UNPARSABLE}},
    "source-rules-refused": {
        "base": SOURCE_RULES_BASE,
        "head": {"tests/data/python/refused.py": REFUSED},
        "args": ["--enforce"],
    },
    "demoted-absolute-rule": {
        "base": {**COUNTER, **SOURCE_RULES_BASE},
        "head": {"scripts/check_source_rules.py": DEMOTED_RULES},
        "args": ["--enforce"],
        "subprocess": True,
    },
    "demoted-absolute-rule-approved-in-head": {
        "base": COUNTER,
        "head": {"scripts/check_source_rules.py": DEMOTED_RULES, "scripts/ratchet_approvals.toml": ABSOLUTE_APPROVAL},
        "args": ["--enforce"],
        "subprocess": True,
    },
    "weakened-absolute-rule": {
        "base": {**COUNTER, **SOURCE_RULES_BASE, "scripts/ratchet_approvals.toml": ABSOLUTE_APPROVAL},
        "head": {
            "scripts/check_source_rules.py": DEMOTED_RULES,
            "scripts/ratchet_approvals.toml": "# No pending approval.\n",
            f"{PACKAGE}/user.py": SOURCE_RULES_HEAD[f"{PACKAGE}/user.py"],
        },
        "env": PULL_REQUEST,
        "args": ["--enforce"],
        "subprocess": True,
    },
    "weakened-source-rules": {
        "base": {**COUNTER, **SOURCE_RULES_BASE},
        "head": {
            "scripts/check_source_rules.py": COUNTER["scripts/check_source_rules.py"].replace(
                "node.value.value is Ellipsis", "node.value.value is None"
            ),
            f"{PACKAGE}/hosts.py": SOURCE_RULES_HEAD[f"{PACKAGE}/hosts.py"],
        },
        "args": ["--enforce"],
        "subprocess": True,
    },
    "weakened-counter-unreadable": {
        "base": COUNTER,
        "head": {
            "scripts/check_ratchets.py": SCRIPT.replace(
                'HOOK_UNREADABLE_FLAGS = frozenset({"--select", "--config"})',
                'HOOK_UNREADABLE_FLAGS = frozenset({"--select"})',
            ),
            ".pre-commit-config.yaml": HOOK_START + '    args: ["--config", "lint.preview = true"]\n',
        },
        "args": ["--enforce"],
        "subprocess": True,
    },
}


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def _repository(
    tmp_path: Path,
    base: dict[str, str],
    head: dict[str, str],
    ahead: dict[str, str] | None = None,
    subject: str = "head",
) -> Path:
    """Commit the base files on branch ``base``, ``ahead`` files on a branch beyond it, then the head on ``main``."""
    repository = tmp_path / "repository"
    _write(repository, {**BASE, **base})
    for command in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "base"], ["branch", "base"]):
        subprocess.run([*GIT, *command], cwd=repository, check=True)
    if ahead:
        subprocess.run([*GIT, "switch", "-q", "-c", "ahead"], cwd=repository, check=True)
        _write(repository, ahead)
        for command in (["add", "-A"], ["commit", "-q", "-m", "ahead"], ["switch", "-q", "main"]):
            subprocess.run([*GIT, *command], cwd=repository, check=True)
    _write(repository, head)
    for command in (["add", "-A"], ["commit", "-q", "--allow-empty", "-m", subject]):
        subprocess.run([*GIT, *command], cwd=repository, check=True)
    return repository


def _stable(text: str) -> str:
    """Replace commit ids, which differ on every run, by a placeholder."""
    return re.sub(r"\b[0-9a-f]{40}\b", "<commit>", text)


@pytest.mark.parametrize("case", CASES)
def test_check_ratchets_against_base(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rises are reported with their files and fix; moves and form swaps are neutral; the base's counter also judges.

    The run of the pull request an approval names accepts the rise up to the approved count; the table is checked.
    """
    spec = CASES[case]
    for name in ("GITHUB_EVENT_NAME", "GITHUB_REF"):
        monkeypatch.delenv(name, raising=False)
    for name, value in spec.get("env", {}).items():
        monkeypatch.setenv(name, value)
    repository = _repository(
        tmp_path, spec.get("base", {}), spec["head"], spec.get("ahead"), spec.get("subject", "head")
    )
    args = ["--base", spec.get("base_ref", "base"), *spec.get("args", [])]
    if spec.get("subprocess"):
        command = [sys.executable, "scripts/check_ratchets.py", *args]
        result = subprocess.run(command, cwd=repository, capture_output=True, text=True, check=False)
        code, stdout = result.returncode, result.stdout
    else:
        monkeypatch.chdir(repository)
        code = check_ratchets.main(args)
        stdout = capsys.readouterr().out
    assert_output(f"exit status {code}\n{_stable(stdout)}")


def test_check_ratchets_reports_a_failing_base_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A base copy that cannot count a tree ends the run with its message and the command to reproduce it."""
    repository = _repository(tmp_path, {"scripts/check_ratchets.py": "import sys\n\nsys.exit('cannot read')\n"}, {})
    monkeypatch.chdir(repository)
    with pytest.raises(SystemExit) as raised:
        check_ratchets.main(["--base", "base"])
    assert_output(f"{_stable(str(raised.value))}\n{capsys.readouterr().out}")


def test_check_ratchets_counts_one_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without a base the script prints each key's count; --count prints them per file; --ruff-pin the hook's pin."""
    repository = _repository(tmp_path, {}, OTHER_KINDS)
    monkeypatch.chdir(repository / "src")
    code = check_ratchets.main([])
    assert_output(f"exit status {code}\n{capsys.readouterr().out}", name="table")
    code = check_ratchets.main(["--count", ".."])
    assert_output(f"exit status {code}\n{capsys.readouterr().out}", name="json")
    code = check_ratchets.main(["--ruff-pin"])
    assert_output(f"exit status {code}\n{capsys.readouterr().out}", name="ruff-pin")
