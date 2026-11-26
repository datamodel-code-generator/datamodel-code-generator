"""Classify every output change between a release or a git revision and a checkout by its behaviour.

Usage:
  python scripts/behaviour_diff.py gate [BASE] [CANDIDATE] [--tier pr|nightly|release] [--types T1,T2]
      [--shard I/N] [-j N] [--check] [--json OUT] [--verdicts OUT] [--identity] [--pick CATALOG]
  python scripts/behaviour_diff.py catalog OUT [REVISION] [--types T1,T2] [--options 'default;--x'] [--select GLOB]

BASE is a released version (default: the latest tag) or a git revision of the candidate's repository (this
repository for a candidate revision). CANDIDATE is a checkout path (default: this checkout; its `.venv` is used when
it has one) or a git revision of this repository. scripts/compare_release.py resolves both (`resolve_base`,
`source_tree`): a revision is extracted with `git archive` and run from the candidate's interpreter through
PYTHONPATH; a release is installed into a cached environment pinned to the candidate's dependency versions. The
`[[base_skip]]` entries of the allowlist file apply to a release base given as a version only (not `refs/tags/<tag>`).

`--identity` compares without the allowlist and implies `--check`: any difference in a cell's output bytes, warnings
(category, message, module), handled exceptions, messages (errors by class and message, without traceback frames),
exit status or refused single-file attempt is a violation, and so is every option or option value one side has and
the other lacks and every cell that uses one the candidate has and the base lacks. A cell the same on both sides is
`same`, or `both-fail` where both sides failed alike; such a cell is not compared where the timeout or the memory cap
stopped both sides or where every cell of its input fails on both sides. Identity also fails where its evidence
compared too little (`compare_release.Evidence`): no input group, an input group (the inputs' directory, files or
directories, output model type and option set; a cli-doc example, payload case or project job by its kind) none of
whose cells compared (generated on both sides with all output in the run's directory), and, against a git revision, a
changed code object (`scripts/_change_units.py`: what the candidate adds on its side, what it removes on the base's)
that no compared cell ran on its side (on lines a test ran, by `--test-coverage`, or not run), that is
unmeasurable, or that the Python (before 3.12) cannot measure. A sharded identity run is refused: a shard cannot judge
the whole run. Every gate prints these input groups and the cells the candidate refuses and the base generated (the
single output file included). Refactoring steps use it against their base commit.

A cell is one input x output model type x option set. Tiers:
  pr       inputs changed since BASE (with `--allowlist`, also the inputs its `[[accepted]]` entries name) and a
           fixed sample of 300 fixtures,
           x {pydantic_v2.BaseModel, msgspec.Struct}, default options; and every family cell of those types
  nightly  every fixture and payload case x 5 types (default options), every fixture x the released presets,
           every fixture x 5 types x each option set of OPTION_SETS, every `@pytest.mark.cli_doc` example, the
           projects tier: each job of scripts/behaviour_diff_projects.json (a project's own arguments on its own
           schemas, checked out shallowly at the pinned commit into the cache directory), and the full product of
           the family cells: each shape x 5 types x (default options, the released presets, OPTION_SETS), and its
           client and server
  release  nightly (the option tiers of the design come later)

Family cells are the shapes of the candidate's family library (`tests/main/shape_families/families.py`, `FAMILIES`:
ordinary modelling idioms enumerated completely within a stated bound), written once per run into the cache
directory (`family_cases`) and labelled `family:<family>/<parameter>=<value>,...`. Each shape is generated as JSON
Schema for the tier's output model types and option sets, and its OpenAPI spelling with a client (its body's fields as
arguments too) and with a server (`FAMILY_TARGETS`, no other option) for pydantic_v2.BaseModel where the tier has that
type (`family_cells`). A candidate without the library (a revision before it) has none; a library the gate cannot
list is one family cell that fails. They join every tier; `--select` runs them where one of its patterns names them
(`family:*`). The `families` key of the allowlist file decides what they do: `report` (until the family cells have
run for a cycle) prints their differences and their classes apart and leaves them out of the verdict, the evidence
and `--json`; `fail` judges them as every other cell. `guide` runs none.

Cells whose bytes differ get a behaviour signature per side (scripts/_behaviour_signature.py, a fresh process per
tree; each module of a package is also imported first in a process of its own) with a validation probe per name
and, for payload cases, payload verdicts; scripts/_behaviour_classify.py puts each cell into one class. The PR tier
signs no family cell it reports only whose sides both generated other bytes: it reports that cell as
`differs-unsigned` (`UNSIGNED_CLASS`); the nightly and release tiers sign and class every family cell. A cell
that uses an option, value or input type BASE does not have is `opt-in` and is not generated.

Without `--identity` the change records judge every cell whose facts (`compare_release.cell_facts`) differ. A PR run
(BASE a revision) compares the merge base of BASE and the candidate and judges by the records whose `id` that base
lacks; a PR that changes nothing under src/ passes without generating (a record with `[[change.cells]]` fails then,
except one whose own `refs` all name pull requests merged after the latest tag up to that base, extending, if it
extends, a record of the base: a change already on main, whose cells the release runs judge), and one that changes the
gate's own files also runs the base's gate. A release run (BASE a release version or `refs/tags/<tag>`) judges by the
fragments the tag lacks. Presets come from the latest tag. The run adds the cells the records' selectors name and, in
a PR run, the nightly cells of the inputs it changes; a differing cell's head runs twice, and the records that cover
it must admit every kind of its difference with their categories' evidence (`_behaviour_classify.judge`: payload
cells, rule (b), companion cells). Every selector must match a differing cell (in a release run, staleness in a run of
the whole nightly or release tier), judged by the runs that build its label space (fixture paths in every run;
`payload:` and `project:` cells in the nightly and release tiers); the stored modules an `output-text` record covers,
and the changed labels a `harness` record covers in a src PR, are proved by signatures, rule (b) and values drawn from
their models' schemas (`_stored-module`); `--verdicts OUT` (shard 1) writes the verdicts on those `harness` labels by
their base and head contents, which `declared_changes.py check --verdicts OUT` reads. The report lists the uncovered
cells with a starting `[[change.cells]]` block.

scripts/behaviour_diff_allowlist.toml holds `base`, `families` and `[[base_skip]]` entries (a test keeps it free of
`[[accepted]]` entries: the records judge changes, see docs/development-contributing.md "Declared changes"). A
`[[base_skip]]` entry (`input` glob, `bases`: releases such as `["0.88.0"]` or a maximum such as `["<=0.88.0"]`,
`reason`, `issue`) names an input a release is known to hang on: the release does not run for those cells, which are
`base-skipped` once the candidate generated and imported (a candidate failure is a violation). `--check` also fails on
a `[[base_skip]]` entry without those keys, and, in a run of the whole tier, on one that names BASE (`bases`) and
covers no cell (an entry for another release is not judged), and, where the table has `[[accepted]]` entries, on a
table whose base is neither BASE nor an older release. The PR tier is a sample, so it never judges staleness.
`--shard I/N` runs the cells of shard I by input hash; the cells a `[[base_skip]]` entry covers all run in one shard
with those of each entry that shares a cell with them, the shard of their first input. So the N shards report the
stale entries the unsharded run reports: each shard the entries whose cells it runs, shard 1 also the entries that
cover no cell. `--select` narrows the inputs, so a selection may leave an entry's cells out.

`--pick CATALOG` adds to the cells of the tier (and of `--select`) the catalog's cells that run the change against a
BASE revision (`scripts/_cell_catalog.py`): it only adds cells, it never drops one, and the evidence stays the judge of
what the run compared. It prints the pick (the requirements no catalog cell meets too); where it cannot pick (a
release base, no catalog file, another format, a catalog revision that is no ancestor of BASE) it says so and the tier
runs alone. A picked family cell is the shape of that label in the candidate's library. `catalog OUT [REVISION]`
records the catalog of the fixtures x `--types` x `--options` (option sets separated by ';', `default` for none) on a
revision of this repository (default: HEAD), with what each cell ran, and of the family cells of this checkout's
library where `--select` names them (`family:*`); its `-j` defaults to 2, as each recording worker imports the whole
package first. A pick only adds cells, so the PR tier runs every family cell, recorded in the catalog or not.

`record VERSION --shard I/N` records the cells of shard I (by input hash) and marks the record as that shard;
`merge-record VERSION PART... --out DIR` merges the records of all N shards into the record an unsharded run writes.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import datetime as dt
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Final, TypedDict

import tomllib

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from scripts._change_records import CellSelector, Record

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import _behaviour_signature, _cell_catalog, compare_release, declared_changes, record_prs  # ruff: ignore[module-import-not-at-top-of-file]
from scripts._behaviour_classify import (  # ruff: ignore[module-import-not-at-top-of-file]
    CLASSES,
    LINE_ASPECT,
    SIGNATURE_FAILED,
    Allowlist,
    CellEvidence,
    Side,
    SourceModule,
    Verdict,
    change_lines,
    classify_base_skipped,
    classify_cell,
    covers,
    diff,
    judge,
    kinds,
    payload_changes,
    probe_changes,
    rule_b,
    selector_paths,
)
from scripts._behaviour_report import compare_records, redact, summarize_gate  # ruff: ignore[module-import-not-at-top-of-file]
from scripts._change_records import (  # ruff: ignore[module-import-not-at-top-of-file]
    COMPOSITION_SPACE,
    FAMILY_SPACE,
    FIXTURE_SPACE,
    PAYLOAD_SPACE,
    PRESET_OPTION,
    PROJECT_SPACE,
    Category,
    owner_of,
    parse_entry,
    read_records,
    resolved_record,
    rule_problems,
    split_options,
    version_key,
)

SIGNATURE_SCRIPT = Path(__file__).with_name("_behaviour_signature.py")
DEFAULT_ALLOWLIST = Path(__file__).with_name("behaviour_diff_allowlist.toml")
MIGRATION_DATA = ROOT / "docs" / "data" / "migration"
FIXTURE_DIRS = (
    ("jsonschema", (".json", ".yaml", ".yml"), True),
    ("openapi", (".json", ".yaml", ".yml"), True),
    ("asyncapi", (".json", ".yaml", ".yml"), True),
    ("graphql", (".graphql",), False),
    ("avro", (".avsc",), True),
    ("protobuf", (".proto",), True),
    ("xmlschema", (".xsd",), True),
)
PR_TYPES = ("pydantic_v2.BaseModel", "msgspec.Struct")
PR_SAMPLE = 300
SELECTOR_CELL_LIMIT = 1500
"""The most cells one `[[change.cells]]` selector may name; a whole PR run of the gate's own tier is about this size."""
MODEL_OUTPUT_TYPES = {
    "pydantic_v2": "pydantic_v2.BaseModel",
    "pydantic_v2_dataclass": "pydantic_v2.dataclass",
    "dataclass": "dataclasses.dataclass",
    "typeddict": "typing.TypedDict",
    "msgspec": "msgspec.Struct",
}
PRESET_PYTHONS_NEWEST = ("py310", "py314")
PRESET_PYTHON_OLDER = "py312"
PAYLOAD_VALID = 10
PAYLOAD_INVALID = 12
PAYLOAD_DRAW_TIMEOUT = 90
EXCLUDED_OPTIONS = frozenset({"--watch", "--debug"})
"""Options no cell runs: `--watch` never returns, and the trace `--debug` prints holds object addresses and times, so
its messages differ on every run."""
OPTION_SETS = (
    ("--strip-default-none",),
    ("--strip-default-none", "--keyword-only"),
    ("--strip-default-none", "--use-default-factory-for-optional-nested-models"),
)
DEFAULT_PROJECTS = Path(__file__).with_name("behaviour_diff_projects.json")
PROJECT_COMMON_ARGS = ("--disable-timestamp",)
PROJECT_REPO = "{repo}"
FETCH_TRIES = 3
DEFAULT_RECORDS = compare_release._default_cache_dir().parent / "behaviour-records"  # ruff: ignore[private-member-access]
ERA_PYTHONS = ("3.14", "3.13", "3.12", "3.11")
ERA_EXTRAS = "graphql,http,protobuf"
# Installed beside every release: the formatters it may call and what the signature step and output imports need.
ERA_PACKAGES = ("black", "isort", "msgspec", "email-validator")
SNIPPET_LINES = 40
OPT_IN_TRIES = 5
FRAGMENTS = "docs/data/migration/unreleased"
RELEASES = "docs/data/migration/releases.toml"
PRESET_NAMES = "src/datamodel_code_generator/preset_names.py"
PRESET_ALIAS = "PresetName"
EXPECTED = "tests/data/expected/"
ORACLE_FILES = (
    "scripts/_behaviour_classify.py",
    "scripts/_behaviour_report.py",
    "scripts/_behaviour_signature.py",
    "scripts/_cell_catalog.py",
    "scripts/_change_records.py",
    "scripts/_change_units.py",
    "scripts/_source_model.py",
    "scripts/_stored_module.py",
    "scripts/_template_compiler/__init__.py",
    "scripts/_template_compiler/compiler.py",
    "scripts/_template_compiler/inventory.py",
    "scripts/behaviour_diff.py",
    "scripts/build_behaviour_docs.py",
    "scripts/check_generation_store_usage.py",
    "scripts/compare_release.py",
    "scripts/compile_builtin_templates.py",
    "scripts/declared_changes.py",
    "scripts/fact_tables.py",
    "scripts/os_selection.py",
    "scripts/record_prs.py",
    "scripts/select_ci_test_shard.py",
)
"""The gate's own files: a change to one also runs the base's gate (`base_gate`).

Every `scripts` module the gate imports, directly or through another, or runs in a subprocess; a test keeps the list
equal to them (`tests/test_pre_commit_hooks.py`)."""
BASE_GATE_FILES = ("scripts", "tests/__init__.py", "tests/units.py")
BASE_GATE_ENV = "BEHAVIOUR_DIFF_BASE_GATE"
REPOSITORY_ENV = "BEHAVIOUR_DIFF_REPOSITORY"
"""The checkout a base's gate runs for (`base_gate`): its default candidate and the repository of its revisions; the
gate's own checkout without it."""
HEAD_STOPPED = (
    "the head stopped at the timeout or the memory cap where the base finished: no record covers an unfinished output"
)
"""Set in the environment of the base's gate, so that it does not run its own base's gate again."""
UNCOVERED = "no record's [[change.cells]] covers the cell"
FAMILY_KIND = "family"
FAMILY_PREFIX = FAMILY_SPACE
FAMILY_LIBRARY = "tests/main/shape_families/families.py"
FAMILY_SPELLINGS = {"jsonschema": "schema.json", "openapi": "openapi.json"}
"""The file of each spelling of a family shape, by its input file type."""
FAMILY_TARGET_TYPE = "pydantic_v2.BaseModel"
FAMILY_TARGETS = (
    (
        "--openapi-scopes",
        "schemas",
        "api",
        "--generate-client",
        "httpx2",
        "--client-output",
        "client",
        "--client-package",
        "client",
        "--client-model-package",
        "model",
        "--client-body-arguments",
        "both",
    ),
    (
        "--openapi-scopes",
        "schemas",
        "api",
        "--generate-server",
        "fastapi",
        "--server-output",
        "server",
        "--server-package",
        "server",
        "--server-model-package",
        "model",
    ),
)
"""The client and the server of a family shape's OpenAPI spelling, each a package beside the run's models (`model`,
the run's `--output`).

The client also takes the body's fields as arguments (`--client-body-arguments both`): a field argument's annotation
is where a target decision on a field shows (#7114 changed one), and with the default `body` the client takes the
model whole and has no such annotation. The server runs with its defaults. The bound: dcg's one client (httpx2) and
one server (fastapi), OpenAPI 3.1, the shape as the JSON body of one operation, pydantic_v2.BaseModel. Not covered:
the OpenAPI 3.0 spelling (`nullable: true`), shapes as parameters and non-JSON bodies, pydantic_v2.dataclass targets,
other target options (the client's `body` mode and deep objects; the server's body and handler modes, layout,
request inclusion), and presets and `OPTION_SETS` on the targets."""
TARGET_OPTIONS = ("--generate-client", "--generate-server")
"""The options that make a run write target packages beside its models, which its signature covers."""
FAMILIES_KEY = "families"
FAMILY_MODES = ("report", "fail")
"""The values of the allowlist file's `families` key: report the family cells only (the default), or judge them."""
UNSIGNED_CLASS = "differs-unsigned"
"""The class the PR tier reports a family cell under that it reports only and whose sides both generated other bytes.

The PR tier signs no such cell: a target cell signs every module of its packages in a process of its own (about 5 s a
cell on a runner), and the class would change nothing. The nightly tier signs and classes every family cell.
"""
VERDICTS_OPTION = "--verdicts"
PICK_OPTION = "--pick"
HEAD_ONLY_OPTIONS = (VERDICTS_OPTION, PICK_OPTION)
"""The options of the head's gate its base's gate does not get (`base_arguments`)."""
REGEX_SPECIAL = re.compile(r"[\\.^$*+?{}\[\]|()]")
MSGSPEC_IMPORT = re.compile(r"^\s*(?:import msgspec|from msgspec\b)", re.MULTILINE)
COMPANION_LINE = re.compile(r"^(?:valid: |invalid .*: rejected \()", re.MULTILINE)
NOT_COMPARED_STOPPED = "not compared: the timeout or the memory cap stopped both sides"
NOT_COMPARED_INPUT = "not compared: every cell of the input fails on both sides"
RESTORE_GROUPS = (
    "Typing customization",
    "Field customization",
    "Model customization",
    "Template customization",
    "OpenAPI-only options",
    "GraphQL-only options",
)
ALL_GROUPS = (*RESTORE_GROUPS, "Options", "General options", "Target generation options (experimental)")
RESTORE_INVENTORY = """
import json
from datamodel_code_generator.arguments import arg_parser
found = []
for group in arg_parser._action_groups:
    if group.title in GROUPS:
        for action in group._group_actions:
            if action.nargs == 0:
                found += [[name] for name in action.option_strings]
            elif action.choices:
                found += [[action.option_strings[-1], str(c)] for c in action.choices if c != action.default]
print(json.dumps(found))
"""
OPTION_INVENTORY = """
import json
try:
    from datamodel_code_generator.arguments import arg_parser
except ImportError:  # releases before 0.21 define the parser in __main__
    from datamodel_code_generator.__main__ import arg_parser
options = {o: sorted(map(str, a.choices)) if a.choices else None for a in arg_parser._actions for o in a.option_strings}
print(json.dumps(options))
"""
FAMILY_SHAPES = """
import json
from tests.main.shape_families.families import FAMILIES
shapes = (shape for family in FAMILIES.values() for shape in family.shapes())
print(json.dumps([[s.family, [[n, str(v)] for n, v in s.params], s.document, s.openapi] for s in shapes]))
"""
"""Print every shape of the family library of the working directory: family, parameters and both spellings."""


class PayloadCase(TypedDict):
    """One payload-validation case of the candidate's harness, as `_payload-cases` indexes it."""

    key: str
    case_id: str
    input: str
    file_type: str
    args: dict[str, list[str]]


class FamilyCase(TypedDict):
    """One shape of the candidate's family library, as `family_cases` writes it: its label and its directory."""

    label: str
    key: str


ListedShape = tuple[str, list[tuple[str, str]], dict[str, object], dict[str, object]]
"""A shape as `FAMILY_SHAPES` lists it: its family, its parameters as text and its two spellings."""


class StoredSide(TypedDict, total=False):
    """What `scripts/_stored_module.py` writes for one side: its models and their draws, or the verdicts."""

    models: list[str]
    values: dict[str, list[object]]
    verdicts: dict[str, object]


@dataclass(frozen=True)
class Cell:
    """One input x output model type x option set."""

    input: Path
    label: str
    file_type: str
    backend: str
    options: tuple[str, ...] = ()
    kind: str = "default"
    payload_key: str | None = None
    stubs: tuple[tuple[str, str], ...] = ()

    @property
    def option_label(self) -> str:
        """The option set as one string."""
        return " ".join(self.options) or "default"

    def args(self) -> list[str]:
        """Return the generation arguments without `--output`; a project's own arguments keep its formatters."""
        return [
            "--input",
            str(self.input),
            "--input-file-type",
            self.file_type,
            "--output-model-type",
            self.backend,
            *(PROJECT_COMMON_ARGS if self.kind == "project" else compare_release.COMMON_ARGS),
            *self.options,
        ]


@dataclass
class Candidate:
    """The checkout or revision under test."""

    root: Path
    python: Path
    env: dict[str, str]
    revision: str | None

    @property
    def repo(self) -> Path:
        """The repository the candidate's revisions and a revision base resolve in."""
        return repository() if self.revision else self.root


@dataclass
class Result:
    """The classification of one cell and, where its facts differ, the verdict of the change records on it.

    `judged` is None for a cell whose sides' facts are equal, or that the timeout or the memory cap stopped on both
    sides; `lines` are the change lines of a differing cell.
    """

    cell: Cell
    verdict: Verdict
    base_skip: int | None = None
    judged: Verdict | None = None
    lines: tuple[str, ...] = ()

    @property
    def final(self) -> Verdict:
        """The verdict the run fails on: the records' where the facts differ, else the classification."""
        return self.judged or self.verdict


@dataclass
class Project:
    """A project that runs datamodel-codegen on its own schemas, pinned to one commit (behaviour_diff_projects.json)."""

    name: str
    url: str
    sha: str
    paths: tuple[str, ...]
    root: Path
    lock: threading.Lock = field(default_factory=threading.Lock)

    def fetch(self) -> None:
        """Check out the pinned commit's `paths` into `root` once (shallow, sparse, blobless)."""
        with self.lock:
            if (self.root / ".complete").exists():
                return
            for _ in range(FETCH_TRIES):
                shutil.rmtree(self.root, ignore_errors=True)
                self.root.mkdir(parents=True)
                steps = [
                    ["init", "-q"],
                    ["remote", "add", "origin", self.url],
                    ["fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", self.sha],
                    ["sparse-checkout", "set", "--no-cone", *self.paths],
                    ["-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD"],
                ]
                if all(compare_release.git(self.root, *step).returncode == 0 for step in steps):
                    (self.root / ".complete").touch()
                    return
            msg = f"cannot fetch {self.url} at {self.sha}"
            raise RuntimeError(msg)


def load_projects(manifest: Path, cache_dir: Path) -> tuple[dict[str, Project], list[Cell]]:
    """Return the projects of a manifest and one cell per job: the project's own arguments on its own schema."""
    data = json.loads(manifest.read_text(encoding="utf-8"))
    projects: dict[str, Project] = {}
    cells = []
    for entry in data["projects"]:
        paths_key = _stable(json.dumps(entry["paths"]))[:8]
        root = cache_dir / "projects" / f"{entry['name']}-{entry['sha'][:12]}-{paths_key}"
        projects[entry["name"]] = Project(
            entry["name"],
            entry.get("url") or f"https://github.com/{entry['repo']}.git",
            entry["sha"],
            tuple(entry["paths"]),
            root,
        )
        cells += [
            Cell(
                root / job["input"],
                f"project:{entry['name']}/{job['name']}",
                job["input_file_type"],
                job["output_model_type"],
                tuple(arg.replace(PROJECT_REPO, str(root)) for arg in job["args"]),
                "project",
                stubs=tuple(sorted(job.get("stubs", {}).items())),
            )
            for job in entry["jobs"]
        ]
    return projects, cells


def project_of(gate: Gate, cell: Cell) -> Project | None:
    """Return the project a cell of the projects tier belongs to."""
    if cell.kind != "project":
        return None
    return gate.projects[cell.label.removeprefix("project:").partition("/")[0]]


@dataclass
class Gate:
    """Everything one gate run needs."""

    base: compare_release.Runner
    candidate: compare_release.Runner
    cache: compare_release.OutputCache
    candidate_python: Path
    allowlist: Allowlist
    loaded_allowlist: Allowlist
    timeout: int
    payload_dir: Path
    payload_root: Path
    candidate_info: Candidate
    family_root: Path = Path()
    families_judged: bool = False
    tier: str = ""
    family_index: list[FamilyCase] | None = None
    family_problem: str = ""
    base_version: str = ""
    signature_python: Path | None = None
    projects: dict[str, Project] = field(default_factory=dict)
    draw_locks: dict[str, threading.Lock] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    identity: bool = False
    evidence: compare_release.Evidence = field(default_factory=lambda: compare_release.Evidence(None))
    plan: Plan | None = None
    rerun: compare_release.Runner | None = None
    payload_index: list[PayloadCase] | None = None
    head_inventory: dict[str, list[str] | None] | None = None
    signature_only: set[str] = field(default_factory=set)
    refused_alike: set[str] = field(default_factory=set)
    no_payloads: set[str] = field(default_factory=set)
    verdicts: list[dict[str, str | None]] = field(default_factory=list)
    picked: list[_cell_catalog.CatalogCell] = field(default_factory=list)


def repository() -> Path:
    """Return the checkout the gate runs for: the one a base's gate is started for (`REPOSITORY_ENV`), else this one."""
    return Path(os.environ.get(REPOSITORY_ENV) or ROOT)


def latest_tag() -> str:
    """Return the latest release tag reachable from HEAD."""
    return compare_release.git(repository(), "describe", "--tags", "--abbrev=0").stdout.strip()


def resolve_candidate(spec: str | None, python: Path | None, cache_dir: Path) -> Candidate:
    """Resolve a checkout path or a revision to a root, an interpreter and the environment to run it with.

    Either way the candidate imports the root's `src` (`compare_release.run_environment`).
    """
    if spec is None or Path(spec).is_dir():
        root = Path(spec or repository()).resolve()
        venv = root / ".venv" / "bin" / "python"
        chosen = python or (venv if venv.exists() else Path(sys.executable))
        return Candidate(root, chosen.absolute(), compare_release.run_environment(root / "src"), None)
    if not (revision := compare_release.commit_of(spec, repository())):
        msg = f"{spec} is neither a directory nor a revision of {repository()}"
        raise SystemExit(msg)
    root = compare_release.source_tree(revision, cache_dir, repository(), paths=("src", "tests"))
    python = (python or Path(sys.executable)).absolute()
    return Candidate(root, python, compare_release.run_environment(root / "src"), revision)


def option_inventory(python: Path, env: dict[str, str] | None = None) -> dict[str, list[str] | None]:
    """Return every option of an interpreter's datamodel-codegen with its choices."""
    output = subprocess.run(
        [str(python), "-c", OPTION_INVENTORY], capture_output=True, text=True, check=True, env=env
    ).stdout
    return json.loads(output)


def opt_in_reason(args: list[str], inventory: dict[str, list[str] | None]) -> str | None:
    """Return the first option or option value of `args` that the release does not have."""
    for index, arg in enumerate(args):
        if not arg.startswith("--"):
            continue
        name, _, inline = arg.partition("=")
        if name not in inventory:
            return name
        choices = inventory[name]
        value = inline or (args[index + 1] if index + 1 < len(args) else "")
        if choices and value and not value.startswith("--") and value not in choices:
            return f"{name} {value}"
    return None


def changed_files(candidate: Candidate, base: str) -> list[str]:
    """Return the files under tests/data that changed between the release and the candidate."""
    return changed_paths(candidate.repo, base, candidate.revision, ("tests/data",))


def added_files(candidate: Candidate, base: str, directory: str) -> list[str]:
    """Return the files added below tests/data/<directory> since the release."""
    revision = [candidate.revision] if candidate.revision else []
    args = ("diff", "--name-only", "--diff-filter=A", base, *revision, "--", f"tests/data/{directory}")
    return sorted(compare_release.git(candidate.repo, *args).stdout.split())


def fixture_inputs(candidate: Candidate, base: str) -> list[tuple[str, str]]:
    """Return the fixture inputs as (path relative to the root, input file type).

    Top-level files and first-level directories (modular inputs) of each input directory, plus nested inputs added
    since the release.
    """
    found: dict[str, str] = {}
    for directory, suffixes, modular in FIXTURE_DIRS:
        top = candidate.root / "tests" / "data" / directory
        if not top.is_dir():
            continue
        for path in sorted(top.iterdir()):
            if path.name.startswith(".") or path.name == "__pycache__":
                continue
            if (path.is_file() and path.suffix in suffixes) or (modular and path.is_dir()):
                found[path.relative_to(candidate.root).as_posix()] = directory
        for name in added_files(candidate, base, directory):
            if len(Path(name).parts) >= 5 and Path(name).suffix in suffixes and (candidate.root / name).is_file():  # ruff: ignore[magic-value-comparison]
                found.setdefault(name, directory)
    return sorted(found.items())


def _stable(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def pr_inputs(inputs: list[tuple[str, str]], changed: list[str], allowlist: Allowlist) -> list[tuple[str, str]]:
    """Return the inputs of the PR tier: changed ones, ones the allowlist names and a fixed sample."""
    sample = {name for name, _ in sorted(inputs, key=lambda item: _stable(item[0]))[:PR_SAMPLE]}
    return [
        (name, file_type)
        for name, file_type in inputs
        if name in sample
        or any(path == name or path.startswith(f"{name}/") for path in changed)
        or any(
            allowlist.covers(index, name, backend) for index in range(len(allowlist.entries)) for backend in PR_TYPES
        )
    ]


def preset_option_sets(presets: list[str], types: list[str]) -> list[tuple[str, tuple[str, ...]]]:
    """Return (output model type, options) for the released presets.

    The newest date in both families for py310 and py314 with every type; each older date in both families for
    py312 with pydantic_v2.BaseModel and msgspec.Struct.
    """
    dates = sorted({name.rsplit("-", 1)[-1] for name in presets})
    if not dates:
        return []
    chosen = []
    for name in presets:
        family_python, _, date = name.rpartition("-")
        python = family_python.rpartition("-")[2]
        if date == dates[-1] and python in PRESET_PYTHONS_NEWEST:
            chosen += [(backend, ("--preset", name)) for backend in types]
        elif date != dates[-1] and python == PRESET_PYTHON_OLDER:
            chosen += [(backend, ("--preset", name)) for backend in types if backend in PR_TYPES]
    return chosen


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return node.id if isinstance(node, ast.Name) else ""


def _literal(node: ast.expr) -> object:
    try:
        return ast.literal_eval(node)
    except ValueError:
        return None


def cli_doc_examples(root: Path) -> list[dict[str, Any]]:
    """Return the literal `input_schema`, `cli_args` and `model_outputs` of every `@pytest.mark.cli_doc` example."""
    examples = []
    for file in sorted((root / "tests").rglob("*.py")):
        if file.is_relative_to(root / "tests" / "data"):
            continue
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _dotted(node.func).endswith("mark.cli_doc"):
                continue
            kwargs = {keyword.arg: _literal(keyword.value) for keyword in node.keywords if keyword.arg}
            schema, cli_args = kwargs.get("input_schema"), kwargs.get("cli_args")
            if isinstance(schema, str) and isinstance(cli_args, list) and all(isinstance(a, str) for a in cli_args):
                examples.append({
                    "input_schema": schema,
                    "cli_args": cli_args,
                    "model_outputs": kwargs.get("model_outputs"),
                })
    return examples


def _without(args: list[str], option: str) -> tuple[list[str], str | None]:
    """Remove one valued option from `args` and return its value."""
    rest: list[str] = []
    value = None
    skip = False
    for index, arg in enumerate(args):
        if skip:
            skip = False
        elif arg == option and index + 1 < len(args):
            value, skip = args[index + 1], True
        elif arg.startswith(f"{option}="):
            value = arg.partition("=")[2]
        else:
            rest.append(arg)
    return rest, value


def anchor_cells(root: Path, types: list[str]) -> list[Cell]:
    """Return one cell per cli_doc example and output model type it documents, but none of `EXCLUDED_OPTIONS`."""
    cells: dict[tuple[str, str, tuple[str, ...]], Cell] = {}
    for example in cli_doc_examples(root):
        schema = root / "tests" / "data" / example["input_schema"]
        if not schema.exists():
            continue
        if EXCLUDED_OPTIONS.intersection(example["cli_args"]):
            continue
        args, backend = _without(example["cli_args"], "--output-model-type")
        args, file_type = _without(args, "--input-file-type")
        file_type = file_type or Path(example["input_schema"]).parts[0]
        outputs = example["model_outputs"] if isinstance(example["model_outputs"], dict) else {}
        backends = [MODEL_OUTPUT_TYPES[key] for key in outputs if key in MODEL_OUTPUT_TYPES] or [
            backend or "pydantic_v2.BaseModel"
        ]
        label = schema.relative_to(root).as_posix()
        for model_type in backends:
            if model_type in types:
                key = (label, model_type, tuple(args))
                cells.setdefault(key, Cell(schema, label, file_type, model_type, tuple(args), "cli-doc"))
    return list(cells.values())


def payload_index(gate: Gate) -> list[PayloadCase]:
    """Write the payload-validation cases of the candidate once and return their index."""
    with gate.lock:
        if gate.payload_index is None:
            candidate = gate.candidate_info
            subprocess.run(
                [str(gate.candidate_python), __file__, "_payload-cases", str(candidate.root), str(gate.payload_root)],
                cwd=candidate.root,
                env=candidate.env,
                check=True,
            )
            gate.payload_index = json.loads((gate.payload_root / "index.json").read_text(encoding="utf-8"))
        return gate.payload_index


def payload_cells(gate: Gate, types: list[str]) -> list[Cell]:
    """Write the payload-validation cases of the candidate and return their cells."""
    index = payload_index(gate)
    return [
        Cell(
            gate.payload_root / case["input"],
            f"payload:{case['case_id']}",
            case["file_type"],
            backend,
            tuple(args),
            "payload",
            case["key"],
        )
        for case in index
        for backend, args in case["args"].items()
        if backend in types
    ]


def family_cases(python: Path, candidate: Candidate, out: Path) -> tuple[list[FamilyCase], str]:
    """Write every shape of a checkout's family library in both spellings under `out`; return their index and a problem.

    The checkout's interpreter lists the shapes (`FAMILY_SHAPES`); each gets a directory of its own, named by its
    label, so the cache of a base output covers that shape only. A checkout without the library (a revision before
    it) has no family cells: the run says so. A library the listing cannot read (an interpreter error, an output that
    is no JSON: a library without a name the listing reads, such as `Shape.openapi` before the gate's cells) gives no
    cells and the problem, which the run turns into one family cell (`family_problem`), so the `families` key decides
    whether it fails the run.
    """
    if not (candidate.root / FAMILY_LIBRARY).is_file():
        print(f"families: {candidate.root} has no {FAMILY_LIBRARY}: no family cells")
        return [], ""
    listed = subprocess.run(
        [str(python), "-c", FAMILY_SHAPES],
        cwd=candidate.root,
        env=candidate.env,
        capture_output=True,
        text=True,
        check=False,
    )
    detail = (listed.stderr.strip().splitlines() or [f"exit status {listed.returncode}"])[-1]
    if listed.returncode == 0:
        try:
            shapes = json.loads(listed.stdout)
        except json.JSONDecodeError as error:
            detail = f"the listing is no JSON ({error})"
        else:
            return _written_family_cases(shapes, out), ""
    problem = f"{candidate.root}: the library could not be listed: {detail}"
    print(f"families: {problem}")
    return [], problem


def _written_family_cases(shapes: list[ListedShape], out: Path) -> list[FamilyCase]:
    """Write each listed shape (family, parameters, both spellings) into a directory named by its label.

    Each file is written beside its place and moved there (`Path.replace`), so another gate run that shares the cache
    directory reads the whole old file or the whole new one, never one cut short.
    """
    index: list[FamilyCase] = []
    for family, params, *documents in shapes:
        label = f"{FAMILY_PREFIX}{family}/" + ",".join(f"{name}={value}" for name, value in params)
        directory = out / (key := _stable(label)[:16])
        directory.mkdir(parents=True, exist_ok=True)
        for name, document in zip(FAMILY_SPELLINGS.values(), documents, strict=True):
            written = directory / f".{name}.{os.getpid()}.{threading.get_ident()}.tmp"
            written.write_text(json.dumps(document, indent=2), encoding="utf-8")
            written.replace(directory / name)
        index.append({"label": label, "key": key})
    return index


def family_index(gate: Gate) -> list[FamilyCase]:
    """Write the shapes of the candidate's family library once and return their index (`family_cases`)."""
    with gate.lock:
        if gate.family_index is None:
            gate.family_index, gate.family_problem = family_cases(
                gate.candidate_python, gate.candidate_info, gate.family_root
            )
        return gate.family_index


def family_problem(gate: Gate) -> list[Result]:
    """Return the family cell of a library the run could not list (`family_cases`), none where it listed or ran none."""
    if not gate.family_problem:
        return []
    return [
        Result(
            Cell(Path(), f"{FAMILY_PREFIX}library", "", "", (), FAMILY_KIND),
            Verdict("violation", (gate.family_problem,)),
        )
    ]


def tier_product(tier: str, types: Sequence[str], presets: list[str]) -> list[tuple[str, tuple[str, ...]]]:
    """Return the (output model type, options) pairs of a tier's family cells.

    The PR tier: each type with default options. The nightly and release tiers, the full product: each type with
    default options, the released presets (`preset_option_sets`) and each option set of `OPTION_SETS`.
    """
    pairs: list[tuple[str, tuple[str, ...]]] = [(backend, ()) for backend in types]
    if tier == "pr":
        return pairs
    others = [(backend, options) for options in OPTION_SETS for backend in types]
    return [*pairs, *preset_option_sets(presets, list(types)), *others]


def family_cells(cases: Sequence[FamilyCase], root: Path, pairs: Sequence[tuple[str, tuple[str, ...]]]) -> list[Cell]:
    """Return the family cells of (output model type, options) `pairs`.

    Each shape as JSON Schema x the pairs, and, where the pairs hold the targets' type with default options, its
    OpenAPI spelling with each target (`FAMILY_TARGETS`) and no other option. A target's output carries its whole
    runtime (about 200 KB a cell in the base-output cache), so the targets do not multiply by option sets: the full
    product would put about 10 GB of target outputs into the nightly tier's caches.
    """
    models = [(case, "jsonschema", backend, options) for case in cases for backend, options in pairs]
    targets = FAMILY_TARGETS if (FAMILY_TARGET_TYPE, ()) in pairs else ()
    targeted = [(case, "openapi", FAMILY_TARGET_TYPE, target) for case in cases for target in targets]
    return [
        Cell(root / case["key"] / FAMILY_SPELLINGS[file_type], case["label"], file_type, backend, options, FAMILY_KIND)
        for case, file_type, backend, options in (*models, *targeted)
    ]


def families_judged(path: Path) -> bool:
    """Return whether the allowlist file's `families` key judges the family cells (`fail`) or reports them only."""
    mode = tomllib.loads(path.read_text(encoding="utf-8")).get(FAMILIES_KEY, "report") if path.exists() else "report"
    if mode not in FAMILY_MODES:
        msg = f"{path}: {FAMILIES_KEY} = {mode!r}: write {' or '.join(map(repr, FAMILY_MODES))}"
        raise SystemExit(msg)
    return mode == "fail"


def in_verdict(gate: Gate, cell: Cell) -> bool:
    """Return whether a cell counts in the run's verdict: every cell but a family cell the run reports only."""
    return cell.kind != FAMILY_KIND or gate.families_judged


@dataclass(frozen=True)
class Placement:
    """Where the cells of a run go, and the allowlist entries that cover a cell of it."""

    anchors: dict[tuple[str, str], str] = field(default_factory=dict)
    entries: frozenset[int] = frozenset()
    skips: frozenset[int] = frozenset()
    groups: frozenset[int] = frozenset()

    def shard(self, cell: Cell, total: int) -> int:
        """Return the 1-based shard of a cell by the hash of its input, or of the first input of its entries."""
        return int(_stable(self.anchors.get((cell.label, cell.backend), cell.label)), 16) % total + 1


def place(
    pairs: Iterable[tuple[str, str]],
    allowlist: Allowlist,
    version: str,
    groups: Sequence[frozenset[tuple[str, str]]] = (),
) -> Placement:
    """Place the cells of these (input, output model type) pairs, so the cells of an allowlist entry share a shard.

    Entries that cover a common pair form a group (`[[base_skip]]` entries that skip the release `version` too, and
    `groups`: the pairs each selector of the change records covers); the pairs of a group are placed by its first
    input, and every other pair by its own input.
    """
    count = len(allowlist.entries)
    skips = count + len(allowlist.base_skips)
    covering: dict[tuple[str, str], list[int]] = {}
    for label, backend in set(pairs):
        nodes = [index for index in range(count) if allowlist.covers(index, label, backend)]
        if (skip := allowlist.skip_for(label, version)) is not None:
            nodes.append(count + skip)
        nodes += [skips + index for index, group in enumerate(groups) if (label, backend) in group]
        if nodes:
            covering[label, backend] = nodes
    parent = list(range(skips + len(groups)))

    def root(node: int) -> int:
        while parent[node] != node:
            node = parent[node]
        return node

    for nodes in covering.values():
        for node in nodes[1:]:
            parent[root(node)] = root(nodes[0])
    first: dict[int, str] = {}
    for (label, _), nodes in covering.items():
        group = root(nodes[0])
        first[group] = min(first.get(group, label), label)
    used = {node for nodes in covering.values() for node in nodes}
    return Placement(
        {pair: first[root(nodes[0])] for pair, nodes in covering.items()},
        frozenset(node for node in used if node < count),
        frozenset(node - count for node in used if count <= node < skips),
        frozenset(node - skips for node in used if node >= skips),
    )


def _write_tree(tree: dict[str, str], directory: Path) -> Path:
    for name, text in tree.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return directory / "model.py" if "model.py" in tree else directory / "pkg"


def cell_signature(
    gate: Gate, cell: Cell, tree: dict[str, str], directory: Path, payloads: Path | None
) -> dict[str, object]:
    """Write a cell's output tree (with its project's stubs) under `directory` and return its behaviour signature.

    A run that writes target packages (`TARGET_OPTIONS`: a client, a server) is signed as one tree, its models and
    each target package with its callables (`_behaviour_signature.run_signature`), so a change of a target alone
    shows; any other run by its models module or package.
    """
    models = _write_tree(tree | dict(cell.stubs), directory)
    targets = any(option in cell.options for option in TARGET_OPTIONS)
    return signature(gate, directory if targets else models, cell.backend, payloads, targets=targets)


def signature(
    gate: Gate, target: Path, backend: str, payloads: Path | None, *, targets: bool = False
) -> dict[str, Any]:
    """Compute a behaviour signature in a fresh process of the candidate's interpreter.

    With `targets`, `target` is a run's directory whose target packages are signed beside its models.
    """
    flags = [_behaviour_signature.TARGETS_FLAG] if targets else []
    arguments = [backend, *([str(payloads)] if payloads else []), *flags]
    return _signed(gate.signature_python or gate.candidate_python, gate.timeout, target, arguments)


def signature_of(python: Path, timeout: int, target: Path, backend: str, payloads: Path | None) -> dict[str, Any]:
    """Compute the behaviour signature of a generated module or package in a fresh process of `python`."""
    return _signed(python, timeout, target, [backend, *([str(payloads)] if payloads else [])])


def _signed(python: Path, timeout: int, target: Path, arguments: list[str]) -> dict[str, object]:
    """Run `scripts/_behaviour_signature.py` on `target` with `arguments` in a fresh process of `python`."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("DATAMODEL_CODE_GENERATOR")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        result = subprocess.run(
            [str(python), "-I", str(SIGNATURE_SCRIPT), str(target), *arguments],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=target.parent,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"import_error": f"signature timed out after {timeout}s", SIGNATURE_FAILED: True}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"import_error": "signature crashed: " + result.stderr.strip()[-200:], SIGNATURE_FAILED: True}


def payloads_for(gate: Gate, cell: Cell) -> Path | None:
    """Return the fixed payload vector of a payload case, drawn once with the candidate's test harness."""
    if cell.payload_key is None:
        return None
    digest = _stable(json.dumps([cell.label, cell.input.read_text(encoding="utf-8"), PAYLOAD_VALID, PAYLOAD_INVALID]))
    path = gate.payload_dir / f"{digest[:24]}.json"
    with gate.lock:
        lock = gate.draw_locks.setdefault(digest, threading.Lock())
    with lock:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    str(gate.candidate_python),
                    __file__,
                    "_draw-payloads",
                    str(gate.candidate_info.root),
                    cell.label.removeprefix("payload:"),
                    str(path),
                ],
                cwd=gate.candidate_info.root,
                env=gate.candidate_info.env,
                check=False,
                capture_output=True,
                timeout=PAYLOAD_DRAW_TIMEOUT * 3,
            )
    return path if path.exists() else None


def run_base_skipped(gate: Gate, cell: Cell, index: int) -> Result:
    """Generate one cell on the candidate only: the release is known to hang on the input (`[[base_skip]]`)."""
    entry = gate.allowlist.base_skips[index]
    note = f"base skipped: {entry.get('reason', '')} ({entry.get('issue', '')})"
    with tempfile.TemporaryDirectory() as name:
        tmp = Path(name)
        candidate = gate.candidate.generate(cell.args(), tmp)
        sig = cell_signature(gate, cell, candidate.tree, tmp / "sig-cand", None) if candidate.ok else None
    return Result(cell, classify_base_skipped(Side(candidate.ok, candidate.error, sig), note=note), index)


def run_cell(gate: Gate, cell: Cell) -> Result:
    """Generate one cell on both sides and classify it.

    The release's output is signed where the candidate refuses it too, so a refusal of an output that failed to
    import is told from one of an output that worked. A release that `[[base_skip]]` names for the input does not
    run at all; the base's output comes from the cache where it has it (`judge_outcomes` judges the two).
    """
    if (project := project_of(gate, cell)) is not None:
        try:
            project.fetch()
        except RuntimeError as exc:
            return Result(cell, Verdict("violation", (f"project checkout failed: {exc}",)))
        if not cell.input.exists():
            return Result(cell, Verdict("violation", (f"project input missing in the checkout: {cell.input.name}",)))
    if (skip := gate.allowlist.skip_for(cell.label, gate.base_version)) is not None:
        return run_base_skipped(gate, cell, skip)
    args = cell.args()
    with tempfile.TemporaryDirectory() as name:
        tmp = Path(name)
        base = gate.cache.get(cell.input, args, import_check=False)
        if base is None:
            base = gate.base.generate(args, tmp)
            gate.cache.put(cell.input, args, base, import_check=False)
        return judge_outcomes(gate, cell, (base, gate.candidate.generate(args, tmp)), tmp)


def judge_outcomes(
    gate: Gate, cell: Cell, outcomes: tuple[compare_release.Outcome, compare_release.Outcome], tmp: Path
) -> Result:
    """Classify a cell whose base and candidate generated into `tmp` (`run_cell`), signing their trees there.

    It adds the cell to the evidence, then gives the identity verdict, or the classification and, where the sides'
    facts differ, the verdict of the change records (`judge_cell`). A family cell the run reports only (`in_verdict`)
    adds nothing to the evidence, and no change record judges it; the PR tier does not sign it where both sides
    generated other bytes (`UNSIGNED_CLASS`).
    """
    base, candidate = outcomes
    if in_verdict(gate, cell):
        run = f"{cell.label} | {cell.backend} | {cell.option_label}"
        gate.evidence.add(input_group(cell), run, base, candidate)
    if gate.identity:
        return Result(cell, identity_verdict(base, candidate))
    same = base.ok and candidate.ok and base.tree == candidate.tree
    if gate.tier == "pr" and base.ok and candidate.ok and not same and not in_verdict(gate, cell):
        return Result(cell, Verdict(UNSIGNED_CLASS))
    sides = [Side(base.ok, base.error), Side(candidate.ok, candidate.error)]
    valid: frozenset[str] = frozenset()
    if base.ok and not same:
        payloads = payloads_for(gate, cell) if candidate.ok else None
        if payloads is not None:
            entries = json.loads(payloads.read_text(encoding="utf-8"))
            valid = frozenset(e["id"] for e in entries if e.get("kind") == "valid" and e.get("expect") == "accept")
        sides = [
            Side(
                generated=True,
                signature=cell_signature(gate, cell, outcome.tree, tmp / f"sig-{side}", payloads),
            )
            if outcome.ok
            else failed
            for (side, outcome), failed in zip((("base", base), ("cand", candidate)), sides, strict=True)
        ]
    verdict = classify_cell(
        sides[0],
        sides[1],
        input_path=cell.label,
        backend=cell.backend,
        allowlist=gate.allowlist,
        same_bytes=same,
        valid_payloads=valid,
    )
    if gate.plan is None or not in_verdict(gate, cell):
        return Result(cell, verdict)
    plain = classify_cell(
        sides[0],
        sides[1],
        input_path=cell.label,
        backend=cell.backend,
        allowlist=Allowlist(),
        same_bytes=same,
        valid_payloads=valid,
    )
    judged, lines = judge_cell(gate, cell, (base, candidate), sides, plain.cls)
    return Result(cell, verdict, judged=judged, lines=lines)


def identity_verdict(base: compare_release.Outcome, candidate: compare_release.Outcome) -> Verdict:
    """Return the identity verdict of a cell: any difference is a violation; a run a cap stopped is not compared."""
    if changes := compare_release.identity_changes(base, candidate):
        return Verdict("violation", tuple(detail for _, detail in changes))
    if base.ok:
        return Verdict("same")
    return Verdict("both-fail", (NOT_COMPARED_STOPPED,) if base.stopped() else ())


def not_compared(results: list[Result]) -> list[Result]:
    """Mark the cells of an input that fails on both sides in every cell as not compared."""
    cells = Counter(result.cell.label for result in results)
    failed = Counter(result.cell.label for result in results if result.verdict.cls == "both-fail")
    return [
        replace(result, verdict=Verdict("both-fail", (*result.verdict.notes, NOT_COMPARED_INPUT)))
        if failed[result.cell.label] == cells[result.cell.label]
        else result
        for result in results
    ]


def input_group(cell: Cell) -> str:
    """Return the input group of a cell (`compare_release.Evidence`).

    A cli-doc example, payload case or project job brings its own options, so its group is named by its kind.
    """
    directory = cell.kind if cell.kind == "payload" else Path(cell.label).parent.as_posix()
    options = cell.kind if cell.kind in {"cli-doc", "payload", "project"} else cell.option_label
    return compare_release.Evidence.group(cell.input, directory, cell.backend, options)


def option_changes(base: dict[str, list[str] | None], candidate: dict[str, list[str] | None]) -> list[str]:
    """Return each option and option value one side's datamodel-codegen has and the other's lacks."""

    def surface(inventory: dict[str, list[str] | None]) -> set[str]:
        return {
            " ".join(filter(None, (name, value)))
            for name, values in inventory.items()
            for value in (None, *(values or ()))
        }

    old, new = surface(base), surface(candidate)
    return [f"option added: {name}" for name in sorted(new - old)] + [
        f"option removed: {name}" for name in sorted(old - new)
    ]


@dataclass
class Plan:
    """What a gate run judges its differing cells by: the base of its changes, its records and the released presets.

    A PR run (BASE a revision) compares `base`, the merge base of BASE and the candidate, and judges by the records
    whose `id` that base lacks. A release run (BASE a release version or `refs/tags/<tag>`) compares the tag and
    judges by the records of the fragments the tag lacks; its `base` is the tag's commit, None where the repository
    has no such tag. `judged` are those records, an extending record as its target with its own id, covers and cells;
    `head` and `before` the candidate's and the base's records by id. `presets` are the latest tag's (None: the
    base's own, for a release the repository has no tag of). `changed` are the files below tests/data a PR changes;
    `src` says whether the candidate's src/ differs from the base's, which decides whether cells are generated (the
    stored proofs are judged per change, `stored_problems`), and `oracle` whether it changes the gate's own files (a
    release run always runs); `groups`
    the (input, output model type) pairs of the run each selector covers, which share a shard (`place`); `spaces` the
    label spaces whose cells the run builds (`build_cells`), which judge the selectors of those spaces only. `landed`
    are the records of a PR run that record a change merged before its base (`landed_records`), by id, with the
    pull requests they name.
    """

    base: str | None
    release: bool
    tag: str
    tag_commit: str | None
    judged: list[Record] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    presets: list[str] | None = None
    changed: list[str] = field(default_factory=list)
    src: bool = True
    oracle: bool = False
    head: dict[str, Record] = field(default_factory=dict)
    before: dict[str, Record] = field(default_factory=dict)
    groups: list[frozenset[tuple[str, str]]] = field(default_factory=list)
    spaces: frozenset[str] = frozenset({FIXTURE_SPACE})
    landed: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def selectors(self) -> list[tuple[Record, int, CellSelector]]:
        """Return each selector of the judged records with its record and its 1-based index there."""
        return [(record, index, selector) for record in self.judged for index, selector in enumerate(record.cells, 1)]


def _git_names(repo: Path, *args: str) -> list[str]:
    """Return the NUL-separated names a git command lists (`-z`); none where it fails."""
    return [name for name in compare_release.git(repo, *args).stdout.split("\0") if name]


def changed_paths(repo: Path, base: str, revision: str | None, paths: Sequence[str]) -> list[str]:
    """Return the files below `paths` that differ between two revisions of a repository (None: its checkout).

    A checkout counts its uncommitted and untracked files too, so a local run sees what CI will see after a commit.
    """
    names = _git_names(
        repo, "diff", "--name-only", "--no-renames", "-z", base, *([revision] if revision else []), "--", *paths
    )
    if not revision:
        names += _git_names(repo, "ls-files", "-z", "--others", "--exclude-standard", "--", *paths)
    return sorted(set(names))


def fragment_texts(repo: Path, revision: str | None, root: Path) -> dict[str, str]:
    """Return the text of each unreleased fragment by file name: a revision's, or the checkout's at `root` (None)."""
    if revision is None:
        return {path.name: path.read_text(encoding="utf-8") for path in sorted((root / FRAGMENTS).glob("*.toml"))}
    names = _git_names(repo, "ls-tree", "-z", "--name-only", revision, "--", f"{FRAGMENTS}/")
    return {
        PurePosixPath(name).name: compare_release.git(repo, "show", f"{revision}:{name}").stdout
        for name in names
        if name.endswith(".toml")
    }


def read_fragments(texts: dict[str, str], *, strict: bool) -> tuple[dict[str, Record], dict[str, list[str]]]:
    """Return the records of fragments by id and the problems of each id's table.

    A fragment that does not parse stops a `strict` read (the candidate's): the gate generates nothing then. A base's
    fragments are read for their ids and cells only, so one that does not parse there is left out.
    """
    records: dict[str, Record] = {}
    problems: dict[str, list[str]] = {}
    for name, text in sorted(texts.items()):
        try:
            entries = tomllib.loads(text).get("change", [])
        except tomllib.TOMLDecodeError as error:
            if strict:
                msg = f"{FRAGMENTS}/{name} does not parse: {error}"
                raise SystemExit(msg) from error
            continue
        for entry in entries if isinstance(entries, list) else []:
            record, found = parse_entry(entry, f"unreleased/{name}")
            if record is not None:
                records[record.id] = record
            elif found:
                problems.setdefault(str(entry.get("id")), []).extend(found)
    return records, problems


def _fragment_ids(texts: dict[str, str]) -> set[str]:
    """Return the ids the fragments of a revision name, whether or not their tables hold a record."""
    ids: set[str] = set()
    for text in texts.values():
        with contextlib.suppress(tomllib.TOMLDecodeError):
            ids |= {str(entry.get("id")) for entry in tomllib.loads(text).get("change", []) if isinstance(entry, dict)}
    return ids


def tag_presets(repo: Path, commit: str) -> list[str]:
    """Return the preset names a tag ships: its `preset_names.py`, read through the source model (none: no file)."""
    text = compare_release.git(repo, "show", f"{commit}:{PRESET_NAMES}").stdout
    if not text:
        return []
    module = SourceModule(text, PRESET_NAMES)
    names: list[str] = []
    for binding in module.scopes[0].bindings.get(PRESET_ALIAS, []):
        if isinstance(value := binding.value, ast.Subscript):
            literal = _literal(value.slice)
            names += [name for name in (literal if isinstance(literal, tuple) else (literal,)) if isinstance(name, str)]
    return names


def _released_ids(candidate: Candidate, tag: str, commit: str | None) -> set[str] | None:
    """Return the ids of the fragments a release shipped: those of its tag's tree.

    Without the tag (a shallow checkout), a release `releases.toml` lists, or one older than a listed release, was
    stamped: its fragments moved to `changes/`, so the fragments left are unreleased. Else None: they cannot be told.
    """
    if commit:
        return _fragment_ids(fragment_texts(candidate.repo, commit, candidate.root))
    with contextlib.suppress(FileNotFoundError, tomllib.TOMLDecodeError, ValueError):
        rows = tomllib.loads((candidate.root / RELEASES).read_text(encoding="utf-8")).get("release", [])
        if any(version_key(str(row["version"])) >= version_key(tag) for row in rows):
            return set()
    return None


def plan_records(ns: argparse.Namespace, candidate: Candidate) -> Plan:
    """Resolve the base of a gate run's changes and the records it judges by (`Plan`).

    An unresolvable base, merge base or latest tag of a PR run stops the gate, as does a candidate fragment that
    does not parse: nothing is generated then.
    """
    repo, spec = candidate.repo, ns.base
    head, head_problems = read_fragments(fragment_texts(repo, candidate.revision, candidate.root), strict=True)
    if compare_release.RELEASE.fullmatch(spec) or spec.startswith("refs/tags/"):
        tag = spec.removeprefix("refs/tags/")
        tag_commit = compare_release.commit_of(f"refs/tags/{tag}", repo) or None
        released = _released_ids(candidate, tag, tag_commit)
        if released is None:
            if any(record.cells for record in head.values()):
                msg = f"the fragments {tag} shipped cannot be told: {repo} has no tag {tag} and {RELEASES} lacks it"
                raise SystemExit(msg)
            released = set(head)
        plan = Plan(tag_commit, release=True, tag=tag, tag_commit=tag_commit, head=head)
        added = [record for record_id, record in head.items() if record_id not in released]
        problems = [
            problem for record_id, found in head_problems.items() if record_id not in released for problem in found
        ]
    else:
        if not (commit := compare_release.commit_of(spec, repo)):
            msg = f"{spec} is neither a released version nor a revision of {repo}"
            raise SystemExit(msg)
        head_commit = candidate.revision or compare_release.commit_of("HEAD", repo)
        if not (base := compare_release.git(repo, "merge-base", commit, head_commit).stdout.strip()):
            msg = f"{spec} and the candidate have no merge base in {repo}: fetch the history (fetch-depth 0)"
            raise SystemExit(msg)
        if not (tag := compare_release.git(repo, "describe", "--tags", "--abbrev=0", head_commit).stdout.strip()):
            msg = f"the candidate has no release tag in its history in {repo}: its presets and options come from it"
            raise SystemExit(msg)
        before, _ = read_fragments(fragment_texts(repo, base, candidate.root), strict=False)
        tag_commit = compare_release.commit_of(f"refs/tags/{tag}", repo) or None
        plan = Plan(base, release=False, tag=tag, tag_commit=tag_commit, head=head, before=before)
        plan.changed = changed_paths(repo, base, candidate.revision, ("tests/data",))
        plan.src = bool(changed_paths(repo, base, candidate.revision, ("src",)))
        plan.oracle = bool(changed_paths(repo, base, candidate.revision, ORACLE_FILES))
        added = [record for record_id, record in head.items() if record_id not in before]
        problems = [
            problem for record_id, found in head_problems.items() if record_id not in before for problem in found
        ]
        problems += [
            f"entry {record_id}: a record of the base gains [[change.cells]]; add a record with extends instead"
            for record_id, record in head.items()
            if record_id in before and set(record.cells) - set(before[record_id].cells)
        ]
        plan.landed = landed_records(repo, plan, added)
    for record in added:
        problems += rule_problems(record, fragment=True, added=not plan.release, records=head)
        resolved, _ = resolved_record(record, head)
        plan.judged += [resolved] if resolved is not None else []
    if tag_commit:
        plan.presets = tag_presets(repo, tag_commit)
    plan.problems = list(dict.fromkeys(problems))
    return plan


def merged_pulls(repo: Path, tag_commit: str | None, base: str) -> set[str]:
    """Return the pull requests merged after the latest tag up to a PR run's base, as `#n`.

    A squash commit's subject ends with its pull request (`record_prs.PR_SUFFIX`); the first-parent history after the
    tag (all of it without the tag) holds the changes the release lacks.
    """
    span = f"{tag_commit}..{base}" if tag_commit else base
    subjects = compare_release.git(repo, "log", "--first-parent", "--format=%s", span).stdout.splitlines()
    return {f"#{found[1]}" for subject in subjects if (found := record_prs.PR_SUFFIX.search(subject.rstrip()))}


def landed_records(repo: Path, plan: Plan, added: Sequence[Record]) -> dict[str, tuple[str, ...]]:
    """Return the records a PR run adds that record a change already merged at its base, with their pull requests.

    A record names its change's pull requests in its own `refs`: where every one of them was merged after the latest
    tag up to the base (`merged_pulls`), and an extending record's target is a record of the base, the change is
    already on main and the PR adds only its record. A PR that changes nothing under src/ may name its cells (no
    cell can differ from the base, `shortcut`); the runs against the release judge them.
    """
    candidates = [
        record
        for record in added
        if record.cells and record.refs and (record.extends is None or record.extends in plan.before)
    ]
    if not candidates or plan.base is None:
        return {}
    merged = merged_pulls(repo, plan.tag_commit, plan.base)
    return {record.id: record.refs for record in candidates if set(record.refs) <= merged}


def surface_problems(plan: Plan, inventory: dict[str, list[str] | None]) -> list[str]:
    """Return the selectors that name an option, option value, preset or input type the latest tag lacks (M3)."""
    found = []
    for record, index, selector in plan.selectors():
        sets = [list(option_set) for option_set in selector.options or ()]
        sets += [["--input-file-type", selector.input_file_type]] if selector.input_file_type else []
        found += [
            f"entry {record.id}: cells {index} name {reason}, which {plan.tag} lacks; unreleased surface is judged by "
            "the stored outputs only"
            for option_set in sets
            if (reason := opt_in_reason(option_set, inventory))
        ]
    return list(dict.fromkeys(found))


def plan_surface_problems(gate: Gate, inventory: dict[str, list[str] | None] | None, cache_dir: Path) -> list[str]:
    """Return the selectors of the gate's records that name a surface the latest tag lacks (`surface_problems`).

    `inventory` is the base's options: the released ones for a release plan, else (or None) the latest tag's are read.
    """
    plan = gate.plan
    if plan is None or not any(selector.options or selector.input_file_type for *_, selector in plan.selectors()):
        return []
    return surface_problems(
        plan, inventory if plan.release and inventory is not None else tag_inventory(gate, cache_dir)
    )


def tag_inventory(gate: Gate, cache_dir: Path) -> dict[str, list[str] | None]:
    """Return the options of the latest tag's datamodel-codegen, run from its tree with the candidate's interpreter."""
    plan, candidate = gate.plan, gate.candidate_info
    if plan is None or plan.tag_commit is None:
        msg = "the latest tag's options cannot be read: the repository has no tag in the candidate's history"
        raise SystemExit(msg)
    tree = compare_release.source_tree(plan.tag_commit, cache_dir, candidate.repo)
    return option_inventory(candidate.python, compare_release.run_environment(tree / "src"))


def nightly_sets(presets: list[str], backends: Sequence[str]) -> list[tuple[str, tuple[str, ...]]]:
    """Return the (output model type, options) pairs the nightly runs for one input, restricted to `backends`.

    Default options, the presets as `preset_option_sets` picks them and each option set of `OPTION_SETS`.
    """
    return [
        *((backend, ()) for backend in backends),
        *preset_option_sets(presets, list(backends)),
        *((backend, options) for options in OPTION_SETS for backend in backends),
    ]


def selector_cells(selector: CellSelector, root: Path, presets: list[str]) -> list[Cell]:
    """Return the cells a selector names: its input globs over the candidate's tree x its backends x its option sets.

    `options = "*"` names the nightly option sets of each input (`nightly_sets`); a cell's input type is the
    selector's, or the input's folder below tests/data.
    """
    backends = selector.backends or compare_release.DEFAULT_TYPES
    labels = sorted({label for glob in selector.input for label in selector_paths(glob, root)})
    cells = []
    for label in labels:
        parts = label.split("/")
        below = parts[2:] if parts[:2] == ["tests", "data"] else []
        if (file_type := selector.input_file_type or (below[0] if below else None)) is None:
            continue
        pairs = (
            nightly_sets(presets, backends)
            if selector.options is None
            else [(backend, options) for options in selector.options for backend in backends]
        )
        cells += [Cell(root / label, label, file_type, backend, options, "selector") for backend, options in pairs]
    return cells


def _other_text(outcome: compare_release.Outcome) -> str:
    """Return a side's other facts as lines (`compare_release.cell_facts`): messages, handled exceptions, refusal."""
    lines = [*outcome.messages.splitlines(), *(f"handled: {name}" for name in outcome.handled)]
    lines += [f"first attempt: {json.dumps(outcome.first, sort_keys=True)}"] * bool(outcome.first)
    lines += [f"import error: {outcome.import_error}"] * bool(outcome.import_error)
    return "\n".join(lines)


def _model_module(name: str) -> bool:
    """Return whether an output file is a model module: `model.py`, or a module of the package `pkg`."""
    return name == "model.py" or (name.startswith("pkg/") and name.endswith(".py"))


def _differing(old: compare_release.Facts, new: compare_release.Facts) -> list[str]:
    """Return the files whose hash differs between two sides' facts, or that one side lacks."""
    before, after = dict(old[1]), dict(new[1])
    return sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))


def _under(path: str, label: str) -> bool:
    return path == label or path.startswith(f"{label}/")


def payload_case_equal(gate: Gate, cell: Cell, case: PayloadCase) -> bool:
    """Return whether one payload case under a cell's input gives equal verdicts and parsed values on both sides.

    The case runs with the cell's options after its own; with `--preset` the case's `--target-python-version` is
    dropped, as the preset sets the target. Sides that both refuse the case alike give no payload evidence.
    """
    args = list(case["args"].get(cell.backend, []))
    if PRESET_OPTION in split_options(cell.options):
        args, _ = _without(args, "--target-python-version")
    probe = Cell(
        gate.payload_root / case["input"],
        f"payload:{case['case_id']}",
        case["file_type"],
        cell.backend,
        (*args, *cell.options),
        "payload",
        case["key"],
    )
    with tempfile.TemporaryDirectory() as name:
        tmp = Path(name)
        base = gate.cache.get(probe.input, probe.args(), import_check=False)
        if base is None:
            base = gate.base.generate(probe.args(), tmp)
            gate.cache.put(probe.input, probe.args(), base, import_check=False)
        head = gate.candidate.generate(probe.args(), tmp)
        if not (base.ok and head.ok):
            if equal := compare_release.cell_facts(base) == compare_release.cell_facts(head):
                with gate.lock:
                    gate.refused_alike.add(case["case_id"])
            return equal
        if (payloads := payloads_for(gate, probe)) is None:
            return False
        old, new = (
            signature(gate, _write_tree(outcome.tree, tmp / f"sig-{side}"), cell.backend, payloads)
            for side, outcome in (("base", base), ("cand", head))
        )
    return not (old.get(SIGNATURE_FAILED) or new.get(SIGNATURE_FAILED)) and old.get("payloads") == new.get("payloads")


def payloads_equal(gate: Gate, cell: Cell) -> bool:
    """Return whether every payload case under a cell's input gives equal verdicts and parsed values (6.1)."""
    cases = [case for case in payload_index(gate) if _under(f"tests/data/{case['case_id']}", cell.label)]
    if not cases:
        with gate.lock:
            gate.signature_only.add(cell.label)
    return all(payload_case_equal(gate, cell, case) for case in cases)


def without_features(options: tuple[str, ...], features: Iterable[str]) -> tuple[str, ...]:
    """Return a cell's options without those of experimental `cli-option.<name>` features, and their values."""
    names = {f"--{name}" for kind, _, name in (feature.partition(".") for feature in features) if kind == "cli-option"}
    rest: list[str] = []
    skip = False
    for option in options:
        if skip and not option.startswith("--"):
            skip = False
            continue
        skip = option in names
        if not skip and option.partition("=")[0] not in names:
            rest.append(option)
    return tuple(rest)


def companion_equal(gate: Gate, cell: Cell, records: Sequence[Record]) -> bool:
    """Return whether an experimental cell's companion is equal or covered: the cell without the features' options.

    A feature that is an input format has no options to remove; its cells have no companion.
    """
    features = [
        feature for record in records if record.category == Category.EXPERIMENTAL for feature in record.features
    ]
    if (rest := without_features(cell.options, features)) == cell.options:
        return True
    companion = run_cell(gate, replace(cell, options=rest, kind="companion"))
    return companion.judged is None or companion.judged.cls == "accepted"


def removed_options(gate: Gate, cell: Cell) -> frozenset[str]:
    """Return the options of a cell the candidate's datamodel-codegen no longer has."""
    with gate.lock:
        if gate.head_inventory is None:
            gate.head_inventory = option_inventory(gate.candidate.side.python, gate.candidate.env)
        inventory = gate.head_inventory
    return frozenset(
        name
        for option in cell.options
        if option.startswith("--") and (name := option.partition("=")[0]) not in inventory
    )


def judge_cell(
    gate: Gate,
    cell: Cell,
    outcomes: tuple[compare_release.Outcome, compare_release.Outcome],
    sides: list[Side],
    cls: str,
) -> tuple[Verdict | None, tuple[str, ...]]:
    """Judge a cell by the change records where its sides' facts differ, and return the verdict and change lines.

    Equal facts, and sides the timeout or the memory cap stopped both, need nothing (None). A head stopped where the
    base finished is a violation no record covers: its output is unfinished. Otherwise the head runs a second time,
    which must give the same facts; then the records that cover the cell must admit every kind of its difference and
    explain it together with the evidence of their categories (`judge`), computed only for the categories that cover
    it.
    """
    base, candidate = outcomes
    old, new = compare_release.cell_facts(base), compare_release.cell_facts(candidate)
    if gate.plan is None or (base.stopped() and candidate.stopped()) or old == new:
        return None, ()
    if candidate.stopped():
        return Verdict("violation", (HEAD_STOPPED,)), ()
    if gate.rerun is not None:
        with tempfile.TemporaryDirectory() as name:
            repeated = compare_release.cell_facts(gate.rerun.generate(cell.args(), Path(name)))
        if repeated != new:
            rows = (f"head: {json.dumps(new)[:300]}", f"rerun: {json.dumps(repeated)[:300]}")
            return Verdict("violation", ("nondeterministic: the head's second run gives other facts", *rows)), ()
    old_sig, new_sig = sides[0].signature, sides[1].signature
    changes = (
        [*diff(old_sig, new_sig), *probe_changes(old_sig, new_sig), *payload_changes(old_sig, new_sig)]
        if old_sig is not None and new_sig is not None
        else []
    )
    lines = change_lines(
        changes,
        exits=(base.code, candidate.code),
        warnings=(base.warnings, candidate.warnings),
        messages=(_other_text(base), _other_text(candidate)),
    )
    records = gate.plan.judged
    covering = [record for record in records if any(covers(selector, cell) for selector in record.cells)]
    categories = {record.category for record in covering}
    files = _differing(old, new)
    signature_equal = (
        old_sig is not None and new_sig is not None and not [c for c in diff(old_sig, new_sig) if c.behaviour]
    )
    text = Category.OUTPUT_TEXT in categories
    model_files = all(map(_model_module, files))
    held = (
        text
        and model_files
        and cls == "no-behaviour"
        and all(
            name in base.tree
            and name in candidate.tree
            and rule_b(base.tree[name], candidate.tree[name], signature_equal=signature_equal)
            for name in files
        )
    )
    evidence = CellEvidence(
        cls,
        lines,
        (base.code, candidate.code),
        model_files=model_files,
        rule_b=held,
        payloads=held and payloads_equal(gate, cell),
        signature_failed=any((side.signature or {}).get(SIGNATURE_FAILED) for side in sides),
        companion_equal=Category.EXPERIMENTAL in categories and companion_equal(gate, cell, covering),
        removed=removed_options(gate, cell) if Category.API in categories else frozenset(),
    )
    return judge(cell, kinds(old, new), records, evidence), lines


def _stored_files(side: declared_changes.Side, label: str) -> dict[str, str]:
    """Return the Python modules a stored label holds on one side, laid out as a run writes them.

    A file label is `model.py`; a tree label is the package `pkg` with its `.py` members. They are read from the
    storage the label's references compare (the label's own when it is one of them), so a shared output has its text.
    """
    storages = side.storages(label)
    storage = label if label in storages else storages[0]
    path, pattern = declared_changes.split_label(storage)
    if pattern is None:
        text = side.text(f"{EXPECTED}{path}")
        return {"model.py": text} if text is not None else {}
    return {
        f"pkg/{relative}": text
        for relative, file in side.members(storage)
        if relative.endswith(".py") and (text := side.text(file)) is not None
    }


def _module_label(label: str) -> bool:
    """Return whether a stored label holds Python modules: a `.py` file, or a tree (a package)."""
    path, pattern = declared_changes.split_label(label)
    return path.endswith(".py") if pattern is None else True


def stored_side(gate: Gate, target: Path, backend: str, values: Path | None) -> StoredSide:
    """Run `scripts/_stored_module.py` on one side's stored module in a fresh process: draw values, or judge them."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("DATAMODEL_CODE_GENERATOR")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    out = target.parent / f"{target.name}.{'verdicts' if values else 'values'}.json"
    command = [str(gate.candidate_python), "-E", "-m", "scripts._stored_module", str(target), backend, str(out)]
    with contextlib.suppress(subprocess.TimeoutExpired):
        subprocess.run(
            [*command, *([str(values)] if values else [])],
            cwd=ROOT,
            env=env,
            capture_output=True,
            check=False,
            timeout=PAYLOAD_DRAW_TIMEOUT * 3,
        )
    return json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}


def stored_payload_problem(gate: Gate, label: str, targets: list[Path], backend: str) -> str | None:
    """Return why the values drawn from each side's model schemas get other verdicts or parsed values, or None.

    Each side draws `STORED_DRAWS` values per public model from its own JSON Schema; both sides judge the union, so a
    value one side newly accepts or newly refuses shows. A model neither side can draw from has no payloads.
    """
    drawn = [stored_side(gate, target, backend, None) for target in targets]
    if not all(drawn):
        return f"{label}: a side wrote no draws (the timeout or a crash stopped it), so no payload could be compared"
    models = set(drawn[0].get("models", [])) & set(drawn[1].get("models", []))
    union: dict[str, list[object]] = {}
    for key in sorted(models):
        values = {
            json.dumps(value, sort_keys=True): value for side in drawn for value in side.get("values", {}).get(key, [])
        }
        if values:
            union[key] = list(values.values())
    if not union:
        with gate.lock:
            gate.no_payloads.add(label)
        return None
    given = targets[0].parent.parent / "values.json"
    given.write_text(json.dumps(union), encoding="utf-8")
    verdicts = [stored_side(gate, target, backend, given).get("verdicts", {}) for target in targets]
    changed = [key for key in union if verdicts[0].get(key) is None or verdicts[0].get(key) != verdicts[1].get(key)]
    return f"{label}: {changed[0]}: a value drawn from the models' schemas gets another verdict" if changed else None


def stored_module_problem(
    gate: Gate, label: str, sides: tuple[declared_changes.Side, declared_changes.Side]
) -> str | None:
    """Return why a stored module changed its behaviour between the base and the head, or None (6.2).

    Its signatures (`msgspec` imports read as `msgspec.Struct`, else the pydantic adapter path) show no behaviour
    change, rule (b) holds on each changed module, and values drawn from both sides' schemas get the same verdicts and
    parsed values.
    """
    if any(len(side.storages(label)) > 1 for side in sides):
        return f"{label}: its references compare several stored units, which the proof cannot read as one module"
    old, new = (_stored_files(side, label) for side in sides)
    if set(old) != set(new):
        return f"{label}: a module was added or removed, which the proof cannot pair"
    backend = (
        "msgspec.Struct"
        if any(MSGSPEC_IMPORT.search(text) for text in (*old.values(), *new.values()))
        else "pydantic_v2.BaseModel"
    )
    with tempfile.TemporaryDirectory() as name:
        tmp = Path(name)
        targets = [_write_tree(files, tmp / side) for files, side in ((old, "base"), (new, "head"))]
        before, after = (signature(gate, target, backend, None) for target in targets)
        if before.get(SIGNATURE_FAILED) or after.get(SIGNATURE_FAILED):
            return f"{label}: a signature could not be taken"
        if changes := [change for change in diff(before, after) if change.behaviour] + probe_changes(before, after):
            return f"{label}: {changes[0].line()}"
        if not all(rule_b(old[file], new[file], signature_equal=True) for file in old if old[file] != new[file]):
            return f"{label}: rule (b) does not hold"
        return stored_payload_problem(gate, label, targets, backend)


def stored_label_problem(
    gate: Gate, label: str, category: Category, sides: tuple[declared_changes.Side, declared_changes.Side]
) -> str | None:
    """Return why a stored label an `output-text` or `harness` record of a PR covers fails its proof, or None.

    A label that exists on one side only follows R2's rule for added and removed outputs, and an unchanged one needs
    nothing. A changed one is proved as a module (`stored_module_problem`); a payload listing (`payloads/*`) is
    judged per reference, which fails where an existing reference's content changed; any other changed label fails:
    a payload companion holds verdicts, and the content of another kind of output changed with no step to tell a
    changed call from a changed package. A `harness` label `declared_changes.needs_proof` does not name is admitted
    (6.4): internal output, and a label that is unchanged or that one side lacks.
    """
    base, head = sides
    owners = {owner_of(reference.nodeid, label) for side in sides for reference in side.by_label.get(label, [])}
    if category == Category.HARNESS and not declared_changes.needs_proof(label, base, head):
        return None
    if label.startswith("payloads/"):
        changed = [
            reference.nodeid
            for reference in head.by_label.get(label, [])
            if reference in base.references
            and [base.digest(stored) for *_, stored in base.references[reference]]
            != [head.digest(stored) for *_, stored in head.references[reference]]
        ]
        return f"{label}: the stored payloads of {changed[0]} changed" if changed else None
    old, new = base.content(label), head.content(label)
    if old is None or new is None or old == new:
        return None
    if _module_label(label):
        return stored_module_problem(gate, label, sides)
    if category == Category.HARNESS:
        return (
            f"{label}: a harness record covers a changed {', '.join(sorted(owners)) or 'models'} output in a src PR; "
            "move the test change to its own PR, or declare the change with its category"
        )
    companion = label.endswith(".payloads.txt") or any(
        COMPANION_LINE.search(side.text(f"{EXPECTED}{label}") or "") for side in sides
    )
    kind = "a payload companion: its verdict lines" if companion else "neither a module nor a payload companion: it"
    return f"{label}: {kind} changed"


def stored_problems(gate: Gate) -> list[str]:
    """Return the stored labels of a PR's src changes whose proof fails: the covers those changes add to records.

    The PR is each change from the base as main takes them (`declared_changes.change_steps`), so a push of several
    pull requests to main is judged as each of them was, also where a later one reverts src/, and a change that
    leaves src/ alone needs no proof.
    """
    plan, candidate = gate.plan, gate.candidate_info
    if plan is None or plan.release or plan.base is None:
        return []
    repo, problems = candidate.repo, []
    records = {plan.base: plan.before, candidate.revision: plan.head}
    for old, new in declared_changes.change_steps(repo, plan.base, candidate.revision):
        for revision in {old, new} - records.keys():
            records[revision] = read_fragments(fragment_texts(repo, revision, candidate.root), strict=False)[0]
        if changed_paths(repo, old, new, ("src",)):
            problems += step_problems(gate, (old, new), records[old], records[new])
    return problems


def step_problems(
    gate: Gate, revisions: tuple[str, str | None], before: dict[str, Record], head: dict[str, Record]
) -> list[str]:
    """Return the stored labels of one src change whose proof fails, by the records `before` and `head` it.

    The labels are those of the `output-text` records the change adds, and those a `harness` record adds to its
    covers; the verdict on each `harness` label that needs the proof goes to `Gate.verdicts` by its base and head
    contents. A revision None is the candidate's checkout.
    """
    labels: list[tuple[str, Category]] = [
        (label, Category.OUTPUT_TEXT)
        for record_id, record in head.items()
        if record_id not in before
        and (resolved := resolved_record(record, head)[0]) is not None
        and resolved.category == Category.OUTPUT_TEXT
        for label in resolved.covers
    ]
    for record_id, record in head.items():
        target, _ = resolved_record(record, head)
        if target is not None and target.category == Category.HARNESS:
            known = set(before[record_id].covers) if record_id in before else set()
            labels += [(label, Category.HARNESS) for label in record.covers if label not in known]
    if not labels:
        return []
    repo = gate.candidate_info.repo
    sides = (declared_changes.Side(repo, revisions[0]), declared_changes.Side(repo, revisions[1]))
    problems = []
    for label, category in dict.fromkeys(labels):
        problem = stored_label_problem(gate, label, category, sides)
        if category == Category.HARNESS and declared_changes.needs_proof(label, *sides):
            digests = {"base": sides[0].content(label), "head": sides[1].content(label)}
            gate.verdicts.append({"label": label, **digests, "problem": problem})
        problems += [problem] if problem else []
    return problems


def judge_stored_labels(gate: Gate, ns: argparse.Namespace) -> None:
    """Judge the stored labels of a PR's src changes and write the verdicts on their `harness` labels to `--verdicts`.

    `declared_changes.py check` reads the file. Shard 1 judges the stored labels (`stored_problems`, their problems
    join the plan's) and writes the file; the other shards leave it alone, so the shards of one run may share it.
    """
    if gate.plan is not None and ns.shard_index == 1:
        gate.plan.problems += stored_problems(gate)
    if ns.verdicts and ns.shard_index == 1:
        ns.verdicts.parent.mkdir(parents=True, exist_ok=True)
        ns.verdicts.write_text(json.dumps({"verdicts": gate.verdicts}, indent=1) + "\n", encoding="utf-8")


def base_arguments(argv: list[str]) -> list[str]:
    """Return the arguments of the head's gate without `HEAD_ONLY_OPTIONS` (argparse also takes a unique prefix).

    The base's verdicts judge nothing; a pick adds the head's cells, which the base's gate does not judge; and a base
    before an option refuses it. What follows `--` is positional (a checkout named like an option) and stays.
    """
    dropped: set[int] = set()
    index = 0
    while index < len(argv) and argv[index] != "--":
        name, given, _ = argv[index].partition("=")
        taken = 1
        if len(name) > len("--") and any(option.startswith(name) for option in HEAD_ONLY_OPTIONS):
            taken += not given
            dropped |= set(range(index, index + taken))
        index += taken
    return [arg for index, arg in enumerate(argv) if index not in dropped]


def base_gate(plan: Plan, ns: argparse.Namespace, repo: Path) -> int | None:
    """Run the base's gate on the same arguments where a PR changes the gate's own files, and return its status.

    The base's `scripts/` (and the test units it reads) are extracted with `git archive`, once under a lock as another
    run may share the cache; a base without the gate, and the base's own gate (`BASE_GATE_ENV`), run none. The base's
    gate runs for the same checkout (`REPOSITORY_ENV`), and writes its `--json` and `--evidence` files beside its
    scripts, not over the head's; it gets no `--verdicts` (`base_arguments`). The step fails if either gate fails (S7).
    """
    if plan.release or not plan.oracle or plan.base is None or os.environ.get(BASE_GATE_ENV):
        return None
    if compare_release.git(repo, "cat-file", "-e", f"{plan.base}:scripts/behaviour_diff.py").returncode:
        print(f"base gate: {plan.base[:12]} has no scripts/behaviour_diff.py")
        return None
    root = ns.cache_dir.absolute() / "base-gates" / plan.base
    compare_release.built(root, lambda: _extract_gate(plan.base or "", repo, root))
    reports = (("--json", ns.json), ("--evidence", ns.evidence))
    own = [arg for name, path in reports if path is not None for arg in (name, str(root / f"base-{name[2:]}.out"))]
    env = {**os.environ, BASE_GATE_ENV: "1", REPOSITORY_ENV: str(repo)}
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "behaviour_diff.py"), *base_arguments(ns.argv), *own],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    print("".join(f"base gate: {line}\n" for line in (result.stdout + result.stderr).splitlines()[-40:]), end="")
    print(f"base gate ({plan.base[:12]}): exit {result.returncode}")
    return result.returncode


def _extract_gate(base: str, repo: Path, root: Path) -> None:
    """Extract the gate's files of a base revision (`BASE_GATE_FILES`, those it has) into `root`."""
    root.mkdir(parents=True)
    paths = [
        path for path in BASE_GATE_FILES if not compare_release.git(repo, "cat-file", "-e", f"{base}:{path}").returncode
    ]
    archive = root / "gate.tar"
    compare_release.git(repo, "archive", "--output", str(archive), base, *paths).check_returncode()
    subprocess.run(["tar", "-x", "-f", str(archive), "-C", str(root)], check=True)
    archive.unlink()


def build_cells(ns: argparse.Namespace, gate: Gate, inventory: dict[str, list[str] | None]) -> list[Cell]:
    """Return the cells of the selected tier, and set the label spaces it builds (`Plan.spaces`).

    Every run builds the fixture cells every selector names; the nightly and release tiers also build the payload
    and project cells, and the family cells where the run judges them (the PR tier's family cells are a sample of
    the product, and a report-only run judges none).
    """
    candidate = gate.candidate_info
    inputs = fixture_inputs(candidate, ns.base)
    types = (
        ns.types.split(",")
        if ns.types
        else (list(PR_TYPES) if ns.tier == "pr" else list(compare_release.DEFAULT_TYPES))
    )
    plan = gate.plan
    presets = (inventory.get("--preset") or []) if plan is None or plan.presets is None else plan.presets
    changed = plan.changed if plan is not None and not plan.release else changed_files(candidate, ns.base)
    every = inputs
    if ns.select:
        inputs = [(name, file_type) for name, file_type in inputs if _selected(name, ns.select)]
    elif ns.tier == "pr":
        inputs = pr_inputs(inputs, changed, gate.allowlist)
    cells = [Cell(candidate.root / name, name, file_type, backend) for name, file_type in inputs for backend in types]
    if plan is not None:
        if not plan.release:
            cells += [
                Cell(candidate.root / name, name, file_type, backend, options, "changed")
                for name, file_type in every
                if _selected(name, ns.select) and any(_under(path, name) for path in changed)
                for backend, options in nightly_sets(presets, compare_release.DEFAULT_TYPES)
            ]
        for record, index, selector in plan.selectors():
            named = selector_cells(selector, candidate.root, presets)
            if len(named) > SELECTOR_CELL_LIMIT:
                msg = (
                    f"entry {record.id}: cells {index} names {len(named)} cells, more than the {SELECTOR_CELL_LIMIT} "
                    "a run may take from one selector: narrow its input, backends or options"
                )
                raise SystemExit(msg)
            cells += [cell for cell in named if _selected(cell.label, ns.select)]
    spaces = {FIXTURE_SPACE}
    if ns.tier != "pr":
        spaces.add(PROJECT_SPACE)
        cells += [
            Cell(candidate.root / name, name, file_type, backend, options, "preset")
            for backend, options in preset_option_sets(presets, types)
            for name, file_type in inputs
        ]
        cells += [
            Cell(candidate.root / name, name, file_type, backend, options, "option-set")
            for options in OPTION_SETS
            for backend in types
            for name, file_type in inputs
        ]
        cells += [cell for cell in anchor_cells(candidate.root, types) if _selected(cell.label, ns.select)]
        gate.projects, projects = load_projects(ns.projects, ns.cache_dir.absolute())
        cells += [cell for cell in projects if cell.backend in types and _selected(cell.label, ns.select)]
        if not ns.select or any(pattern.startswith("payload:") for pattern in ns.select):
            spaces.add(PAYLOAD_SPACE)
            cells += [cell for cell in payload_cells(gate, types) if _selected(cell.label, ns.select)]
    if ns.families and (not ns.select or any(pattern.startswith(FAMILY_PREFIX) for pattern in ns.select)):
        named = family_cells(family_index(gate), gate.family_root, tier_product(ns.tier, types, presets))
        cells += [cell for cell in named if _selected(cell.label, ns.select)]
        spaces |= {FAMILY_SPACE} if ns.tier != "pr" and gate.families_judged else set()
    if plan is not None:
        plan.spaces = frozenset(spaces)
    cells += picked_cells(gate)
    unique: dict[tuple[str, str, str, tuple[str, ...]], Cell] = {}
    for cell in cells:
        unique.setdefault((cell.label, cell.file_type, cell.backend, cell.options), cell)
    return list(unique.values())


def picked_cells(gate: Gate) -> list[Cell]:
    """Return the cells of the pick (`--pick`) whose input the candidate has; `--select` narrows only the tier.

    A family cell's input is the spelling of its shape in the candidate's family library (`family_index`).
    """
    families = any(row.kind == FAMILY_KIND for row in gate.picked)
    keys = {case["label"]: case["key"] for case in family_index(gate)} if families else {}
    cells = []
    for row in gate.picked:
        if (path := _picked_input(gate, row, keys)) is None or not path.exists():
            print(f"pick: cell {row.label} has no input in the candidate: left out")
        else:
            cells.append(Cell(path, row.label, row.file_type, row.backend, row.options, row.kind))
    return cells


def _picked_input(gate: Gate, row: _cell_catalog.CatalogCell, keys: dict[str, str]) -> Path | None:
    """Return the input of a picked row: a fixture below the candidate, a family shape's spelling of its file type."""
    if row.kind != FAMILY_KIND:
        return gate.candidate_info.root / row.label
    if (key := keys.get(row.label)) is None or row.file_type not in FAMILY_SPELLINGS:
        return None
    return gate.family_root / key / FAMILY_SPELLINGS[row.file_type]


def pick_rows(
    ns: argparse.Namespace, candidate: Candidate, base: compare_release.Base, units: compare_release.Units | None
) -> list[_cell_catalog.CatalogCell]:
    """Return the catalog rows `--pick` adds (`_cell_catalog.pick_change`) and print the pick, or why there is none.

    The change is mapped as identity maps it (`units`, else mapped here). Its sub-line keys need the code objects
    of the interpreter that mapped it: where the candidate's is another than this one, the pick keys lines only.
    """
    if ns.pick is None:
        return []
    catalog, reason = _cell_catalog.loaded(ns.pick.absolute(), candidate.repo, base.revision)
    if catalog is None:
        print(f"pick: {reason}: the tier runs alone")
        return []
    src = candidate.root / "src"
    mapped = (
        units
        if units is not None
        else compare_release.changed_units(candidate.python, candidate.repo, base, candidate.revision, src)
    )
    same = Path(candidate.python).resolve() == Path(sys.executable).resolve()
    sides = (
        _cell_catalog.ChangeSide(base.revision, base.source if same else None),
        _cell_catalog.ChangeSide(candidate.revision, src if same else None),
    )
    chosen = _cell_catalog.pick_change(catalog, candidate.repo, mapped, sides)
    print("\n".join(chosen.lines()))
    return [catalog.cells[index] for index in chosen.picked]


def _selected(label: str, patterns: list[str]) -> bool:
    return not patterns or any(fnmatch.fnmatch(label, pattern) for pattern in patterns)


def _memory_mb(text: str) -> int:
    """Read `--memory-mb`: a number of MB, 0 for no cap; a negative cap is refused, as no limit could hold it."""
    if (value := int(text)) < 0:
        msg = f"{value}: the cap is 0 (no cap) or more MB"
        raise argparse.ArgumentTypeError(msg)
    return value


def _environment_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-j", type=int, default=os.cpu_count() or 1, help="parallel cells (default: CPU count)")
    parser.add_argument("--timeout", type=int, default=300, help="seconds before one generation or signature fails")
    parser.add_argument(
        "--memory-mb",
        type=_memory_mb,
        default=8192,
        help="data memory of a worker while it generates a cell, a safety net far above any real cell (0: no cap)",
    )
    parser.add_argument("--cache-dir", type=Path, default=compare_release._default_cache_dir())  # ruff: ignore[private-member-access]


def _shard_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--shard", default="1/1", help="run shard I of N, e.g. 2/8")


def _gate_args(parser: argparse.ArgumentParser) -> None:
    _environment_args(parser)
    parser.add_argument("--tier", choices=("pr", "nightly", "release"), default="pr")
    parser.add_argument("--types", default="", help="comma-separated --output-model-type values (default: per tier)")
    _shard_arg(parser)
    parser.add_argument(
        "--select",
        action="append",
        default=[],
        help="only cells whose input or label matches this glob (payload cases: 'payload:<case id>'; project jobs: "
        "'project:<project>/<job>'; family cells: 'family:<family>/<parameter>=<value>,...'); replaces the PR sample",
    )
    parser.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST)
    parser.add_argument(
        "--projects",
        type=Path,
        default=DEFAULT_PROJECTS,
        help="manifest of the projects tier (nightly and release): repository, pinned commit, license, jobs",
    )
    parser.add_argument("--base-python", type=Path, help="interpreter with the release already installed")
    parser.add_argument("--candidate-python", type=Path, help="interpreter for the candidate")
    compare_release.add_evidence_args(parser)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    gate = commands.add_parser("gate", help="classify every output change of the candidate against the base")
    gate.add_argument("base", nargs="?", help="released version (default: the latest tag) or git revision")
    gate.add_argument("candidate", nargs="?", help="checkout path or git revision (default: this checkout)")
    _gate_args(gate)
    gate.add_argument("--check", action="store_true", help="exit 1 on a violation or an invalid or stale allowlist")
    gate.add_argument("--json", type=Path, help="write every cell as one JSON line to this file (read by `guide`)")
    gate.add_argument(
        VERDICTS_OPTION,
        type=Path,
        help="write the verdicts on the harness labels of a src PR to this file (read by `declared_changes.py check`); "
        "shard 1 writes it",
    )
    gate.add_argument(
        "--identity",
        action="store_true",
        help="no allowlist: any difference in output, warnings, handled exceptions, messages, exit status or "
        "options is a violation, and so is evidence that compared too little; implies --check",
    )
    gate.add_argument(
        PICK_OPTION,
        type=Path,
        help="cell catalog (`catalog`): add its cells that run the change against a BASE revision to the run",
    )
    gate.set_defaults(families=True)
    catalog = commands.add_parser("catalog", help="record a cell catalog: the fixtures' cells and what each ran")
    catalog.add_argument("out", type=Path, help="catalog file to write (gzip JSON)")
    catalog.add_argument("revision", nargs="?", default="HEAD", help="revision of this repository (default: HEAD)")
    catalog.add_argument("--types", default="pydantic_v2.BaseModel", help="comma-separated --output-model-type values")
    catalog.add_argument("--options", default="default", help="option sets separated by ';' ('default' = none)")
    catalog.add_argument(
        "--select",
        action="append",
        default=[],
        help="only fixtures whose path matches this glob; family cells where one names them ('family:*')",
    )
    _environment_args(catalog)
    catalog.set_defaults(j=2)
    record = commands.add_parser("record", help="write the behaviour record of one release")
    record.add_argument("version", help="release version, or the version a checkout is recorded as")
    record.add_argument("checkout", nargs="?", help="checkout path or git revision to record instead of the release")
    _environment_args(record)
    record.add_argument("--out", type=Path, default=DEFAULT_RECORDS, help="record directory")
    record.add_argument("--types", default=",".join(compare_release.DEFAULT_TYPES))
    record.add_argument("--select", action="append", default=[], help="only inputs matching this glob")
    record.add_argument("--python", type=Path, help="interpreter that has the release (default: an era environment)")
    _shard_arg(record)
    merge = commands.add_parser("merge-record", help="merge the records of `record --shard I/N` into one record")
    merge.add_argument("version", help="recorded version")
    merge.add_argument("parts", type=Path, nargs="+", help="record directories of the shards")
    merge.add_argument("--out", type=Path, default=DEFAULT_RECORDS, help="record directory")
    query = commands.add_parser("query", help="compare two recorded releases without generating anything")
    query.add_argument("base", help="older recorded version, or `all` for every recorded version before VERSION")
    query.add_argument("version", help="newer recorded version")
    query.add_argument("--records", type=Path, default=DEFAULT_RECORDS, help="record directory")
    query.add_argument("--md", type=Path, help="write the comparison as markdown")
    query.add_argument("--json", type=Path, help="write the comparison as JSON (the upgrade page data with `all`)")
    guide = commands.add_parser("guide", help="write the output-change page data and release notes of a release")
    guide.add_argument("base", help="previous release")
    guide.add_argument("version", help="the release the page describes")
    guide.add_argument("--candidate", help="checkout path or git revision of the release (default: this checkout)")
    _gate_args(guide)
    guide.set_defaults(tier="release", identity=False, verdicts=None, pick=None, families=False)
    guide.add_argument("--cells", type=Path, action="append", default=[], help="gate --json output (default: run)")
    guide.add_argument("--docs", type=Path, required=True, help="page data to write, docs/data/output-changes/V.json")
    guide.add_argument("--notes", type=Path, help="release-note fragment to write")
    ns = parser.parse_args(argv)
    if ns.command in {"gate", "guide"}:
        ns.base = ns.base or latest_tag()
    if ns.command in {"gate", "guide", "record"}:
        index, _, total = ns.shard.partition("/")
        try:
            ns.shard_index, ns.shard_total = int(index), int(total or 1)
        except ValueError:
            parser.error(f"--shard {ns.shard}: I and N must be integers, e.g. 2/8")
        if not 1 <= ns.shard_index <= ns.shard_total:
            parser.error(f"--shard {ns.shard}: I must be between 1 and N")
        if ns.command == "gate" and ns.identity and ns.shard_total > 1:
            parser.error(
                f"--identity --shard {ns.shard}: identity judges the evidence of the whole run, run it unsharded"
            )
    return ns


def cell_row(result: Result) -> dict[str, Any]:
    """Return one classified cell as a JSON row (the input of `guide`)."""
    cell, verdict = result.cell, result.verdict
    return {
        "input": cell.label,
        "file_type": cell.file_type,
        "type": cell.backend,
        "options": cell.option_label,
        "kind": cell.kind,
        "class": verdict.cls,
        "notes": list(verdict.notes),
        "accepted_by": sorted(verdict.accepted_by),
    }


COMPOSITION_PREFIX: Final = COMPOSITION_SPACE
"""The start of the labels of the random tier's composition cells, which the gate has none of."""


def selector_problems(
    plan: Plan, results: list[Result], ns: argparse.Namespace, shards: tuple[Placement, Placement]
) -> list[str]:
    """Return the selectors that match no differing cell of the run (9.3): no cell, or only equal ones.

    A PR run judges every selector of its records; a release run judges staleness in a run of the whole nightly or
    release tier only, as the PR tier is a sample. A selection may leave a selector's cells out, so it judges none.
    A selector is judged in the shard that runs its cells (`place`), or in shard 1 where it covers no cell at all.
    A run judges only the selectors of the label spaces it builds (`Plan.spaces`): the PR tier has no payload or
    project cells, and no run of this gate the random tier's compositions.
    """
    here, run = shards
    if ns.select or (plan.release and ns.tier == "pr"):
        return []
    lead = ns.shard_index == 1
    stale = " (stale: edit the record)" if plan.release else ""
    found = []
    for position, (record, index, selector) in enumerate(plan.selectors()):
        if not selector.spaces <= plan.spaces:
            continue
        if position not in here.groups and not (lead and position not in run.groups):
            continue
        matched = [result for result in results if covers(selector, result.cell)]
        if not any(result.judged is not None for result in matched):
            what = "only equal cells" if matched else "no cell of the run"
            landed = (
                f" (it records {', '.join(plan.landed[record.id])}, merged before {(plan.base or '')[:12]}: name its "
                "cells in a PR that changes nothing under src/)"
                if record.id in plan.landed
                else ""
            )
            found.append(f"entry {record.id}: cells {index} matches {what}{stale}{landed}")
    return found


def cells_block(results: list[Result]) -> list[str]:
    """Return a starting `[[change.cells]]` block for the differing cells no record covers, one selector per input.

    `options = "*"` where an input's uncovered cells span more than one option set; the change lines' aspects and a
    first line as a `match` for a decision category, as comments.
    """
    groups: dict[tuple[str, str], list[Result]] = {}
    for result in results:
        if result.judged is not None and UNCOVERED in result.judged.notes:
            groups.setdefault((result.cell.label, result.cell.file_type), []).append(result)
    lines: list[str] = []
    for (label, file_type), group in sorted(groups.items()):
        sets = sorted({result.cell.options for result in group})
        parts = label.split("/")
        lines += [
            "[[change.cells]]",
            f"input = {json.dumps([label])}",
            f"backends = {json.dumps(sorted({result.cell.backend for result in group}))}",
            'options = "*"' if len(sets) > 1 else f"options = {json.dumps([list(sets[0])])}",
        ]
        if parts[:2] != ["tests", "data"] or parts[2:3] != [file_type]:
            lines.append(f"input_file_type = {json.dumps(file_type)}")
        if changed := sorted({line for result in group for line in result.lines}):
            aspects = sorted({found[1] for line in changed if (found := LINE_ASPECT.match(line))})
            match = "^" + REGEX_SPECIAL.sub(lambda special: "\\" + special[0], changed[0]) + "$"
            lines += [f"# a decision names: aspects = {json.dumps(aspects)}", f"# match = {json.dumps(match)}"]
        lines.append("")
    return lines


def report_families(results: list[Result]) -> None:
    """Print the family cells of a run that reports them only (`families = "report"`) apart from its verdict.

    Each cell that is neither the same on both sides nor opt-in, with its notes, and the count of each class.
    """
    if not results:
        return
    for result in results:
        if (verdict := result.verdict).cls not in {"same", "opt-in"}:
            cell = result.cell
            print(f"family (report only) {verdict.cls}: {cell.label} | {cell.backend} | {cell.option_label}")
            print("".join(f"    {note[:240]}\n" for note in verdict.notes[:6]), end="")
    counts = Counter(result.verdict.cls for result in results)
    classes = ", ".join(f"{name}={counts[name]}" for name in (*CLASSES, UNSIGNED_CLASS) if counts[name])
    print(f"families (report only, the allowlist file's {FAMILIES_KEY} key): {len(results)} cells, {classes}")


def _report(results: list[Result], gate: Gate, ns: argparse.Namespace, run: Placement | None = None) -> int:
    """Print the results of a shard and return the exit code; `run` places the cells of every shard (default: these).

    With change records (`Gate.plan`) a cell fails on the records' verdict where its facts differ (`Result.final`),
    and the run on the records' problems (`Plan.problems`) and on a selector that matches no differing cell.
    """
    counts = dict.fromkeys(CLASSES, 0)
    used: set[int] = set()
    skipped = Counter(r.base_skip for r in results if r.base_skip is not None)
    plan = gate.plan
    for result in results:
        counts[result.verdict.cls] += 1
        used |= result.verdict.accepted_by
        shown = result.final if plan is not None else result.verdict
        if shown.cls == "violation" or (shown.cls == "accepted" and plan is None):
            cell = result.cell
            print(f"{shown.cls}: {cell.label} | {cell.backend} | {cell.option_label}")
            for note in [*shown.notes, *(result.verdict.notes if shown is not result.verdict else ())][:6]:
                print(f"    {note[:240]}")
    for index, cells in sorted(skipped.items()):
        entry = gate.allowlist.base_skips[index]
        print(f"base-skipped: {entry.get('input')} | {entry.get('reason')} ({entry.get('issue')}) | {cells} cells")
    problems = gate.loaded_allowlist.problems(ns.base)
    groups = plan.groups if plan is not None else ()
    here = place([(r.cell.label, r.cell.backend) for r in results], gate.allowlist, ns.base, groups)
    run = here if run is None else run
    judged = ns.tier != "pr"
    whole = judged and not ns.select
    lead = whole and ns.shard_index == 1
    problems += [
        f"base_skip entry {index + 1} ({entry.get('input')}) is stale: it covers no cell"
        for index, entry in enumerate(gate.allowlist.base_skips)
        if whole
        and index not in skipped
        and gate.allowlist.skip_applies(index, ns.base)
        and (index in here.skips or (lead and index not in run.skips))
    ]
    problems += [
        f"allowlist entry {index + 1} ({gate.allowlist.entries[index].get('input')}) is stale: "
        + ("it accepted no change" if index in here.entries else "it covers no cell")
        for index in range(len(gate.allowlist.entries))
        if judged and index not in used and (index in here.entries or (lead and index not in run.entries))
    ]
    for problem in problems:
        print(f"allowlist: {problem}")
    print("summary:", ", ".join(f"{name}={count}" for name, count in counts.items()))
    print(
        f"cells: {len(results)} (tier {ns.tier}, shard {ns.shard_index}/{ns.shard_total}), "
        f"cached base outputs: {gate.cache.hits}"
    )
    failed = counts["violation"]
    if plan is not None:
        records = [*plan.problems, *selector_problems(plan, results, ns, (here, run))]
        problems += records
        verdicts = Counter(result.judged.cls for result in results if result.judged is not None)
        failed = sum(result.final.cls == "violation" for result in results)
        accepted, violations = verdicts["accepted"], verdicts["violation"]
        if verdicts or records:
            print(f"records: {len(plan.judged)} judged; differing cells accepted={accepted}, violation={violations}")
        print("".join(f"records: {problem}\n" for problem in records), end="")
        applied = Counter(
            index for result in results if result.judged is not None for index in result.judged.accepted_by
        )
        for index, count in sorted(applied.items()):
            record = plan.judged[index]
            decided = f"; decision {', '.join(record.decision)}" if record.decision else ""
            print(f"covered: {record.id} ({record.category}): {count} differing cells{decided}")
        if gate.signature_only:
            print(f"signature only (no payload case under the input): {', '.join(sorted(gate.signature_only))}")
        if gate.refused_alike:
            print(
                f"payload cases both sides refused alike (no payload evidence): {', '.join(sorted(gate.refused_alike))}"
            )
        if gate.no_payloads:
            print(
                f"stored modules with no payloads (signatures and rule (b) only): {', '.join(sorted(gate.no_payloads))}"
            )
        if block := cells_block(results):
            print("uncovered differing cells; a starting [[change.cells]] block for their record:")
            print("\n".join(block), end="")
    if ns.json:
        ns.json.write_text("".join(json.dumps(cell_row(result)) + "\n" for result in results), encoding="utf-8")
    return 1 if ns.check and (failed or problems) else 0


def build_gate(ns: argparse.Namespace) -> Gate:
    """Return the gate of a base and a candidate."""
    cache_dir = ns.cache_dir.absolute()
    candidate = resolve_candidate(ns.candidate, ns.candidate_python, cache_dir)
    if ns.base_python is not None and not (
        compare_release.RELEASE.fullmatch(ns.base) or ns.base.startswith("refs/tags/")
    ):
        msg = f"--base-python runs an installed release, but {ns.base} is a revision: its own tree is its base"
        raise SystemExit(msg)
    plan = None if ns.identity else plan_records(ns, candidate)
    spec = plan.base if plan is not None and not plan.release and plan.base is not None else ns.base
    base = (
        compare_release.installed_base(ns.base_python.absolute())
        if ns.base_python is not None
        else compare_release.resolve_base(spec, candidate.python, cache_dir, candidate.repo)
    )
    listed = not ns.identity and base.revision is None and ns.allowlist.exists()
    allowlist = Allowlist.load(ns.allowlist) if listed else Allowlist()
    units = (
        compare_release.changed_units(
            candidate.python, candidate.repo, base, candidate.revision, candidate.root / "src"
        )
        if ns.identity
        else None
    )
    gate = Gate(
        base=compare_release.Runner(
            compare_release.Side("base", base.python, base.package),
            base.env(),
            import_check=False,
            timeout=ns.timeout,
            memory_mb=ns.memory_mb,
        ),
        candidate=compare_release.Runner(
            compare_release.Side("cand", candidate.python, candidate.root / "src"),
            candidate.env,
            import_check=False,
            timeout=ns.timeout,
            memory_mb=ns.memory_mb,
        ),
        cache=compare_release.OutputCache(
            cache_dir / "outputs" / base.cache_name, compare_release.measured_key(units, base.python)
        ),
        candidate_python=candidate.python,
        allowlist=allowlist if allowlist.base == ns.base else Allowlist(allowlist.base, [], allowlist.base_skips),
        loaded_allowlist=allowlist,
        timeout=ns.timeout,
        payload_dir=cache_dir / "payloads",
        payload_root=cache_dir / "payload-inputs" / _stable(str(candidate.root))[:12],
        candidate_info=candidate,
        family_root=cache_dir / "family-inputs" / _stable(str(candidate.root))[:12],
        families_judged=families_judged(ns.allowlist),
        tier=ns.tier,
        base_version=ns.base,
        identity=ns.identity,
        evidence=compare_release.Evidence(units),
        plan=plan,
    )
    if plan is not None:
        gate.rerun = compare_release.Runner(
            compare_release.Side("cand", candidate.python, candidate.root / "src"),
            candidate.env,
            import_check=False,
            timeout=ns.timeout,
            memory_mb=ns.memory_mb,
        )
    if units is not None:
        gate.base.probes, gate.candidate.probes = units.probes["base"], units.probes["head"]
    gate.picked = pick_rows(ns, candidate, base, units)
    return gate


def gate_of(argv: Sequence[str]) -> Gate:
    """Return the gate that `gate ARGV...` runs (`build_gate`), for a caller that generates and judges cells of its own.

    Nothing is generated: the caller runs its cells through `gate.base` and `gate.candidate` and judges them with
    `judge_outcomes`, by the identity verdict (`--identity`) or by the change records the base lacks.
    """
    return build_gate(_parse_args(["gate", *argv]))


def shortcut(gate: Gate, plan: Plan, ns: argparse.Namespace) -> int:
    """Pass a PR that changes nothing under src/ without generating: every cell is equal by construction.

    A PR record that names cells fails then, as no cell can differ, except a record of a change already merged
    (`Plan.landed`), whose cells the runs against the release judge: its selectors must name released surface only
    (`plan_surface_problems`), as those runs refuse any other. The base's gate still runs on a change of the gate's
    own files (`base_gate`). The stored proofs of its src changes (a push to main that reverts one) are judged before
    (`judge_stored_labels`), and their problems are the plan's.
    """
    base = (plan.base or "")[:12]
    problems = [
        *plan.problems,
        *(
            f"entry {record.id}: src/ is unchanged since {base}, so its cells cannot differ"
            for record in plan.judged
            if record.cells and record.id not in plan.landed
        ),
        *plan_surface_problems(gate, None, ns.cache_dir.absolute()),
    ]
    print(f"src/ unchanged since the merge base {base}: every cell is equal")
    print(
        "".join(
            f"records: entry {record_id} records {', '.join(refs)}, merged before {base}: the runs against the release "
            "judge its cells\n"
            for record_id, refs in plan.landed.items()
        ),
        end="",
    )
    print("".join(f"records: {problem}\n" for problem in problems), end="")
    status = base_gate(plan, ns, gate.candidate_info.repo)
    return 1 if ns.check and (problems or status) else 0


def gate_main(ns: argparse.Namespace) -> int:
    """Run the gate; `--identity` fails on any difference, as `--check` does.

    Without `--identity` the change records judge every cell whose facts differ (`Plan`, `judge_cell`).
    """
    started = time.monotonic()
    ns.check = ns.check or ns.identity
    gate = build_gate(ns)
    plan = gate.plan
    if plan is not None and not plan.release and not plan.src:
        judge_stored_labels(gate, ns)
        return shortcut(gate, plan, ns)
    inventory = option_inventory(gate.base.side.python, gate.base.env)
    offered = option_inventory(gate.candidate.side.python, gate.candidate.env) if gate.identity else inventory
    if plan is not None:
        plan.problems += plan_surface_problems(gate, inventory, ns.cache_dir.absolute())
    cells = build_cells(ns, gate, inventory)
    if plan is not None:
        plan.groups = [
            frozenset((cell.label, cell.backend) for cell in cells if covers(selector, cell))
            for *_, selector in plan.selectors()
        ]
    run = place([(cell.label, cell.backend) for cell in cells], gate.allowlist, ns.base, plan.groups if plan else ())
    cells = [cell for cell in cells if run.shard(cell, ns.shard_total) == ns.shard_index]
    reasons = {cell: reason for cell in cells if (reason := opt_in_reason(cell.args(), inventory))}
    runnable = [cell for cell in cells if cell not in reasons]
    pool = ThreadPoolExecutor(ns.j)
    try:
        results = list(pool.map(lambda cell: run_cell(gate, cell), runnable))
    except BaseException:
        pool.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        pool.shutdown(wait=True)
    finally:
        gate.base.close()
        gate.candidate.close()
        if gate.rerun is not None:
            gate.rerun.close()
    judge_stored_labels(gate, ns)
    if gate.identity:
        results = not_compared(results)
        if ns.shard_index == 1 and (surface := option_changes(inventory, offered)):
            results.append(
                Result(Cell(Path(), "option surface", "", "", (), "options"), Verdict("violation", tuple(surface)))
            )
    results += [
        Result(cell, Verdict("opt-in", (f"the release has no {reason}",)))
        if opt_in_reason(cell.args(), offered)
        else Result(cell, Verdict("violation", (f"the base has no {reason}, the candidate has it",)))
        for cell, reason in reasons.items()
    ]
    results += family_problem(gate) if ns.shard_index == 1 else []
    report_families([result for result in results if not in_verdict(gate, result.cell)])
    results = [result for result in results if in_verdict(gate, result.cell)]
    code = _report(results, gate, ns, run)
    if plan is not None and base_gate(plan, ns, gate.candidate_info.repo) and ns.check:
        code = 1
    if gate.identity:
        unseen = sum(any(note.startswith("not compared") for note in result.verdict.notes) for result in results)
        failed = sum(result.verdict.cls == "both-fail" for result in results)
        print(f"not compared: {unseen} of the {failed} cells that fail on both sides")
    tests = compare_release.tested_lines(ns.test_coverage) if ns.test_coverage else None
    if gate.evidence.report(tests) and gate.identity:
        code = 1
    if ns.evidence:
        gate.evidence.write(ns.evidence)
    print(f"elapsed: {time.monotonic() - started:.1f}s", file=sys.stderr)
    return code


def _drop_unsupported_formatters(args: list[str], inventory: dict[str, list[str] | None]) -> list[str]:
    """Drop `--formatters` from a release that predates it (formatting is not behaviour)."""
    if "--formatters" in inventory or "--formatters" not in args:
        return args
    index = args.index("--formatters")
    end = index + 1
    while end < len(args) and not args[end].startswith("--"):
        end += 1
    return args[:index] + args[end:]


def _pypi_upload_date(version: str) -> str:
    import urllib.request  # ruff: ignore[import-outside-top-level]

    url = f"https://pypi.org/pypi/datamodel-code-generator/{version}/json"
    with urllib.request.urlopen(url, timeout=30) as response:
        files = json.load(response)["urls"]
    return min(file["upload_time_iso_8601"] for file in files)


def era_python(version: str, cache_dir: Path) -> Path:
    """Install a release with the packages of its release day (`uv --exclude-newer`) and return its interpreter.

    The newest Python of ERA_PYTHONS the release imports on is used. The signature step imports msgspec, which no
    release requires, so it is installed like black and isort, as is email-validator (`format: email` output).
    """
    import fcntl  # ruff: ignore[import-outside-top-level] - POSIX only, like compare_release

    root = cache_dir / "era" / f"{version}-{ERA_EXTRAS.replace(',', '-')}-{'-'.join(ERA_PACKAGES)}"
    root.parent.mkdir(parents=True, exist_ok=True)
    with (root.parent / f"{root.name}.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (root / ".complete").exists():
            return root / "bin" / "python"
        uploaded = dt.datetime.fromisoformat(_pypi_upload_date(version).replace("Z", "+00:00"))
        newer = (uploaded + dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for python in ERA_PYTHONS:
            shutil.rmtree(root, ignore_errors=True)
            install = [
                ["uv", "venv", "-q", "--python", python, str(root)],
                [
                    "uv",
                    "pip",
                    "install",
                    "-q",
                    "--python",
                    str(root / "bin" / "python"),
                    "--exclude-newer",
                    newer,
                    f"datamodel-code-generator[{ERA_EXTRAS}]=={version}",
                    *ERA_PACKAGES,
                ],
                [str(root / "bin" / "python"), "-c", "import datamodel_code_generator.__main__"],
            ]
            if all(subprocess.run(step, capture_output=True, check=False).returncode == 0 for step in install):
                (root / ".complete").touch()
                return root / "bin" / "python"
    msg = f"datamodel-code-generator {version} installs on none of {', '.join(ERA_PYTHONS)}"
    raise SystemExit(msg)


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def release_environment(python: Path) -> str:
    """Return the identity of a release environment: interpreter and package versions, not where it is installed."""
    freeze = subprocess.run(
        ["uv", "pip", "freeze", "--python", str(python)], capture_output=True, text=True, check=True
    ).stdout
    is_generator = compare_release._is_generator_requirement  # ruff: ignore[private-member-access]
    pins = sorted(line for line in freeze.splitlines() if line and not is_generator(line))
    return json.dumps([*compare_release._probe(python), pins])  # ruff: ignore[private-member-access]


def record_cell(gate: Gate, cell: Cell, args: list[str], out: Path, environment: str) -> dict[str, Any]:
    """Generate one cell with the recorded release and store its signature under `out/signatures`."""
    with tempfile.TemporaryDirectory() as name:
        tmp = Path(name)
        outcome = gate.cache.get(cell.input, args, import_check=False)
        if outcome is None:
            outcome = gate.base.generate(args, tmp)
            gate.cache.put(cell.input, args, outcome, import_check=False)
        if not outcome.ok:
            return {"gen": "fail", "error": redact(outcome.error)[:200]}
        payloads = payloads_for(gate, cell)
        payload_text = payloads.read_text(encoding="utf-8") if payloads else ""
        key = _stable(json.dumps([outcome.tree, cell.backend, payload_text, environment]))[:24]
        path = out / "signatures" / f"{key}.json"
        if not path.exists():
            _write_json(path, cell_signature(gate, cell, outcome.tree, tmp / "sig", payloads))
    entry: dict[str, Any] = {"gen": "ok", "sig": key}
    if payload_text:
        valid = [e["id"] for e in json.loads(payload_text) if e.get("kind") == "valid" and e.get("expect") == "accept"]
        entry["valid"] = sorted(valid)
    return entry


def catalog_main(ns: argparse.Namespace) -> int:
    """Record the cell catalog of the fixtures x types x option sets on a revision (`_cell_catalog.record`).

    The family cells of this checkout's library join where `--select` names them (`family:*`), as in the gate.

    The workers record with `sys.monitoring`: an interpreter without it (Python 3.11) would write a catalog of no keys.
    """
    if not hasattr(sys, "monitoring"):
        msg = "catalog: recording what each cell ran needs sys.monitoring (Python 3.12 or later)"
        raise SystemExit(msg)
    python = Path(sys.executable).absolute()
    base = compare_release.resolve_base(ns.revision, python, ns.cache_dir.absolute(), repository())
    revision = base.revision or ns.revision
    candidate = Candidate(repository(), python, compare_release.run_environment(repository() / "src"), None)
    inputs = [
        (name, file_type) for name, file_type in fixture_inputs(candidate, revision) if _selected(name, ns.select)
    ]
    pairs = [(backend, options) for options in _cell_catalog.option_sets(ns.options) for backend in ns.types.split(",")]
    cells = [
        Cell(candidate.root / name, name, file_type, backend, options, "option-set" if options else "default")
        for backend, options in pairs
        for name, file_type in inputs
    ]
    if any(pattern.startswith(FAMILY_PREFIX) for pattern in ns.select):
        root = ns.cache_dir.absolute() / "family-inputs" / _stable(str(candidate.root))[:12]
        named = family_cells(family_cases(python, candidate, root)[0], root, pairs)
        cells += [cell for cell in named if _selected(cell.label, ns.select)]
    side = compare_release.Side("catalog", base.python, base.package)
    runner = compare_release.Runner(side, base.env(), import_check=False, timeout=ns.timeout, memory_mb=ns.memory_mb)
    catalog = _cell_catalog.record(runner, cells, revision, ns.j)
    catalog.write(ns.out)
    counts = (len(cells), len(catalog.failed), len(catalog.keys), len(catalog.sets), catalog.python or "lines only")
    print("catalog: {} cells, {} failed, {} keys, {} sets, sub-line keys of Python {}".format(*counts))
    return 0


def record_main(ns: argparse.Namespace) -> int:
    """Write the behaviour record of one release: every fixture and payload case x type with default options."""
    started = time.monotonic()
    cache_dir, out = ns.cache_dir.absolute(), ns.out.absolute()
    recorder = resolve_candidate(None, None, cache_dir)
    package = None
    if ns.checkout:
        source = resolve_candidate(ns.checkout, ns.python, cache_dir)
        python, env, cache = source.python, source.env, compare_release.OutputCache(None)
        package = source.root / "src"
        environment = release_environment(python)
    else:
        python = (ns.python or era_python(ns.version, cache_dir)).absolute()
        env = {k: v for k, v in recorder.env.items() if k != "PYTHONPATH"}
        environment = release_environment(python)
        name = python.parent.parent.name if ns.python is None else f"python-{_stable(environment)[:12]}"
        cache = compare_release.OutputCache(cache_dir / "outputs" / f"record-{ns.version}-{name}")
    runner = compare_release.Runner(
        compare_release.Side("rec", python, package),
        env,
        import_check=False,
        timeout=ns.timeout,
        memory_mb=ns.memory_mb,
    )
    gate = Gate(
        base=runner,
        candidate=runner,
        cache=cache,
        candidate_python=Path(sys.executable).absolute(),
        signature_python=python,
        allowlist=Allowlist(),
        loaded_allowlist=Allowlist(),
        timeout=ns.timeout,
        payload_dir=cache_dir / "payloads",
        payload_root=cache_dir / "payload-inputs" / _stable(str(recorder.root))[:12],
        candidate_info=recorder,
    )
    types = ns.types.split(",")
    inputs = [(n, t) for n, t in fixture_inputs(recorder, latest_tag()) if _selected(n, ns.select)]
    cells = [Cell(recorder.root / name, name, file_type, backend) for name, file_type in inputs for backend in types]
    if not ns.select or any(pattern.startswith("payload:") for pattern in ns.select):
        cells += [cell for cell in payload_cells(gate, types) if _selected(cell.label, ns.select)]
    cells = [cell for cell in cells if Placement().shard(cell, ns.shard_total) == ns.shard_index]
    inventory = option_inventory(python, env)

    def run(cell: Cell) -> tuple[str, dict[str, Any]]:
        key = f"{cell.label}|{cell.backend}|{cell.option_label}"
        args = _drop_unsupported_formatters(cell.args(), inventory)
        if reason := opt_in_reason(args, inventory):
            return key, {"gen": "unsupported", "error": f"the release has no {reason}"}
        return key, record_cell(gate, cell, args, out, environment)

    try:
        with ThreadPoolExecutor(ns.j) as pool:
            entries = dict(pool.map(run, cells))
    finally:
        runner.close()
    record: dict[str, Any] = {"version": ns.version, "types": types, "cells": entries}
    if ns.shard_total > 1:
        record["shard"] = [ns.shard_index, ns.shard_total]
    _write_json(out / f"{ns.version}.json", record)
    counts = Counter(entry["gen"] for entry in entries.values())
    print(f"recorded {ns.version}: {len(entries)} cells ({dict(counts)}) in {time.monotonic() - started:.0f}s")
    return 0


def merge_record_main(ns: argparse.Namespace) -> int:
    """Merge the shard records of one release into the record an unsharded `record` writes."""
    out = ns.out.absolute()
    records = [json.loads((part / f"{ns.version}.json").read_text(encoding="utf-8")) for part in ns.parts]
    shards = sorted(tuple(record.pop("shard", (1, 1))) for record in records)
    total = shards[0][1]
    if shards != [(index, total) for index in range(1, total + 1)]:
        msg = f"merge-record {ns.version}: the parts are shards {shards}, not each shard of 1..{total} once"
        raise SystemExit(msg)
    if any(record["types"] != records[0]["types"] for record in records):
        msg = f"merge-record {ns.version}: the parts record different types"
        raise SystemExit(msg)
    (out / "signatures").mkdir(parents=True, exist_ok=True)
    for part in ns.parts:
        for path in sorted((part / "signatures").glob("*.json")):
            if not (target := out / "signatures" / path.name).exists():
                shutil.copyfile(path, target)
    cells = {key: entry for record in records for key, entry in record["cells"].items()}
    _write_json(out / f"{ns.version}.json", {"version": ns.version, "types": records[0]["types"], "cells": cells})
    print(f"merged {ns.version}: {len(cells)} cells from {total} shards")
    return 0


def load_records(directory: Path) -> tuple[dict[str, dict[str, Any]], Callable[[str], dict[str, Any]]]:
    """Return every record in a directory and a cached signature loader."""
    records = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in directory.glob("*.json")
        if path.stem[:1].isdigit()
    }
    cache: dict[str, dict[str, Any]] = {}

    def load(key: str) -> dict[str, Any]:
        if key not in cache:
            cache[key] = json.loads((directory / "signatures" / f"{key}.json").read_text(encoding="utf-8"))
        return cache[key]

    return records, load


def query_main(ns: argparse.Namespace) -> int:
    """Compare recorded releases; with `all`, every recorded release before VERSION against VERSION."""
    from scripts.build_behaviour_docs import render_comparison  # ruff: ignore[import-outside-top-level]

    records, load = load_records(ns.records)
    missing = [v for v in (ns.base, ns.version) if v != "all" and v not in records]
    if missing:
        msg = f"no record for {', '.join(missing)} in {ns.records}: run `behaviour_diff.py record VERSION` first"
        raise SystemExit(msg)
    olds = (
        sorted((v for v in records if version_key(v) < version_key(ns.version)), key=version_key)
        if ns.base == "all"
        else [ns.base]
    )
    if parts := [v for v in (*olds, ns.version) if "shard" in records[v]]:
        msg = f"query: {', '.join(parts)} in {ns.records} is a shard part: merge the parts with `merge-record` first"
        raise SystemExit(msg)
    comparisons = [compare_records(records, old, ns.version, load) for old in olds]
    data: dict[str, Any] = (
        {"schema_version": 1, "to": ns.version, "rows": comparisons} if ns.base == "all" else comparisons[0]
    )
    if ns.json:
        ns.json.parent.mkdir(parents=True, exist_ok=True)
        ns.json.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    text = "\n\n".join(render_comparison(comparison, level=1) for comparison in comparisons) + "\n"
    if ns.md:
        ns.md.write_text(text, encoding="utf-8")
    if not ns.json and not ns.md:
        print(text, end="")
    return 0


def _diff_text(old: dict[str, str], new: dict[str, str], limit: int = SNIPPET_LINES) -> str:
    """Return a unified diff of two generated trees, cut after `limit` lines."""
    import difflib  # ruff: ignore[import-outside-top-level]

    lines: list[str] = []
    for name in sorted(set(old) | set(new)):
        lines += difflib.unified_diff(
            old.get(name, "").splitlines(), new.get(name, "").splitlines(), name, name, n=2, lineterm=""
        )
    if len(lines) > limit:
        lines = [*lines[:limit], f"... ({len(lines) - limit} more lines)"]
    return "\n".join(lines)


def without_option(options: list[str], option: str, flags: set[str]) -> list[str]:
    """Return `options` without one option (`--name` or `--name value`), as an opt-in reason names it."""
    name, _, value = option.partition(" ")
    rest: list[str] = []
    skip = False
    for index, arg in enumerate(options):
        if skip:
            skip = False
            continue
        if arg == f"{name}={value}" or (not value and arg.startswith(f"{name}=")):
            continue
        if arg == name and (not value or options[index + 1 : index + 2] == [value]):
            skip = name not in flags and index + 1 < len(options) and not options[index + 1].startswith("--")
            continue
        rest.append(arg)
    return rest


def restore_candidates(python: Path, env: dict[str, str], groups: tuple[str, ...] = RESTORE_GROUPS) -> list[list[str]]:
    """Return every flag and choice value of the options in `groups` (default: the customization options)."""
    code = RESTORE_INVENTORY.replace("GROUPS", repr(groups))
    output = subprocess.run([str(python), "-c", code], capture_output=True, text=True, check=True, env=env).stdout
    return json.loads(output)


def _generate(runner: compare_release.Runner, args: list[str]) -> compare_release.Outcome:
    with tempfile.TemporaryDirectory() as name:
        return runner.generate(args, Path(name))


def _restoring_options(gate: Gate, cell: Cell, old: compare_release.Outcome, ns: argparse.Namespace) -> list[str]:
    """Return the options that make the candidate reproduce the release's behaviour signature for a cell."""
    candidate = gate.candidate_info
    new = _generate(gate.candidate, cell.args())
    with tempfile.TemporaryDirectory() as name:
        old_signature = cell_signature(gate, cell, old.tree, Path(name) / "old", None)

    def restores(option: list[str]) -> str | None:
        args = cell.args() + option
        outcome = _generate(gate.candidate, args)
        if not outcome.ok or outcome.tree == new.tree:
            return None
        if outcome.tree == old.tree:
            return " ".join(option)
        with tempfile.TemporaryDirectory() as name:
            tried = cell_signature(gate, cell, outcome.tree, Path(name) / "new", None)
        return " ".join(option) if not [c for c in diff(old_signature, tried) if c.behaviour] else None

    options = restore_candidates(candidate.python, candidate.env)
    with ThreadPoolExecutor(ns.j) as pool:
        return sorted(found for found in pool.map(restores, options) if found)


def _smallest_fixture(rows: list[dict[str, Any]], root: Path) -> dict[str, Any] | None:
    fixtures = [row for row in rows if not row["input"].startswith("payload:") and (root / row["input"]).exists()]
    order = {"default": 0, "preset": 1, "cli-doc": 2}

    def size(row: dict[str, Any]) -> tuple[int, int, str, str]:
        path = root / row["input"]
        total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.is_dir() else path.stat().st_size
        return order.get(row["kind"], 3), total, row["input"], row["type"]

    return min(fixtures, key=size, default=None)


def _row_cell(row: dict[str, Any], root: Path) -> Cell:
    data = root / "tests" / "data"
    options = tuple(
        str(data / option) if not option.startswith("-") and (data / option).exists() else option
        for option in ([] if row["options"] == "default" else row["options"].split(" "))
    )
    return Cell(root / row["input"], row["input"], row["file_type"], row["type"], options, row["kind"])


def enrich_accepted(gate: Gate, rows: list[dict[str, Any]], data: dict[str, Any], ns: argparse.Namespace) -> None:
    """Add an example diff and the options that keep the old output to every accepted change."""
    root = gate.candidate_info.root
    for group in data["accepted"]:
        members = [row for row in rows if row["class"] == "accepted" and row["input"] in group["inputs"]]
        if (row := _smallest_fixture(members, root)) is None:
            continue
        cell = _row_cell(row, root)
        old = _generate(gate.base, cell.args())
        new = _generate(gate.candidate, cell.args())
        group["example"] = {
            "input": row["input"],
            "type": row["type"],
            "options": row["options"],
            "diff": _diff_text(old.tree, new.tree),
        }
        group["restored_by"] = _restoring_options(gate, cell, old, ns)


def enrich_opt_in(gate: Gate, rows: list[dict[str, Any]], data: dict[str, Any]) -> None:
    """Add, for each new option, the candidate's output with and without it on its smallest affected input."""
    root = gate.candidate_info.root
    candidate = gate.candidate_info
    flags = {
        option[0] for option in restore_candidates(candidate.python, candidate.env, ALL_GROUPS) if len(option) == 1
    }
    for group in data["opt_in"]:
        members = [
            row
            for row in rows
            if row["class"] == "opt-in" and row["notes"][0] == f"the release has no {group['option']}"
        ]
        for _ in range(OPT_IN_TRIES):
            if (row := _smallest_fixture(members, root)) is None:
                break
            members.remove(row)
            cell = _row_cell(row, root)
            rest = without_option(list(cell.options), group["option"], flags)
            plain = Cell(cell.input, cell.label, cell.file_type, cell.backend, tuple(rest))
            with_option, without = _generate(gate.candidate, cell.args()), _generate(gate.candidate, plain.args())
            if with_option.ok and without.ok and with_option.tree != without.tree:
                group["example"] = {
                    "input": row["input"],
                    "type": row["type"],
                    "options": row["options"],
                    "diff": _diff_text(without.tree, with_option.tree),
                }
                break


def render_notes(data: dict[str, Any]) -> str:
    """Return the release-note fragment of a release page's data."""
    version, previous = data["version"], data["previous"]
    totals = Counter()
    for by_class in data["counts"].values():
        totals.update(by_class)
    lines = [
        f"## Output changes from {previous}",
        "",
        (
            f"Compared over {sum(totals.values())} generations ({', '.join(data['types'])}); details: "
            f"[Output changes in {version}](https://datamodel-code-generator.koxudaxi.dev/output-changes/{version}/)."
        ),
        "",
        f"- Unchanged: {totals['same']}; formatting or import order only: {totals['no-behaviour']}",
        f"- Fixed crashes (the release failed to generate, import or validate): {totals['crash-fix']}",
    ]
    for group in data["accepted"]:
        keep = ", ".join(f"`{option}`" for option in group.get("restored_by", [])) or "no option restores it"
        lines.append(f"- {group['release_note']} ({group['decision']}; to keep the old output: {keep})")
    if data["opt_in"]:
        lines.append("- New opt-in options: " + ", ".join(f"`{group['option']}`" for group in data["opt_in"]))
    return "\n".join(lines) + "\n"


def name_accepted_changes(data: dict[str, Any], records: Iterable[Record]) -> None:
    """Give each accepted change of a release page the title of the change record it names, or else its reason."""
    titles = {record.id: record.title for record in records if record.title}
    for group in data["accepted"]:
        group["release_note"] = titles.get(group["migration"], group["reason"])


def guide_main(ns: argparse.Namespace) -> int:
    """Write a release page's data (and release notes) from a gate run of the release against the previous one."""
    gate = build_gate(ns)
    try:
        if not ns.cells:
            ns.check, ns.json = False, ns.cache_dir.absolute() / f"guide-{ns.base}-{ns.version}.jsonl"
            gate_main(ns)
            ns.cells = [ns.json]
        rows = [json.loads(line) for path in ns.cells for line in path.read_text(encoding="utf-8").splitlines()]
        data = summarize_gate(rows, gate.loaded_allowlist.entries)
        data = {"version": ns.version, "previous": ns.base, "tier": ns.tier, **data}
        name_accepted_changes(data, read_records(MIGRATION_DATA))
        enrich_accepted(gate, rows, data, ns)
        enrich_opt_in(gate, rows, data)
    finally:
        gate.base.close()
        gate.candidate.close()
    ns.docs.parent.mkdir(parents=True, exist_ok=True)
    ns.docs.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if ns.notes:
        ns.notes.write_text(render_notes(data), encoding="utf-8")
    return 0


def _candidate_tests(root: Path) -> None:
    """Make `tests` import from the candidate at `root`: this checkout's test units the gate loaded are dropped."""
    sys.path.insert(0, str(root))
    for name in [name for name in sys.modules if name.partition(".")[0] == "tests"]:
        del sys.modules[name]


def _payload_cases(root: Path, out: Path) -> None:
    """Write every payload-validation case of the checkout at `root` and an index of their arguments.

    The harness has no TypedDict backend (a TypedDict validates nothing by itself); TypedDict cells use the
    dataclass arguments, and their payload verdicts come from the TypedDict's pydantic TypeAdapter.
    """
    _candidate_tests(root)
    from tests.main.payload_validation import SCHEMA_CASES  # ruff: ignore[import-outside-top-level]
    from tests.main.payload_validation.codegen import (  # ruff: ignore[import-outside-top-level]
        _payload_codegen_args,  # ruff: ignore[import-private-name]
        _write_input_schema,  # ruff: ignore[import-private-name]
    )
    from tests.main.payload_validation.models import PayloadBackend  # ruff: ignore[import-outside-top-level]

    out.mkdir(parents=True, exist_ok=True)
    index = []
    for case in SCHEMA_CASES:
        key = _stable(case.id)[:16]
        directory = out / key
        directory.mkdir(exist_ok=True)
        schema = _write_input_schema(case, directory)
        args = {}
        for backend in PayloadBackend:
            options = _payload_codegen_args(case, schema, directory / "out.py", backend)
            for option in ("--input", "--output", "--output-model-type", "--input-file-type"):
                options, _ = _without(options, option)
            options, formatters = _without(options, "--formatters")
            args[backend.output_model_type] = [opt for opt in options if opt != formatters]
        args["typing.TypedDict"] = args[PayloadBackend.DATACLASSES.output_model_type]
        index.append({
            "key": key,
            "case_id": case.id,
            "input": schema.relative_to(out).as_posix(),
            "file_type": case.input_file_type,
            "args": args,
        })
    (out / "index.json").write_text(json.dumps(index), encoding="utf-8")


def _draw_payloads(root: Path, case_id: str, out: Path) -> None:
    """Draw a fixed vector of valid and near-miss invalid payloads for one case (derandomised)."""
    import signal  # ruff: ignore[import-outside-top-level]

    _candidate_tests(root)
    from hypothesis import HealthCheck, Phase, given, settings  # ruff: ignore[import-outside-top-level]
    from hypothesis import strategies as st  # ruff: ignore[import-outside-top-level]

    from tests.main.payload_validation import (  # ruff: ignore[import-outside-top-level]
        SCHEMA_CASES,
        invalid_payload_mutations,
        payload_strategy,
        source_schema_validator,
    )
    from tests.main.payload_validation.native_numeric import native_float_multiple_errors  # ruff: ignore[import-outside-top-level]

    case = next(case for case in SCHEMA_CASES if case.id == case_id)
    signal.alarm(PAYLOAD_DRAW_TIMEOUT)
    validator = source_schema_validator(case)
    valid: list[Any] = []
    seen: set[str] = set()

    @settings(
        database=None,
        deadline=None,
        derandomize=True,
        max_examples=PAYLOAD_VALID,
        phases=[Phase.generate],
        suppress_health_check=list(HealthCheck),
    )
    @given(data=st.data())
    def collect(data: st.DataObject) -> None:
        payload = data.draw(payload_strategy(case))
        if validator.is_valid(payload) and (key := json.dumps(payload, sort_keys=True)) not in seen:
            seen.add(key)
            valid.append(payload)

    collect()
    entries = [
        {
            "id": f"v{index}",
            "kind": "valid",
            "payload": payload,
            "expect": "reject" if native_float_multiple_errors(case, payload) else "accept",
        }
        for index, payload in enumerate(valid)
    ]
    by_constraint: dict[str, list[Any]] = {}
    for payload in valid:
        for mutation in invalid_payload_mutations(case, payload):
            if (key := json.dumps(mutation.payload, sort_keys=True)) not in seen:
                seen.add(key)
                by_constraint.setdefault(mutation.constraint, []).append(mutation)
    queues = [list(mutations) for _, mutations in sorted(by_constraint.items())]
    picked: list[Any] = []
    while len(picked) < PAYLOAD_INVALID and any(queues):
        picked += [queue.pop(0) for queue in queues if queue][: PAYLOAD_INVALID - len(picked)]
    entries += [
        {"id": f"i{index}", "kind": "invalid", "payload": mutation.payload, "expect": "reject"}
        for index, mutation in enumerate(picked)
    ]
    signal.alarm(0)
    out.write_text(json.dumps(entries), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Run a subcommand."""
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["_payload-cases"]:
        _payload_cases(Path(argv[1]), Path(argv[2]))
        return 0
    if argv[:1] == ["_draw-payloads"]:
        with contextlib.suppress(Exception):
            _draw_payloads(Path(argv[1]), argv[2], Path(argv[3]))
        return 0
    ns = _parse_args(argv)
    ns.argv = argv
    commands = {
        "gate": gate_main,
        "catalog": catalog_main,
        "record": record_main,
        "merge-record": merge_record_main,
        "query": query_main,
        "guide": guide_main,
    }
    return commands[ns.command](ns)


if __name__ == "__main__":
    sys.exit(main())
