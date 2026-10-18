"""Calibrate the option recommender's rules against a pinned corpus of public schemas.

Usage:
  python scripts/recommender_calibrate.py --out DIR [--manifest scripts/recommender_corpus.toml] [--cache-dir DIR]
      [--jobs N] [--only ID [ID ...]] [--import-check] [--check-upstream]
      [--calibration scripts/recommender_calibration.json] [--output-model-type TYPE] [--target-python-version V]
  python scripts/recommender_calibrate.py --check-manifest
  python scripts/recommender_calibrate.py --fetch-only [--cache-dir DIR] [--only ID [ID ...]]

The manifest pins every spec to a commit and a sha256. Specs are downloaded into the cache directory at run time
and used only when the digest matches; nothing is vendored. A spec with a `$ref` outside the document is rejected
unless its entry stubs external references, so generation never reads other files or the network, and every
generation runs with --no-allow-remote-refs, so a remote `$ref` that slips through fails instead of being fetched. The
newest pinned version of a schema is the subject and the previous one, when pinned, its baseline (`--diff-against`).

The recommender is the installed `datamodel-codegen --recommend-options full --output-format json`, called in this
process. For every subject the script takes its recommendation, then scores each configuration of the manifest's
decision grid on top of the latest standard preset: the configuration is passed with `--preset` and measured as the
trial's `current` candidate. A grid value is a table of config fields; an empty table leaves the decision to the
preset, and a value that no option can express is rejected. Configurations are ranked like the trial ranks its
candidates (numbered and doubled class names, other problem names, renamed locations, median length, fields generated
without their constraints; then fewer options); regret counts problem names. Rule conditions are evaluated with the
recommender's own evaluator on the shipped rules data. The script writes to --out (which must be empty):
- report.md: regret per schema, rules whose decision disagrees with the grid, threshold proposals, preset
  proposals, import checks, upstream heads (with --check-upstream), failures and changes since the last calibration;
- scores.json: every number behind the report;
- calibration.json: the proposed contents of scripts/recommender_calibration.json (with --only, the manifest's other
  schemas keep their entries from --calibration);
- rules.diff: threshold proposals as a diff of the rules data, when there are any.
It never edits the rules, the calibration file or the manifest.

With --import-check it generates the recommended, the best and the preset-candidate configurations into fresh
temporary directories and imports them in an isolated copy of this interpreter (no user site, PYTHON* variables
ignored, a temporary home), so it needs a Python at least as new as --target-python-version. That is the only place
generated code runs.

Runs use only the pinned refs. With --check-upstream, `git ls-remote` asks each schema's source repository for its
HEAD, and the report proposes rotating the entries whose head is not the newest pinned ref.

Known limitations: a rule is judged by its own `when`; `contested_when` and the order of rules within a decision are
not modelled. Each grid point is a full trial, which also generates the recommendation's neighbours when rules decide
fields the grid leaves unset, and generates the baseline too, so large schemas take a while.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import difflib
import functools
import hashlib
import io
import itertools
import json
import multiprocessing
import os
import re
import signal
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import warnings
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
    from typing import IO

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "scripts" / "recommender_corpus.toml"
DEFAULT_CALIBRATION = ROOT / "scripts" / "recommender_calibration.json"
RULES_PACKAGE = "datamodel_code_generator.resources"
RULES_FILE = "recommender_rules.json"
RULES_SOURCE = f"src/{RULES_PACKAGE.replace('.', '/')}/{RULES_FILE}"
PRESET_FAMILY = "standard"
OPTIONAL_RULE = "optional, not calibrated"
MANIFEST_FORMAT = 1
CALIBRATION_FORMAT = 1
KINDS = ("asyncapi", "jsonschema", "openapi")
ROLES = ("calibration", "holdout")
LAYOUTS = ("module", "package")
TOP_LEVEL_KEYS = frozenset({"format_version", "grid", "schema"})
SCHEMA_KEYS = frozenset({
    "id",
    "kind",
    "role",
    "license",
    "source",
    "url",
    "represents",
    "layout",
    "stub_external_refs",
    "versions",
})
VERSION_KEYS = frozenset({"ref", "date", "sha256"})
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]*")
FIELD_PATTERN = re.compile(r"[a-z][a-z0-9_]*")
REF_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
BASENAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.(?:json|yaml|yml)")
LICENSE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]*(?: (?:AND|OR|WITH) [A-Za-z0-9][A-Za-z0-9.+-]*)*")
ERROR_MESSAGE = re.compile(r"\berror:\s*(?P<message>\S.*)", re.IGNORECASE)
EXCEPTION_LINE = re.compile(r"[A-Za-z_][\w.]*(?:Error|Exception)\b")
UNSAFE_TEXT = re.compile(r"[^A-Za-z0-9 _.,:;'\"`()\[\]{}<>=+*/\\#@!?$%&~^-]")
URL_SCHEMES = ("https", "file")
MAX_BYTES = 64 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
FETCH_TIMEOUT = 120
GENERATE_TIMEOUT = 1800
IMPORT_TIMEOUT = 600
MESSAGE_LIMIT = 160
SHORT_REF = 12
USER_AGENT = "datamodel-code-generator-recommender-calibration"
GUARD_FLAGS = ("--ignore-pyproject", "--no-allow-remote-refs")
THRESHOLD_PRECISION = Decimal("0.0001")
REF_KEY = "$ref"
IO_OPTIONS = frozenset({"--input", "--url", "--output"})
CONTEXT_OPTIONS = frozenset({"--input-file-type", "--output-model-type", "--target-python-version"})
DROPPED_PAYLOAD_KEYS = frozenset({"content", "config", "config_complete"})
BOUND_OPERATORS = frozenset({">=", ">", "<=", "<"})
PACKAGE = "calibration_pkg"
VIRTUAL_OUTPUTS = {"module": "models.py", "package": "models"}
PRESET_WIN_RATIO = 0.7
PRESET_MAX_LOSS = 2
DISAGREEMENT_LIMIT = 2
IMPORT_SCRIPT = """
import importlib, sys
from pathlib import Path
root, package = Path(sys.argv[1]), sys.argv[2]
sys.path.insert(0, str(root))
for file in sorted((root / package).rglob("*.py")):
    importlib.import_module(".".join(file.relative_to(root).with_suffix("").parts).removesuffix(".__init__"))
"""

_Task = tuple[tuple[str, ...], bool]


class CalibrationError(Exception):
    """A problem that stops the calibration run."""


@dataclass(frozen=True)
class Version:
    """One pinned version of a schema."""

    ref: str
    date: str
    sha256: str


@dataclass(frozen=True)
class Schema:
    """One corpus entry of the manifest."""

    schema_id: str
    kind: str
    role: str
    license: str
    source: str
    url: str
    represents: str
    layout: str
    stub_external_refs: bool
    versions: tuple[Version, ...]

    def location(self, version: Version) -> str:
        """Return the URL of one pinned version."""
        return self.url.replace("{ref}", version.ref)

    def label(self, version: Version) -> str:
        """Return a short label for messages."""
        return f"{self.schema_id}@{version.ref[:SHORT_REF]}"

    @property
    def basename(self) -> str:
        """The file name the spec is stored under, as published upstream."""
        return urllib.parse.urlsplit(self.url).path.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class Manifest:
    """The corpus manifest: decision grid and schemas."""

    grid: dict[str, tuple[dict[str, Any], ...]]
    schemas: tuple[Schema, ...]


@dataclass(frozen=True)
class Subject:
    """A verified schema version to calibrate on, with its baseline version."""

    schema: Schema
    version: Version
    input_path: Path
    baseline: Version | None
    baseline_path: Path | None


@dataclass(frozen=True)
class Settings:
    """Run-wide generation settings: the context options and the preset the grid builds on."""

    output_model_type: str
    target_python_version: str
    preset: str


@dataclass
class Run:
    """Recommender results for one subject: its recommendation and its grid scores."""

    subject: Subject
    payload: dict[str, Any]
    grid: list[dict[str, Any]]

    @property
    def failed(self) -> bool:
        """Whether the recommendation itself failed."""
        return "error" in self.payload


def _default_cache_dir() -> Path:
    root = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(root) / "datamodel-code-generator" / "recommender-calibration"


def _safe(text: object) -> str:
    """Return one sanitized line of at most MESSAGE_LIMIT characters for reports."""
    line = next((part for part in str(text).splitlines() if part.strip()), "")
    return UNSAFE_TEXT.sub("?", line.strip())[:MESSAGE_LIMIT]


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {_safe(exc)}"


def _text(entry: Mapping[str, Any], key: str, where: str, problems: list[str], pattern: re.Pattern[str]) -> str:
    value = entry.get(key)
    if isinstance(value, str) and pattern.fullmatch(value):
        return value
    problems.append(f"{where}: {key} {value!r} is missing or invalid")
    return ""


def _choice(entry: Mapping[str, Any], key: str, where: str, problems: list[str], choices: Sequence[str]) -> str:
    value = entry.get(key, LAYOUTS[0] if key == "layout" else None)
    if value in choices:
        return value
    problems.append(f"{where}: {key} must be one of {', '.join(choices)}, not {value!r}")
    return choices[0]


def _versions(entry: Mapping[str, Any], where: str, problems: list[str], *, commit_refs: bool) -> tuple[Version, ...]:
    raw = entry.get("versions")
    if not isinstance(raw, list) or not raw or not all(isinstance(item, dict) for item in raw):
        problems.append(f"{where}: versions must be a non-empty list of tables")
        return ()
    versions: list[Version] = []
    for index, item in enumerate(raw):
        label = f"{where} versions[{index}]"
        if unknown := sorted(set(item) - VERSION_KEYS):
            problems.append(f"{label}: unknown keys {', '.join(unknown)}")
        ref = _text(item, "ref", label, problems, COMMIT_PATTERN if commit_refs else REF_PATTERN)
        versions.append(
            Version(
                ref,
                _text(item, "date", label, problems, DATE_PATTERN),
                _text(item, "sha256", label, problems, SHA256_PATTERN),
            )
        )
    dates = [version.date for version in versions]
    if dates != sorted(set(dates)):
        problems.append(f"{where}: versions must have distinct dates in ascending order")
    return tuple(versions)


def _schema(entry: object, index: int, problems: list[str]) -> Schema | None:
    where = f"schema[{index}]"
    if not isinstance(entry, dict):
        problems.append(f"{where}: must be a table")
        return None
    if isinstance(entry.get("id"), str):
        where = f"schema {entry['id']!r}"
    if unknown := sorted(set(entry) - SCHEMA_KEYS):
        problems.append(f"{where}: unknown keys {', '.join(unknown)}")
    url = entry.get("url")
    parts = urllib.parse.urlsplit(url) if isinstance(url, str) else None
    if parts is None or parts.scheme not in URL_SCHEMES or "{ref}" not in url:
        problems.append(f"{where}: url must be an https:// or file:// URL containing {{ref}}")
        url = ""
    elif not BASENAME_PATTERN.fullmatch(parts.path.rsplit("/", 1)[-1]):
        problems.append(f"{where}: url must end in a .json, .yaml or .yml file name")
    if not isinstance(stub := entry.get("stub_external_refs", False), bool):
        problems.append(f"{where}: stub_external_refs must be a boolean")
    source = entry.get("source")
    if not isinstance(source, str) or urllib.parse.urlsplit(source).scheme not in URL_SCHEMES:
        problems.append(f"{where}: source must be the https:// or file:// URL of a git repository")
    represents = entry.get("represents")
    if not isinstance(represents, str) or not represents.strip():
        problems.append(f"{where}: represents must say which schema style the entry stands for")
    return Schema(
        schema_id=_text(entry, "id", where, problems, ID_PATTERN),
        kind=_choice(entry, "kind", where, problems, KINDS),
        role=_choice(entry, "role", where, problems, ROLES),
        license=_text(entry, "license", where, problems, LICENSE_PATTERN),
        source=source if isinstance(source, str) else "",
        url=url,
        represents=represents if isinstance(represents, str) else "",
        layout=_choice(entry, "layout", where, problems, LAYOUTS),
        stub_external_refs=stub is True,
        versions=_versions(entry, where, problems, commit_refs=url.startswith("https://")),
    )


def _grid(raw: object, problems: list[str]) -> dict[str, tuple[dict[str, Any], ...]]:
    if not isinstance(raw, dict) or not raw:
        problems.append("grid must be a non-empty table of decisions")
        return {}
    grid: dict[str, tuple[dict[str, Any], ...]] = {}
    for decision, values in raw.items():
        valid = (
            ID_PATTERN.fullmatch(decision)
            and isinstance(values, list)
            and len(values) > 1
            and all(isinstance(value, dict) for value in values)
            and all(
                FIELD_PATTERN.fullmatch(name) and isinstance(item, str | bool | int | float)
                for value in values
                for name, item in value.items()
            )
            and len({json.dumps(value, sort_keys=True) for value in values}) == len(values)
        )
        if not valid:
            problems.append(
                f"grid {decision!r}: needs two or more distinct tables of config fields and scalar values "
                "(an empty table leaves the decision to the preset)"
            )
            continue
        grid[decision] = tuple(values)
    return grid


def _load_manifest(path: Path) -> Manifest:
    from datamodel_code_generator.util import load_toml  # ruff: ignore[import-outside-top-level] - keeps --help fast

    try:
        data = load_toml(path)
    except (OSError, ValueError) as exc:
        msg = f"cannot read manifest {path.name}: {_describe(exc)}"
        raise CalibrationError(msg) from exc
    problems: list[str] = []
    if data.get("format_version") != MANIFEST_FORMAT:
        problems.append(f"format_version must be {MANIFEST_FORMAT}")
    if unknown := sorted(set(data) - TOP_LEVEL_KEYS):
        problems.append(f"unknown top-level keys {', '.join(unknown)}")
    grid = _grid(data.get("grid"), problems)
    raw_schemas = data.get("schema")
    if not isinstance(raw_schemas, list) or not raw_schemas:
        problems.append("schema must be a non-empty array of tables")
        raw_schemas = []
    schemas = tuple(schema for index, entry in enumerate(raw_schemas) if (schema := _schema(entry, index, problems)))
    ids = [schema.schema_id for schema in schemas]
    if duplicates := sorted({schema_id for schema_id in ids if ids.count(schema_id) > 1}):
        problems.append(f"duplicate ids {', '.join(duplicates)}")
    if problems:
        msg = f"invalid manifest {path.name}:\n  " + "\n  ".join(problems)
        raise CalibrationError(msg)
    return Manifest(grid=grid, schemas=schemas)


def _manifest_summary(manifest: Manifest) -> str:
    roles = [schema.role for schema in manifest.schemas]
    files = sum(len(schema.versions) for schema in manifest.schemas)
    lines = [
        f"manifest: {len(roles)} schemas ({_roles_text(roles)}), {files} pinned files",
        f"grid: {len(_grid_points(manifest.grid))} configurations over {', '.join(manifest.grid)}",
    ]
    lines.extend(
        f"{schema.schema_id}: {schema.kind}, {schema.role}, {schema.license}, {schema.layout}"
        f"{', external refs stubbed' if schema.stub_external_refs else ''}; "
        + ", ".join(f"{version.date} {version.ref[:SHORT_REF]}" for version in schema.versions)
        for schema in manifest.schemas
    )
    return "\n".join(lines)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _stream(response: Any, out: IO[bytes], url: str, label: str) -> str:
    """Copy a response into a file, failing closed on a scheme change or an oversized body; return its sha256."""
    if urllib.parse.urlsplit(response.geturl()).scheme != urllib.parse.urlsplit(url).scheme:
        msg = f"{label}: redirected to another scheme"
        raise CalibrationError(msg)
    digest = hashlib.sha256()
    size = 0
    while chunk := response.read(CHUNK_BYTES):
        size += len(chunk)
        if size > MAX_BYTES:
            msg = f"{label}: larger than {MAX_BYTES} bytes"
            raise CalibrationError(msg)
        digest.update(chunk)
        out.write(chunk)
    return digest.hexdigest()


def _download(url: str, target: Path, label: str) -> str:
    partial = target.with_name(f".{target.name}.{os.getpid()}.part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response, partial.open("wb") as out:
            digest = _stream(response, out, url, label)
        partial.replace(target)
    except OSError as exc:
        msg = f"{label}: download failed: {_describe(exc)}"
        raise CalibrationError(msg) from exc
    finally:
        partial.unlink(missing_ok=True)
    return digest


def _fetch(schema: Schema, version: Version, cache_dir: Path) -> tuple[Path, str]:
    """Return the verified local copy of one version and whether it was cached or fetched."""
    label = schema.label(version)
    target = cache_dir / schema.schema_id / version.ref / schema.basename
    if target.is_file() and _sha256(target) == version.sha256:
        return target, "cached"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    if (actual := _download(schema.location(version), target, label)) != version.sha256:
        target.unlink()
        msg = f"{label}: sha256 {actual} does not match the pinned {version.sha256}"
        raise CalibrationError(msg)
    return target, "fetched"


def _load_document(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text)
    import yaml  # ruff: ignore[import-outside-top-level] - only YAML specs need it

    from datamodel_code_generator.util import get_safe_loader  # ruff: ignore[import-outside-top-level]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return yaml.load(text, Loader=get_safe_loader())
        except yaml.YAMLError as exc:
            raise ValueError(str(exc)) from exc


def _external_refs(document: Any) -> list[str]:
    """Return every `$ref` that leaves the document, walking shared YAML nodes once."""
    found: list[str] = []
    seen: set[int] = set()
    stack = [document]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict | list) or id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, dict):
            if isinstance(ref := node.get(REF_KEY), str) and not ref.startswith("#"):
                found.append(ref)
            stack.extend(node.values())
        else:
            stack.extend(node)
    return sorted(found)


def _stub_external_refs(node: Any) -> Any:
    if isinstance(node, dict):
        if isinstance(ref := node.get(REF_KEY), str) and not ref.startswith("#"):
            return {"type": "object"}
        return {key: _stub_external_refs(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_stub_external_refs(item) for item in node]
    return node


def _prepare(schema: Schema, version: Version, path: Path) -> tuple[Path, str]:
    """Return the file the recommender reads; fail closed on `$ref`s that leave the document.

    A stubbed copy is written as JSON text under the upstream file name (JSON is valid YAML), so the input stem the
    recommender derives names from stays the same.
    """
    label = schema.label(version)
    try:
        document = _load_document(path)
    except (ValueError, UnicodeDecodeError) as exc:
        msg = f"{label}: cannot parse: {_describe(exc)}"
        raise CalibrationError(msg) from exc
    if not (refs := _external_refs(document)):
        return path, ""
    if not schema.stub_external_refs:
        msg = (
            f"{label}: {len(refs)} $ref value(s) point outside the document (first: {_safe(refs[0])!r}); "
            "set stub_external_refs = true to replace them"
        )
        raise CalibrationError(msg)
    prepared = path.parent / "prepared" / path.name
    prepared.parent.mkdir(exist_ok=True)
    prepared.write_text(
        json.dumps(_stub_external_refs(document), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return prepared, f", {len(refs)} external $ref stubbed"


def _fetch_subjects(schemas: Iterable[Schema], cache_dir: Path) -> list[Subject]:
    subjects: list[Subject] = []
    for schema in schemas:
        paths: list[Path] = []
        for version in schema.versions[-2:]:
            cached, status = _fetch(schema, version, cache_dir)
            prepared, note = _prepare(schema, version, cached)
            print(f"fetch {schema.label(version)}: {status}{note}", flush=True)
            paths.append(prepared)
        baseline = schema.versions[-2] if len(schema.versions) > 1 else None
        subjects.append(Subject(schema, schema.versions[-1], paths[-1], baseline, paths[0] if baseline else None))
    return subjects


@functools.cache
def _cli() -> Callable[[Sequence[str]], int]:
    """Return the datamodel-codegen entry point, keeping this process's Ctrl-C handler over the one it installs."""
    handler = signal.getsignal(signal.SIGINT)
    from datamodel_code_generator.__main__ import main  # ruff: ignore[import-outside-top-level] - keeps --help fast

    signal.signal(signal.SIGINT, handler)
    return main


def _failure(stderr: str, code: object) -> str:
    """Return why a command failed: the message of its `error:` line without the program name, else its first line."""
    found = next((match["message"] for line in stderr.splitlines() if (match := ERROR_MESSAGE.search(line))), stderr)
    return _safe(found) or f"exit code {code}"


def _candidate(trial: Mapping[str, Any], label: str) -> dict[str, Any]:
    return next(candidate for candidate in trial["candidates"] if candidate["label"] == label)


def _measurement(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Return a trial candidate's class-name metrics, renamed locations and fields without their constraints.

    A rejected candidate gives the error that rejected it.
    """
    if candidate["error"] is not None:
        return {"error": candidate["error"]}
    stability = candidate["stability"]
    return {
        "metrics": candidate["metrics"],
        "renamed": None if stability is None else stability["locations"]["renamed"],
        "dropped_constraints": candidate["dropped_constraint_warnings"],
    }


def _execute(task: _Task) -> dict[str, Any]:
    """Run one `--recommend-options` command in this process.

    Return the recommendation payload without its text report and paths, or for a scored configuration the
    measurement of the trial's `current` candidate (the configuration as set); a failure is an `error` result.
    """
    arguments, score = task
    stdout, stderr = io.StringIO(), io.StringIO()
    with warnings.catch_warnings(), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        warnings.simplefilter("ignore")
        try:
            code = _cli()(arguments)
        except SystemExit as exc:
            code = exc.code
        except Exception as exc:  # ruff: ignore[blind-except] - one schema's failure is a result, not the run's
            return {"error": _describe(exc)}
    if code:
        return {"error": _failure(stderr.getvalue(), code)}
    payload = json.loads(stdout.getvalue())
    if score:
        return _measurement(_candidate(payload["trial"], payload["trial"]["current"]))
    kept = {key: value for key, value in payload.items() if key not in DROPPED_PAYLOAD_KEYS}
    arguments = [token for token in _without(payload["arguments"], IO_OPTIONS) if token not in GUARD_FLAGS]
    return {**kept, "arguments": arguments}


def _run_tasks(tasks: list[_Task], jobs: int) -> list[dict[str, Any]]:
    if jobs <= 1:
        return [_execute(task) for task in tasks]
    with ProcessPoolExecutor(jobs, mp_context=multiprocessing.get_context("spawn")) as pool:
        return list(pool.map(_execute, tasks))


def _context(subject: Subject, settings: Settings) -> list[str]:
    return [
        "--input-file-type",
        subject.schema.kind,
        "--output-model-type",
        settings.output_model_type,
        "--target-python-version",
        settings.target_python_version,
    ]


def _invocation(subject: Subject, settings: Settings) -> list[str]:
    """Return the recommender command for a subject; --output is a virtual path that selects the layout."""
    output = subject.input_path.parent / VIRTUAL_OUTPUTS[subject.schema.layout]
    baseline = ["--diff-against", str(subject.baseline_path)] if subject.baseline_path else []
    return [
        "--input",
        str(subject.input_path),
        "--output",
        str(output),
        *_context(subject, settings),
        *baseline,
        *GUARD_FLAGS,
        "--recommend-options",
        "full",
        "--output-format",
        "json",
    ]


def _settings(output_model_type: str, target_python_version: str) -> Settings:
    """Return the run settings with the latest standard preset for the target Python version."""
    from datamodel_code_generator import DataModelType, PythonVersion  # ruff: ignore[import-outside-top-level]
    from datamodel_code_generator.preset import latest_preset_name  # ruff: ignore[import-outside-top-level]

    try:
        DataModelType(output_model_type)
        preset = latest_preset_name(PRESET_FAMILY, PythonVersion(target_python_version))
    except (KeyError, ValueError) as exc:
        msg = (
            f"no {PRESET_FAMILY} preset for --output-model-type {output_model_type} "
            f"and --target-python-version {target_python_version}"
        )
        raise CalibrationError(msg) from exc
    return Settings(output_model_type, target_python_version, preset)


def _effective_values(settings: Settings, kind: str, names: Iterable[str]) -> dict[str, Any]:
    """Return what the grid's preset gives these config fields, as pyproject.toml spells them.

    The preset is resolved as --preset resolves it; a field it leaves alone keeps the config default, None when unset.
    """
    from datamodel_code_generator import (  # ruff: ignore[import-outside-top-level]
        DataModelType,
        InputFileType,
        PythonVersion,
    )
    from datamodel_code_generator._recommend._rules import (  # ruff: ignore[import-outside-top-level, import-private-name]
        toml_value,
    )
    from datamodel_code_generator.base_config import BaseGenerateConfig  # ruff: ignore[import-outside-top-level]
    from datamodel_code_generator.preset import (  # ruff: ignore[import-outside-top-level]
        PresetContext,
        resolve_preset_config_updates,
    )

    context = PresetContext(
        input_file_type=InputFileType(kind),
        output_model_type=DataModelType(settings.output_model_type),
        target_python_version=PythonVersion(settings.target_python_version),
    )
    resolved = resolve_preset_config_updates(
        settings.preset, context=context, use_annotated=False, explicit_fields=set()
    )
    preset = {item.field_name: item.pyproject_value for item in resolved.items}
    fields = BaseGenerateConfig.model_fields
    return {
        name: preset[name]
        if name in preset
        else toml_value(fields[name].get_default(call_default_factory=True))
        if name in fields
        else None
        for name in names
    }


def _option(name: str, value: Any) -> list[str]:
    flag = name.replace("_", "-")
    if value is True:
        return [f"--{flag}"]
    if value is False:
        return [f"--no-{flag}"]
    return [f"--{flag}", str(value)]


def _plan(values: Mapping[str, Any], effective: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return the grid values the preset does not already give, and the options that set them."""
    delta = {name: value for name, value in values.items() if effective[name] != value}
    return delta, [token for name, value in delta.items() for token in _option(name, value)]


def _scored(preset: str, plan: tuple[dict[str, Any], list[str]], outcome: Mapping[str, Any]) -> dict[str, Any]:
    delta, arguments = plan
    result = {"preset": preset, "delta": delta, "arguments": arguments}
    return {**result, "rejected": outcome["error"]} if "error" in outcome else {**result, **outcome}


@functools.cache
def _condition_adapter() -> Any:
    from pydantic import TypeAdapter  # ruff: ignore[import-outside-top-level]

    from datamodel_code_generator._recommend._rules import (  # ruff: ignore[import-outside-top-level, import-private-name]
        Condition,
    )

    return TypeAdapter(Condition)


def _evaluate(when: Any, signals: Mapping[str, Any]) -> bool:
    """Evaluate a rule condition of the rules data with the recommender's own evaluator."""
    from datamodel_code_generator._recommend._rules import (  # ruff: ignore[import-outside-top-level, import-private-name]
        matches,
    )

    return matches(_condition_adapter().validate_python(when), signals)


def _rules_path() -> Path:
    from importlib.resources import files  # ruff: ignore[import-outside-top-level]

    return Path(str(files(RULES_PACKAGE).joinpath(RULES_FILE)))


def _upstream(schemas: Iterable[Schema]) -> list[dict[str, Any]]:
    """Ask each schema's source repository for its HEAD with git ls-remote; nothing is downloaded."""
    rows: list[dict[str, Any]] = []
    for schema in schemas:
        newest = schema.versions[-1]
        row: dict[str, Any] = {"schema": schema.schema_id, "pinned": newest.ref, "date": newest.date}
        rows.append(row)
        try:
            completed = subprocess.run(
                ["git", "ls-remote", schema.source, "HEAD"],
                capture_output=True,
                text=True,
                timeout=FETCH_TIMEOUT,
                check=False,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            row["error"] = _describe(exc)
            continue
        heads = [fields[0] for line in completed.stdout.splitlines() if (fields := line.split())[1:] == ["HEAD"]]
        if completed.returncode or not heads:
            row["error"] = _safe(completed.stderr) or "no HEAD reported"
        else:
            row["head"] = heads[0]
    return rows


def _grid_points(grid: Mapping[str, tuple[dict[str, Any], ...]]) -> list[dict[str, dict[str, Any]]]:
    return [dict(zip(grid, values, strict=True)) for values in itertools.product(*grid.values())]


def _merged(point: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    return {name: value for axis in point.values() for name, value in axis.items()}


def _ok(result: Mapping[str, Any]) -> bool:
    return "metrics" in result


def _rank(result: Mapping[str, Any]) -> tuple[int, int, int, int, int]:
    """Return the trial's ranking key: numbered and doubled names, other problem names, renamed locations, median.

    Fields generated without their constraints come last, after the class names.
    """
    metrics = result["metrics"]
    return (
        metrics["numbered"] + metrics["doubled"],
        metrics["other_problems"],
        result.get("renamed") or 0,
        metrics["median_length"],
        result.get("dropped_constraints") or 0,
    )


def _order(result: Mapping[str, Any]) -> tuple[Any, ...]:
    arguments = result["arguments"]
    return _rank(result), sum(token.startswith("--") for token in arguments), " ".join(arguments)


def _best(run: Run, indexes: Iterable[int]) -> int | None:
    candidates = [index for index in indexes if _ok(run.grid[index])]
    return min(candidates, key=lambda index: _order(run.grid[index])) if candidates else None


def _best_rank(run: Run, indexes: Iterable[int]) -> tuple[int, int, int, int] | None:
    best = _best(run, indexes)
    return None if best is None else _rank(run.grid[best])


def _split(
    points: list[dict[str, dict[str, Any]]], decision: str, value: Mapping[str, Any]
) -> tuple[list[int], list[int]]:
    inside = [index for index, point in enumerate(points) if point[decision] == value]
    return inside, [index for index in range(len(points)) if index not in inside]


def _selected(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the measurement of the candidate the trial selected, None when every candidate failed."""
    trial = payload["trial"]
    return None if trial["selected"] is None else _measurement(_candidate(trial, trial["selected"]))


def _without(arguments: Sequence[str], options: frozenset[str]) -> list[str]:
    """Return the arguments without the given options and their values."""
    kept: list[str] = []
    skip = False
    for token in arguments:
        if skip:
            skip = False
        elif token in options:
            skip = True
        elif token.split("=", 1)[0] not in options:
            kept.append(token)
    return kept


def _point_arguments(subject: Subject, settings: Settings, result: Mapping[str, Any]) -> list[str]:
    return [*_context(subject, settings), "--preset", result["preset"], *result["arguments"]]


def _subject_summary(run: Run, points: list[dict[str, dict[str, Any]]]) -> dict[str, Any]:
    subject = run.subject
    best = _best(run, range(len(points)))
    summary: dict[str, Any] = {
        "schema": subject.schema.schema_id,
        "role": subject.schema.role,
        "ref": subject.version.ref,
        "diff_against": subject.baseline.ref if subject.baseline else None,
        "rejected": sum(not _ok(result) for result in run.grid),
        "best": None
        if best is None
        else {
            "values": _merged(points[best]),
            "preset": run.grid[best]["preset"],
            "arguments": run.grid[best]["arguments"],
            "metrics": run.grid[best]["metrics"],
            "renamed": run.grid[best]["renamed"],
        },
    }
    if run.failed:
        return {**summary, "error": run.payload["error"]}
    selected = _selected(run.payload) or {}
    recommended = selected.get("metrics")
    summary["recommended"] = {
        "rules": sorted(item["rule"] for item in run.payload["recommendations"]),
        "arguments": _without(run.payload["arguments"], CONTEXT_OPTIONS),
        "metrics": recommended,
        "renamed": selected.get("renamed"),
    }
    summary["regret"] = (
        None if recommended is None or best is None else recommended["problems"] - run.grid[best]["metrics"]["problems"]
    )
    return summary


def _matching_value(
    grid: Mapping[str, tuple[dict[str, Any], ...]], decision: str, wanted: Mapping[str, Any]
) -> dict[str, Any] | None:
    return next(
        (value for value in grid.get(decision, ()) if wanted and all(value.get(k) == v for k, v in wanted.items())),
        None,
    )


def _applies(rule: Mapping[str, Any], subject: Subject, settings: Settings) -> bool:
    applies = rule.get("applies_to") or {}
    return subject.schema.kind in (applies.get("input_file_types") or [subject.schema.kind]) and (
        settings.output_model_type in (applies.get("output_model_types") or [settings.output_model_type])
    )


def _verdict(run: Run, points: list[dict[str, dict[str, Any]]], decision: str, value: Mapping[str, Any]) -> str | None:
    """Return whether the grid prefers the rule's values on this schema: fire, hold or either."""
    inside, outside = (_best_rank(run, indexes) for indexes in _split(points, decision, value))
    if inside is None or outside is None:
        return None
    if inside == outside:
        return "either"
    return "fire" if inside < outside else "hold"


def _disagreements(when: Any, cases: list[tuple[Run, str]]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {role: [] for role in ROLES}
    for run, verdict in cases:
        if _evaluate(when, run.payload["signals"]) != (verdict == "fire"):
            found[run.subject.schema.role].append(run.subject.schema.schema_id)
    return found


def _leaves(node: Any, path: tuple[str | int, ...] = ()) -> Iterator[tuple[tuple[str | int, ...], dict[str, Any]]]:
    """Yield the numeric bound leaves of a predicate tree with their paths."""
    if not isinstance(node, dict):
        return
    value = node.get("value")
    if node.get("op") in BOUND_OPERATORS and isinstance(value, int | float) and not isinstance(value, bool):
        yield path, node
    for key in ("all", "any"):
        for index, child in enumerate(node.get(key) or ()):
            yield from _leaves(child, (*path, key, index))
    if "not" in node:
        yield from _leaves(node["not"], (*path, "not"))


def _operand(leaf: Mapping[str, Any], signals: Mapping[str, Any]) -> float | None:
    if "signal" in leaf:
        return signals.get(leaf["signal"])
    numerator, denominator = (signals.get(name) for name in leaf.get("ratio") or ("", ""))
    return numerator / denominator if numerator is not None and denominator else None


def _leaf_text(leaf: Mapping[str, Any]) -> str:
    operand = leaf["signal"] if "signal" in leaf else "/".join(leaf["ratio"])
    return f"{operand} {leaf['op']} {leaf['value']}"


def _with_value(when: Any, path: tuple[str | int, ...], value: float) -> Any:
    edited = copy.deepcopy(when)
    node = edited
    for step in path:
        node = node[step]
    node["value"] = value
    return edited


def _bound(value: float, op: str) -> float:
    """Round a candidate threshold to four decimals on the side that keeps each observed value's verdict.

    A schema whose value is a candidate fires under `>=` and `<=` and stays quiet under `>` and `<`; rounding down for
    `>=` and `<` and up for `>` and `<=` keeps it so.
    """
    if isinstance(value, int):
        return value
    rounding = ROUND_FLOOR if op in {">=", "<"} else ROUND_CEILING
    return float(Decimal(repr(value)).quantize(THRESHOLD_PRECISION, rounding=rounding))


def _thresholds(leaf: Mapping[str, Any], cases: list[tuple[Run, str]]) -> list[float]:
    """Return the candidate thresholds of a leaf as the rules data would record them: observed values and one beyond."""
    observed = sorted({operand for run, _ in cases if (operand := _operand(leaf, run.payload["signals"])) is not None})
    if not observed:
        return []
    beyond = observed[-1] + 1 if leaf["op"] in {">=", "<"} else observed[0] - 1
    return sorted({_bound(value, leaf["op"]) for value in (*observed, beyond)} - {leaf["value"]})


def _threshold_proposal(
    index: int, rule: Mapping[str, Any], cases: list[tuple[Run, str]], current: dict[str, list[str]]
) -> dict[str, Any] | None:
    """Return the single leaf change that removes most calibration disagreements without hurting the holdout."""
    best: tuple[tuple[float, ...], dict[str, Any]] | None = None
    for path, leaf in _leaves(rule["when"]):
        for value in _thresholds(leaf, cases):
            trial = _disagreements(_with_value(rule["when"], path, value), cases)
            if len(trial["calibration"]) >= len(current["calibration"]) or len(trial["holdout"]) > len(
                current["holdout"]
            ):
                continue
            key = (len(trial["calibration"]), len(trial["holdout"]), abs(value - leaf["value"]), value)
            if best is None or key < best[0]:
                best = (
                    key,
                    {
                        "rule": rule["id"],
                        "pointer": f"/rules/{index}/when" + "".join(f"/{step}" for step in path) + "/value",
                        "path": list(path),
                        "rule_index": index,
                        "leaf": _leaf_text(leaf),
                        "current": leaf["value"],
                        "proposed": value,
                        "calibration": [len(current["calibration"]), len(trial["calibration"])],
                        "holdout": [len(current["holdout"]), len(trial["holdout"])],
                    },
                )
    return None if best is None else best[1]


def _rule_rows(
    rules: list[dict[str, Any]],
    runs: list[Run],
    grid: Mapping[str, tuple[dict[str, Any], ...]],
    settings: Settings,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Judge every rule against the grid and search one threshold change per disagreeing rule.

    Optional rules list options for the user to decide and never enter the command, so they are labelled, not judged.
    """
    points = _grid_points(grid)
    rows: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    for index, rule in enumerate(rules):
        decision = rule.get("decision", "")
        row: dict[str, Any] = {"rule": rule.get("id"), "decision": decision}
        rows.append(row)
        if rule.get("optional"):
            row["covered"] = OPTIONAL_RULE
            continue
        if decision not in grid:
            row["covered"] = f"decision {decision} has no grid axis"
            continue
        if (value := _matching_value(grid, decision, rule.get("set") or {})) is None:
            row["covered"] = "its values are not a grid value"
            continue
        row["covered"] = "yes"
        cases: list[tuple[Run, str]] = []
        row["fires"], row["neutral"] = [], []
        for run in runs:
            if run.failed or not _applies(rule, run.subject, settings):
                continue
            schema_id = run.subject.schema.schema_id
            if _evaluate(rule["when"], run.payload["signals"]):
                row["fires"].append(schema_id)
            if (verdict := _verdict(run, points, decision, value)) in {"fire", "hold"}:
                cases.append((run, verdict))
            else:
                row["neutral"].append(schema_id)
        row["disagrees"] = _disagreements(rule["when"], cases)
        if row["disagrees"]["calibration"] and (proposal := _threshold_proposal(index, rule, cases, row["disagrees"])):
            proposals.append(proposal)
    return rows, proposals


def _preset_value(run: Run, points: list[dict[str, dict[str, Any]]], decision: str, value: Mapping[str, Any]) -> bool:
    """Return whether a grid value is what the preset gives a run's input kind, from the delta of its configurations."""
    index = _split(points, decision, value)[0][0]
    return not any(name in run.grid[index]["delta"] for name in value)


def _preset_point(
    run: Run,
    points: list[dict[str, dict[str, Any]]],
    grid: Mapping[str, tuple[dict[str, Any], ...]],
    decision: str,
    value: Mapping[str, Any],
) -> int | None:
    """Return the grid point of the preset with only this value changed for a run's input kind, if the grid has it."""
    target = {
        axis: value
        if axis == decision
        else next((item for item in items if _preset_value(run, points, axis, item)), None)
        for axis, items in grid.items()
    }
    return None if None in target.values() else points.index(target)


def _preset_candidates(
    runs: list[Run],
    points: list[dict[str, dict[str, Any]]],
    grid: Mapping[str, tuple[dict[str, Any], ...]],
    settings: Settings,
) -> list[dict[str, Any]]:
    """Compare each value the preset does not set for some input kind with the rest of its axis on every schema.

    A value would become part of a new dated preset, so its import check generates the latest preset with only
    that value changed, as the preset resolves for each schema's input kind.
    """
    candidates: list[dict[str, Any]] = []
    for decision, values in grid.items():
        for value in values:
            if all(_preset_value(run, points, decision, value) for run in runs):
                continue
            inside_indexes, outside_indexes = _split(points, decision, value)
            wins = {role: [0, 0] for role in ROLES}
            losses: dict[str, float] = {}
            import_arguments: dict[str, tuple[str, ...] | None] = {}
            for run in runs:
                schema = run.subject.schema
                preset_index = _preset_point(run, points, grid, decision, value)
                preset_result = None if preset_index is None else run.grid[preset_index]
                import_arguments[schema.schema_id] = (
                    tuple(_point_arguments(run.subject, settings, preset_result))
                    if preset_result is not None and _ok(preset_result)
                    else None
                )
                inside, outside = _best(run, inside_indexes), _best(run, outside_indexes)
                if inside is None or outside is None:
                    continue
                with_value, without_value = run.grid[inside], run.grid[outside]
                wins[schema.role][1] += 1
                if _rank(with_value) < _rank(without_value):
                    wins[schema.role][0] += 1
                elif _rank(with_value) > _rank(without_value):
                    extra = with_value["metrics"]["problems"] - without_value["metrics"]["problems"]
                    losses[schema.schema_id] = max(extra, 0)
            eligible = all(total and won / total >= PRESET_WIN_RATIO for won, total in wins.values()) and (
                max(losses.values(), default=0) <= PRESET_MAX_LOSS
            )
            candidates.append({
                "decision": decision,
                "values": value,
                "wins": wins,
                "losses": losses,
                "eligible": eligible,
                "import_arguments": import_arguments,
            })
    return candidates


def _isolated_environment(home: Path) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        **dict.fromkeys(("HOME", "USERPROFILE", "TMPDIR", "TEMP", "TMP"), str(home)),
    }
    if system_root := os.environ.get("SYSTEMROOT"):
        env["SYSTEMROOT"] = system_root
    return env


def _without_local_paths(text: str, cwd: Path) -> str:
    """Replace the working directory, this interpreter's prefixes and the home directory with placeholders."""
    places = {"<tmp>": cwd, "<prefix>": Path(sys.prefix), "<base prefix>": Path(sys.base_prefix), "<home>": Path.home()}
    for old, new in sorted(
        {(str(variant), name) for name, path in places.items() for variant in (path, path.resolve())},
        key=lambda pair: (-len(pair[0]), pair[1]),
    ):
        text = text.replace(old, new)
    return text


def _isolated_run(command: list[str], *, cwd: Path, env: dict[str, str], timeout: int) -> str | None:
    """Run one command in cwd; return None on success, else its last exception line (or last line), paths replaced."""
    try:
        completed = subprocess.run(
            command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return f"timed out after {timeout} s"
    if completed.returncode == 0:
        return None
    lines = [_without_local_paths(line, cwd) for line in completed.stderr.splitlines() if line.strip()]
    found = next((line for line in reversed(lines) if EXCEPTION_LINE.match(line)), lines[-1] if lines else None)
    return _safe(found or f"exit code {completed.returncode}")


def _import_check(subject: Subject, arguments: list[str]) -> str:
    """Generate one configuration into a fresh directory and import it in an isolated interpreter."""
    with tempfile.TemporaryDirectory(prefix="dcg-calibration-") as name:
        root = Path(name)
        (home := root / "home").mkdir()
        (package := root / PACKAGE).mkdir()
        output = package if subject.schema.layout == "package" else package / "__init__.py"
        env = _isolated_environment(home)
        python = [sys.executable, "-I", "-B"]
        generate = [
            *python,
            "-m",
            "datamodel_code_generator",
            "--input",
            str(subject.input_path),
            "--output",
            str(output),
            *GUARD_FLAGS,
        ]
        if error := _isolated_run([*generate, *arguments], cwd=root, env=env, timeout=GENERATE_TIMEOUT):
            return f"generation failed: {error}"
        importer = [*python, "-c", IMPORT_SCRIPT, str(root), PACKAGE]
        if error := _isolated_run(importer, cwd=root, env=env, timeout=IMPORT_TIMEOUT):
            return f"import failed: {error}"
    return "ok"


def _import_targets(
    runs: list[Run], summaries: Mapping[str, Mapping[str, Any]], candidates: list[dict[str, Any]], settings: Settings
) -> dict[tuple[str, tuple[str, ...]], list[str]]:
    """Return the configurations to import per schema: recommended, grid best and preset proposals."""
    targets: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    for run in runs:
        schema_id = run.subject.schema.schema_id
        configurations: list[tuple[str, Sequence[str]]] = []
        if not run.failed:
            configurations.append(("recommended", _without(run.payload.get("arguments", ()), IO_OPTIONS)))
        if (best := summaries[schema_id]["best"]) is not None:
            configurations.append(("grid best", _point_arguments(run.subject, settings, best)))
        configurations.extend(
            (f"preset + {_values_text(candidate['values'])}", arguments)
            for candidate in candidates
            if candidate["eligible"] and (arguments := candidate["import_arguments"].get(schema_id))
        )
        for label, arguments in configurations:
            targets.setdefault((schema_id, tuple(arguments)), []).append(label)
    return targets


def _values_text(values: Mapping[str, Any]) -> str:
    return " ".join(f"{name}={json.dumps(value)}" for name, value in values.items())


def _recorded(entry: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not entry or not entry.get("metrics"):
        return None
    metrics = entry["metrics"]
    recorded = {
        "arguments": entry["arguments"],
        "problems": metrics["problems"],
        "median_length": metrics["median_length"],
    }
    return {**recorded, "preset": entry["preset"]} if "preset" in entry else recorded


def _calibration(
    summaries: list[dict[str, Any]],
    rules_document: Mapping[str, Any],
    settings: Settings,
    grid: Mapping[str, Any],
    kept: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the proposed calibration file: this run's schemas, and the kept entries of schemas it did not select."""
    return {
        "format_version": CALIBRATION_FORMAT,
        "rules_revision": rules_document.get("rules_revision"),
        "calibrated_presets": rules_document.get("calibrated_presets"),
        "settings": {
            "output_model_type": settings.output_model_type,
            "target_python_version": settings.target_python_version,
            "preset": settings.preset,
        },
        "grid": {"decisions": list(grid), "configurations": len(_grid_points(grid))},
        "schemas": {
            **kept,
            **{
                summary["schema"]: {
                    "ref": summary["ref"],
                    "diff_against": summary["diff_against"],
                    "rules": summary["recommended"]["rules"],
                    "recommended": _recorded(summary["recommended"]),
                    "best": _recorded(summary["best"]),
                    "regret": summary["regret"],
                }
                for summary in summaries
                if "error" not in summary
            },
        },
    }


def _brief(value: object) -> str:
    if isinstance(value, list):
        return "[" + " ".join(map(str, value)) + "]"
    return json.dumps(value, sort_keys=True) if isinstance(value, dict) else str(value)


def _flatten(entry: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in entry.items():
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{prefix}{key}."))
        else:
            flat[f"{prefix}{key}"] = value
    return flat


def _changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    flat_before, flat_after = _flatten(before), _flatten(after)
    return [
        f"{key} {_brief(flat_before.get(key))} -> {_brief(flat_after.get(key))}"
        for key in sorted(flat_before.keys() | flat_after.keys())
        if flat_before.get(key) != flat_after.get(key)
    ]


def _drift(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> list[str]:
    """Compare this run with the last accepted calibration file."""
    if previous is None:
        return ["No previous calibration file; every result is new."]
    header = {key: value for key, value in current.items() if key != "schemas"}
    lines = _changes({key: previous.get(key) for key in header}, header)
    before_schemas, after_schemas = previous.get("schemas") or {}, current["schemas"]
    for schema_id in sorted(before_schemas.keys() | after_schemas.keys()):
        if schema_id not in before_schemas:
            lines.append(f"{schema_id}: new")
        elif schema_id not in after_schemas:
            lines.append(f"{schema_id}: not in this run")
        elif changes := _changes(before_schemas[schema_id] or {}, after_schemas[schema_id]):
            lines.append(f"{schema_id}: " + "; ".join(changes))
    return lines or ["No changes since the last calibration."]


def _cell(value: object) -> str:
    return ("" if value is None else str(value)).replace("|", "/") or "-"


def _code(arguments: Sequence[str]) -> str:
    return f"`{' '.join(arguments)}`" if arguments else "(preset only)"


def _table(header: Sequence[str], rows: Iterable[Sequence[object]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines.extend("| " + " | ".join(_cell(item) for item in row) + " |" for row in rows)
    return lines


def _metric(entry: Mapping[str, Any] | None, key: str) -> object:
    return (entry.get("metrics") or {}).get(key) if entry else None


def _roles_text(roles: Sequence[str]) -> str:
    return ", ".join(f"{roles.count(role)} {role}" for role in ROLES)


def _schemas_section(calibration: Mapping[str, Any], summaries: list[dict[str, Any]]) -> list[str]:
    settings, grid = calibration["settings"], calibration["grid"]
    rows = [
        (
            summary["schema"],
            summary["role"],
            summary["ref"][:SHORT_REF]
            + (f" (diff against {summary['diff_against'][:SHORT_REF]})" if summary["diff_against"] else ""),
            "failed" if "error" in summary else ", ".join(summary["recommended"]["rules"]),
            _metric(summary.get("recommended"), "problems"),
            _metric(summary.get("recommended"), "median_length"),
            _code(summary["best"]["arguments"]) if summary.get("best") else None,
            _metric(summary.get("best"), "problems"),
            _metric(summary.get("best"), "median_length"),
            summary.get("regret"),
        )
        for summary in summaries
    ]
    return [
        "# Recommender calibration",
        "",
        f"- Rules: revision {calibration['rules_revision']}, calibrated on {calibration['calibrated_presets']} presets",
        f"- Settings: {settings['output_model_type']}, target Python {settings['target_python_version']}",
        (
            f"- Grid: {grid['configurations']} configurations over {', '.join(grid['decisions'])}, "
            f"each on top of {settings['preset']}"
        ),
        f"- Schemas: {len(summaries)} ({_roles_text([summary['role'] for summary in summaries])})",
        "",
        "## Schemas",
        "",
        (
            "Problems and median: problem names and median class-name length of the configuration the "
            "--recommend-options trial selects and of the grid's best one. Grid configurations are ranked like the "
            "trial ranks candidates: numbered and doubled names, other problem names, renamed locations, median "
            "length, then fewer options. Regret: the recommendation's problem names minus the best grid "
            "configuration's."
        ),
        "",
        *_table(
            (
                "Schema",
                "Role",
                "Version",
                "Rules fired",
                "Problems",
                "Median",
                "Grid best",
                "Problems",
                "Median",
                "Regret",
            ),
            rows,
        ),
    ]


def _rules_section(rules: list[dict[str, Any]]) -> list[str]:
    covered = [row for row in rules if row["covered"] == "yes"]
    lines = [
        "",
        "## Rules against the grid",
        "",
        (
            "A rule should fire on a schema when the grid's best configuration with its values beats the best one "
            "without them. Neutral: a tie, or one side has no valid configuration."
        ),
        "",
        *_table(
            ("Rule", "Decision", "Fires on", "Disagrees (calibration)", "Disagrees (holdout)", "Neutral"),
            (
                (
                    row["rule"],
                    row["decision"],
                    ", ".join(row["fires"]),
                    ", ".join(row["disagrees"]["calibration"]),
                    ", ".join(row["disagrees"]["holdout"]),
                    ", ".join(row["neutral"]),
                )
                for row in covered
            ),
        ),
        "",
    ]
    lines.extend(
        f"- Not covered: {row['rule']} ({row['covered']})"
        for row in rules
        if row["covered"] not in {"yes", OPTIONAL_RULE}
    )
    lines.extend(
        f"- Optional, not calibrated: {row['rule']} (listed for the user to decide, never in the command)"
        for row in rules
        if row["covered"] == OPTIONAL_RULE
    )
    if lost := [row for row in covered if sum(map(len, row["disagrees"].values())) >= DISAGREEMENT_LIMIT]:
        lines += ["", f"Rules that disagree on {DISAGREEMENT_LIMIT} or more schemas:", ""]
        lines.extend(
            f"- {row['rule']}: {', '.join(sorted(itertools.chain(*row['disagrees'].values())))}" for row in lost
        )
    return lines


def _proposals_section(proposals: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> list[str]:
    lines = ["", "## Threshold proposals", ""]
    if proposals:
        lines += [
            (
                "One leaf per rule: the observed signal value that removes most calibration disagreements without "
                "adding holdout ones. See rules.diff."
            ),
            "",
            *_table(
                ("Rule", "Leaf", "Proposed", "Calibration disagreements", "Holdout disagreements"),
                (
                    (
                        item["rule"],
                        f"`{item['leaf']}`",
                        item["proposed"],
                        " -> ".join(map(str, item["calibration"])),
                        " -> ".join(map(str, item["holdout"])),
                    )
                    for item in proposals
                ),
            ),
        ]
    else:
        lines.append("None.")
    return [
        *lines,
        "",
        "## Preset proposals",
        "",
        (
            f"A value is proposed for a new dated preset when it wins on at least {PRESET_WIN_RATIO:.0%} of the "
            f"calibration and of the holdout schemas, loses none by more than {PRESET_MAX_LOSS} problem names and "
            "generates and imports on every schema of the run as the latest preset with only that value changed. "
            "Wins compare the best grid "
            "configuration with the value against the best one without it by the trial's ranking; losses show the "
            "extra problem names (+0: lost on another ranking metric)."
        ),
        "",
        *_table(
            ("Value", "Calibration wins", "Holdout wins", "Losses", "Status"),
            (
                (
                    f"`{_values_text(item['values'])}`",
                    "/".join(map(str, item["wins"]["calibration"])),
                    "/".join(map(str, item["wins"]["holdout"])),
                    ", ".join(f"{schema_id} +{loss}" for schema_id, loss in sorted(item["losses"].items())),
                    item["status"],
                )
                for item in candidates
            ),
        ),
    ]


def _stale(upstream: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in upstream if row.get("head") != row["pinned"]]


def _rotation_section(upstream: list[dict[str, Any]]) -> list[str]:
    lines = [
        "",
        "## Proposed rotation",
        "",
        (
            "Schemas whose source repository's HEAD (git ls-remote) is not the newest pinned ref. Rotating pins the "
            "head with its date and sha256 as the newest version, and the version pinned before becomes the "
            "baseline; nothing was downloaded."
        ),
        "",
    ]
    if not (stale := _stale(upstream)):
        return [*lines, "None: every newest pinned ref is its upstream head."]
    rows = (
        (
            row["schema"],
            f"{row['pinned'][:SHORT_REF]} ({row['date']})",
            row["head"][:SHORT_REF] if "head" in row else f"not checked: {row['error']}",
        )
        for row in stale
    )
    return [*lines, *_table(("Schema", "Newest pinned", "Upstream head"), rows)]


def _report(scores: Mapping[str, Any], drift: list[str]) -> str:
    summaries = scores["schemas"]
    lines = [
        *_schemas_section(scores["calibration"], summaries),
        *_rules_section(scores["rules"]),
        *_proposals_section(scores["threshold_proposals"], scores["preset_candidates"]),
    ]
    if (imports := scores["import_check"]) is not None:
        rows = (
            (item["schema"], ", ".join(item["labels"]), _code(item["arguments"]), item["result"]) for item in imports
        )
        lines += ["", "## Import check", "", *_table(("Schema", "Configurations", "Arguments", "Result"), rows)]
    if (upstream := scores["upstream"]) is not None:
        lines += _rotation_section(upstream)
    if failures := [summary for summary in summaries if "error" in summary]:
        lines += ["", "## Failures", ""]
        lines.extend(f"- {summary['schema']}: {summary['error']}" for summary in failures)
    lines += ["", "## Changes since the last calibration", ""]
    lines.extend(f"- {line}" for line in drift)
    return "\n".join(lines) + "\n"


def _rules_diff(rules_document: Any, proposals: list[dict[str, Any]]) -> str:
    """Return the threshold proposals as a unified diff of the rules source, serialized with two-space indentation.

    The rules are read from the installed package, which a checkout installs from this source file.
    """
    edited = copy.deepcopy(rules_document)
    for proposal in proposals:
        rule = edited["rules"][proposal["rule_index"]]
        rule["when"] = _with_value(rule["when"], tuple(proposal["path"]), proposal["proposed"])
    before, after = (json.dumps(document, indent=2, ensure_ascii=False) + "\n" for document in (rules_document, edited))
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True), f"a/{RULES_SOURCE}", f"b/{RULES_SOURCE}"
        )
    )


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-manifest", action="store_true", help="validate the manifest offline and summarize it")
    mode.add_argument("--fetch-only", action="store_true", help="download, verify and prepare the specs only")
    mode.add_argument(
        "--out", type=Path, help="empty directory for report.md, scores.json, calibration.json, rules.diff"
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--cache-dir", type=Path, default=_default_cache_dir())
    parser.add_argument("--only", nargs="+", metavar="ID", help="calibrate only these schema ids")
    parser.add_argument("--jobs", type=int, default=1, help="processes (each holds one generation in memory)")
    parser.add_argument("--import-check", action="store_true", help="import generated code in an isolated interpreter")
    parser.add_argument(
        "--check-upstream", action="store_true", help="with --out, propose rotating pins whose upstream head moved"
    )
    parser.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION, help="last accepted calibration file")
    parser.add_argument("--output-model-type", default="pydantic_v2.BaseModel")
    parser.add_argument("--target-python-version", default="3.14")
    return parser.parse_args(argv)


def _select(manifest: Manifest, only: Sequence[str] | None) -> list[Schema]:
    if unknown := sorted(set(only or ()) - {schema.schema_id for schema in manifest.schemas}):
        msg = f"unknown schema ids: {', '.join(unknown)}"
        raise CalibrationError(msg)
    return [schema for schema in manifest.schemas if not only or schema.schema_id in only]


def _settle_candidates(
    candidates: list[dict[str, Any]], results: Mapping[tuple[str, tuple[str, ...]], str] | None
) -> None:
    """Set each candidate's status; a proposal needs the preset with its value imported on every schema of the run."""
    for candidate in candidates:
        import_arguments = candidate.pop("import_arguments")
        unchecked = sorted(schema_id for schema_id, arguments in import_arguments.items() if arguments is None)
        failing = sorted(
            schema_id
            for schema_id, arguments in import_arguments.items()
            if arguments is not None and (results or {}).get((schema_id, arguments)) != "ok"
        )
        reasons = [
            *([f"imports fail on {', '.join(failing)}"] if failing else []),
            *([f"not import-checked on {', '.join(unchecked)}"] if unchecked else []),
        ]
        if not candidate["eligible"]:
            candidate["status"] = "not proposed"
        elif results is None:
            candidate["status"] = "needs --import-check"
        else:
            candidate["status"] = f"not proposed: {'; '.join(reasons)}" if reasons else "proposed"


def _out_settings(ns: argparse.Namespace) -> Settings:
    """Check what a calibration run needs before any work and return its settings.

    --out must be empty, the settings must have a preset, and --import-check imports the generated code with this
    interpreter, so it must be at least as new as the target Python version.
    """
    if ns.out.exists() and (not ns.out.is_dir() or any(ns.out.iterdir())):
        msg = "--out must be a new or empty directory"
        raise CalibrationError(msg)
    settings = _settings(ns.output_model_type, ns.target_python_version)
    target = tuple(int(part) for part in settings.target_python_version.split("."))
    if ns.import_check and sys.version_info[:2] < target:
        msg = (
            f"--import-check imports code generated for Python {settings.target_python_version} with Python "
            f"{sys.version_info[0]}.{sys.version_info[1]}; run the script with Python "
            f"{settings.target_python_version} or newer, or lower --target-python-version"
        )
        raise CalibrationError(msg)
    return settings


def _calibrate(ns: argparse.Namespace, settings: Settings, manifest: Manifest, subjects: list[Subject]) -> int:
    points = _grid_points(manifest.grid)
    names = {name for values in manifest.grid.values() for value in values for name in value}
    effective = {
        kind: _effective_values(settings, kind, names) for kind in {subject.schema.kind for subject in subjects}
    }
    plans = {kind: [_plan(_merged(point), values) for point in points] for kind, values in effective.items()}
    tasks: list[_Task] = []
    for subject in subjects:
        invocation = _invocation(subject, settings)
        tasks.append((tuple(invocation), False))
        tasks.extend(
            ((*invocation, "--preset", settings.preset, *arguments), True)
            for _, arguments in plans[subject.schema.kind]
        )
    print(f"run {len(subjects)} schemas x (recommendation + {len(points)} grid configurations)", flush=True)
    results = iter(_run_tasks(tasks, ns.jobs))
    runs = [
        Run(
            subject,
            next(results),
            [_scored(settings.preset, plan, next(results)) for plan in plans[subject.schema.kind]],
        )
        for subject in subjects
    ]
    rules_document = json.loads(_rules_path().read_text(encoding="utf-8"))
    summaries = [_subject_summary(run, points) for run in runs]
    rules, proposals = _rule_rows(rules_document["rules"], runs, manifest.grid, settings)
    candidates = _preset_candidates(runs, points, manifest.grid, settings)
    imports: list[dict[str, Any]] | None = None
    if ns.import_check:
        by_id = {run.subject.schema.schema_id: run.subject for run in runs}
        targets = _import_targets(runs, {summary["schema"]: summary for summary in summaries}, candidates, settings)
        imports = [
            {
                "schema": schema_id,
                "labels": labels,
                "arguments": list(arguments),
                "result": _import_check(by_id[schema_id], list(arguments)),
            }
            for (schema_id, arguments), labels in targets.items()
        ]
    _settle_candidates(
        candidates,
        None if imports is None else {(item["schema"], tuple(item["arguments"])): item["result"] for item in imports},
    )
    upstream = _upstream(subject.schema for subject in subjects) if ns.check_upstream else None
    previous = json.loads(ns.calibration.read_text(encoding="utf-8")) if ns.calibration.is_file() else None
    unselected = {schema.schema_id for schema in manifest.schemas} - {subject.schema.schema_id for subject in subjects}
    kept = {
        schema_id: entry
        for schema_id, entry in ((previous or {}).get("schemas") or {}).items()
        if schema_id in unselected
    }
    calibration = _calibration(summaries, rules_document, settings, manifest.grid, kept)
    scores = {
        "calibration": calibration,
        "schemas": summaries,
        "rules": rules,
        "threshold_proposals": [
            {key: value for key, value in proposal.items() if key not in {"path", "rule_index"}}
            for proposal in proposals
        ],
        "preset_candidates": candidates,
        "import_check": imports,
        "upstream": upstream,
        "grid": [_merged(point) for point in points],
        "results": {run.subject.schema.schema_id: {"recommendation": run.payload, "grid": run.grid} for run in runs},
    }
    ns.out.mkdir(parents=True, exist_ok=True)
    (ns.out / "report.md").write_text(_report(scores, _drift(previous, calibration)), encoding="utf-8")
    _write_json(ns.out / "scores.json", scores)
    _write_json(ns.out / "calibration.json", calibration)
    written = ["report.md", "scores.json", "calibration.json"]
    if proposals:
        (ns.out / "rules.diff").write_text(_rules_diff(rules_document, proposals), encoding="utf-8")
        written.append("rules.diff")
    failures = [summary["schema"] for summary in summaries if "error" in summary]
    regrets = ", ".join(f"{summary['schema']} {summary.get('regret')}" for summary in summaries)
    proposed = sum(candidate["status"] == "proposed" for candidate in candidates)
    line = f"regret: {regrets}; threshold proposals: {len(proposals)}; preset proposals: {proposed}"
    if upstream is not None:
        line += f"; upstream heads to rotate: {len(_stale(upstream))} of {len(upstream)}"
    print(line + (f"; failed: {', '.join(failures)}" if failures else ""))
    print(f"wrote {', '.join(written)}")
    return 1 if failures else 0


def _run(ns: argparse.Namespace) -> int:
    if ns.check_upstream and ns.out is None:
        msg = "--check-upstream reports in --out"
        raise CalibrationError(msg)
    settings = None if ns.out is None else _out_settings(ns)
    manifest = _load_manifest(ns.manifest)
    if ns.check_manifest:
        print(_manifest_summary(manifest))
        return 0
    schemas = _select(manifest, ns.only)
    subjects = _fetch_subjects(schemas, ns.cache_dir)
    if settings is None:
        print(f"verified {sum(min(len(schema.versions), 2) for schema in schemas)} files")
        return 0
    return _calibrate(ns, settings, manifest, subjects)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the calibration."""
    ns = _parse_args(argv)
    try:
        return _run(ns)
    except CalibrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
