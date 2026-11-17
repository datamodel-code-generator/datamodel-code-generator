"""The change records of `docs/data/migration`: their schema, the rule of each category, and their `covers` block.

A record is one `[[change]]` table: an upgrade-guide entry, or a record of a stored-output change that needs no entry.
Its `category` says why the stored output changed and decides what the record must hold (`RULES`): the guide `kind`
and `action` it may have, the facts it names (`refs`, `decision`, `surface`, `affects.experimental`) and the kinds of
stored output its `covers` may list. Entries of released changes may lack a category; they are guide entries.

`scripts/build_migration_docs.py` reads every entry through `parse_entry`, and `scripts/declared_changes.py` reads the
records of any revision the same way. Standard library only, so the static check needs no environment.
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import tomllib

from scripts._behaviour_report import version_key

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping


class Category(StrEnum):
    """Why a stored output changed."""

    NEW_SURFACE = "new-surface"
    EXPERIMENTAL = "experimental"
    CRASH_FIX = "crash-fix"
    OUTPUT_TEXT = "output-text"
    DEFAULT_FIX = "default-fix"
    DECIDED_CHANGE = "decided-change"
    NOTICE = "notice"
    API = "api"
    DOCS_TEXT = "docs-text"
    INTERNAL = "internal"
    HARNESS = "harness"


class Kind(StrEnum):
    """What a guide entry changes; the release notes group breaking changes by it."""

    DEFAULT_OUTPUT = "default-output"
    OUTPUT_TEXT = "output-text"
    EXPERIMENTAL = "experimental"
    TEMPLATE = "template"
    REMOVAL = "removal"
    CLI = "cli"
    PYTHON_API = "python-api"
    WARNING = "warning"
    RUNTIME = "runtime"
    ERROR = "error"
    PRESET = "preset"


class Action(StrEnum):
    """What a user of an earlier release does about a guide entry."""

    REQUIRED = "required"
    KEEP = "keep"
    REVIEW = "review"
    WARN = "warn"
    NONE = "none"


class Source(StrEnum):
    """Where a guide entry's claim comes from."""

    RUN = "run"
    RELEASE_NOTE = "release-note"
    REGISTRY = "registry"
    SURFACE = "surface"


class Owner(StrEnum):
    """The kind of a stored output, as far as a category may cover it."""

    MODELS = "models"
    LISTING = "listing"
    NOTICE = "notice"
    API = "api"
    INTERNAL = "internal"


AFFECTS_KEYS = ("output_model_types", "input_file_types", "options", "python_api", "templates", "experimental")
GUIDE_TEXT_KEYS = ("title", "summary", "before", "after", "migrate", "reverts")
RECORD_TEXT_KEYS = ("extends",)
GUIDE_KEYS = frozenset({
    "kind",
    "action",
    "affects",
    "before",
    "after",
    "keep",
    "migrate",
    "supersedes",
    "reverts",
    "source",
    "verify",
})
"""The keys only a guide entry has; a record of a category without a guide entry holds none of them."""
RECORD_KEYS = frozenset({"id", "category", "title", "summary", "refs", "covers", "decision", "surface", "extends"})
LIST_KEYS = ("refs", "supersedes", "covers", "decision", "surface")
REF_KEYS = ("refs", "decision")
REQUIRED_KEYS = ("kind", "action", "title", "summary", "source")
KEEP_KEYS = frozenset({"flags", "note"})
VERIFY_KEYS = frozenset({"schema", "args"})
REF_PATTERN = re.compile(r"#[1-9]\d*")
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9.:_-]*")
DERIVED_PREFIXES = ("deprecation:", "removal:", "experimental:", "preset:", "option:")
PRESET_SURFACE = "preset:"
CHANGE_HEADER = re.compile(r"\[\[\s*change\s*\]\]\s*(#.*)?")
CLIENT_SERVER_FEATURES = frozenset({"cli-option.generate-client", "cli-option.generate-server"})
"""Experimental features whose output changes need no owner decision (owner, 2026-10-19)."""
COVERS_INDENT = "    "


@dataclass(frozen=True)
class Keep:
    """How to keep the previous output: option flags, or a note outside the command line."""

    flags: tuple[str, ...] | None = None
    note: str | None = None


@dataclass(frozen=True)
class Verify:
    """The minimal fixture of an entry whose claims are proven by runs."""

    schema: str | None = None
    args: tuple[Any, ...] | None = None


@dataclass(frozen=True)
class Record:
    """One `[[change]]` table, typed; `keys` are the keys the table writes, so `guide_fields` returns only those."""

    id: str
    keys: frozenset[str]
    category: Category | None = None
    title: str | None = None
    summary: str | None = None
    refs: tuple[str, ...] = ()
    covers: tuple[str, ...] = ()
    decision: tuple[str, ...] = ()
    surface: tuple[str, ...] = ()
    extends: str | None = None
    kind: Kind | None = None
    action: Action | None = None
    source: Source | None = None
    affects: tuple[tuple[str, tuple[str, ...]], ...] = ()
    before: str | None = None
    after: str | None = None
    keep: Keep | None = None
    migrate: str | None = None
    supersedes: tuple[str, ...] = ()
    reverts: str | None = None
    verify: Verify | None = None

    @property
    def derived(self) -> bool:
        """Return whether the record extends an entry a registry or the surface comparison gives, by its id."""
        return self.id.startswith(DERIVED_PREFIXES)

    @property
    def guide(self) -> bool:
        """Return whether the record is an upgrade-guide entry: no category, or a category with guide entries."""
        return self.extends is None and (self.category is None or RULES[self.category].guide is not None)

    @property
    def features(self) -> tuple[str, ...]:
        """Return the experimental features the record names in `affects.experimental`."""
        return dict(self.affects).get("experimental", ())

    def guide_fields(self) -> dict[str, Any]:
        """Return the guide keys and values the table writes, as the TOML reads them (lists and tables)."""
        values: dict[str, Any] = {
            "title": self.title,
            "summary": self.summary,
            "refs": list(self.refs),
            "kind": self.kind,
            "action": self.action,
            "source": self.source,
            "affects": {key: list(values) for key, values in self.affects},
            "before": self.before,
            "after": self.after,
            "migrate": self.migrate,
            "supersedes": list(self.supersedes),
            "reverts": self.reverts,
        }
        if self.keep is not None:
            values["keep"] = {
                name: list(value) if isinstance(value, tuple) else value
                for name, value in (("flags", self.keep.flags), ("note", self.keep.note))
                if value is not None
            }
        if self.verify is not None:
            values["verify"] = {
                name: list(value) if isinstance(value, tuple) else value
                for name, value in (("schema", self.verify.schema), ("args", self.verify.args))
                if value is not None
            }
        plain = {key: str(value) if isinstance(value, StrEnum) else value for key, value in values.items()}
        return {key: value for key, value in plain.items() if key in self.keys}


@dataclass(frozen=True)
class Rule:
    """What a record of one category holds.

    `requires`: fields that must not be empty (`affects.experimental` is the experimental features); `guide`: the
    (kind, action) pairs its guide entry may have, or None for a record without a guide entry, which then holds no
    guide key; `owners`: the kinds of stored output its `covers` may list, None for any; `surface`: whether it may name
    `surface` items.
    """

    requires: frozenset[str]
    guide: frozenset[tuple[Kind, Action]] | None
    owners: frozenset[Owner] | None
    surface: bool = False


def _pairs(kinds: Collection[Kind], actions: Collection[Action]) -> frozenset[tuple[Kind, Action]]:
    return frozenset((kind, action) for kind in kinds for action in actions)


RULES: Mapping[Category, Rule] = MappingProxyType({
    Category.NEW_SURFACE: Rule(
        frozenset({"title", "surface"}), None, frozenset({Owner.LISTING, Owner.MODELS}), surface=True
    ),
    Category.EXPERIMENTAL: Rule(
        frozenset({"refs", "decision", "affects.experimental"}),
        _pairs([Kind.EXPERIMENTAL], [Action.REQUIRED, Action.REVIEW, Action.NONE]),
        None,
    ),
    Category.CRASH_FIX: Rule(
        frozenset({"refs"}),
        _pairs([Kind.DEFAULT_OUTPUT, Kind.EXPERIMENTAL], [Action.NONE]),
        frozenset({Owner.MODELS, Owner.NOTICE}),
    ),
    Category.OUTPUT_TEXT: Rule(
        frozenset({"refs"}), _pairs([Kind.OUTPUT_TEXT], [Action.NONE]), frozenset({Owner.MODELS})
    ),
    Category.DEFAULT_FIX: Rule(
        frozenset({"refs", "decision"}),
        _pairs([Kind.DEFAULT_OUTPUT], [Action.REVIEW, Action.KEEP]),
        frozenset({Owner.MODELS}),
    ),
    Category.DECIDED_CHANGE: Rule(
        frozenset({"refs", "decision", "keep"}),
        _pairs([Kind.DEFAULT_OUTPUT, Kind.TEMPLATE], [Action.KEEP, Action.REVIEW, Action.REQUIRED]),
        None,
    ),
    Category.NOTICE: Rule(
        frozenset({"refs"}),
        _pairs([Kind.WARNING, Kind.ERROR], [Action.WARN, Action.NONE, Action.REQUIRED]),
        frozenset({Owner.NOTICE}),
    ),
    Category.API: Rule(
        frozenset({"refs", "decision"}),
        _pairs([Kind.REMOVAL, Kind.CLI, Kind.PYTHON_API, Kind.RUNTIME], [Action.REQUIRED, Action.REVIEW]),
        frozenset({Owner.API, Owner.LISTING}),
    ),
    Category.DOCS_TEXT: Rule(frozenset({"title"}), None, frozenset({Owner.LISTING})),
    Category.INTERNAL: Rule(frozenset({"title"}), None, frozenset({Owner.INTERNAL})),
    Category.HARNESS: Rule(frozenset({"title", "summary"}), None, None),
})
"""The rule of each category.

`new-surface` covers listings and the model output of runs that set a surface item the latest release lacks, so only
model outputs the latest release does not have; a dated preset only bundles released options, so a `preset:` item
covers listings only and the model output a new preset changes carries the category of the released option's change.
`notice` covers warnings, standard error and error messages, `api` the public API output and listings; a model change
beside either needs its own record. An `experimental` change of a feature other than the generated clients and
servers, and an `api` change the deprecation registry does not schedule, need the owner's `decision`.

`crash-fix`: the previous package failed generation, import or decoding of the output; for an added stored output, the
previous package fails to generate it or writes a unit the behaviour classifier (`scripts/_behaviour_classify.py`)
calls unusable. `output-text`: the text changes and the models behave the same, that is the modules' syntax trees are
equal apart from docstrings, comments and the order of imports, or else their signatures are equal and no changed node
is in a function body, a decorator or a configuration key the signature does not list.
"""


OWNERS: tuple[tuple[str, str, Owner], ...] = (
    ("*", "*.warnings.txt", Owner.NOTICE),
    ("*", "*.stderr.txt", Owner.NOTICE),
    ("*", "*.notices.txt", Owner.NOTICE),
    ("tests/main/test_error_messages.py::*", "*", Owner.NOTICE),
    ("tests/main/test_migration_warnings.py::*", "*", Owner.NOTICE),
    ("tests/main/test_main_general.py::test_show_help*", "*", Owner.LISTING),
    ("tests/main/test_main_general.py::test_list_experimental*", "*", Owner.LISTING),
    ("tests/main/test_main_general.py::test_invalid_cli_option_suggestions*", "*", Owner.LISTING),
    ("tests/main/test_main_recommend_options.py::*", "*", Owner.LISTING),
    ("tests/main/test_option_stage_order.py::test_cli_option_relation_rows*", "*", Owner.LISTING),
    ("tests/main/test_agent_skill.py::*", "*", Owner.LISTING),
    ("tests/skills/*", "*", Owner.LISTING),
    ("tests/test_build_preset_docs_script.py::*", "*", Owner.LISTING),
    ("tests/test_build_schema_docs_script.py::*", "*", Owner.LISTING),
    ("tests/test_build_docs_examples_script.py::*", "*", Owner.LISTING),
    ("tests/test_docs_formatting.py::*", "*", Owner.LISTING),
    ("tests/test_recommender_calibrate_script.py::*", "*", Owner.LISTING),
    ("tests/test_prompt.py::*", "*", Owner.LISTING),
    ("tests/test_main_kr.py::*", "*", Owner.LISTING),
    ("tests/test_package_metadata.py::*", "*", Owner.LISTING),
    ("tests/test_input_model.py::test_input_model_config_class*", "*", Owner.LISTING),
    ("tests/parser/test_builtin_formatter_contract.py::*", "*", Owner.LISTING),
    ("tests/main/test_public_api_signature_baseline.py::*", "*", Owner.API),
    ("tests/parser/test_pipeline.py::*", "*", Owner.INTERNAL),
    ("tests/parser/test_generation_store_usage.py::*", "*", Owner.INTERNAL),
    ("tests/parser/test_fact_finality.py::*", "*", Owner.INTERNAL),
    ("tests/main/test_generation_observation.py::*", "*", Owner.INTERNAL),
    ("tests/checks/*", "*", Owner.INTERNAL),
    ("tests/test_architecture_boundaries.py::*", "*", Owner.INTERNAL),
    ("tests/test_source_rules.py::*", "*", Owner.INTERNAL),
    ("*", "payloads/*", Owner.MODELS),
    ("*", "*native*error*", Owner.MODELS),
    ("*", "*generated*error*", Owner.MODELS),
    ("*", "*warn*.txt", Owner.NOTICE),
    ("*", "*refus*.txt", Owner.NOTICE),
    ("*", "*error*.txt", Owner.NOTICE),
)
"""The stored outputs that are not model output, as (nodeid glob, label glob, kind) rows; the first match decides.

Warnings, standard error and error messages are notices; help, listings, documentation, prompts and recommender
output are listings; traces and reports of internals, and the checks that read the repository's source, are internal.
Every other stored output is model output. This table and `RULES` judge the records, so a change to either is a change
of the check.

Notices are told from model output by their labels for now: a text output named for a warning, a refusal or an error
is a notice, except payload outputs and the runtime errors of generated models against the native types, which are
behaviour. The exact kind, the one the ledger captured (warnings, standard error or error text) written into the
`.refs` line, comes with the replay.
"""


def owner_of(nodeid: str, label: str) -> Owner:
    """Return the kind of the stored output a test item compares at a label."""
    return next(
        (
            owner
            for nodes, labels, owner in OWNERS
            if fnmatch.fnmatchcase(nodeid, nodes) and fnmatch.fnmatchcase(label, labels)
        ),
        Owner.MODELS,
    )


@dataclass(frozen=True)
class Facts:
    """What the source tree and the latest release say about the names records give, for the rules that read them.

    `features`: the experimental features a release shipped; `unreleased_features`: those not released yet;
    `scheduled`: the targets whose removal the deprecation registry schedules; `new_surface`: the surface items
    (`--name`, `--name value`, public names, `preset:<date>`) this tree has and the latest release lacks; `surface`:
    every surface item this tree has.
    """

    features: frozenset[str] = frozenset()
    unreleased_features: frozenset[str] = frozenset()
    scheduled: frozenset[str] = frozenset()
    new_surface: frozenset[str] = frozenset()
    surface: frozenset[str] = frozenset()


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _enum_problem(where: str, key: str, value: object, enum: type[StrEnum]) -> str | None:
    if isinstance(value, str) and value in enum.__members__.values():
        return None
    return f"{where}: {key} {value!r} is not one of {', '.join(enum)}"


def _shape_problems(where: str, entry: Mapping[str, Any]) -> list[str]:
    """Return why a table is not a record, in the order the keys are checked."""
    problems = [f"{where}: unknown key {key}" for key in sorted(set(entry) - GUIDE_KEYS - RECORD_KEYS)]
    problems.extend(
        f"{where}: {key} is not a string"
        for key in (*GUIDE_TEXT_KEYS, *RECORD_TEXT_KEYS)
        if key in entry and not isinstance(entry[key], str)
    )
    for key, enum in (("kind", Kind), ("action", Action), ("source", Source), ("category", Category)):
        if key in entry and (problem := _enum_problem(where, key, entry[key], enum)):
            problems.append(problem)
    affects = entry.get("affects", {})
    if not isinstance(affects, dict):
        problems.append(f"{where}: affects is not a table")
    else:
        problems.extend(f"{where}: unknown affects key {key}" for key in sorted(set(affects) - set(AFFECTS_KEYS)))
        problems.extend(
            f"{where}: affects.{key} is not a list of strings"
            for key, values in affects.items()
            if not _strings(values)
        )
    for key, allowed in (("keep", KEEP_KEYS), ("verify", VERIFY_KEYS)):
        if not isinstance(table := entry.get(key, {}), dict):
            problems.append(f"{where}: {key} is not a table")
            continue
        problems.extend(f"{where}: unknown {key} key {name}" for name in sorted(set(table) - allowed))
    keep, verify = entry_table(entry, "keep"), entry_table(entry, "verify")
    if "flags" in keep and not _strings(keep["flags"]):
        problems.append(f"{where}: keep.flags is not a list of strings")
    if "note" in keep and not isinstance(keep["note"], str):
        problems.append(f"{where}: keep.note is not a string")
    if "schema" in verify and not isinstance(verify["schema"], str):
        problems.append(f"{where}: verify.schema is not a string")
    if "args" in verify and not isinstance(verify["args"], list):
        problems.append(f"{where}: verify.args is not a list")
    problems.extend(f"{where}: {key} is not a list of strings" for key in LIST_KEYS if not _strings(entry.get(key, [])))
    problems.extend(
        f"{where}: {'ref' if key == 'refs' else key} {ref!r} is not #<number>"
        for key in REF_KEYS
        if isinstance(values := entry.get(key, []), list)
        for ref in values
        if not REF_PATTERN.fullmatch(str(ref))
    )
    return problems


def entry_table(entry: Mapping[str, Any], key: str) -> dict[str, Any]:
    """Return a table of an entry, or an empty one where the entry holds something else (a reported problem)."""
    return value if isinstance(value := entry.get(key), dict) else {}


def parse_entry(entry: Mapping[str, Any], where: str) -> tuple[Record | None, list[str]]:
    """Return the record a `[[change]]` table holds, or None with every reason it holds none.

    `where` names the file in the messages. The id must be a lower-case slug or a derived entry's id; which derived
    ids exist is the build's to check.
    """
    entry_id = entry.get("id")
    if not isinstance(entry_id, str) or not (entry_id.startswith(DERIVED_PREFIXES) or ID_PATTERN.fullmatch(entry_id)):
        return None, [f"{where}: id {entry_id!r} is not a lower-case slug"]
    if problems := _shape_problems(f"entry {entry_id}", entry):
        return None, problems
    keep, verify = entry_table(entry, "keep"), entry_table(entry, "verify")
    record = Record(
        id=entry_id,
        keys=frozenset(entry) - {"id"},
        category=Category(entry["category"]) if "category" in entry else None,
        title=entry.get("title"),
        summary=entry.get("summary"),
        refs=tuple(entry.get("refs", ())),
        covers=tuple(entry.get("covers", ())),
        decision=tuple(entry.get("decision", ())),
        surface=tuple(entry.get("surface", ())),
        extends=entry.get("extends"),
        kind=Kind(entry["kind"]) if "kind" in entry else None,
        action=Action(entry["action"]) if "action" in entry else None,
        source=Source(entry["source"]) if "source" in entry else None,
        affects=tuple((key, tuple(values)) for key, values in entry.get("affects", {}).items()),
        before=entry.get("before"),
        after=entry.get("after"),
        keep=Keep(tuple(keep["flags"]) if "flags" in keep else None, keep.get("note")) if "keep" in entry else None,
        migrate=entry.get("migrate"),
        supersedes=tuple(entry.get("supersedes", ())),
        reverts=entry.get("reverts"),
        verify=(
            Verify(verify.get("schema"), tuple(verify["args"]) if "args" in verify else None)
            if "verify" in entry
            else None
        ),
    )
    return record, []


def _filled(record: Record, name: str) -> bool:
    if name == "affects.experimental":
        return bool(record.features)
    if name == "keep":
        return record.keep is not None and bool(record.keep.flags or record.keep.note)
    return bool(getattr(record, name))


def _decision_needed(record: Record, facts: Facts | None) -> bool:
    """Return whether a record needs the owner's decision; without facts, only the build knows an `api` record's."""
    match record.category:
        case Category.EXPERIMENTAL:
            return not set(record.features) <= CLIENT_SERVER_FEATURES
        case Category.API:
            named = {value for _, values in record.affects for value in values}
            return facts is not None and not (named and named <= facts.scheduled)
    return True


def rule_problems(record: Record, *, fragment: bool, facts: Facts | None = None) -> list[str]:
    """Return how a record breaks the rule of its category; `fragment` for an unreleased fragment.

    Every fragment has a category except one that extends a derived entry (it only adds text to the registry's entry)
    or another record (`extends`: the same change, more covers). Released entries without a category are guide
    entries that need `refs`. The facts, when given, check the features, surface items and scheduled removals the
    record names.
    """
    where = f"entry {record.id}"
    if record.extends is not None:
        if extra := sorted(record.keys - {"extends", "covers", "refs"}):
            return [f"{where}: an extending record holds only extends, covers and refs, not {', '.join(extra)}"]
        return []
    if record.derived:
        return [f"{where}: an entry that extends a derived entry has no category"] if record.category else []
    if record.category is None:
        if fragment:
            return [f"{where}: a fragment needs a category ({', '.join(Category)})"]
        return [] if record.refs else [f"{where} has no refs"]
    rule = RULES[record.category]
    problems = [f"{where} has no {name}" for name in sorted(rule.requires - {"decision"}) if not _filled(record, name)]
    if "decision" in rule.requires and not record.decision and _decision_needed(record, facts):
        problems.append(f"{where} has no decision")
    if rule.guide is None:
        if extra := sorted(record.keys & GUIDE_KEYS):
            problems.append(f"{where}: category {record.category} has no guide entry, so no {', '.join(extra)}")
    elif record.kind and record.action and (record.kind, record.action) not in rule.guide:
        problems.append(
            f"{where}: category {record.category} has no kind {record.kind} with action {record.action}, only "
            + ", ".join(f"{kind}/{action}" for kind, action in sorted(rule.guide))
        )
    if record.surface and not rule.surface:
        problems.append(f"{where}: only category {Category.NEW_SURFACE} names surface items")
    if facts is not None:
        problems += _fact_problems(record, facts)
    return problems


def _fact_problems(record: Record, facts: Facts) -> list[str]:
    where = f"entry {record.id}"
    problems = []
    for feature in record.features if record.category == Category.EXPERIMENTAL else ():
        if feature in facts.unreleased_features:
            problems.append(f"{where}: {feature} is not released yet; its output is new surface")
        elif feature not in facts.features:
            problems.append(f"{where}: {feature} is not an experimental feature")
    for item in record.surface:
        if item not in facts.surface:
            problems.append(f"{where}: surface item {item} is not in this source tree")
        elif item not in facts.new_surface:
            problems.append(f"{where}: surface item {item} is not new: the latest release has it")
    return problems


def guide_problems(entry: Mapping[str, Any]) -> list[str]:
    """Return what a guide entry lacks, once a hand entry has extended the derived entry of its id."""
    where = f"entry {entry['id']}"
    problems = [f"{where}: missing {key}" for key in REQUIRED_KEYS if not entry.get(key)]
    keep = entry_table(entry, "keep")
    if entry.get("action") == Action.KEEP and not (keep.get("flags") or keep.get("note")):
        problems.append(f"{where}: action keep needs keep.flags or keep.note")
    if keep.get("flags") and entry.get("action") != Action.KEEP:
        problems.append(f"{where}: keep.flags belong to an entry with action keep")
    if entry.get("action") == Action.REQUIRED and not entry.get("migrate"):
        problems.append(f"{where}: action required needs migrate")
    verify = entry_table(entry, "verify")
    if entry.get("source") == Source.RUN and not (
        isinstance(verify.get("schema"), str) and isinstance(verify.get("args"), list)
    ):
        problems.append(f"{where}: source run needs verify.schema and verify.args")
    return problems


def stamp_pending(latest_tag: str, released: Collection[str]) -> str | None:
    """Return why the release table lags the latest tag, or None: a tag newer than every row needs `--stamp`."""
    if released and version_key(latest_tag) <= max(map(version_key, released)):
        return None
    return (
        f"stamp pending: {latest_tag} is tagged but docs/data/migration/releases.toml does not list it; run "
        f"'python scripts/build_migration_docs.py --stamp {latest_tag} --tag-tree <checkout of {latest_tag}>'"
    )


COVERS_BLOCK = re.compile(r'^covers = \[\n(?:    "(?:[^"\\\n]|\\.)*",\n)*\]\n', re.MULTILINE)


def covers_block(labels: Collection[str]) -> str:
    """Return the `covers` key as `declared_changes.py declare` writes it: one label a line, sorted, unique."""
    rows = "".join(f"{COVERS_INDENT}{json.dumps(label, ensure_ascii=False)},\n" for label in sorted(set(labels)))
    return f"covers = [\n{rows}]\n"


def covers_written(text: str, record: Record) -> bool:
    """Return whether a fragment writes its `covers` as `covers_block` does, or has none."""
    blocks = COVERS_BLOCK.findall(text)
    return blocks == [covers_block(record.covers)] if "covers" in record.keys else not blocks


def with_covers(text: str, labels: Collection[str]) -> str:
    """Return a fragment's text with its `covers` block replaced by one of the labels (dropped for no labels).

    The block goes after the keys of the `[[change]]` table, before its first sub-table, or right after the table's
    header where a line of a multi-line string looks like a header. The text must write its `covers`, if any, as
    `covers_block` does; raises ValueError otherwise.
    """
    wanted = tomllib.loads(text)
    stripped = COVERS_BLOCK.sub("", text, count=1)
    if "covers" in tomllib.loads(stripped)["change"][0]:
        msg = "its covers are not written as declared_changes.py declare writes them"
        raise ValueError(msg)
    wanted["change"][0].pop("covers", None)
    if labels:
        wanted["change"][0]["covers"] = sorted(set(labels))
    lines = stripped.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    if not labels:
        return "".join(lines)
    header = next(index for index, line in enumerate(lines) if CHANGE_HEADER.fullmatch(line.strip())) + 1
    after_keys = next((index for index in range(header, len(lines)) if lines[index].startswith("[")), len(lines))
    while after_keys > header and not lines[after_keys - 1].strip():
        after_keys -= 1
    block = [covers_block(labels)]
    candidates = ("".join([*lines[:position], *block, *lines[position:]]) for position in (after_keys, header))
    return next(candidate for candidate in candidates if tomllib.loads(candidate) == wanted)


__all__ = [
    "AFFECTS_KEYS",
    "OWNERS",
    "PRESET_SURFACE",
    "RULES",
    "Action",
    "Category",
    "Facts",
    "Keep",
    "Kind",
    "Owner",
    "Record",
    "Rule",
    "Source",
    "Verify",
    "covers_block",
    "covers_written",
    "guide_problems",
    "owner_of",
    "parse_entry",
    "rule_problems",
    "stamp_pending",
    "with_covers",
]
