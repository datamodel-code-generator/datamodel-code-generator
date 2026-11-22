"""Count the escape hatches of a source tree and compare them with a base revision.

An escape hatch switches a check off for some code. Each one is counted under a key ``<kind>:<code>@<area>``, where
the area is ``src``, ``tests``, ``scripts`` or ``other``; keys of configuration entries have no area:

- ``ruff:<rule>``: findings that a suppression hides, whatever its form (a ``noqa`` comment, a ``ruff: ignore``
  comment, a per-file ignore, a narrower rule selection), as the difference between a ruff run as the pre-commit hook
  runs it and one without those suppressions. Moving a hatch between these forms is neutral; hiding a new finding in
  the configuration counts like a new comment.
- ``ruff-config:ignore:<rule>``: rules ignored in the whole project, one key per rule whether ``lint.ignore`` or the
  hook's ``--extend-ignore`` lists it (their findings grow with every new function, so the entries are counted per
  entry, not per finding); ``ruff-config:exclude:<path>`` and ``ruff-config:hook-exclude:<pattern>``: paths ruff does
  not check, keyed by their text, so widening one in place is a new key; ``ruff-config:file:<path>``: every other
  ruff configuration file (``ruff.toml``, ``.ruff.toml``, a nested ``pyproject.toml`` with ``[tool.ruff]``).
- ``pragma:no-cover``, ``pragma:no-branch``, ``pragma:conditional-cover``, ``ty-ignore:<rule>``,
  ``pyright-ignore:<rule>`` and ``type-ignore:<code>`` comments, read with ``tokenize``.
- ``heki-ignore:<rule>``: ``# heki: ignore`` comments, bare (``BARE``) or per listed rule, and ``# noqa`` comments,
  bare (``BARE``) or naming a heki rule, which heki reads as suppressions too. A rule is named by its code
  (``EFF101``, ``MUT201``, ``DATA101``, ``OPT110``) or its name (``write-owner-violation``; anywhere after
  ``noqa:``, or inside ``heki: ignore[...]``) and counts under the rule's code. A bracketed list that is not closed
  or names no rule silences nothing in heki and counts none. A ``noqa`` also counts under ``ruff:`` where ruff
  would report the finding. The source model reports the ``heki: ignore`` comments as suppressions of tool ``heki``.
- ``cast``, ``any`` (``Any`` inside an annotation or a ``cast`` target), ``getattr-private`` (a constant private
  name given to ``getattr`` and its siblings), ``dynamic-import``, ``filterwarnings-ignore``, ``no-type-check`` and
  ``abnormal-path`` (the test marker), read with ``ast``.
- ``import-cycle:<measure>@<area>``: the modules on import cycles of the package (``src``) and of ``scripts``, read by
  `scripts/_import_cycles.py` from the model's import graph: ``module-level`` counts the cycles of the imports that run
  when their module is imported, ``lazy`` those of every import (function-local and ``TYPE_CHECKING`` ones too) and
  ``largest-lazy`` the largest of those; each module counts once, at its first import into its cycle.
- ``source-rule:<rule>``: the sites of each rule of `scripts/check_source_rules.py` that runs without a base, as its
  ``--count`` prints them. A rule absolute in the head or in the base revision (read with the base's own rules) fails
  on any site, whatever the base holds. The base-relative rules (``lost-last-caller``) report candidates that need
  coverage data to decide: a run with ``--base`` prints them and never fails on them.
- ``private-scope-walk``, in ``scripts`` and ``tests``: a check that reads bindings, scopes, imports or comments with
  ``ast``, ``symtable`` or ``tokenize`` itself instead of through ``scripts/_source_model.py`` (`scope_walk_sites`),
  read by origin with the model; the model and its test are not counted.
- ``named-stored-output@tests``: the stored outputs that a test names by hand. Each ``.refs`` reference (a test item and
  the label it compares) whose label is not where the harness derives it for that item counts once: not in the module's
  derived directory (``main/test_x.py`` stores in ``main/x/``) or not starting with the item's derived name (a ``name=``
  role or a suffix after it stays inside). AGENTS.md forbids it, because pytest names, shares and splits stored files
  itself. The count reads the ``.refs`` files the harness reads and asks `tests/units.py`, which also names the outputs
  of running tests, whether the harness writes each label (`is_derived_label`). It leaves out what no ``.refs`` file
  records: a stored file read through ``stored_expected`` (another test's output), an output compared outside
  ``tests/data/expected``, a hand-named label equal to the one the harness would write, and (see `_module_tests`)
  a test whose sibling the module gets by inheritance, import or assignment. Renaming a test counts the hand-named
  labels that happened to match its old derived name. It may stay or fall, never rise: the base is the
  budget.
- ``comment:plain@src`` and ``comment:todo``: the comments of the package that are no hatch, read through the source
  model. ``comment:todo`` counts those with a to-do marker (``TODO``, ``FIXME``, ``XXX``, ``HACK``),
  ``comment:plain@src`` the other plain comments (a directive such as ``noqa``, a pragma, a type ignore, a shebang or
  an encoding line is none). A comment that explains a hard line is legitimate, so the budget is today's count, paid
  by turning another comment into a docstring.
- ``expected:env-override`` and ``expected:conditional``: the ``.refs`` lines that carry an environment predicate
  (``@...``) and those marked conditional (``?``), per sidecar. They are reported in the table and never fail.
- Entries: ty rules below error, coverage ``omit`` and ``exclude`` lists (by text), pytest ``ignore`` warning filters
  and the direct-assert exemption list, the generation store allowlist, the host baseline
  (``allowlist:host-private-access``, the sum of its access counts), heki's baseline (the sum of the site counts of
  its entries: ``allowlist:heki-baseline:EFF201`` for the reads of the environment the package runs in, which no other
  key budgets, ``allowlist:heki-baseline`` for the rest), and the allowlists of the test style and architecture
  checks, the callers of decision homes that no row lists (``allowlist:UNLISTED_CALLERS``) and the bypasses of builder
  homes (``allowlist:BUILDER_BYPASSES``) (``allowlist:<NAME>:unreadable`` when one is written in a form this script
  cannot read).
- ``stored-list:<test>:items``, ``stored-list:<test>:lines`` and ``stored-list:<test>:blind``: the lists that a test
  of ``STORED_LISTS`` stores per item (through its ``.refs`` file), which may only shrink. ``items`` counts the
  records, an item's default and each environment's override (``@<predicate>``), which is never a rise. ``lines``
  counts each line under its item, its text and its environment's predicate, split by tabs. Each environment of an
  item reads its override or else the default, and compares with the record it reads in the base: it rises with a
  line that record did not hold, while a new item, and an environment whose base record is a blind line (its checks
  did not run), starts at the lines it stores. ``blind`` counts the lines of ``BLIND_LINES``, which say that the item
  checked nothing (a seed the model rejects): it rises with a blind line the base record does not hold, in a new item
  too, once the base stores the test. These keys rise by their lines, not their totals, so a fix that drops one line
  does not pay for a regression that adds another, nor one environment's fix for another's regression.
- ``source-rule:unreadable``: a file that parses and that the source model refuses, with its error, which no source
  rule can read.
- ``unreadable:<what>`` and ``allowlist:<NAME>:unreadable``: a hook setting (no value or a block scalar, a ``--select``
  or ``--config`` argument) or an allowlist or host baseline this script cannot read. Such a key always fails under
  ``--enforce``, because what it hides cannot be counted.

The source comments and calls are counted in every ``.py`` and ``.pyi`` file outside ``tests/data``, whether ruff
checks it or not; ruff findings only in the files the hook checks. ``--base <ref>`` counts the merge-base of ``<ref>``
and HEAD and the working tree (untracked files included) and prints every key with both counts; the base is the budget,
so a key may stay or fall. When the base has its own copy of this script, that copy counts both trees too, so a change
cannot weaken the counter that judges it. For each key that rose, the report names the files whose count rose, their
sites and how to fix it. The exit status is 0 unless ``--enforce`` is given and a key rose.

``scripts/ratchet_approvals.toml`` lists the rises the owner approved, each for one pull request; it is read from the
base revision, so a change cannot approve itself. A run of that pull request on GitHub Actions accepts the key up to the
approved count; every other run fails as before. An entry that the tree already reaches accepts no rise and is stale,
and a stale or malformed entry fails under ``--enforce`` (`_judged_pull_requests`, `_approval_problems`). No entry, in
either table, approves the key of a rule that is absolute in the head or in the base revision: such an entry is
malformed.

``--count <dir>`` prints the counts of one tree as JSON, ``{key: {file: count}}``; a later copy of this script reads it
from the base revision's copy, so its form stays.
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import tokenize
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts._import_cycles import import_cycles
from scripts._source_model import SourceModule
from scripts.check_source_rules import RULES
from scripts.check_source_rules import Tree as SourceTree
from scripts.check_source_rules import run as run_source_rules
from tests.units import RefsIndex, derived_file_name, derived_siblings, is_derived_label

if TYPE_CHECKING:
    from collections.abc import Callable

SCRIPT = "scripts/check_ratchets.py"
STORE_ALLOWLIST = "scripts/generation_store_allowlist.toml"
HOST_BASELINE = "scripts/host_private_access_baseline.toml"
HEKI_BASELINE = "scripts/heki_baseline.toml"
HEKI_OWN_KEY_RULES = frozenset({"EFF201"})
"""The heki rules whose baseline sites count under a key of their own: no other key budgets them, so a new site cannot
be paid for by a site of another rule."""
ENVIRONMENT_ADVICE = (
    "Output must not depend on the environment the generator runs in: key the decision on an option or a static rule, "
    "not on the interpreter, the platform, a variable or an installed package. A read that belongs to the runtime "
    "itself (an optional dependency) is paid for by removing another one."
)
"""How to fix a read of the environment: the advice of its key, and the help of heki's environment boundary."""
EXPECTED_ROOT = "tests/data/expected"
TESTS_ROOT = "tests"
STORED_LIST = "stored-list"
STORED_LISTS = (
    ("tests/main/test_payload_validation.refs", "test_generated_models_decide_value_neighbours_like_the_schema"),
)
"""The ``.refs`` files and tests whose stored outputs are lists of known gaps that may only shrink."""
NO_SEED_LINE = "no source-valid seed"
REJECTED_SEED_LINE = "seed: valid, model rejects"
BLIND_LINES = frozenset({NO_SEED_LINE, REJECTED_SEED_LINE})
"""The lines that say a stored item checked nothing, so that no new one passes as a shorter list."""
APPROVALS = "scripts/ratchet_approvals.toml"
APPROVAL_FIELDS = ("key", "head", "pr", "decision", "reason")
PUBLIC_REFERENCE = re.compile(r"#[1-9]\d*")
PULL_REQUEST_REF = re.compile(r"refs/pull/([1-9]\d*)/merge")
SQUASH_SUBJECT = re.compile(r"\(#([1-9]\d*)\)$")
AREAS = frozenset({"src", "tests", "scripts"})
FIX_FLAGS = frozenset({"--fix", "--fix-only", "--unsafe-fixes", "--exit-non-zero-on-fix", "--show-fixes"})
IGNORE_FLAGS = frozenset({"--ignore", "--extend-ignore"})
RAW_FLAGS = (
    "--ignore-noqa",
    "--select",
    "ALL",
    "--preview",
    "--config",
    "lint.explicit-preview-rules = false",
)
PREVIEW_FLAGS = frozenset({"--preview", "--no-preview"})
UNCHECKED = ("tests/data/",)
HOOK_UNREADABLE_FLAGS = frozenset({"--select", "--config"})
AREA_GLOB = re.compile(r"(?:src|tests|scripts)/(?:\*\*/\*|\*\*|\*)(?:\.pyi?)?")
CHUNK = 200
PRAGMA = re.compile(r"\bpragma:\s*(no\s+cover|no\s+branch|\S+(?:\s+no)?\s+cover)\b")
PRAGMA_KINDS = {"no cover": "no-cover", "no branch": "no-branch"}
TYPE_IGNORE = re.compile(r"\b(ty|pyright|type):\s*ignore\b(?:\[([^\]]*)\])?")
HEKI_IGNORE = re.compile(r"\bheki:\s*ignore\b(?!\[[^\]]*$)(?:\[([^\]]*)\])?", re.IGNORECASE)
HEKI_NOQA = re.compile(r"#\s*noqa(?![\w-])(?::(.*))?", re.IGNORECASE)
HEKI_RULES = {
    "cached-property-stale-mutation": "MUT101",
    "summary-effect-regression": "MUT103",
    "iterator-double-consumption": "MUT106",
    "hash-attr-mutation": "MUT107",
    "readonly-type-mutation": "MUT108",
    "write-without-invalidation": "MUT109",
    "readonly-param-mutation": "MUT110",
    "cached-property-stale-read": "MUT111",
    "conditional-ownership-mutation": "MUT112",
    "iterated-container-mutation": "MUT113",
    "cached-result-mutation": "MUT114",
    "write-owner-violation": "MUT201",
    "readonly-return-mutation": "MUT202",
    "read-owner-violation": "MUT203",
    "call-owner-violation": "MUT204",
    "unresolved-guarded-write": "MUT205",
    "unresolved-guarded-read": "MUT206",
    "unresolved-guarded-call": "MUT207",
    "async-blocking-call": "EFF101",
    "async-possibly-blocking-call": "EFF111",
    "loop-unpooled-client": "EFF102",
    "loop-db-query": "EFF103",
    "loop-possible-db-query": "EFF113",
    "async-blocking-fs": "EFF105",
    "lock-or-tx-held-across-io": "EFF106",
    "lock-reacquired-while-held": "EFF109",
    "exclusive-object-fanout": "EFF110",
    "unretained-async-task": "EFF107",
    "effect-boundary-violation": "EFF201",
    "loop-carried-concat": "DATA101",
    "list-quadratic-concat": "OPT101",
    "set-dict-quadratic-merge": "OPT102",
    "immutable-quadratic-concat": "OPT103",
    "loop-linear-membership": "OPT110",
    "loop-linear-dedup": "OPT111",
    "loop-linear-list-lookup": "OPT112",
    "nested-loop-equijoin": "OPT113",
    "list-front-pop": "OPT120",
    "list-front-insert": "OPT121",
    "loop-invariant-container-rebuild": "OPT130",
}
"""The rules of heki 0.2.0 by name (``heki rule --all``): a suppression may name a rule by its code or its name."""
HEKI_CODE = re.compile(r"(?:EFF|MUT|DATA|OPT)\d{3}", re.IGNORECASE)
COMMENT_HINT = re.compile(r"pragma:|ignore|noqa", re.IGNORECASE)
CALL_HINT = re.compile(
    r"cast|attr\(|attrgetter|__getattribute__|import_module|__import__|filterwarnings|simplefilter|catch_warnings"
    r"|abnormal_path|no_type_check|\bAny\b"
)
PRIVATE_READERS = {"getattr": 1, "setattr": 1, "hasattr": 1, "delattr": 1, "attrgetter": 0, "__getattribute__": 1}
WARNING_FILTERS = frozenset({"filterwarnings", "simplefilter", "catch_warnings"})
MARKERS = {"abnormal_path": "abnormal-path", "no_type_check": "no-type-check"}
SCOPE_WALK_FORMS = frozenset({
    "Name",
    "Store",
    "Del",
    "Global",
    "Nonlocal",
    "NamedExpr",
    "MatchAs",
    "MatchStar",
    "MatchMapping",
    "ExceptHandler",
    "arg",
    "arguments",
    "alias",
    "comprehension",
    "Lambda",
    "ListComp",
    "SetComp",
    "DictComp",
    "GeneratorExp",
    "Import",
    "ImportFrom",
    "FunctionDef",
    "AsyncFunctionDef",
    "ClassDef",
    "Assign",
    "AnnAssign",
    "AugAssign",
    "For",
    "AsyncFor",
    "With",
    "AsyncWith",
    "withitem",
})
"""The `ast` classes of bindings, scopes and imports, which `scripts/_source_model.py` reads for every check."""
SCOPE_WALK_HINT = re.compile(r"\b(?:ast|symtable|tokenize)\b")
SCOPE_WALK_AREAS = frozenset({"scripts", "tests"})
SOURCE_MODEL_HOME = frozenset({"scripts/_source_model.py", "tests/test_source_model.py"})
"""The model and the test of its answers, the one place that reads these forms itself."""
SOURCE_RULES = tuple(rule for rule in RULES if not rule.needs_base)
"""The source rules counted on every tree, under ``source-rule:<rule>``."""
REPORTED_RULES = tuple(rule for rule in RULES if rule.needs_base)
"""The base-relative source rules: their sites are candidates, printed by a run with a base and never failing it."""
ABSOLUTE_KEYS = frozenset(f"source-rule:{rule.name}" for rule in SOURCE_RULES if rule.absolute)
"""The keys of the head's absolute rules; a run judges with these and the base revision's (`_absolute_keys`)."""
ABSOLUTE_RULES_OF_TREE = (
    "import json, sys; sys.path.insert(0, sys.argv[1]); from scripts.check_source_rules import RULES; "
    "print(json.dumps([rule.name for rule in RULES if rule.absolute and not rule.needs_base]))"
)
"""The program that prints the absolute rules of the tree given as its argument, with that tree's own rule table."""
ALLOWLISTS = (
    (
        "tests/assert_helper_allowlists.py",
        frozenset({
            "JUSTIFIED_VIOLATIONS",
            "FROZEN_VIOLATIONS",
            "LEGACY_VIOLATIONS",
            "FROZEN_EXEMPT_FILES",
            "FROZEN_UNCOVERED_FILES",
        }),
    ),
    ("tests/generated_import_closure_allowlist.py", frozenset({"UNBOUND_NAME_VIOLATIONS"})),
    ("tests/decision_home_allowlist.py", frozenset({"UNLISTED_CALLERS", "BUILDER_BYPASSES"})),
    ("scripts/check_architecture_boundaries.py", frozenset({"DEFAULT_ALLOWLIST"})),
    ("tests/test_checks_are_run.py", frozenset({"UNREACHED"})),
)
PLAIN_COMMENT_AREAS = frozenset({"src"})
"""The areas whose plain comments are counted."""
TODO_MARKER = re.compile(r"\b(?:TODO|FIXME|XXX|HACK)\b")
COMMENT_DIRECTIVE = re.compile(
    r"^#(?:!|\s*-\*-|\s*(?:vim?|coding)[:=]|\s*(?:type|ty|pyright|mypy|pylint|isort|fmt)\s*:)", re.IGNORECASE
)
"""A comment that starts as a tool directive (a shebang, an encoding line, ``type:``, ``fmt:``, ...), no reader.

Comments the source model reports as suppressions (``noqa``, ``pragma``, ``ruff:``, type ignores) are directives too.
"""
REPORT_ONLY = frozenset({"expected"})
"""The kinds of keys that are reported in the table and never fail."""
ADVICE = {
    "ruff": (
        "Fix the finding instead of suppressing it. A noqa comment, a `ruff: ignore` comment and a per-file ignore "
        "count the same, so moving one changes nothing; if one must stay, remove another suppression of this rule in "
        "the same area."
    ),
    "ruff-config": (
        "A project-wide ignore or exclusion hides every present and future finding: fix the findings, or suppress "
        "each where it occurs (counted per finding)."
    ),
    "pragma": (
        "Reach the line with a test, or delete the code if no input reaches it; if the pragma must stay, remove "
        "another one in the same area."
    ),
    "comment:todo": (
        "Open an issue for the work and delete the marker, or do the work now: a to-do in the source is a defect "
        "nobody tracks."
    ),
    "comment": "Say it in the docstring or fix it: a comment that only explains a stopgap hides the stopgap.",
    "ty-ignore": "Fix the type error or the annotation instead of ignoring it.",
    "pyright-ignore": "pyright checks only generated packages: drop the comment outside `_runtime`, or fix the type.",
    "type-ignore": "Fix the type error or the annotation instead of ignoring it.",
    "heki-ignore": "Fix the finding or the contract instead of silencing heki on the line; the baseline only shrinks.",
    "allowlist:heki-baseline:EFF201": ENVIRONMENT_ADVICE,
    "cast": "Narrow the type with isinstance, an annotation or a TypeGuard instead of cast().",
    "any": "Name the type (a Protocol, a TypeVar, object or a union) instead of Any.",
    "no-type-check": "Fix the annotations instead of switching the type checker off for the function.",
    "getattr-private": "A private name read through getattr() hides it from SLF001: give its owner a method instead.",
    "dynamic-import": (
        "importlib and __import__ hide an import from the lint, and a computed module name hides it from the "
        "import-cycle keys: import it by name."
    ),
    "import-cycle": (
        "The lines are each module's first import into its cycle. Move what the modules of the cycle share into a "
        "module that imports none of them; importing back inside a function or under TYPE_CHECKING still counts for "
        "the lazy keys, and only the module-level key ignores it."
    ),
    "filterwarnings-ignore": "Fix the warning's cause, or assert it with pytest.warns, instead of ignoring it.",
    "abnormal-path": "Reach the path end to end through the public entry points instead of marking it abnormal.",
    "private-scope-walk": (
        "Read bindings, references, imports, suppressions and extents through scripts/_source_model.py; add a form "
        "to tests/data/source_forms/ when it lacks one."
    ),
    "named-stored-output": (
        "Name no stored output: call the assert helper without an expected path or directory, run the test and commit "
        "what pytest writes (AGENTS.md, Tests); `name=` for a second output stays inside the derived name. A path, a "
        "case list or a helper that makes the stored name is the same thing."
    ),
    "coverage-config": "An omitted or excluded file is never measured: reach it with tests instead.",
    "ty-config": "A ty rule set below error hides its findings: fix them and keep the rule an error.",
    "pytest-config": "Fix the warning's cause, or assert it with pytest.warns, instead of filtering it for all tests.",
    "store-allowlist": (
        "Route the write through the generation store's mutation APIs (AGENTS.md) instead of listing it."
    ),
    "allowlist": "Allowlist entries only shrink: fix the violation instead of listing it.",
    STORED_LIST: (
        "A stored list of known gaps only shrinks: the new line is a regression of the generated output (or, for a "
        "blind line, an item whose checks no longer run). Fix the change or file the defect; never store the line "
        "with the fix command the test prints."
    ),
    "allowlist:UNREACHED": (
        "Every check runs on the tree: run the new check from a workflow step, a tox environment a workflow runs, a "
        "hook entry, the ratchet or a test that asserts its exit status on the repository tree instead of listing it "
        "as unreached."
    ),
    **{
        f"source-rule:{rule.name}": (
            f"{rule.fix[:1].upper()}{rule.fix[1:]}; `python scripts/check_source_rules.py --rule {rule.name}` prints "
            "each site with its detail."
        )
        for rule in SOURCE_RULES
    },
    "allowlist:host-private-access": (
        "Host baseline entries only shrink: declare the member on the Host Protocol the function takes, "
        "or move the code into the class."
    ),
    "unreadable": (
        "Write the ruff hook's `args:` and `exclude:` as inline values or a block list, without --select or --config "
        "(put those in pyproject.toml), so that the counter can read them."
    ),
    "unreadable-stored-list": (
        "Store the list as a JSON list of strings at the storage its `.refs` line names (pytest writes both), so "
        "that the counter can read it."
    ),
    "unreadable-source-rule": (
        "The source model refuses the file (the error is named), so no source rule can read its sites. Fix the file, "
        "or give the model a rule and a form for it (scripts/_source_model.py, tests/data/source_forms/)."
    ),
    "unreadable-allowlist": (
        "Write the allowlist or baseline in the plain form its checker reads (literal entries, tables of integer "
        "counts), so that the counter can read it."
    ),
}


@dataclass
class Tally:
    """The sites of every key: key -> file -> line numbers (0 where an entry has no line)."""

    sites: defaultdict[str, defaultdict[str, list[int]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(list))
    )

    def add(self, key: str, path: str, line: int = 0) -> None:
        """Count one hatch of ``key`` in ``path``."""
        self.sites[key][path].append(line)

    def counts(self) -> dict[str, dict[str, int]]:
        """Return the number of hatches per key and file."""
        return {
            key: {path: len(lines) for path, lines in sorted(files.items())}
            for key, files in sorted(self.sites.items())
        }


@dataclass(frozen=True)
class Hook:
    """The ruff check hook of ``.pre-commit-config.yaml``."""

    rev: str = ""
    exclude: str = ""
    args: tuple[str, ...] = ()
    unreadable: tuple[str, ...] = ()


@dataclass(frozen=True)
class Approval:
    """An entry of the approvals table: ``key`` may rise up to ``head`` in pull request ``pr``."""

    key: str
    head: int
    pr: int
    decision: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class Approvals:
    """The approvals of the base revision's table and the pull requests that the head being judged is."""

    entries: tuple[Approval, ...] = ()
    pull_requests: frozenset[int] = frozenset()

    def covering(self, key: str, total: int) -> Approval | None:
        """Return the approval that lets ``key`` reach ``total`` in a judged pull request, or None."""
        return next(
            (
                entry
                for entry in self.entries
                if entry.key == key and entry.pr in self.pull_requests and total <= entry.head
            ),
            None,
        )


NO_APPROVALS = Approvals()


def _area(path: str) -> str:
    return head if (head := path.split("/", 1)[0]) in AREAS else "other"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)


def _tree_files(root: Path) -> list[str]:
    """List the files of a tree: tracked and untracked unignored files of a work tree, else every file."""
    top = _git(root, "rev-parse", "--show-toplevel")
    if top.returncode == 0 and Path(top.stdout.decode().strip()).resolve() == root.resolve():
        names = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout.decode().split("\0")
        return sorted({name for name in names if name and (root / name).is_file()})
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


def _yaml_value(text: str) -> object:
    """Read the one-line YAML scalars and flow lists the hook configuration uses."""
    value = text.split(" #", 1)[0].strip()
    if value.startswith(("'", '"', "[")):
        return json.loads(value.replace("'", '"')) if value.startswith("'") else json.loads(value)
    return value


def _block_list(lines: list[str], indent: int) -> list[str]:
    """Read the items of the block-style YAML list that follows a key indented by ``indent``."""
    items: list[str] = []
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.match(r"(\s*)-\s+(\S.*)", line)
        if not match or len(match[1]) < indent or re.match(r"[\w-]+:(\s|$)", match[2]):
            break
        items.append(str(_yaml_value(match[2])))
    return items


def _hook_value(text: str, following: list[str], indent: int) -> object:
    """Read an inline value or a block-style list; None where the form is neither."""
    inline = text.split(" #", 1)[0].strip()
    if inline[:1] in {"|", ">"}:
        return None
    try:
        return _yaml_value(inline) if inline else _block_list(following, indent) or None
    except ValueError:
        return None


def _ruff_hook(root: Path) -> Hook:
    """Read the rev, exclude pattern and arguments of the ruff check hook, and what it could not read."""
    path = root / ".pre-commit-config.yaml"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = text.splitlines()
    repo = hook = rev = ""
    values: dict[str, object] = {}
    unreadable: list[str] = []
    for number, line in enumerate(lines):
        if match := re.match(r"\s*-\s*repo:\s*(\S+)", line):
            repo, hook = match[1], ""
        elif match := re.match(r"\s*rev:\s*(.+)", line):
            rev = str(_yaml_value(match[1])) if "ruff-pre-commit" in repo else rev
        elif match := re.match(r"\s*-\s*id:\s*(\S+)", line):
            hook = str(_yaml_value(match[1]))
        elif (match := re.match(r"(\s*)(exclude|args):\s*(.*)", line)) and "ruff-pre-commit" in repo and hook == "ruff":
            value = _hook_value(match[3], lines[number + 1 :], len(match[1]))
            if isinstance(value, list if match[2] == "args" else str):
                values[match[2]] = value
            else:
                unreadable.append(f"hook-{match[2]}")
    args = tuple(map(str, values.get("args", [])))
    unreadable += [
        f"hook-arg:{flag}" for flag in sorted(HOOK_UNREADABLE_FLAGS & {arg.partition("=")[0] for arg in args})
    ]
    return Hook(rev=rev, exclude=str(values.get("exclude", "")), args=args, unreadable=tuple(unreadable))


def _split_ignores(args: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Split hook arguments into the rules they ignore and the other arguments, without the fix flags."""
    ignored: list[str] = []
    rest: list[str] = []
    remaining = iter(args)
    for arg in remaining:
        flag, separator, inline = arg.partition("=")
        if flag in IGNORE_FLAGS:
            ignored += (inline if separator else next(remaining, "")).split(",")
        elif arg not in FIX_FLAGS:
            rest.append(arg)
    return [rule for rule in ignored if rule], rest


def _ignore_flags(rules: list[str]) -> list[str]:
    return ["--extend-ignore", ",".join(rules)] if rules else []


def _ruff_json(ruff: list[str], root: Path, flags: list[str], files: list[str]) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for start in range(0, len(files), CHUNK):
        command = [
            *ruff,
            "check",
            "--no-cache",
            "--exit-zero",
            "--output-format",
            "json",
            "--force-exclude",
            *flags,
            "--no-fix",
        ]
        output = subprocess.run(
            [*command, *files[start : start + CHUNK]], cwd=root, capture_output=True, text=True, check=True
        )
        findings.extend(json.loads(output.stdout))
    return findings


def _toml(value: object) -> str:
    """Write a value read from a TOML file back as TOML: JSON scalars and lists, inline tables, ISO dates and times."""
    if isinstance(value, dict):
        return "{" + ", ".join(f"{json.dumps(key)} = {_toml(item)}" for key, item in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(map(_toml, value)) + "]"
    return value.isoformat() if isinstance(value, (dt.date, dt.time)) else json.dumps(value)


def _without_extended_ignores(config: dict, root: Path) -> dict:
    """Return the ruff settings without ``extend-per-file-ignores``, which no override can clear: it is additive."""
    lint = {key: value for key, value in config.get("lint", {}).items() if key != "extend-per-file-ignores"}
    kept = {key: value for key, value in config.items() if key not in {"extend-per-file-ignores", "lint"}}
    if "extend" in kept:
        kept["extend"] = str(root / kept["extend"])
    return {**kept, "lint": lint}


def _relative(root: Path, name: str) -> str:
    return Path(name).resolve().relative_to(root.resolve()).as_posix()


def _rule_names(ruff: list[str]) -> dict[str, str]:
    """Map each rule name to its code, so a rule counts once however a configuration names it."""
    output = subprocess.run([*ruff, "rule", "--all", "--output-format", "json"], capture_output=True, check=True)
    return {rule["name"]: rule["code"] for rule in json.loads(output.stdout) if rule.get("code")}


def _ruff_tally(tally: Tally, root: Path, files: list[str], ruff: list[str], pyproject: dict) -> None:
    """Count the findings suppressions hide, the project-wide ignores and the paths ruff does not check."""
    hook = _ruff_hook(root)
    hook_ignores, hook_args = _split_ignores(hook.args)
    candidates = [
        name
        for name in files
        if name.endswith((".py", ".pyi")) and not (hook.exclude and re.search(hook.exclude, name))
    ]
    checked: list[str] = []
    for start in range(0, len(candidates), CHUNK):
        listed = subprocess.run(
            [*ruff, "check", "--show-files", "--force-exclude", *hook_args, *candidates[start : start + CHUNK]],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        checked.extend(_relative(root, line) for line in listed.stdout.splitlines() if line.strip())
    checked.sort()
    config = pyproject.get("tool", {}).get("ruff", {})
    lint = config.get("lint", {})
    project_ignores = [*lint.get("ignore", []), *lint.get("extend-ignore", []), *config.get("ignore", [])]
    area_policies: dict[str, list[str]] = defaultdict(list)
    for table in (lint.get("per-file-ignores", {}), lint.get("extend-per-file-ignores", {})):
        for glob, rules in table.items():
            area_policies[glob] += rules if AREA_GLOB.fullmatch(glob) else []
    names = _rule_names(ruff)
    for source, selectors in (("pyproject.toml", project_ignores), (".pre-commit-config.yaml", hook_ignores)):
        for selector in selectors:
            tally.add(f"ruff-config:ignore:{names.get(selector, selector)}", source)
    for glob, rules in area_policies.items():
        for rule in rules:
            tally.add(f"ruff-config:per-file:{glob}:{names.get(rule, rule)}", "pyproject.toml")
    for entry in (*config.get("exclude", []), *config.get("extend-exclude", [])):
        tally.add(f"ruff-config:exclude:{entry}", "pyproject.toml")
    for pattern in filter(None, re.split(r"\|(?![^()]*\))", hook.exclude)):
        tally.add(f"ruff-config:hook-exclude:{pattern}", ".pre-commit-config.yaml")
    for reason in hook.unreadable:
        tally.add(f"unreadable:{reason}", ".pre-commit-config.yaml")
    policy = [*project_ignores, *hook_ignores]
    kept = ", ".join(f"{json.dumps(glob)} = {json.dumps(rules)}" for glob, rules in area_policies.items() if rules)
    raw_flags = [*RAW_FLAGS, "--config", f"lint.per-file-ignores = {{{kept}}}"]
    raw_flags += [arg for arg in hook_args if arg not in PREVIEW_FLAGS]
    with tempfile.TemporaryDirectory() as directory:
        if config.get("extend-per-file-ignores") or lint.get("extend-per-file-ignores"):
            settings = Path(directory) / "ruff.toml"
            settings.write_text(
                "\n".join(
                    f"{json.dumps(key)} = {_toml(value)}"
                    for key, value in _without_extended_ignores(config, root).items()
                ),
                encoding="utf-8",
            )
            raw_flags += ["--config", str(settings)]
        raw = _ruff_json(ruff, root, [*raw_flags, *_ignore_flags(policy)], checked)
    normal = _ruff_json(ruff, root, [*hook_args, *_ignore_flags(hook_ignores)], checked)

    def sites(findings: list[dict[str, object]]) -> Counter[tuple[str, str, int]]:
        return Counter(
            (_relative(root, str(item["filename"])), str(item["code"] or item["name"]), item["location"]["row"])
            for item in findings
        )

    for path, code, line in sorted((sites(raw) - sites(normal)).elements()):
        tally.add(f"ruff:{code}@{_area(path)}", path, line)


def _heki_rule(word: str) -> str:
    """Return the code of a heki rule named by its code or its name, else the word as written."""
    return word.upper() if HEKI_CODE.fullmatch(word) else HEKI_RULES.get(word.lower(), word)


def _comment_keys(text: str) -> list[str]:
    pragmas = [
        f"pragma:{PRAGMA_KINDS.get(' '.join(match[1].split()), 'conditional-cover')}" for match in PRAGMA.finditer(text)
    ]
    ignores = [
        f"{match[1]}-ignore:{rule.strip() or 'BARE'}"
        for match in TYPE_IGNORE.finditer(text)
        for rule in (match[2] or "").split(",")
    ]
    heki: list[str] = []
    for match in HEKI_IGNORE.finditer(text):
        listed = None if match[1] is None else [rule.strip() for rule in match[1].split(",") if rule.strip()]
        heki += ["heki-ignore:BARE"] if listed is None else [f"heki-ignore:{_heki_rule(rule)}" for rule in listed]
    for match in HEKI_NOQA.finditer(text):
        words = re.findall(r"[\w-]+", match[1] or "")
        rules = [rule for word in words if HEKI_CODE.fullmatch(rule := _heki_rule(word))]
        heki += [f"heki-ignore:{rule}" for rule in rules] if words else ["heki-ignore:BARE"]
    return [*pragmas, *ignores, *heki]


def _private_name(node: ast.expr) -> bool:
    if isinstance(node, ast.JoinedStr):
        node = node.values[0] if node.values else node
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("_")
        and not node.value.endswith("__")
    )


def _ignores_warnings(node: ast.Call) -> bool:
    actions = [*node.args[:1], *(keyword.value for keyword in node.keywords if keyword.arg == "action")]
    return any(
        isinstance(action, ast.Constant)
        and isinstance(action.value, str)
        and (action.value == "ignore" or action.value.startswith("ignore:"))
        for action in actions
    )


def _name(node: ast.AST) -> str:
    return node.id if isinstance(node, ast.Name) else getattr(node, "attr", "")


def _type_positions(node: ast.AST) -> list[ast.AST]:
    """Return the parts of an annotation that are types, not ``Literal`` values or ``Annotated`` metadata."""
    if not isinstance(node, ast.Subscript):
        return list(ast.iter_child_nodes(node))
    kind = _name(node.value)
    if kind == "Literal":
        return [node.value]
    if kind == "Annotated" and isinstance(node.slice, ast.Tuple):
        return [node.value, *node.slice.elts[:1]]
    return list(ast.iter_child_nodes(node))


def _any_lines(node: ast.AST) -> list[int]:
    """Return the lines of the ``Any`` names in an annotation, string annotations included."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            inner = ast.parse(node.value.strip(), mode="eval").body
        except (SyntaxError, ValueError):
            return []
        return [node.lineno] * len(_any_lines(inner))
    lines = [node.lineno] if _name(node) == "Any" else []
    return lines + [line for part in _type_positions(node) for line in _any_lines(part)]


def _annotation(node: ast.AST) -> ast.expr | None:
    if isinstance(node, (ast.arg, ast.AnnAssign)):
        return node.annotation
    return node.returns if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None


def _node_sites(node: ast.AST, casts: set[str]) -> list[tuple[str, int]]:
    """Return the (key, line) hatches of one node: calls, markers and ``Any`` in annotations."""
    line = getattr(node, "lineno", 0)
    annotation = _annotation(node)
    sites = [("any", row) for row in _any_lines(annotation)] if annotation else []
    if isinstance(node, (ast.Name, ast.Attribute)) and _name(node) in MARKERS:
        sites.append((MARKERS[_name(node)], line))
    if not isinstance(node, ast.Call):
        return sites
    name = _name(node.func)
    index = PRIVATE_READERS.get(name, -1)
    keys = {
        "cast": name in casts,
        "getattr-private": 0 <= index < len(node.args) and _private_name(node.args[index]),
        "dynamic-import": name in {"import_module", "__import__"},
        "filterwarnings-ignore": name in WARNING_FILTERS and _ignores_warnings(node),
    }
    sites += [(key, line) for key, hit in keys.items() if hit]
    if name in casts and node.args:
        sites += [("any", row) for row in _any_lines(node.args[0])]
    return sites


def _source_sites(data: bytes) -> list[tuple[str, int]]:
    """Return the hatch comments (by tokenize) and hatch calls (by ast) of one file, none where it does not parse."""
    text = data.decode("utf-8", "replace")
    try:
        comments = (
            [token for token in tokenize.tokenize(io.BytesIO(data).readline) if token.type == tokenize.COMMENT]
            if COMMENT_HINT.search(text)
            else []
        )
        nodes = list(ast.walk(ast.parse(data))) if CALL_HINT.search(text) else []
    except (SyntaxError, tokenize.TokenError):
        return []
    casts = {"cast"}
    casts.update(
        alias.asname
        for node in nodes
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name == "cast" and alias.asname
    )
    return [
        *((key, token.start[0]) for token in comments for key in _comment_keys(token.string)),
        *(site for node in nodes for site in _node_sites(node, casts)),
    ]


def scope_walk_sites(text: str, path: str) -> list[int]:
    """Return the lines where a module reads bindings, scopes, imports or comments itself, not through the model.

    Read by origin through `scripts/_source_model.py`: a load of one of `SCOPE_WALK_FORMS` from `ast` that is not the
    callee of a call (building a node reads nothing), a ``visit_<form>`` method, a ``symtable`` read and
    ``tokenize.COMMENT``; ``import ast as a; a.Name`` counts like ``ast.Name``. None where the module does not parse.
    """
    try:
        model = SourceModule(text, path)
    except (SyntaxError, ValueError):
        return []
    nodes = list(ast.walk(model.tree))
    callees = {id(node.func) for node in nodes if isinstance(node, ast.Call)}
    chained = {id(node.value) for node in nodes if isinstance(node, ast.Attribute)}
    loads = [reference.node for reference in model.references if reference.context == "load"]
    loads += [node for node in nodes if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)]
    lines = []
    for node in loads:
        head, _, form = (model.origin(node) or "").partition(".")
        node_class = head == "ast" and form in SCOPE_WALK_FORMS and id(node) not in callees
        reads = node_class or (head == "symtable" and bool(form)) or (head, form) == ("tokenize", "COMMENT")
        lines += [node.lineno] if reads and id(node) not in chained else []
    visitors = [
        binding.line
        for binding in model.bindings()
        if binding.kind == "def"
        and binding.scope.kind == "class"
        and binding.name.startswith("visit_")
        and binding.name.removeprefix("visit_") in SCOPE_WALK_FORMS
    ]
    return sorted([*lines, *visitors])


def comment_sites(model: SourceModule) -> tuple[list[int], list[int]]:
    """Return the lines of the plain comments and of the to-do comments of a module (directives are neither)."""
    directives = {item.line for item in model.suppressions()}
    plain: list[int] = []
    todo: list[int] = []
    for line, comment in sorted(model.comments.items()):
        if TODO_MARKER.search(comment):
            todo.append(line)
        elif line not in directives and not COMMENT_DIRECTIVE.match(comment):
            plain.append(line)
    return plain, todo


def _comment_tally(tally: Tally, root: Path, files: list[str]) -> None:
    """Count the plain and to-do comments of the package, through the source model."""
    for path in files:
        if not path.endswith((".py", ".pyi")) or path.startswith(UNCHECKED) or _area(path) not in PLAIN_COMMENT_AREAS:
            continue
        try:
            model = SourceModule((root / path).read_text(encoding="utf-8", errors="replace"), path)
            plain, todo = comment_sites(model)
        except (SyntaxError, ValueError):
            continue
        for line in plain:
            tally.add(f"comment:plain@{_area(path)}", path, line)
        for line in todo:
            tally.add("comment:todo", path, line)


def _expected_tally(tally: Tally, root: Path) -> None:
    """Count the ``.refs`` lines with an environment predicate and the conditional ones, per sidecar (report only)."""
    tests_root = root / TESTS_ROOT
    for sidecar, lines in RefsIndex(tests_root).all_lines().items():
        for line in lines:
            if line.predicate:
                tally.add("expected:env-override", sidecar.relative_to(root).as_posix())
            if line.conditional:
                tally.add("expected:conditional", sidecar.relative_to(root).as_posix())


def _source_tally(tally: Tally, root: Path, files: list[str]) -> None:
    """Count the hatch comments and calls of every Python file, whether ruff checks it or not.

    The scope walks (`scope_walk_sites`) are counted in ``scripts`` and ``tests``, outside the model's own files.
    """
    for path in files:
        if path.endswith((".py", ".pyi")) and not path.startswith(UNCHECKED):
            data = (root / path).read_bytes()
            for key, line in _source_sites(data):
                tally.add(f"{key}@{_area(path)}", path, line)
            text = data.decode("utf-8", "replace")
            walked = _area(path) in SCOPE_WALK_AREAS and path not in SOURCE_MODEL_HOME and SCOPE_WALK_HINT.search(text)
            for line in scope_walk_sites(text, path) if walked else []:
                tally.add(f"private-scope-walk@{_area(path)}", path, line)


def _ruff_files(tally: Tally, root: Path, files: list[str]) -> None:
    """Count every ruff configuration file other than the root ``pyproject.toml``."""
    for path in files:
        name = path.rsplit("/", 1)[-1]
        nested = name == "pyproject.toml" and "/" in path and b"[tool.ruff" in (root / path).read_bytes()
        if (name in {"ruff.toml", ".ruff.toml"} or nested) and not path.startswith(UNCHECKED):
            tally.add(f"ruff-config:file:{path}", path)


def _is_one_entry(value: ast.expr) -> bool:
    """Return whether a dict value is a constant or a record built by a class (``Entry(...)``): one entry, not many."""
    if isinstance(value, ast.Call):
        callee = ast.unparse(value.func)
        return callee.isidentifier() and callee[:1].isupper()
    return isinstance(value, ast.Constant)


def _entries(node: ast.expr) -> list[ast.expr] | None:
    """Return the entries of an allowlist literal, or None where it is not one this script can read.

    The entries are the elements, or the members of a dict's collection values, or the keys of a dict whose values are
    all constants or records (``Entry(...)``). A dict with any other value (``tuple([...])``, ``a + b``, a name) is
    not readable: its members could be counted only by running it, and counting its keys would hide them.
    """
    value = node.args[0] if isinstance(node, ast.Call) and node.args else node
    if isinstance(value, ast.Dict):
        groups = [item.elts for item in value.values if isinstance(item, (ast.Tuple, ast.List, ast.Set))]
        if len(groups) == len(value.values):
            return [entry for group in groups for entry in group]
        if all(_is_one_entry(item) for item in value.values):
            return [key for key in value.keys if key is not None]
        return None
    return list(value.elts) if isinstance(value, (ast.Tuple, ast.List, ast.Set)) else None


def _listed_name(node: ast.AST | None, names: frozenset[str]) -> str:
    """Return the listed name a target or owner expression is, or subscripts (``X["g"]``), or an empty string."""
    while isinstance(node, ast.Subscript):
        node = node.value
    return node.id if isinstance(node, ast.Name) and node.id in names else ""


def _method_owner(statement: ast.stmt, names: frozenset[str]) -> str:
    """Return the listed name a statement calls a method on (``update``, ``add``, ...), or an empty string."""
    call = statement.value if isinstance(statement, ast.Expr) else None
    owner = call.func.value if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) else None
    return _listed_name(owner, names)


def _toml_leaves(root: Path, path: str) -> list[object]:
    """Return the values of an allowlist file of ``["file"."function"]`` tables, or [] where there is none."""
    file = root / path
    tables = tomllib.loads(file.read_text(encoding="utf-8")) if file.is_file() else {}
    return [value for table in tables.values() for entry in table.values() for value in entry.values()]


def _host_baseline_tally(tally: Tally, root: Path) -> None:
    """Count the accesses of the host baseline; a file that is not tables of integer counts is unreadable."""
    try:
        counts = _toml_leaves(root, HOST_BASELINE)
    except (tomllib.TOMLDecodeError, AttributeError):
        counts = [None]
    for count in counts:
        key = "allowlist:host-private-access" + ("" if type(count) is int else ":unreadable")
        for _ in range(count if type(count) is int else 1):
            tally.add(key, HOST_BASELINE)


def _heki_baseline_tally(tally: Tally, root: Path) -> None:
    """Count the sites of heki's baseline, those of `HEKI_OWN_KEY_RULES` under a key per rule.

    A file that is not entries with integer counts is unreadable.
    """
    file = root / HEKI_BASELINE
    try:
        entries = tomllib.loads(file.read_text(encoding="utf-8")).get("entries", []) if file.is_file() else []
        counted = [(entry.get("count"), entry.get("rule_code")) for entry in entries]
    except (tomllib.TOMLDecodeError, AttributeError, TypeError):
        counted = [(None, None)]
    for count, rule in counted:
        own = f":{rule}" if rule in HEKI_OWN_KEY_RULES else ""
        key = "allowlist:heki-baseline" + (own if type(count) is int else ":unreadable")
        for _ in range(count if type(count) is int else 1):
            tally.add(key, HEKI_BASELINE)


def _config_tally(tally: Tally, root: Path, pyproject: dict) -> None:
    """Count the configuration entries and allowlist entries that switch a check off."""
    tool = pyproject.get("tool", {})
    coverage = tool.get("coverage", {})
    for section, name, key in (
        ("run", "omit", "omit"),
        ("report", "omit", "omit"),
        ("report", "exclude_lines", "exclude"),
        ("report", "exclude_also", "exclude"),
    ):
        for entry in coverage.get(section, {}).get(name, []):
            tally.add(f"coverage-config:{key}:{entry}", "pyproject.toml")
    ty = tool.get("ty", {})
    for rules in (ty.get("rules", {}), *(override.get("rules", {}) for override in ty.get("overrides", []))):
        for rule in (rule for rule, level in rules.items() if level in {"ignore", "warn"}):
            tally.add(f"ty-config:{rule}", "pyproject.toml")
    pytest = tool.get("pytest", {})
    filters = [*pytest.get("filterwarnings", []), *pytest.get("ini_options", {}).get("filterwarnings", [])]
    for _ in (entry for entry in filters if str(entry).startswith("ignore")):
        tally.add("pytest-config:filterwarnings-ignore", "pyproject.toml")
    for _ in pytest.get("ini_options", {}).get("assert_helper_direct_assert_exempt_files", []):
        tally.add("allowlist:direct-assert-exempt-files", "pyproject.toml")
    _host_baseline_tally(tally, root)
    _heki_baseline_tally(tally, root)
    for value in _toml_leaves(root, STORE_ALLOWLIST):
        counted = isinstance(value, int)
        for _ in range(value if counted else 1):
            tally.add(f"store-allowlist:{'counted' if counted else 'witness'}", STORE_ALLOWLIST)
    for path, names in ALLOWLISTS:
        module = (
            ast.parse((root / path).read_bytes()) if (root / path).is_file() else ast.Module(body=[], type_ignores=[])
        )
        for statement in module.body:
            target = getattr(statement, "target", None) or next(iter(getattr(statement, "targets", [])), None)
            value = getattr(statement, "value", None)
            if owner := _method_owner(statement, names):
                tally.add(f"allowlist:{owner}:unreadable", path, statement.lineno)
            elif (listed := _listed_name(target, names)) and value is not None:
                entries = _entries(value)
                for entry in entries or []:
                    tally.add(f"allowlist:{listed}", path, entry.lineno)
                if entries is None:
                    tally.add(f"allowlist:{listed}:unreadable", path, statement.lineno)


def _stored_lines(path: Path) -> list[str] | None:
    """Return a stored list of strings, or None where the file is missing or holds anything else."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, list) and all(isinstance(line, str) for line in value) else None


def _stored_list_tally(tally: Tally, root: Path) -> None:
    """Count the items and lines of the stored lists, reading each record's storage through its ``.refs`` file.

    Only the records of the test's own list count, the output the harness names after the item with no role: the
    other outputs it stores for an item under a role, such as the ``warnings`` of its generation, are no list of gaps.
    """
    for sidecar, test in STORED_LISTS:
        path = root / sidecar
        stem = PurePosixPath(sidecar).stem.removeprefix("test_")
        siblings = derived_siblings(test, _module_tests(root, path.with_suffix(".py")))
        section = ""
        for raw in path.read_text(encoding="utf-8").splitlines() if path.is_file() else []:
            if raw.startswith("["):
                section = raw[1:-1]
            if section != test or raw.startswith(("[", "#")):
                continue
            item, label, *flags = raw.split("\t")
            if label.rpartition("/")[2] != derived_file_name(stem, test, item, siblings, suffix=".txt"):
                continue
            storage = next((flag.removeprefix("= ") for flag in flags if flag.startswith("= ")), label)
            environment = next((flag for flag in flags if flag.startswith("@")), "")
            tally.add(f"{STORED_LIST}:{test}:items", "\t".join(filter(None, (item, environment))))
            if (lines := _stored_lines(root / EXPECTED_ROOT / storage)) is None:
                tally.add(f"{STORED_LIST}:{test}:unreadable", f"{EXPECTED_ROOT}/{storage}")
            for line in lines or []:
                tally.add(
                    f"{STORED_LIST}:{test}:{'blind' if line in BLIND_LINES else 'lines'}",
                    "\t".join(filter(None, (item, line, environment))),
                )


def _module_tests(root: Path, module: Path) -> list[str]:
    """Return the tests a test module defines as nodeids show them: ``test*`` functions, in ``Test*`` classes too.

    A test a class inherits, or a module imports or assigns, is not seen (the harness counts it as a sibling): none
    exists under ``tests/`` today, and it would make a harness-written label count.
    """
    try:
        model = SourceModule(module.read_text(encoding="utf-8"), module.relative_to(root).as_posix())
    except (OSError, SyntaxError, ValueError):
        return []
    tests = []
    for binding in model.bindings():
        owners, scope = [], binding.scope
        while scope.kind == "class":
            owners.insert(0, scope.qualname.rpartition(".")[2])
            scope = scope.parent
        named = binding.kind == "def" and binding.name.startswith("test") and scope.kind == "module"
        if named and all(owner.startswith("Test") for owner in owners):
            tests.append("::".join([*owners, binding.name]))
    return tests


def _named_output_tally(tally: Tally, root: Path) -> None:
    """Count the stored outputs that a test names itself, from the ``.refs`` file of each test module.

    The files are the ones the harness reads (`RefsIndex.all_lines`), and a reference is derived where
    `is_derived_label` says the harness writes its label. A reference counts once per item and label, whatever its
    environment overrides and flags. The key leaves out what no ``.refs`` file records: a stored file read through
    ``stored_expected`` (it names another test's output), an output compared outside ``tests/data/expected`` (never
    stored by the harness), a hand-named label that equals the one the harness would derive, and the tests a module
    gets by inheritance, import or assignment (`_module_tests`).
    """
    tests_root = root / TESTS_ROOT
    for sidecar, lines in RefsIndex(tests_root).all_lines().items():
        module = PurePosixPath(sidecar.relative_to(tests_root).with_suffix(".py").as_posix())
        prefix = f"{module.stem.removeprefix('test_')}_"
        tests = (
            _module_tests(root, sidecar.with_suffix(".py"))
            if any(line.function.rpartition("::")[2].removeprefix("test_").startswith(prefix) for line in lines)
            else []
        )
        siblings: dict[str, set[str]] = {}
        for line in {(line.function, line.param, line.label): line for line in lines}.values():
            owner = line.function.rpartition("::")[0]
            if owner not in siblings:
                siblings[owner] = derived_siblings(line.function, tests)
            if not is_derived_label(module, line, siblings[owner]):
                tally.add("named-stored-output@tests", sidecar.relative_to(root).as_posix())


def _import_cycle_tally(tally: Tally, root: Path, files: list[str]) -> None:
    """Count the modules on the import cycles of the package and of ``scripts`` among the tree's files."""
    for key, found in import_cycles(root, files=files).items():
        for path, line in (site for cycle in found for site in cycle):
            tally.add(key, path, line)


def _source_rule_tally(tally: Tally, root: Path, files: list[str]) -> None:
    """Count the sites of every source rule that runs without a base, in the Python files of the tree."""
    tree = SourceTree(root, frozenset(files))
    findings = run_source_rules(tree, SOURCE_RULES, None)
    for finding in (finding for found in findings.values() for finding in found):
        tally.add(f"source-rule:{finding.rule}", finding.path, finding.line)
    for path, error in tree.unreadable().items():
        tally.add("source-rule:unreadable", f"{path} ({error})")


def _candidate_lines(base_root: Path, head_root: Path) -> list[str]:
    """List the candidates of the base-relative source rules for the head against the base; they never fail."""
    head = SourceTree(head_root, frozenset(_tree_files(head_root)))
    findings = run_source_rules(head, REPORTED_RULES, SourceTree(base_root))
    lines: list[str] = []
    for rule in REPORTED_RULES:
        if not (found := findings[rule.name]):
            continue
        lines += [
            "",
            f"source-rule:{rule.name}: {len(found)} candidates (report only, never fails). Check: {rule.fix}.",
        ]
        lines += [f"  {finding.path}:{finding.line}: {finding.detail}" for finding in found]
    return lines


def count_tree(root: Path, ruff: list[str]) -> Tally:
    """Count every escape hatch of the tree at ``root``."""
    path = root / "pyproject.toml"
    pyproject = tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    tally = Tally()
    files = _tree_files(root)
    _ruff_tally(tally, root, files, ruff, pyproject)
    _ruff_files(tally, root, files)
    _source_tally(tally, root, files)
    _comment_tally(tally, root, files)
    _expected_tally(tally, root)
    _config_tally(tally, root, pyproject)
    _stored_list_tally(tally, root)
    _named_output_tally(tally, root)
    _import_cycle_tally(tally, root, files)
    _source_rule_tally(tally, root, files)
    return tally


def _totals(counts: dict[str, dict[str, int]]) -> dict[str, int]:
    return {key: sum(files.values()) for key, files in counts.items()}


def _rises(
    base: dict[str, dict[str, int]], head: dict[str, dict[str, int]], approvals: Approvals, absolute: frozenset[str]
) -> list[str]:
    """Return the keys that rose, those of ``absolute`` whenever they hold a site, the others beyond their approval."""
    base_totals, head_totals = _totals(base), _totals(head)
    return [
        key
        for key, total in sorted(head_totals.items())
        if _kind(key) not in {"unreadable", *REPORT_ONLY}
        and (
            key in absolute
            or (
                (bool(_risen(key, base, head)) if _kind(key) == STORED_LIST else total > base_totals.get(key, 0))
                and not approvals.covering(key, total)
            )
        )
    ]


def _risen(key: str, base: dict[str, dict[str, int]], head: dict[str, dict[str, int]]) -> list[tuple[str, int, int]]:
    """Return the files (for a stored list, the lines) of a key whose count rose, with both counts."""
    if _kind(key) != STORED_LIST:
        return _file_rises(base.get(key, {}), head[key])
    test, _, part = key.rpartition(":")
    if part not in {"lines", "blind"}:
        return []
    base_lists, head_lists = _environment_lists(base, test), _environment_lists(head, test)
    new: list[tuple[str, int, int]] = []
    for item, head_records in sorted(head_lists.items()):
        if (base_records := base_lists.get(item)) is None:
            if part == "blind" and base_lists:
                new.extend((path, 0, 1) for path in _stored_paths(item, head_records, part))
            continue
        for environment in sorted(head_records.keys() | base_records.keys()):
            now, was = _record(head_records, environment), _record(base_records, environment)
            if part == "lines" and was["blind"]:
                continue
            new.extend((_stored_path(item, line, environment), 0, 1) for line in sorted(now[part] - was[part]))
    return new


_StoredRecord = dict[str, set[str]]
"""The ``lines`` and ``blind`` lines one record of a stored list holds."""


def _environment_lists(counts: dict[str, dict[str, int]], test: str) -> dict[str, dict[str, _StoredRecord]]:
    """Return the records of a stored list's items by environment: ``""`` for the default, ``@<predicate>`` otherwise.

    An environment's override is its own record, so a line that one environment's record gains is new even where
    another environment's record holds it.
    """
    lists: dict[str, dict[str, _StoredRecord]] = {}
    for path in counts.get(f"{test}:items", {}):
        item, _, environment = path.partition("\t")
        lists.setdefault(item, {})[environment] = {"lines": set(), "blind": set()}
    for part in ("lines", "blind"):
        for path in counts.get(f"{test}:{part}", {}):
            item, line, *environment = path.split("\t")
            records = lists.setdefault(item, {})
            records.setdefault("".join(environment), {"lines": set(), "blind": set()})[part].add(line)
    return lists


def _record(records: dict[str, _StoredRecord], environment: str) -> _StoredRecord:
    """Return the record an environment reads: its override, or the item's default record."""
    return records.get(environment) or records.get("") or {"lines": set(), "blind": set()}


def _stored_path(item: str, line: str, environment: str) -> str:
    return "\t".join(filter(None, (item, line, environment)))


def _stored_paths(item: str, records: dict[str, _StoredRecord], part: str) -> list[str]:
    return sorted(
        _stored_path(item, line, environment) for environment, record in records.items() for line in record[part]
    )


def _file_rises(base: dict[str, int], head: dict[str, int]) -> list[tuple[str, int, int]]:
    return [(path, base.get(path, 0), count) for path, count in sorted(head.items()) if count > base.get(path, 0)]


def _kind(key: str) -> str:
    if key.endswith(":unreadable"):
        return "unreadable"
    return re.split(r"[:@]", key, maxsplit=1)[0]


def _table(columns: tuple[str, ...], rows: dict[str, tuple[str, ...]]) -> list[str]:
    width = max(map(len, rows), default=3)
    lines = [f"{'key':<{width}}" + "".join(f"  {column:>6}" for column in columns)]
    lines += [f"{key:<{width}}" + "".join(f"  {value:>6}" for value in values) for key, values in sorted(rows.items())]
    return lines


def _change_table(base: dict[str, int], head: dict[str, int]) -> list[str]:
    pairs = {key: (base.get(key, 0), head.get(key, 0)) for key in {*base, *head}}
    rows = {key: (str(was), str(now), f"{now - was:+d}" if now != was else "0") for key, (was, now) in pairs.items()}
    return _table(("base", "head", "change"), rows)


def _rise_lines(
    key: str,
    base: dict[str, dict[str, int]],
    head: dict[str, dict[str, int]],
    sites: dict[str, list[int]],
    absolute: frozenset[str],
) -> list[str]:
    """Describe one key that rose: its totals, how to fix it, and each file whose count rose with its sites."""
    before, after = sum(base.get(key, {}).values()), sum(head[key].values())
    advice = ADVICE.get(key, ADVICE.get(_kind(key), ""))
    if key in absolute:
        lines = ["", f"{key} is absolute and holds {after} sites (the base {before}): it must be 0. {advice}"]
        changes = [(path, base.get(key, {}).get(path, 0), count) for path, count in sorted(head[key].items())]
    else:
        changes = _risen(key, base, head)
        added = f"{len(changes)} new {'line' if len(changes) == 1 else 'lines'}"
        change = f"has {added}" if _kind(key) == STORED_LIST else f"rose by {after - before}"
        lines = ["", f"{key} {change} ({before} -> {after}). {advice}"]
    for path, was, now in changes:
        at = ", ".join(map(str, sorted(line for line in sites.get(path, []) if line)))
        lines.append(f"  {path}: {was} -> {now}" + (f" (lines {at})" if at else ""))
    return lines


def _report_rises(base: Tally, head: Tally, approvals: Approvals, absolute: frozenset[str]) -> list[str]:
    base_counts, head_counts = base.counts(), head.counts()
    rises = _rises(base_counts, head_counts, approvals, absolute)
    return [line for key in rises for line in _rise_lines(key, base_counts, head_counts, head.sites[key], absolute)]


def _copy_counts(copy: Path, root: Path, ruff_args: list[str], what: str, base: str) -> dict[str, dict[str, int]]:
    output = subprocess.run(
        [sys.executable, str(copy), "--count", str(root), *ruff_args], capture_output=True, text=True, check=False
    )
    if output.returncode:
        msg = (
            f"The base revision's {SCRIPT} cannot count {what}:\n{output.stderr}\n"
            f"To reproduce, run `python <(git show {base}:{SCRIPT}) --count .` at the root of that tree, and change "
            "the head so that the base revision's counter can read it (a hook or configuration form it does not know)."
        )
        raise SystemExit(msg)
    return json.loads(output.stdout)


def _copy_tallies(
    base_root: Path, head_root: Path, ruff_args: list[str], base_commit: str
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    """Count both trees with the base revision's copy of this script; nothing where the base has no copy."""
    copy = base_root / SCRIPT
    if not copy.is_file():
        return {}, {}
    return (
        _copy_counts(copy, base_root, ruff_args, "the base revision", base_commit),
        _copy_counts(copy, head_root, ruff_args, "the working tree", base_commit),
    )


def _report_copy(
    base: dict[str, dict[str, int]],
    head: dict[str, dict[str, int]],
    settled: Callable[[str, int], bool],
    absolute: frozenset[str],
) -> list[str]:
    """Report the rises only the base revision's copy of this script sees in its counts of both trees.

    ``settled`` tells the keys this copy already judged, and the approved rises, from a key and its total; the keys of
    ``absolute`` are judged as in this copy.
    """
    unseen = [key for key in _rises(base, head, NO_APPROVALS, absolute) if not settled(key, sum(head[key].values()))]
    hidden = {key: files for key, files in head.items() if not settled(key, sum(files.values()))}
    header = [
        "",
        f"The base revision's {SCRIPT} also counts these rises, which this copy does not: the change weakens the",
        "counter that judges it. Keep the counter's rules (changes to it are reviewed) and fix the hatches:",
    ]
    body = [line for key in unseen for line in _rise_lines(key, base, head, {}, absolute)]
    body += _unreadable_lines(hidden)
    return [*header, *body] if body else []


def _unreadable_lines(counts: dict[str, dict[str, int]]) -> list[str]:
    """Describe the settings the counter could not read: what they hide cannot be counted, so they always fail."""
    return [
        line
        for key, files in counts.items()
        if _kind(key) == "unreadable"
        for line in (
            "",
            (
                f"{key} in {', '.join(files)}: the counter cannot read it. "
                f"{ADVICE.get('unreadable-' + key.partition(':')[0], ADVICE['unreadable'])}"
            ),
        )
    ]


APPROVAL_RULES = {
    "key": (
        "`key` is an escape-hatch key such as allowlist:JUSTIFIED_VIOLATIONS (an unreadable key, or the key of an "
        "absolute rule, cannot be approved)"
    ),
    "head": "`head` is the approved count of the key, a positive integer",
    "pr": "`pr` is the number of the pull request the rise is approved for, a positive integer",
    "decision": '`decision` lists the public references of the owner decision, such as ["#7172"]',
    "reason": "`reason` says why the rise is needed",
}


def _absolute_keys(root: Path) -> frozenset[str]:
    """Return the keys of the absolute source rules of the tree at ``root``, read with its own rules; none without."""
    if not (root / "scripts/check_source_rules.py").is_file():
        return frozenset()
    names = subprocess.run(
        [sys.executable, "-c", ABSOLUTE_RULES_OF_TREE, str(root)], capture_output=True, text=True, check=True
    ).stdout
    return frozenset(f"source-rule:{name}" for name in json.loads(names))


def _approval_field_ok(name: str, value: object, absolute: frozenset[str]) -> bool:
    if name == "key":
        return isinstance(value, str) and _kind(value) not in {"", "unreadable"} and value not in absolute
    if name in {"head", "pr"}:
        return type(value) is int and value > 0
    if name == "decision":
        return (
            isinstance(value, list)
            and bool(value)
            and all(isinstance(item, str) and PUBLIC_REFERENCE.fullmatch(item) for item in value)
        )
    return isinstance(value, str) and bool(value.strip())


def _approval_problems(entry: object, absolute: frozenset[str]) -> list[str]:
    """Return what makes one entry of the approvals table unusable, a key of ``absolute`` among it, as messages."""
    if not isinstance(entry, dict):
        return ["it is not a table: write it as an `[[approved]]` entry"]
    problems = [f"`{name}` is missing ({APPROVAL_RULES[name]})" for name in APPROVAL_FIELDS if name not in entry]
    problems += [f"`{name}` is not a field of an approval" for name in sorted(set(entry) - set(APPROVAL_FIELDS))]
    problems += [
        f"{APPROVAL_RULES[name]}, not {_toml(entry[name])}"
        for name in APPROVAL_FIELDS
        if name in entry and not _approval_field_ok(name, entry[name], absolute)
    ]
    return problems


def _read_approvals(root: Path, absolute: frozenset[str]) -> tuple[tuple[Approval, ...], list[str]]:
    """Read the approvals table of a tree: its usable entries, and a readable message for each problem.

    An entry for one of the ``absolute`` keys is a problem and never usable, so no approval covers such a key.
    """
    path = root / APPROVALS
    if not path.is_file():
        return (), []
    try:
        table = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        return (), [f"{APPROVALS} is not valid TOML: {error}."]
    problems = [
        f"{APPROVALS}: `{name}` is not part of the table; write each approval as an `[[approved]]` entry."
        for name in sorted(set(table) - {"approved"})
    ]
    entries = table.get("approved", [])
    if not isinstance(entries, list):
        problems.append(f"{APPROVALS}: `approved` is not a list of tables; write each as an `[[approved]]` entry.")
        entries = []
    approvals: list[Approval] = []
    for index, entry in enumerate(entries, 1):
        if found := _approval_problems(entry, absolute):
            problems.append(f"{APPROVALS} entry {index}: {'; '.join(found)}.")
            continue
        approval = Approval(entry["key"], entry["head"], entry["pr"], tuple(entry["decision"]), entry["reason"])
        if any((known.key, known.pr) == (approval.key, approval.pr) for known in approvals):
            problems.append(f"{APPROVALS} entry {index}: a second approval of {approval.key} for #{approval.pr}.")
        approvals.append(approval)
    return tuple(approvals), problems


def _judged_pull_requests(head_root: Path, fork: str) -> frozenset[int]:
    """Return the pull requests that the head being judged is, read from the GitHub Actions run; none outside one.

    A ``pull_request`` run judges the event's pull request (``GITHUB_REF`` is ``refs/pull/<n>/merge``). A ``push``
    run, which the workflow starts only for ``main``, judges the pull requests merged since the base: the ``(#<n>)``
    that ends the subject of each squash commit after it. Every other run, a local one included, judges none, so no
    approval applies there and a rise fails as without the table.
    """
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if event == "pull_request":
        found = PULL_REQUEST_REF.fullmatch(os.environ.get("GITHUB_REF", ""))
        return frozenset({int(found[1])}) if found else frozenset()
    if event != "push":
        return frozenset()
    subjects = _git(head_root, "log", "--format=%s", f"{fork}..HEAD").stdout.decode().splitlines()
    return frozenset(int(found[1]) for subject in subjects if (found := SQUASH_SUBJECT.search(subject)))


def _approval_lines(
    base: dict[str, dict[str, int]],
    head: dict[str, dict[str, int]],
    approvals: Approvals,
    head_root: Path,
    absolute: frozenset[str],
) -> tuple[list[str], list[str]]:
    """Describe the rises an approval covers or names, and the problems of the head's table (stale or malformed).

    An entry of the head's table for a key of ``absolute`` (absolute in the head or the base) is malformed.

    The head's table is judged because it becomes the base: an entry whose key the head already counts at its approved
    count accepts no rise afterwards, so the pull request that reaches the count removes it.
    """
    base_totals, head_totals = _totals(base), _totals(head)
    judged = ", ".join(f"#{number}" for number in sorted(approvals.pull_requests)) or "no pull request"
    notes: list[str] = []
    for key in _rises(base, head, NO_APPROVALS, absolute):
        before, after = base_totals.get(key, 0), head_totals[key]
        if covering := approvals.covering(key, after):
            notes += [
                "",
                (
                    f"{key} rose by {after - before} ({before} -> {after}), approved up to {covering.head} for "
                    f"#{covering.pr} in {APPROVALS} (decision {', '.join(covering.decision)}): {covering.reason}"
                ),
            ]
        notes += [
            line
            for entry in approvals.entries
            if entry.key == key and not covering
            for line in (
                "",
                (
                    f"{key}: {APPROVALS} approves up to {entry.head} for #{entry.pr} only; this run judges {judged} "
                    f"and counts {after}."
                ),
            )
        ]
    entries, problems = _read_approvals(head_root, absolute)
    problems += [
        f"{APPROVALS}: the approval of {entry.key} up to {entry.head} for #{entry.pr} is stale: this tree already "
        f"counts {head_totals.get(entry.key, 0)}, so it accepts no rise. Remove it (in #{entry.pr} itself when that "
        "pull request reaches the count), so that the table holds only pending approvals."
        for entry in entries
        if head_totals.get(entry.key, 0) >= entry.head
    ]
    return notes, [line for problem in problems for line in ("", problem)]


def _compare(base_ref: str, head_root: Path, ruff: list[str], ruff_args: list[str]) -> tuple[list[str], bool]:
    fork = _git(head_root, "merge-base", base_ref, "HEAD").stdout.decode().strip() or base_ref
    with tempfile.TemporaryDirectory() as directory:
        base_root = Path(directory)
        archive = subprocess.run(
            ["git", "archive", "--format=tar", fork], cwd=head_root, capture_output=True, check=True
        ).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(base_root, filter="data")
        base, head = count_tree(base_root, ruff), count_tree(head_root, ruff)
        absolute = ABSOLUTE_KEYS | _absolute_keys(base_root)
        approvals = Approvals(_read_approvals(base_root, absolute)[0], _judged_pull_requests(head_root, fork))
        head_rises = set(_rises(base.counts(), head.counts(), approvals, absolute))
        lines = [f"Escape hatches: {base_ref} (merge-base {fork}) -> working tree", ""]
        lines += _change_table(_totals(base.counts()), _totals(head.counts()))
        lines += _report_rises(base, head, approvals, absolute)
        notes, table_problems = _approval_lines(base.counts(), head.counts(), approvals, head_root, absolute)
        unreadable = _unreadable_lines(head.counts())
        lines += [*notes, *unreadable, *table_problems]
        seen = head_rises | {key for key in head.counts() if _kind(key) == "unreadable"}
        copy_lines = _report_copy(
            *_copy_tallies(base_root, head_root, ruff_args, fork),
            lambda key, total: key in seen or approvals.covering(key, total) is not None,
            absolute,
        )
        candidates = _candidate_lines(base_root, head_root)
    rose = bool(head_rises or copy_lines or unreadable)
    verdict = (
        "Some keys rose: the budget is the base. Exit status 0 unless --enforce is given. "
        f"Re-run with `python {SCRIPT} --base {base_ref}`."
    )
    passed = f"No key rose{' beyond its approval' if notes else ''}."
    if table_problems and not rose:
        passed = f"No key rose, but {APPROVALS} needs the changes above. Exit status 0 unless --enforce is given."
    lines += [*copy_lines, *candidates, "", verdict if rose else passed]
    return lines, rose or bool(table_problems)


def _pin_note(root: Path, ruff: list[str]) -> str:
    pin = _ruff_hook(root).rev.removeprefix("v")
    version = subprocess.run([*ruff, "--version"], capture_output=True, text=True, check=True).stdout.split()[-1]
    return (
        f"note: ruff {version} counts, .pre-commit-config.yaml pins {pin}; pass --ruff <that ruff>\n"
        if pin and pin != version
        else ""
    )


def main(argv: list[str] | None = None) -> int:
    """Print the escape-hatch counts, or their changes against a base revision."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", help="git revision whose counts are the budget")
    parser.add_argument("--enforce", action="store_true", help="exit 1 when a key rose")
    parser.add_argument("--count", type=Path, help="print the counts of this tree as JSON")
    parser.add_argument("--ruff", help="ruff executable (default: this interpreter's ruff module)")
    parser.add_argument("--ruff-pin", action="store_true", help="print the ruff version the pre-commit hook pins")
    args = parser.parse_args(argv)
    ruff = [args.ruff] if args.ruff else [sys.executable, "-m", "ruff"]
    if args.count:
        print(json.dumps(count_tree(args.count.resolve(), ruff).counts(), indent=1, sort_keys=True))
        return 0
    top = _git(Path.cwd(), "rev-parse", "--show-toplevel")
    head_root = Path(top.stdout.decode().strip()) if top.returncode == 0 else Path.cwd()
    if args.ruff_pin:
        print(_ruff_hook(head_root).rev.removeprefix("v"))
        return 0
    sys.stderr.write(_pin_note(head_root, ruff))
    if not args.base:
        counts = _totals(count_tree(head_root, ruff).counts())
        print("\n".join(_table(("count",), {key: (str(count),) for key, count in counts.items()})))
        return 0
    lines, rose = _compare(args.base, head_root, ruff, ["--ruff", args.ruff] if args.ruff else [])
    print("\n".join(lines))
    return 1 if rose and args.enforce else 0


if __name__ == "__main__":
    sys.exit(main())
