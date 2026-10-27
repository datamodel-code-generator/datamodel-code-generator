"""Classify every output change between a release and a checkout by its behaviour.

Usage:
  python scripts/behaviour_diff.py gate [BASE] [CANDIDATE] [--tier pr|nightly|release] [--types T1,T2]
      [--shard I/N] [-j N] [--check] [--json OUT]

BASE is a released version (default: the latest tag). CANDIDATE is a checkout path (default: this checkout; its
`.venv` is used when it has one) or a git revision of this repository, which is extracted with `git archive` and run
from this interpreter through PYTHONPATH. The release is installed into a cached environment pinned to the
candidate's dependency versions, as scripts/compare_release.py does.

A cell is one input x output model type x option set. Tiers:
  pr       inputs changed since BASE, inputs named by the allowlist and a fixed sample of 300 fixtures,
           x {pydantic_v2.BaseModel, msgspec.Struct}, default options
  nightly  every fixture and payload case x 5 types (default options), every fixture x the released presets,
           and every `@pytest.mark.cli_doc` example
  release  nightly (the option tiers of the design come later)

Cells whose bytes differ get a behaviour signature per side (scripts/_behaviour_signature.py, a fresh process per
tree) and, for payload cases, payload verdicts; scripts/_behaviour_classify.py puts each cell into one class. A cell
that uses an option, value or input type BASE does not have is `opt-in` and is not generated.

Accepted changes are listed in scripts/behaviour_diff_allowlist.toml: `base` and `[[accepted]]` entries with
`input` (glob), optional `backends`, `aspects` and `match` (regex on the change line), `reason`, `decision` (the
public PR or issue references of the owner decision, `#1234, #1235`) and `release_note`. With `--check` the run fails
on a violation, on an entry without such a decision and on a table whose base is not BASE; in the nightly and
release tiers also on a stale entry: one that accepted no change in a run that has a cell it covers, or, in a run of
the whole tier, one that covers no cell. The PR tier is a sample, so it never judges staleness.
Cells the allowlist covers all run in shard 1, so that shard judges staleness exactly; `--select` narrows the
inputs, so a selection may leave an entry's changes out.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import compare_release  # ruff: ignore[module-import-not-at-top-of-file]
from scripts._behaviour_classify import CLASSES, Allowlist, Side, Verdict, classify_cell, diff  # ruff: ignore[module-import-not-at-top-of-file]
from scripts._behaviour_report import compare_records, redact, summarize_gate, version_key  # ruff: ignore[module-import-not-at-top-of-file]

SIGNATURE_SCRIPT = Path(__file__).with_name("_behaviour_signature.py")
DEFAULT_ALLOWLIST = Path(__file__).with_name("behaviour_diff_allowlist.toml")
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
DEFAULT_RECORDS = compare_release._default_cache_dir().parent / "behaviour-records"  # ruff: ignore[private-member-access]
ERA_PYTHONS = ("3.14", "3.13", "3.12", "3.11")
ERA_EXTRAS = "graphql,http,protobuf"
SNIPPET_LINES = 40
OPT_IN_TRIES = 5
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
from datamodel_code_generator.arguments import arg_parser
options = {o: sorted(map(str, a.choices)) if a.choices else None for a in arg_parser._actions for o in a.option_strings}
print(json.dumps(options))
"""


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

    @property
    def option_label(self) -> str:
        """The option set as one string."""
        return " ".join(self.options) or "default"

    def args(self) -> list[str]:
        """Return the generation arguments without `--output`."""
        return [
            "--input",
            str(self.input),
            "--input-file-type",
            self.file_type,
            "--output-model-type",
            self.backend,
            *compare_release.COMMON_ARGS,
            *self.options,
        ]


@dataclass
class Candidate:
    """The checkout or revision under test."""

    root: Path
    python: Path
    env: dict[str, str]
    revision: str | None


@dataclass
class Result:
    """The classification of one cell."""

    cell: Cell
    verdict: Verdict


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
    signature_python: Path | None = None
    draw_locks: dict[str, threading.Lock] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


def _git(*args: str, cwd: Path = ROOT) -> str | None:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    return result.stdout if result.returncode == 0 else None


def latest_tag() -> str:
    """Return the latest release tag reachable from HEAD."""
    return (_git("describe", "--tags", "--abbrev=0") or "").strip()


def archive_version(revision: str) -> str:
    """Return the version the build hook writes for a revision (`git archive` leaves the version file out)."""
    described = (_git("describe", "--tags", "--long", "--match", "[0-9]*", "--exclude", "*+*", revision) or "").strip()
    if described.count("-") < 2:  # ruff: ignore[magic-value-comparison]
        return f"0.0.0+g{revision[:9]}"
    tag, distance, commit = described.rsplit("-", 2)
    if distance == "0":
        return tag
    major_minor, _, patch = tag.rpartition(".")
    return f"{major_minor}.{int(patch) + 1}.dev{distance}+{commit}"


def resolve_candidate(spec: str | None, python: Path | None, cache_dir: Path) -> Candidate:
    """Resolve a checkout path or a revision to a root, an interpreter and the environment to run it with."""
    env = {**os.environ, "PYTHONWARNINGS": "ignore", "OBJC_DISABLE_INITIALIZE_FORK_SAFETY": "YES"}
    if spec is None or Path(spec).is_dir():
        root = Path(spec or ROOT).resolve()
        venv = root / ".venv" / "bin" / "python"
        chosen = python or (venv if venv.exists() else Path(sys.executable))
        if not (venv.exists() and chosen.absolute() == venv.absolute()):
            env["PYTHONPATH"] = str(root / "src")
        return Candidate(root, chosen.absolute(), env, None)
    revision = (_git("rev-parse", "--verify", f"{spec}^{{commit}}") or "").strip()
    if not revision:
        msg = f"{spec} is neither a directory nor a revision of {ROOT}"
        raise SystemExit(msg)
    root = cache_dir / "trees" / revision
    if not (root / ".complete").exists():
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        archive = subprocess.run(
            ["git", "archive", revision, "src", "tests"], cwd=ROOT, capture_output=True, check=True
        )
        subprocess.run(["tar", "-x", "-C", str(root)], input=archive.stdout, check=True)
        version_file = root / "src" / "datamodel_code_generator" / "_version.py"
        if not version_file.exists():
            version_file.write_text(f"__version__ = {archive_version(revision)!r}\n", encoding="utf-8")
        (root / ".complete").touch()
    env["PYTHONPATH"] = str(root / "src")
    return Candidate(root, (python or Path(sys.executable)).absolute(), env, revision)


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
    revision = [candidate.revision] if candidate.revision else []
    cwd = ROOT if candidate.revision else candidate.root
    output = _git("diff", "--name-only", "--no-renames", base, *revision, "--", "tests/data", cwd=cwd)
    return sorted(output.split()) if output else []


def added_files(candidate: Candidate, base: str, directory: str) -> list[str]:
    """Return the files added below tests/data/<directory> since the release."""
    revision = [candidate.revision] if candidate.revision else []
    cwd = ROOT if candidate.revision else candidate.root
    output = _git("diff", "--name-only", "--diff-filter=A", base, *revision, "--", f"tests/data/{directory}", cwd=cwd)
    return sorted(output.split()) if output else []


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
    """Return one cell per cli_doc example and output model type it documents."""
    cells: dict[tuple[str, str, tuple[str, ...]], Cell] = {}
    for example in cli_doc_examples(root):
        schema = root / "tests" / "data" / example["input_schema"]
        if not schema.exists():
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


def payload_cells(gate: Gate, types: list[str]) -> list[Cell]:
    """Write the payload-validation cases of the candidate and return their cells."""
    candidate = gate.candidate_info
    subprocess.run(
        [str(gate.candidate_python), __file__, "_payload-cases", str(candidate.root), str(gate.payload_root)],
        cwd=candidate.root,
        env=candidate.env,
        check=True,
    )
    index = json.loads((gate.payload_root / "index.json").read_text(encoding="utf-8"))
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


def shard_of(cell: Cell, allowlist: Allowlist, total: int) -> int:
    """Return the 1-based shard of a cell: allowlist cells all run in shard 1, others by input hash."""
    if any(allowlist.covers(index, cell.label, cell.backend) for index in range(len(allowlist.entries))):
        return 1
    return int(_stable(cell.label), 16) % total + 1


def _write_tree(tree: dict[str, str], directory: Path) -> Path:
    for name, text in tree.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return directory / "model.py" if "model.py" in tree else directory / "pkg"


def signature(gate: Gate, target: Path, backend: str, payloads: Path | None) -> dict[str, Any]:
    """Compute a behaviour signature in a fresh process of the candidate's interpreter."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("DATAMODEL_CODE_GENERATOR")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    command = [str(gate.signature_python or gate.candidate_python), "-I", str(SIGNATURE_SCRIPT), str(target), backend]
    try:
        result = subprocess.run(
            [*command, *([str(payloads)] if payloads else [])],
            capture_output=True,
            text=True,
            timeout=gate.timeout,
            cwd=target.parent,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"import_error": f"signature timed out after {gate.timeout}s"}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"import_error": "signature crashed: " + result.stderr.strip()[-200:]}


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


def run_cell(gate: Gate, cell: Cell) -> Result:
    """Generate one cell on both sides and classify it."""
    args = cell.args()
    with tempfile.TemporaryDirectory() as name:
        tmp = Path(name)
        base = gate.cache.get(cell.input, args, import_check=False)
        if base is None:
            base = gate.base.generate(args, tmp)
            gate.cache.put(cell.input, args, base, import_check=False)
        candidate = gate.candidate.generate(args, tmp)
        same = base.ok and candidate.ok and base.tree == candidate.tree
        sides = [Side(base.ok, base.error), Side(candidate.ok, candidate.error)]
        valid: frozenset[str] = frozenset()
        if base.ok and candidate.ok and not same:
            payloads = payloads_for(gate, cell)
            if payloads is not None:
                entries = json.loads(payloads.read_text(encoding="utf-8"))
                valid = frozenset(e["id"] for e in entries if e.get("kind") == "valid" and e.get("expect") == "accept")
            sides = [
                Side(
                    generated=True,
                    signature=signature(gate, _write_tree(outcome.tree, tmp / f"sig-{side}"), cell.backend, payloads),
                )
                for side, outcome in (("base", base), ("cand", candidate))
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
    return Result(cell, verdict)


def build_cells(ns: argparse.Namespace, gate: Gate, inventory: dict[str, list[str] | None]) -> list[Cell]:
    """Return the cells of the selected tier."""
    candidate = gate.candidate_info
    inputs = fixture_inputs(candidate, ns.base)
    types = (
        ns.types.split(",")
        if ns.types
        else (list(PR_TYPES) if ns.tier == "pr" else list(compare_release.DEFAULT_TYPES))
    )
    if ns.select:
        inputs = [(name, file_type) for name, file_type in inputs if _selected(name, ns.select)]
    elif ns.tier == "pr":
        inputs = pr_inputs(inputs, changed_files(candidate, ns.base), gate.allowlist)
    cells = [Cell(candidate.root / name, name, file_type, backend) for name, file_type in inputs for backend in types]
    if ns.tier != "pr":
        presets = inventory.get("--preset") or []
        cells += [
            Cell(candidate.root / name, name, file_type, backend, options, "preset")
            for backend, options in preset_option_sets(presets, types)
            for name, file_type in inputs
        ]
        cells += [cell for cell in anchor_cells(candidate.root, types) if _selected(cell.label, ns.select)]
        if not ns.select or any(pattern.startswith("payload:") for pattern in ns.select):
            cells += [cell for cell in payload_cells(gate, types) if _selected(cell.label, ns.select)]
    return cells


def _selected(label: str, patterns: list[str]) -> bool:
    return not patterns or any(fnmatch.fnmatch(label, pattern) for pattern in patterns)


def _environment_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-j", type=int, default=os.cpu_count() or 1, help="parallel cells (default: CPU count)")
    parser.add_argument("--timeout", type=int, default=300, help="seconds before one generation or signature fails")
    parser.add_argument("--cache-dir", type=Path, default=compare_release._default_cache_dir())  # ruff: ignore[private-member-access]


def _gate_args(parser: argparse.ArgumentParser) -> None:
    _environment_args(parser)
    parser.add_argument("--tier", choices=("pr", "nightly", "release"), default="pr")
    parser.add_argument("--types", default="", help="comma-separated --output-model-type values (default: per tier)")
    parser.add_argument("--shard", default="1/1", help="run shard I of N, e.g. 2/8")
    parser.add_argument(
        "--select",
        action="append",
        default=[],
        help="only inputs matching this glob (payload cases: 'payload:<case id>'); replaces the PR sample",
    )
    parser.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST)
    parser.add_argument("--base-python", type=Path, help="interpreter with the release already installed")
    parser.add_argument("--candidate-python", type=Path, help="interpreter for the candidate")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    gate = commands.add_parser("gate", help="classify every output change of the candidate against the release")
    gate.add_argument("base", nargs="?", help="released version (default: the latest tag)")
    gate.add_argument("candidate", nargs="?", help="checkout path or git revision (default: this checkout)")
    _gate_args(gate)
    gate.add_argument("--check", action="store_true", help="exit 1 on a violation or an invalid or stale allowlist")
    gate.add_argument("--json", type=Path, help="write every cell as one JSON line to this file (read by `guide`)")
    record = commands.add_parser("record", help="write the behaviour record of one release")
    record.add_argument("version", help="release version, or the version a checkout is recorded as")
    record.add_argument("checkout", nargs="?", help="checkout path or git revision to record instead of the release")
    _environment_args(record)
    record.add_argument("--out", type=Path, default=DEFAULT_RECORDS, help="record directory")
    record.add_argument("--types", default=",".join(compare_release.DEFAULT_TYPES))
    record.add_argument("--select", action="append", default=[], help="only inputs matching this glob")
    record.add_argument("--python", type=Path, help="interpreter that has the release (default: an era environment)")
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
    guide.set_defaults(tier="release")
    guide.add_argument("--cells", type=Path, action="append", default=[], help="gate --json output (default: run)")
    guide.add_argument("--docs", type=Path, required=True, help="page data to write, docs/data/output-changes/V.json")
    guide.add_argument("--notes", type=Path, help="release-note fragment to write")
    ns = parser.parse_args(argv)
    if ns.command in {"gate", "guide"}:
        ns.base = ns.base or latest_tag()
        index, _, total = ns.shard.partition("/")
        ns.shard_index, ns.shard_total = int(index), int(total or 1)
        if not 1 <= ns.shard_index <= ns.shard_total:
            parser.error(f"--shard {ns.shard}: I must be between 1 and N")
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


def _report(results: list[Result], gate: Gate, ns: argparse.Namespace) -> int:
    counts = dict.fromkeys(CLASSES, 0)
    used: set[int] = set()
    for result in results:
        counts[result.verdict.cls] += 1
        used |= result.verdict.accepted_by
        if result.verdict.cls in {"violation", "accepted"}:
            cell = result.cell
            print(f"{result.verdict.cls}: {cell.label} | {cell.backend} | {cell.option_label}")
            for note in result.verdict.notes[:6]:
                print(f"    {note[:240]}")
    problems = gate.loaded_allowlist.problems(ns.base)
    entries = range(len(gate.allowlist.entries))
    covered = {i for r in results for i in entries if gate.allowlist.covers(i, r.cell.label, r.cell.backend)}
    whole = ns.shard_total == 1 and not ns.select
    problems += [
        f"allowlist entry {index + 1} ({gate.allowlist.entries[index].get('input')}) is stale: "
        + ("it accepted no change" if index in covered else "it covers no cell")
        for index in entries
        if ns.tier != "pr" and index not in used and (index in covered or whole)
    ]
    for problem in problems:
        print(f"allowlist: {problem}")
    print("summary:", ", ".join(f"{name}={count}" for name, count in counts.items()))
    print(
        f"cells: {len(results)} (tier {ns.tier}, shard {ns.shard_index}/{ns.shard_total}), "
        f"cached base outputs: {gate.cache.hits}"
    )
    if ns.json:
        ns.json.write_text("".join(json.dumps(cell_row(result)) + "\n" for result in results), encoding="utf-8")
    return 1 if ns.check and (counts["violation"] or problems) else 0


def build_gate(ns: argparse.Namespace) -> tuple[Gate, Path]:
    """Return the gate of a release and a candidate, and the release's interpreter."""
    cache_dir = ns.cache_dir.absolute()
    candidate = resolve_candidate(ns.candidate, ns.candidate_python, cache_dir)
    if ns.base_python is not None:
        base_python = ns.base_python.absolute()
        identity = json.dumps([str(base_python), *compare_release._probe(base_python)])  # ruff: ignore[private-member-access]
        cache_name = f"python-{_stable(identity)[:12]}"
    else:
        base_python = compare_release._base_python(ns.base, candidate.python, cache_dir)  # ruff: ignore[private-member-access]
        cache_name = base_python.parents[1].name
    base_env = {k: v for k, v in candidate.env.items() if k != "PYTHONPATH"}
    allowlist = Allowlist.load(ns.allowlist) if ns.allowlist.exists() else Allowlist()
    gate = Gate(
        base=compare_release.Runner(
            compare_release.Side("base", base_python), base_env, import_check=False, timeout=ns.timeout
        ),
        candidate=compare_release.Runner(
            compare_release.Side("cand", candidate.python), candidate.env, import_check=False, timeout=ns.timeout
        ),
        cache=compare_release.OutputCache(cache_dir / "outputs" / cache_name),
        candidate_python=candidate.python,
        allowlist=allowlist if allowlist.base == ns.base else Allowlist(allowlist.base),
        loaded_allowlist=allowlist,
        timeout=ns.timeout,
        payload_dir=cache_dir / "payloads",
        payload_root=cache_dir / "payload-inputs" / _stable(str(candidate.root))[:12],
        candidate_info=candidate,
    )
    return gate, base_python


def gate_main(ns: argparse.Namespace) -> int:
    """Run the gate."""
    started = time.monotonic()
    gate, base_python = build_gate(ns)
    inventory = option_inventory(base_python)
    cells = [
        cell
        for cell in build_cells(ns, gate, inventory)
        if shard_of(cell, gate.allowlist, ns.shard_total) == ns.shard_index
    ]
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
    results += [Result(cell, Verdict("opt-in", (f"the release has no {reason}",))) for cell, reason in reasons.items()]
    code = _report(results, gate, ns)
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

    The newest Python of ERA_PYTHONS the release imports on is used.
    """
    import datetime as dt  # ruff: ignore[import-outside-top-level]
    import fcntl  # ruff: ignore[import-outside-top-level] - POSIX only, like compare_release

    root = cache_dir / "era" / f"{version}-{ERA_EXTRAS.replace(',', '-')}"
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
                    "black",
                    "isort",
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
            target = _write_tree(outcome.tree, tmp / "sig")
            _write_json(path, signature(gate, target, cell.backend, payloads))
    entry: dict[str, Any] = {"gen": "ok", "sig": key}
    if payload_text:
        valid = [e["id"] for e in json.loads(payload_text) if e.get("kind") == "valid" and e.get("expect") == "accept"]
        entry["valid"] = sorted(valid)
    return entry


def record_main(ns: argparse.Namespace) -> int:
    """Write the behaviour record of one release: every fixture and payload case x type with default options."""
    started = time.monotonic()
    cache_dir, out = ns.cache_dir.absolute(), ns.out.absolute()
    recorder = resolve_candidate(None, None, cache_dir)
    if ns.checkout:
        source = resolve_candidate(ns.checkout, ns.python, cache_dir)
        python, env, cache = source.python, source.env, compare_release.OutputCache(None)
        environment = release_environment(python)
    else:
        python = (ns.python or era_python(ns.version, cache_dir)).absolute()
        env = {k: v for k, v in recorder.env.items() if k != "PYTHONPATH"}
        environment = release_environment(python)
        name = python.parent.parent.name if ns.python is None else f"python-{_stable(environment)[:12]}"
        cache = compare_release.OutputCache(cache_dir / "outputs" / f"record-{ns.version}-{name}")
    runner = compare_release.Runner(compare_release.Side("rec", python), env, import_check=False, timeout=ns.timeout)
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
    _write_json(out / f"{ns.version}.json", {"version": ns.version, "types": types, "cells": entries})
    counts = Counter(entry["gen"] for entry in entries.values())
    print(f"recorded {ns.version}: {len(entries)} cells ({dict(counts)}) in {time.monotonic() - started:.0f}s")
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
        old_signature = signature(gate, _write_tree(old.tree, Path(name) / "old"), cell.backend, None)

    def restores(option: list[str]) -> str | None:
        args = cell.args() + option
        outcome = _generate(gate.candidate, args)
        if not outcome.ok or outcome.tree == new.tree:
            return None
        if outcome.tree == old.tree:
            return " ".join(option)
        with tempfile.TemporaryDirectory() as name:
            tried = signature(gate, _write_tree(outcome.tree, Path(name) / "new"), cell.backend, None)
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


def guide_main(ns: argparse.Namespace) -> int:
    """Write a release page's data (and release notes) from a gate run of the release against the previous one."""
    gate, _ = build_gate(ns)
    try:
        if not ns.cells:
            ns.check, ns.json = False, ns.cache_dir.absolute() / f"guide-{ns.base}-{ns.version}.jsonl"
            gate_main(ns)
            ns.cells = [ns.json]
        rows = [json.loads(line) for path in ns.cells for line in path.read_text(encoding="utf-8").splitlines()]
        data = summarize_gate(rows, gate.loaded_allowlist.entries)
        data = {"version": ns.version, "previous": ns.base, "tier": ns.tier, **data}
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


def _payload_cases(root: Path, out: Path) -> None:
    """Write every payload-validation case of the checkout at `root` and an index of their arguments."""
    sys.path.insert(0, str(root))
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

    sys.path.insert(0, str(root))
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
    commands = {"gate": gate_main, "record": record_main, "query": query_main, "guide": guide_main}
    return commands[ns.command](ns)


if __name__ == "__main__":
    sys.exit(main())
