"""Report stored-output changes that no change record covers.

Usage:
    python scripts/declared_changes.py status [--base REV] [--verdicts FILE]
    python scripts/declared_changes.py declare ID [--category C] [--title T] [--summary S] [--refs '#n' ...]
        [--decision '#n' ...] [--surface ITEM ...] [--extends ID] (--rest | --labels GLOB ... | --tests NODEID-GLOB ...)
        [--base REV]
    python scripts/declared_changes.py prune [--tag TAG]
    python scripts/declared_changes.py check --base REV [--head REV] [--tag TAG] [--main] [--report-only]
        [--verdicts FILE] [--ci]

The unit is a reference: one `.refs` line (a test item and a label), whose value is the content of its stored unit,
read by the rules of `tests/units.py`, with its environment predicate and its `?` flag. A reference changes when its
content, predicate or flag changes, also when pytest splits a shared unit into its own file; when the change touches
neither `src/` nor `uv.lock`, a removed reference whose content an added one has is a move. Every changed and every
other removed reference needs its label in the `covers` of a change record (`docs/data/migration/unreleased/<id>.toml`,
`scripts/_change_records.py`); so does every added reference when the change touches `src/` or `uv.lock`, until a
replay can tell which ones the base package already writes. Coverage is per label, so it covers every reference to
the label. In a change that touches `src/` or `uv.lock`, a label a `harness` record adds to its covers says
the package writes the same output: one whose content changed and that is not internal output (`needs_proof`) needs
the gate's verdict (`behaviour_diff.py gate --verdicts FILE`, read with `--verdicts FILE`), and fails where the gate
refused it or has no verdict for these contents. A change's coverage is the labels it adds to `covers`: records of
its base (a lower stack layer) do not cover its changes, and a label it covers must move in it or be a gap of its base
(a backfill). A label whose references the change brings back to the tag's references and contents needs no record
while no unreleased record covers it, and is stale while one does. Fragments the tag contains are released: they
cover nothing and are not edited.

`check` also judges the release by the same rule, one step per change since the latest tag as main takes them (each
first-parent commit up to the merge base with origin/main, the rest of the base, then the change): every label a step
needed a record for that still differs from the tag is covered by a record the tag does not contain, and every covered
label still differs from the tag (`prune` drops those that do not, and the labels a change added that it may not
cover). So an output a test-only change added needs no record later either, and one it moved keeps the need of the
output it moved, under its new label. The walk needs the history: a shallow clone and a tag that is not an ancestor of
the base are refused. A change is told only of the gaps it adds; with `--main` every gap counts, and so does a tag
that `releases.toml` does not list yet (stamp pending). `--report-only` prints the same and exits 0. `status` reports
the working tree against the merge base with origin/main, and `declare` writes the `covers` of a record from that
diff, or from `--base` (a stack layer passes the layer below): never write them by hand. `--labels` and `--tests` also
take the gaps the change may cover: those of the base that the head still moves from the tag, and those of outputs the
change moves; `--rest` only what this change moves. The check prints one `declare` command per kind of undeclared
output, with `--base` unless the base is that merge base.

`check --ci` is the CI step's form: it reads `--verdicts FILE` only where the gate wrote it (every gate run writes it;
CI skips the gate where the change leaves `src/`, the change records and `scripts/` alone). A push of several pull
requests to main is judged one squash commit at a time (`change_steps`), as each pull request was. When the change
touches `COPY_PATHS` and the base has this check, the base's copy judges it first and the head's own check after it,
and either one fails the step: a change cannot weaken the check that judges it, and a change that tightens it obeys it.
"""

from __future__ import annotations

import argparse
import fnmatch
import glob
import hashlib
import io
import json
import operator
import os
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
from bisect import bisect_left
from dataclasses import dataclass, field
from functools import cached_property
from itertools import count, takewhile
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]

from scripts._change_records import (  # ruff: ignore[module-import-not-at-top-of-file]
    DERIVED_PREFIXES,
    ID_PATTERN,
    PRESET_SURFACE,
    RULES,
    Category,
    Owner,
    covers_block,
    covers_written,
    owner_of,
    parse_entry,
    resolved_record,
    rule_problems,
    stamp_pending,
    with_covers,
)
from tests.units import (  # ruff: ignore[module-import-not-at-top-of-file]
    REFS_SUFFIX,
    label_problem,
    parse_refs,
    predicate_text,
    split_label,
    stored_member,
    unit_digest,
    unit_value,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from scripts._change_records import Record

SCRIPT = "scripts/declared_changes.py"
EXPECTED = "tests/data/expected/"
FRAGMENTS = "docs/data/migration/unreleased/"
RELEASES = "docs/data/migration/releases.toml"
LISTED = ("tests", "docs/data/migration", "src", "uv.lock")
GENERATOR = ("src/", "uv.lock")
DEFAULT_BASE = "origin/main"
COPY_PATHS = ("scripts", "tests/__init__.py", "tests/units.py")
"""What `check --ci` takes from the base as its copy of this check (the paths of the behaviour gate's base copy)."""
HISTORY = ("--first-parent", "--diff-merges=first-parent", "--reverse", "--name-only", "-z", "--format=%x01%H")
"""The `git log` options that list each first-parent commit with the files it changes against its first parent."""
CHANGED, REMOVED, ADDED, MOVED = "changed", "removed", "added", "moved"
SUBJECT_REF = re.compile(
    r"\((#[1-9]\d*)\)\s*$|\b(?:fixes|closes|resolves):?\s+(#[1-9]\d*)", re.IGNORECASE | re.MULTILINE
)
SLUG = re.compile(r"[^a-z0-9.:_-]+")
RECORD_OPTIONS = ("category", "title", "summary", "extends", "refs", "decision", "surface")
"""The `declare` options that a new record takes as its keys, in the order it writes them."""
NO_VERDICT = (
    "the gate has no verdict for these contents: run "
    "'python scripts/behaviour_diff.py gate BASE --tier pr --verdicts FILE' and pass --verdicts FILE"
)
"""Why a label that needs the gate's proof fails without a verdict of the gate for its base and head contents."""
LOCK_ONLY = (
    "the gate judges src/ only, so it can never give a verdict for a change of uv.lock alone: "
    "cover this changed label with a category other than harness"
)
"""What a label that needs the gate's proof in a change of uv.lock alone tells the author."""
Proof = tuple[str, str, str]
"""A label that needs the gate's proof, with the digests of its base and head contents."""
Verdicts = dict[Proof, str | None]
"""The gate's verdicts on the labels `harness` records cover: the problem, or None, by the label's proof."""
SHOWN = 20
"""Labels a message lists one by one before it counts the rest."""
Line = tuple[str, bool, str]
"""One `.refs` line of a reference: its predicate text, its `?` flag and its storage."""
_PARSED: dict[tuple[str, str], dict[Reference, tuple[Line, ...]]] = {}
"""The references of a sidecar by its path and blob id."""


def git(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    """Run git in a repository and return its output; a failure raises CalledProcessError."""
    return subprocess.run(["git", "-C", str(repo), *args], input=stdin, capture_output=True, check=True).stdout


@dataclass(frozen=True, order=True)
class Reference:
    """A test item and a label it compares, as one `.refs` file names them."""

    sidecar: str
    function: str
    param: str
    label: str

    @property
    def nodeid(self) -> str:
        """Return the item's nodeid."""
        item = f"{self.function}[{self.param}]" if self.param else self.function
        return f"{self.sidecar.removesuffix(REFS_SUFFIX)}.py::{item}"


Row = tuple[str, Reference]
"""A reference that needs a record, with its state."""


class Side:
    """One revision of a repository, or its working tree (`rev` None), read through git."""

    def __init__(self, repo: Path, rev: str | None) -> None:
        """List the files the check reads; their contents are read on demand."""
        self.repo, self.rev = repo, rev
        self.files = self._list()
        self.paths = sorted(self.files)
        self._blobs: dict[str, bytes] = {}
        self._digests: dict[str, str | None] = {}

    @property
    def name(self) -> str:
        """Return the revision as messages name it."""
        return self.rev or "the working tree"

    def _list(self) -> dict[str, str]:
        if self.rev is not None:
            entries = git(self.repo, "ls-tree", "-r", "-z", self.rev, "--", *LISTED).decode().split("\0")
            return {path: meta.split()[2] for meta, _, path in (entry.partition("\t") for entry in entries if entry)}
        listed = git(self.repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *LISTED)
        paths = sorted({path for path in listed.decode().split("\0") if path and (self.repo / path).is_file()})
        ids = git(self.repo, "hash-object", "--stdin-paths", stdin="".join(f"{path}\n" for path in paths).encode())
        return dict(zip(paths, ids.decode().split(), strict=True))

    def read(self, path: str) -> bytes:
        """Return the bytes of a listed file."""
        if self.rev is None:
            return (self.repo / path).read_bytes()
        self.load([path])
        return self._blobs[path]

    def load(self, paths: Iterable[str]) -> None:
        """Read the blobs of many files of a revision with one git process."""
        wanted = [path for path in dict.fromkeys(paths) if path not in self._blobs]
        if self.rev is None or not wanted:
            return
        output = git(self.repo, "cat-file", "--batch", stdin="".join(f"{self.files[p]}\n" for p in wanted).encode())
        position = 0
        for path in wanted:
            header = output.index(b"\n", position)
            size = int(output[position:header].split()[2])
            self._blobs[path] = output[header + 1 : header + 1 + size]
            position = header + 2 + size

    def preload(self, storages: Iterable[str]) -> None:
        """Read the stored files of many storages of a revision with one git process; a tree reads its own."""
        self.load(path for storage in storages if (path := f"{EXPECTED}{storage}") in self.files)

    def text(self, path: str) -> str | None:
        """Return the text of a file, or None when the side has no such file."""
        return self.read(path).decode("utf-8") if path in self.files else None

    def members(self, storage: str) -> list[tuple[str, str]]:
        """Return the (path relative to the tree, file) pairs a tree storage holds, as `tests/units.py` reads a tree."""
        directory, pattern = split_label(storage)
        prefix = f"{EXPECTED}{directory}/"
        inside = takewhile(lambda path: path.startswith(prefix), self.paths[bisect_left(self.paths, prefix) :])
        return [
            (relative.as_posix(), path)
            for path in inside
            if stored_member(relative := PurePosixPath(path[len(prefix) :])) and relative.match(pattern or "*")
        ]

    def raw(self, storage: str) -> object:
        """Return the blob ids a storage holds: equal ids are equal units; unequal ids may still be equal units."""
        if split_label(storage)[1] is None:
            return self.files.get(f"{EXPECTED}{storage}")
        return tuple((relative, self.files[path]) for relative, path in self.members(storage))

    def digest(self, storage: str) -> str | None:
        """Return the digest of the unit a storage holds, as the ledger computes it; None when nothing is stored."""
        if storage not in self._digests:
            name, pattern = split_label(storage)
            value: object = None
            if pattern is None:
                if (path := f"{EXPECTED}{name}") in self.files:
                    value = unit_value(name, self.read(path))
            else:
                members = self.members(storage)
                self.load(path for _, path in members)
                value = {relative: unit_value(relative, self.read(path), in_tree=True) for relative, path in members}
            self._digests[storage] = None if value is None else unit_digest(storage, value)
        return self._digests[storage]

    def storages(self, label: str) -> list[str]:
        """Return the storages a label's references compare, sorted; the label itself where nothing references it.

        A shared output (`= other.py`) is stored under another name than its label.
        """
        found = {storage for reference in self.by_label.get(label, []) for *_, storage in self.references[reference]}
        return sorted(found) or [label]

    def content(self, label: str) -> str | None:
        """Return the digest of the content a label's references compare, None when no reference has stored output.

        The digest of one stored unit when every reference compares it, else one of the units by test item and
        predicate, so a label that starts or stops sharing another's stored file changes content as its own file does,
        and so do test items that swap their units.
        """
        if not self.by_label.get(label):
            return self.digest(label)
        lines = {
            (reference.sidecar, reference.function, reference.param, predicate, conditional, digest)
            for reference in self.by_label[label]
            for predicate, conditional, storage in self.references[reference]
            if (digest := self.digest(storage)) is not None
        }
        if len({digest for *_, digest in lines}) <= 1:
            return next(iter(lines))[-1] if lines else None
        return hashlib.sha256(json.dumps(sorted(lines)).encode()).hexdigest()

    @cached_property
    def sidecars(self) -> dict[str, dict[Reference, tuple[Line, ...]]]:
        """Return the references of each sidecar with their lines (predicate, flag, storage), sorted.

        A sidecar is parsed once per content: sides that hold the same blob share its references.
        """
        sidecars = [
            path
            for path in self.paths
            if path.endswith(REFS_SUFFIX)
            and not path.startswith("tests/data/")
            and not any(part.startswith((".", "__")) for part in path.split("/")[1:-1])
        ]
        self.load(sidecar for sidecar in sidecars if (sidecar, self.files[sidecar]) not in _PARSED)
        for sidecar in sidecars:
            if (key := (sidecar, self.files[sidecar])) in _PARSED:
                continue
            found: dict[Reference, list[Line]] = {}
            for line in parse_refs(self.read(sidecar).decode("utf-8").replace("\r\n", "\n"), historical=True)[0]:
                reference = Reference(sidecar, line.function, line.param, line.label)
                found.setdefault(reference, []).append((predicate_text(line.predicate), line.conditional, line.stored))
            _PARSED[key] = {reference: tuple(sorted(lines)) for reference, lines in found.items()}
        return {sidecar: _PARSED[sidecar, self.files[sidecar]] for sidecar in sidecars}

    @cached_property
    def references(self) -> dict[Reference, tuple[Line, ...]]:
        """Return every reference of the side with its lines."""
        return {reference: lines for found in self.sidecars.values() for reference, lines in found.items()}

    @cached_property
    def by_label(self) -> dict[str, list[Reference]]:
        """Return the references of each label."""
        found: dict[str, list[Reference]] = {}
        for reference in self.references:
            found.setdefault(reference.label, []).append(reference)
        return found

    @cached_property
    def records(self) -> dict[str, tuple[Record | None, str, list[str]]]:
        """Return each fragment by its id (file stem): its record, None if it holds none, its text and its problems."""
        found: dict[str, tuple[Record | None, str, list[str]]] = {}
        for path in self.paths:
            stem = path.removeprefix(FRAGMENTS).removesuffix(".toml")
            if not path.startswith(FRAGMENTS) or not path.endswith(".toml") or "/" in stem:
                continue
            text, where = self.read(path).decode("utf-8").replace("\r\n", "\n"), f"unreleased/{stem}.toml"
            try:
                entries = tomllib.loads(text).get("change", [])
            except tomllib.TOMLDecodeError as error:
                found[stem] = (None, text, [f"{where}: {error}"])
                continue
            if [entry.get("id") for entry in entries] != [stem]:
                problem = f"{where}: a fragment holds exactly one [[change]] whose id is the file name"
                found[stem] = (None, text, [problem])
                continue
            record, problems = parse_entry(entries[0], where)
            found[stem] = (record, text, problems)
        return found

    def released(self) -> list[str]:
        """Return the versions `releases.toml` lists."""
        text = self.text(RELEASES)
        return [str(row["version"]) for row in tomllib.loads(text).get("release", [])] if text else []

    def generator(self) -> dict[str, str]:
        """Return the files whose change changes what the package generates."""
        return {path: blob for path, blob in self.files.items() if path.startswith(GENERATOR)}


def _same(old: Side, old_lines: tuple[Line, ...], new: Side, new_lines: tuple[Line, ...]) -> bool:
    """Return whether two sides' lines of a reference compare equal units with the same predicates and flags."""
    if [line[:2] for line in old_lines] != [line[:2] for line in new_lines]:
        return False
    return all(
        old.raw(before) == new.raw(after) or old.digest(before) == new.digest(after)
        for (_, _, before), (_, _, after) in zip(old_lines, new_lines, strict=True)
    )


def _content(side: Side, lines: tuple[Line, ...]) -> tuple[tuple[str, bool, str | None], ...]:
    return tuple((predicate, conditional, side.digest(storage)) for predicate, conditional, storage in lines)


@dataclass
class Diff:
    """The references that differ between two sides; `moved` are removed and added references of equal content.

    `generator` says the step touches `src/` or `uv.lock`; `pairs` are the (removed, added) references of each move,
    and `vanished` the labels of moved removed references that the new side no longer has.
    """

    generator: bool = False
    changed: list[Reference] = field(default_factory=list)
    removed: list[Reference] = field(default_factory=list)
    moved: list[Reference] = field(default_factory=list)
    added: list[Reference] = field(default_factory=list)
    pairs: list[tuple[Reference, Reference]] = field(default_factory=list)
    vanished: set[str] = field(default_factory=set)

    def rows(self) -> list[Row]:
        """Return the references that need a record with their state; the added ones only on a generator step."""
        rows = [(CHANGED, ref) for ref in self.changed] + [(REMOVED, ref) for ref in self.removed]
        return sorted(rows + [(ADDED, ref) for ref in self.added if self.generator], key=operator.itemgetter(1))

    def carried(self, needs: dict[str, set[Row]]) -> list[tuple[Reference, Reference]]:
        """Return the moves whose removed reference's label needs a record: the added reference takes the need."""
        return [(removed, added) for removed, added in self.pairs if removed.label in needs]

    def moving(self) -> set[str]:
        """Return every label with a reference that changed, was removed, moved or was added."""
        return {ref.label for refs in (self.changed, self.removed, self.moved, self.added) for ref in refs}


def _touched(storage: str, files: set[str], folders: set[str]) -> bool:
    """Return whether a storage holds one of the stored files a step changed; `folders` are the files' folders."""
    directory, pattern = split_label(storage)
    return storage in files if pattern is None else directory in folders


def diff(old: Side, new: Side, *, generator: bool | None = None) -> Diff:
    """Return the references that differ in one step between two sides.

    `generator` says the step touches `src/` or `uv.lock` (by default: the sides differ there). Only a step that
    touches neither pairs a removed and an added reference of equal content as a move. A reference whose sidecar and
    stored files the step leaves alone is the same.
    """
    result = Diff(old.generator() != new.generator() if generator is None else generator)
    files = {path[len(EXPECTED) :] for path, _ in old.files.items() ^ new.files.items() if path.startswith(EXPECTED)}
    folders = {parent.as_posix() for path in files for parent in PurePosixPath(path).parents}
    common: list[Reference] = []
    added: list[Reference] = []
    removed: list[Reference] = []
    for sidecar in old.sidecars.keys() | new.sidecars.keys():
        before, after = old.sidecars.get(sidecar, {}), new.sidecars.get(sidecar, {})
        if old.files.get(sidecar) == new.files.get(sidecar):
            common += (ref for ref, lines in after.items() if any(_touched(line[2], files, folders) for line in lines))
            continue
        common += before.keys() & after.keys()
        added += after.keys() - before.keys()
        removed += before.keys() - after.keys()
    before, after = old.references, new.references
    pending = [
        (a, b)
        for ref in common
        for (*_, a), (*_, b) in zip(before[ref], after[ref], strict=False)
        if old.raw(a) != new.raw(b)
    ]
    pair = not result.generator
    old.preload([a for a, _ in pending] + [line[2] for ref in removed if pair for line in before[ref]])
    new.preload([b for _, b in pending] + [line[2] for ref in added if pair for line in after[ref]])
    result.changed = sorted(ref for ref in common if not _same(old, before[ref], new, after[ref]))
    kept = {_content(new, after[ref]) for ref in added} if pair else set()
    gone: dict[tuple[tuple[str, bool, str | None], ...], list[Reference]] = {}
    for ref in sorted(removed):
        if pair and (content := _content(old, before[ref])) in kept:
            result.moved.append(ref)
            gone.setdefault(content, []).append(ref)
        else:
            result.removed.append(ref)
    for ref in sorted(added):
        sources = gone.get(_content(new, after[ref]), [])
        (result.moved if sources else result.added).append(ref)
        result.pairs += ((source, ref) for source in sources)
    result.vanished = {removed.label for removed, _ in result.pairs if removed.label not in new.by_label}
    return result


def _unreleased(side: Side, tag: Side) -> dict[str, Record]:
    """Return the records of a side that the tag does not contain: a fragment the tag contains is released."""
    return {
        stem: record for stem, (record, _, _) in side.records.items() if record is not None and stem not in tag.records
    }


def _by_label(rows: Iterable[tuple[str, Reference]]) -> dict[str, list[tuple[str, Reference]]]:
    found: dict[str, list[tuple[str, Reference]]] = {}
    for state, reference in rows:
        found.setdefault(reference.label, []).append((state, reference))
    return found


def _covers_of(records: dict[str, Record], stem: str) -> set[str]:
    return set(records[stem].covers) if stem in records else set()


def _holders(label: str, *sides: Side) -> list[Reference]:
    return [reference for side in sides for reference in side.by_label.get(label, [])]


@dataclass
class Report:
    """What a change moves that its records do not cover, what they claim wrongly, and what the release lacks."""

    base: str
    head: str
    tag: str
    generator: bool
    branch: str
    undeclared: dict[str, list[tuple[str, Reference]]] = field(default_factory=dict)
    over_claimed: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    moves: int = 0
    gaps: list[str] = field(default_factory=list)
    backfill: dict[str, list[tuple[str, Reference]]] = field(default_factory=dict)
    stale: list[str] = field(default_factory=list)
    older_gaps: int = 0
    stamp: str | None = None
    refs: list[str] = field(default_factory=list)
    main: bool = False
    returned: dict[str, list[str]] = field(default_factory=dict)
    proofs: list[Proof] = field(default_factory=list)
    lock_only: bool = False
    verdicts: Verdicts = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)

    @property
    def refused(self) -> list[str]:
        """The problems of the labels that need the gate's proof: its refusal, or no verdict for their contents."""
        return [
            problem
            for proof in self.proofs
            if (problem := self.verdicts.get(proof, f"{proof[0]}: {LOCK_ONLY if self.lock_only else NO_VERDICT}"))
            is not None
        ]

    @property
    def failed(self) -> bool:
        """Return whether the change breaks a rule; on main a pending stamp does too."""
        return bool(
            self.undeclared
            or self.over_claimed
            or self.problems
            or self.gaps
            or self.stale
            or self.refused
            or (self.main and self.stamp)
        )


def _kinds(label: str, *sides: Side) -> set[Owner]:
    """Return the kinds of stored output the references of a label compare on any of the sides."""
    return {owner_of(reference.nodeid, label) for reference in _holders(label, *sides)}


def needs_proof(label: str, base: Side, head: Side) -> bool:
    """Return whether a label a `harness` record covers in a change of the generator needs the gate's verdict (6.4).

    Internal output is admitted, and so are an unchanged label and one that a side lacks (an added or removed output
    follows the rule for records); every other label needs the gate's proof that the package writes the same output.
    """
    old, new = base.content(label), head.content(label)
    return old is not None and new is not None and old != new and _kinds(label, base, head) != {Owner.INTERNAL}


def load_verdicts(path: Path) -> Verdicts:
    """Return the verdicts a gate run wrote (`behaviour_diff.py gate --verdicts`)."""
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))["verdicts"]
        return {(row["label"], row["base"], row["head"]): row["problem"] for row in rows}
    except (OSError, ValueError, KeyError, TypeError):
        msg = f"{path.as_posix()} holds no verdicts of the gate (behaviour_diff.py gate --verdicts writes them)"
        raise CheckError(msg) from None


def _refused(category: Category, surface: tuple[str, ...] | None, kinds: set[Owner], *, released: bool) -> set[Owner]:
    """Return the kinds of a label's stored output that a record of the category may not cover.

    `surface` is the record's items, None for a record not written yet; `released` says the latest release has the
    label: `new-surface` covers only model output that release does not have.
    """
    if (owners := RULES[category].owners) is None:
        return set()
    if category == Category.NEW_SURFACE:
        if surface is not None and all(item.startswith(PRESET_SURFACE) for item in surface):
            owners = frozenset({Owner.LISTING})
        elif released:
            owners -= {Owner.MODELS}
    return kinds - owners


def _revalidated(stem: str, records: dict[str, Record], base_records: dict[str, Record]) -> set[str]:
    """Return the labels a record's category must cover again: all of its record's when the category or surface changed.

    The labels are those of the record and of the records that extend it; a record the base does not have, one that
    extends another and a fragment without a record only have the labels they add.
    """
    record, before = records.get(stem), base_records.get(stem)
    if record is None or before is None or record.extends is not None:
        return set()
    if (record.category, record.surface) == (before.category, before.surface):
        return set()
    return {label for other in records.values() if other is record or other.extends == stem for label in other.covers}


def _cells_named(record: Record, base: Side) -> bool:
    """Return whether the record must name the cells its category requires: it extends another and the base lacks it."""
    return record.extends is not None and record.id not in base.records


def _record_problems(
    stem: str, sides: tuple[Side, Side, Side], records: dict[str, Record], added: set[str]
) -> list[str]:
    """Return how one record the change adds or edits breaks the rules the static check reads.

    `added` are the labels the record's category must cover: those the record adds, and those its category change
    puts under a new rule.
    """
    base, head, tag = sides
    record, text, problems = head.records[stem]
    if record is None:
        return problems
    where = f"unreleased/{stem}.toml"
    problems = rule_problems(record, fragment=True, added=_cells_named(record, base), records=records)
    if not covers_written(text, record):
        problems.append(f"{where}: its covers are not written as '{SCRIPT} declare' writes them")
    problems += [f"{where}: {problem}" for label in record.covers if (problem := label_problem(label))]
    target, _ = resolved_record(record, records)
    if target is None or target.category is None:
        return problems
    for label in sorted(added):
        released = label in tag.by_label
        if kinds := _refused(target.category, target.surface, _kinds(label, base, head), released=released):
            has = f" that {tag.name} has" if target.category == Category.NEW_SURFACE and released else ""
            problems.append(
                f"{where}: covers {label}, {', '.join(sorted(kinds))} output{has}, which category {target.category} "
                "does not cover: give it a record of its own"
            )
    return problems


def _history(repo: Path, tag: Side, base: Side) -> list[Diff]:
    """Return the steps from the tag to the base, as main takes them: one per change.

    Up to the merge base with origin/main (the whole base without one) a step is a first-parent commit; the rest of
    the base (lower stack layers) is one step. Only a commit that touches stored outputs or sidecars moves references;
    its parent has the stored outputs and sidecars of the last such commit before it, and it touches the generator when
    it touches `src/` or `uv.lock`. A shallow repository is refused, and so is a tag on another line of history than
    the base (a tag after the base, as a release commit's own, has no steps).
    """
    if git(repo, "rev-parse", "--is-shallow-repository").strip() == b"true":
        msg = f"the repository is shallow: judging the release walks every commit since {tag.name}; fetch the history"
        raise CheckError(msg)
    start, end = _rev(repo, tag.rev or "HEAD"), _rev(repo, base.rev or "HEAD")
    try:
        common = git(repo, "merge-base", start, end).decode().strip()
    except subprocess.CalledProcessError:
        common = None
    if common not in {start, end}:
        msg = f"{tag.name} is neither an ancestor nor a descendant of {base.name}; pass the latest release with --tag"
        raise CheckError(msg)
    fork = _merge_base(repo, end) or end
    log = git(repo, "log", *HISTORY, f"{start}..{fork}")
    steps: list[Diff] = []
    last = tag
    for commit, _, names in (entry.partition("\0") for entry in log.decode().split("\x01") if entry):
        paths = names.lstrip("\n").split("\0")
        if any(path.startswith(EXPECTED) or path.endswith(REFS_SUFFIX) for path in paths):
            side = Side(repo, commit)
            steps.append(diff(last, side, generator=any(path.startswith(GENERATOR) for path in paths)))
            last = side
    return steps if fork == end else [*steps, diff(last, base)]


def _needs(steps: Iterable[Diff], needs: dict[str, set[Row]] | None = None) -> dict[str, set[Row]]:
    """Return the references that needed a record by label after the steps, taken in order after `needs`.

    A step needs what `Diff.rows` returns, as `evaluate` judges a change. A move passes the need of its removed
    reference's label to the added reference; the removed label keeps it only while the step's side has the label.
    """
    found = {label: set(rows) for label, rows in (needs or {}).items()}
    for step in steps:
        carried = step.carried(found)
        for label in {removed.label for removed, _ in carried} & step.vanished:
            del found[label]
        for row in [*step.rows(), *((MOVED, added) for _, added in carried)]:
            found.setdefault(row[1].label, set()).add(row)
    return found


def _release(tag: Side, side: Side, needs: dict[str, set[Row]]) -> tuple[set[str], dict[str, list[Row]], set[str]]:
    """Return the labels that moved since the tag, the gaps among them with their references, and the stale ones.

    `needs` are those of the steps from the tag to the side (`_needs`). A gap moved and needs a record, and no
    unreleased record covers it. A stale label is covered and did not move.
    """
    moving = diff(tag, side).moving()
    covered = {label for record in _unreleased(side, tag).values() for label in record.covers}
    rows = [row for label in (moving - covered) & needs.keys() for row in needs[label]]
    return moving, _by_label(sorted(rows, key=operator.itemgetter(1, 0))), covered - moving


def _branch(repo: Path) -> str:
    """Return the id a record of this branch gets: the branch name as a slug."""
    name = os.environ.get("GITHUB_HEAD_REF") or git(repo, "branch", "--show-current").decode().strip()
    return SLUG.sub("-", name.lower()).strip("-") or "<id>"


def _merge_base(repo: Path, head: str | None) -> str | None:
    """Return the merge base of a revision (the working tree's HEAD for None) with origin/main, None without one."""
    try:
        return git(repo, "merge-base", DEFAULT_BASE, head or "HEAD").decode().strip()
    except subprocess.CalledProcessError:
        return None


def change_steps(repo: Path, base: str, head: str | None) -> list[tuple[str, str | None]]:
    """Return the changes from `base` to `head` (None: the working tree) as main takes them, as (from, to) revisions.

    Each first-parent commit from the base up to the head's merge base with origin/main is a change of its own: a push
    of several pull requests to main holds one squash commit for each. The rest (a pull request, a stack layer, the
    working tree) is one change, and so is the whole range where the base is not on that first-parent line.
    """
    fork = _merge_base(repo, head)
    rows = git(repo, "rev-list", "--first-parent", "--reverse", "--parents", f"{base}..{fork}").decode() if fork else ""
    parents = [line.split() for line in rows.splitlines()]
    commits = [commit for commit, *_ in parents] if parents and parents[0][1:2] == [_rev(repo, base)] else []
    if commits and head is not None and _rev(repo, head) == commits[-1]:
        commits.pop()
    return list(zip([base, *commits], [*commits, head], strict=True))


def evaluate(repo: Path, base: Side, head: Side, tag: Side, *, main: bool = False) -> Report:
    """Return what the change from `base` to `head` moves and claims, and what the release since `tag` lacks.

    A label the change moves back to the tag's references and contents is free when no unreleased record covers it,
    and stale when one does; a label a record adds to `covers` must move in the change or be a gap of the base. In a
    change of the generator, a label a `harness` record adds that `needs_proof` is refused unless `Report.verdicts`
    hold the gate's admission of its base and head contents.
    """
    base_needs = _needs(_history(repo, tag, base))
    moved = diff(base, head)
    generator = moved.generator
    report = Report(base.name, head.name, tag.name, generator, _branch(repo), moves=len(moved.moved), main=main)
    head_records, base_records = _unreleased(head, tag), _unreleased(base, tag)
    added = {stem: set(record.covers) - _covers_of(base_records, stem) for stem, record in head_records.items()}
    coverage = set().union(*added.values())
    covered = {label for record in head_records.values() for label in record.covers}
    since, head_rows, head_stale = _release(tag, head, _needs([moved], base_needs))
    _, base_rows, base_stale = _release(tag, base, base_needs)
    head_gaps, base_gaps = set(head_rows), set(base_rows)
    claimable = _claimable(moved, base_gaps)
    rows = moved.rows()
    at_tag = {ref.label for _, ref in rows} - since
    report.returned = {
        label: [stem for stem, record in sorted(base_records.items()) if label in record.covers]
        for label in sorted(at_tag - covered)
    }
    report.undeclared = _by_label((state, ref) for state, ref in rows if ref.label not in coverage | at_tag)
    report.over_claimed = sorted(coverage - claimable)
    report.backfill = {
        label: rows for label, rows in head_rows.items() if label in claimable and label not in report.undeclared
    }
    for stem in sorted(head.records):
        text = head.records[stem][1]
        if stem in tag.records:
            if text != base.records.get(stem, (None, text, []))[1]:
                report.problems.append(
                    f"unreleased/{stem}.toml shipped in {tag.name}; move later edits to a new fragment"
                )
        elif stem not in base.records or text != base.records[stem][1]:
            checked = added.get(stem, set()) | _revalidated(stem, head_records, base_records)
            report.problems += _record_problems(stem, (base, head, tag), head_records, checked)
    renamed = {target.label for source, target in moved.carried(base_needs) if source.label in base_gaps}
    older = set() if main else base_gaps | renamed
    report.gaps = sorted(head_gaps - older - set(report.undeclared))
    report.stale = sorted(head_stale - base_stale - set(report.over_claimed))
    report.older_gaps = len(head_gaps & older)
    report.stamp = stamp_pending(tag.name, head.released())
    if generator:
        report.lock_only = {path: blob for path, blob in base.generator().items() if path.startswith("src/")} == {
            path: blob for path, blob in head.generator().items() if path.startswith("src/")
        }
        harness = {
            label
            for stem, labels in added.items()
            if (target := resolved_record(head_records[stem], head_records)[0]) is not None
            and target.category == Category.HARNESS
            for label in labels
        }
        report.proofs = [
            (label, base.content(label) or "", head.content(label) or "")
            for label in sorted(harness)
            if needs_proof(label, base, head)
        ]
    log = git(repo, "log", "--format=%B", f"{base.rev or 'HEAD'}..{head.rev or 'HEAD'}").decode()
    report.refs = list(dict.fromkeys(ref for match in SUBJECT_REF.finditer(log) for ref in match.groups() if ref))
    default = _merge_base(repo, head.rev) == _rev(repo, base.rev or "HEAD")
    fresh = {stem: record for stem, record in head_records.items() if stem not in base.records}
    report.commands = _commands(report, (base, head, tag), base_records, fresh, None if default else base.rev)
    return report


def _claimable(moved: Diff, base_gaps: set[str]) -> set[str]:
    """Return the labels a change may add to `covers`: those it moves, and the gaps of its base (a backfill)."""
    return moved.moving() | base_gaps


def _admissible(kinds: set[Owner], *, released: bool, generator: bool) -> list[Category]:
    """Return the categories whose records may cover outputs of these kinds; `harness` only if src/ is the same."""
    return [
        category
        for category in RULES
        if not _refused(category, None, kinds, released=released) and not (category == Category.HARNESS and generator)
    ]


def _commands(
    report: Report, sides: tuple[Side, Side, Side], lower: dict[str, Record], fresh: dict[str, Record], base: str | None
) -> list[str]:
    """Return one `declare` command per kind of undeclared output, filled with what the check knows.

    A label a lower layer's record covers extends that record; the others form one group per kind of stored output
    when the change touches the generator (else a `harness` record takes them all). A group goes to the one record
    of its category the change adds, or the branch's record, with no record options; else to a new record.
    """
    old, head, tag = sides
    groups: dict[tuple[str, ...], list[str]] = {}
    for label in report.undeclared:
        target = next((record.extends or stem for stem, record in sorted(lower.items()) if label in record.covers), "")
        kinds = sorted(_kinds(label, old, head)) if report.generator and not target else []
        groups.setdefault((target, *kinds), []).append(label)
    taken = set(head.records) | set(tag.records)
    commands = []
    for (extends, *_), labels in groups.items():
        released = any(label in tag.by_label for label in labels)
        owners = set().union(*(_kinds(label, old, head) for label in labels))
        admissible = _admissible(owners, released=released, generator=report.generator)
        fits = [
            stem
            for stem, record in sorted(fresh.items())
            if (record.extends or "") == extends and (extends or record.category in admissible)
        ]
        selection = ["--labels", *map(glob.escape, labels)] if len(groups) > 1 else ["--rest"]
        tail = [*selection, *(["--base", base] if base else [])]
        if (stem := report.branch if report.branch in fits else fits[0] if len(fits) == 1 else None) is not None:
            commands.append(shlex.join(["python", SCRIPT, "declare", stem, *tail]))
            continue
        ident = next(name for name in _ids(report.branch) if name not in taken)
        taken.add(ident)
        command = ["python", SCRIPT, "declare", ident]
        if extends:
            commands.append(shlex.join([*command, "--extends", extends, *tail]))
            continue
        category = Category.HARNESS if not report.generator else admissible[0] if len(admissible) == 1 else "<category>"
        command += ["--category", category]
        if category == Category.HARNESS:
            command += ["--title", "<title>", "--summary", "<why the package writes the same output>"]
        for ref in report.refs:
            command += ["--refs", ref]
        commands.append(
            shlex.join([*command, *tail])
            + ("" if category != "<category>" else f"\n    <category>: one of {', '.join(admissible)}")
        )
    return commands


def _ids(branch: str) -> Iterator[str]:
    """Yield the ids a new record of the branch may take: the branch's slug, then the slug numbered."""
    yield branch
    yield from (f"{branch}-{number}" for number in count(2))


def _more(items: list[str]) -> list[str]:
    return [f"  ... {len(items) - SHOWN} more"] if len(items) > SHOWN else []


def format_report(report: Report) -> str:
    """Return the report as the check prints it."""
    lines = [f"declared changes: {report.head} against {report.base}, release since {report.tag}"]
    if report.undeclared:
        lines.append(f"{len(report.undeclared)} stored outputs this change moves are not declared:")
        for label, rows in list(report.undeclared.items())[:SHOWN]:
            state, ref = rows[0]
            more = f" (+{len(rows) - 1} more)" if len(rows) > 1 else ""
            note = " (src/ changed: a record until a replay tells)" if state == ADDED else ""
            lines.append(f"  {state:8} {label}  {ref.nodeid}{more}{note}")
        lines += _more(list(report.undeclared))
        lines += ["Declare them:", *(f"  {command}" for command in report.commands)]
        lines.append("Categories: docs/development-contributing.md#declared-changes")
    if report.over_claimed:
        lines.append(f"{len(report.over_claimed)} labels this change adds to covers do not move in it:")
        lines += [f"  {label}" for label in report.over_claimed[:SHOWN]] + _more(report.over_claimed)
        lines.append(f"  python {SCRIPT} prune")
    lines += [f"record problem: {problem}" for problem in report.problems]
    if report.gaps:
        lost = "have no unreleased record" if report.main else "lost their record here"
        lines.append(f"{len(report.gaps)} stored outputs moved since {report.tag} and {lost}:")
        lines += [f"  {label}" for label in report.gaps[:SHOWN]] + _more(report.gaps)
    if report.stale:
        lines.append(f"{len(report.stale)} covered labels no longer differ from {report.tag}:")
        lines += [f"  {label}" for label in report.stale[:SHOWN]] + _more(report.stale)
        lines.append(f"  python {SCRIPT} prune")
    if report.returned:
        lines.append(f"{len(report.returned)} stored outputs returned to {report.tag} and need no record:")
        lines += [
            f"  {label}" + (f" (dropped from {', '.join(stems)})" if stems else "")
            for label, stems in list(report.returned.items())[:SHOWN]
        ] + _more(list(report.returned))
    if report.refused:
        lines.append(f"{len(report.refused)} stored outputs a harness record covers are refused, as src/ changes:")
        lines += [f"  {problem}" for problem in report.refused[:SHOWN]] + _more(report.refused)
    if report.older_gaps:
        lines.append(f"{report.older_gaps} stored outputs moved since {report.tag} with no record before this change")
    if report.stamp:
        lines.append(report.stamp)
    if report.moves:
        lines.append(f"{report.moves} references moved: removed and added with equal content")
    lines.append("failed" if report.failed else "ok")
    return "\n".join(lines) + "\n"


class CheckError(Exception):
    """A command cannot run: a revision or a record it needs is missing or unusable."""


def _rev(repo: Path, rev: str) -> str:
    try:
        return git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}").decode().strip()
    except subprocess.CalledProcessError:
        msg = f"{rev} is not a revision"
        raise CheckError(msg) from None


def _tag(repo: Path, tag: str | None, head: str | None) -> str:
    if tag is not None:
        _rev(repo, tag)
        return tag
    try:
        return git(repo, "describe", "--tags", "--abbrev=0", head or "HEAD").decode().strip()
    except subprocess.CalledProcessError:
        msg = "no release tag is reachable; pass --tag"
        raise CheckError(msg) from None


def _sides(repo: Path, ns: argparse.Namespace) -> tuple[Side, Side, Side]:
    """Return the base, head and tag sides the arguments name; the base defaults to the merge base with origin/main."""
    head = getattr(ns, "head", None)
    if head is not None:
        _rev(repo, head)
    if (base := ns.base or _merge_base(repo, head)) is None:
        msg = f"no merge base with {DEFAULT_BASE}; pass --base"
        raise CheckError(msg)
    _rev(repo, base)
    return Side(repo, base), Side(repo, head), Side(repo, _tag(repo, ns.tag, head))


def _select(report: Report, ns: argparse.Namespace) -> list[str]:
    """Return the labels a selector picks: `--rest` the undeclared ones, the others also the gaps the change may cover.

    Those are the gaps of the base and the gaps of outputs the change moves (a move passes on the need of a record).
    """
    if ns.rest:
        return list(report.undeclared)
    eligible = report.undeclared | report.backfill
    if ns.labels:
        return [label for label in eligible if any(fnmatch.fnmatchcase(label, glob) for glob in ns.labels)]
    return [
        label
        for label, rows in eligible.items()
        if any(fnmatch.fnmatchcase(ref.nodeid, glob) for _, ref in rows for glob in ns.tests)
    ]


def _new_fragment(ns: argparse.Namespace, labels: list[str]) -> str:
    lines = ["[[change]]", f"id = {json.dumps(ns.id)}"]
    lines += [
        f"{key} = {json.dumps(value, ensure_ascii=False)}" for key in RECORD_OPTIONS if (value := getattr(ns, key))
    ]
    return "\n".join(lines) + "\n" + covers_block(labels)


def declare(repo: Path, ns: argparse.Namespace) -> int:
    """Add the selected undeclared outputs to the record `ns.id`, creating it from the options when it is new."""
    if not ID_PATTERN.fullmatch(ns.id) or ns.id.startswith(DERIVED_PREFIXES):
        msg = f"{ns.id!r} is not a lower-case slug"
        raise CheckError(msg)
    base, head, tag = _sides(repo, ns)
    report = evaluate(repo, base, head, tag)
    if not (labels := _select(report, ns)):
        msg = "nothing to declare: no undeclared stored output matches"
        raise CheckError(msg)
    path = repo / FRAGMENTS / f"{ns.id}.toml"
    if ns.id in tag.records:
        msg = f"{ns.id} shipped in {tag.name}; declare a new record"
        raise CheckError(msg)
    if ns.id in head.records:
        record, text, problems = head.records[ns.id]
        given = [key for key in RECORD_OPTIONS if getattr(ns, key)]
        if record is None or given:
            why = "; ".join(problems) or "it exists: edit its other fields by hand"
            msg = f"{path.relative_to(repo).as_posix()}: {why}"
            raise CheckError(msg)
        text = with_covers(text, {*record.covers, *labels})
    else:
        if ns.category is None and ns.extends is None:
            msg = "a new record needs --category, or --extends"
            raise CheckError(msg)
        text = _new_fragment(ns, labels)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    record, _ = parse_entry(tomllib.loads(text)["change"][0], path.name)
    assert record is not None, "_new_fragment writes an entry that parses"
    print(f"{path.relative_to(repo).as_posix()} covers {len(labels)} more stored outputs")
    records = _unreleased(head, tag) | {record.id: record}
    found = rule_problems(record, fragment=True, added=_cells_named(record, base), records=records)
    print("".join(f"  still needed: {problem}\n" for problem in found), end="")
    return 0


def prune(repo: Path, ns: argparse.Namespace) -> int:
    """Drop the covered labels that do not move against the base (added here) or no longer differ from the tag."""
    base, head, tag = _sides(repo, ns)
    base_needs, moved = _needs(_history(repo, tag, base)), diff(base, head)
    claimable = _claimable(moved, set(_release(tag, base, base_needs)[1]))
    since = _release(tag, head, _needs([moved], base_needs))[0]
    base_records = _unreleased(base, tag)
    for stem, record in _unreleased(head, tag).items():
        added = set(record.covers) - _covers_of(base_records, stem)
        drop = {label for label in record.covers if label not in since or (label in added and label not in claimable)}
        if drop:
            text = head.records[stem][1]
            (repo / FRAGMENTS / f"{stem}.toml").write_text(
                with_covers(text, set(record.covers) - drop), encoding="utf-8", newline="\n"
            )
            print(f"unreleased/{stem}.toml: dropped {', '.join(sorted(drop))}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=SCRIPT, description="Report stored-output changes no change record covers")
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status", help="report the working tree against the merge base with origin/main")
    declare_parser = commands.add_parser("declare", help="write the covers of a record from the diff")
    prune_parser = commands.add_parser("prune", help="drop covered labels that no longer move")
    check = commands.add_parser("check", help="judge a change and the release")
    for command in (status, declare_parser, prune_parser, check):
        command.add_argument("--base", required=command is check, help="the revision the change starts from")
        command.add_argument("--tag", help="the latest release tag (default: the newest reachable tag)")
    check.add_argument("--head", help="the revision to judge (default: the working tree)")
    check.add_argument("--main", action="store_true", help="every gap of the release counts, and a pending stamp")
    check.add_argument("--report-only", action="store_true", help="print the report and exit 0")
    check.add_argument(
        "--ci",
        action="store_true",
        help="read --verdicts only if the file exists; a change of this check is judged by the base's copy of it",
    )
    for command in (status, check):
        command.add_argument(
            "--verdicts", type=Path, help="the gate's verdicts on harness labels (behaviour_diff.py gate --verdicts)"
        )
    declare_parser.add_argument("id", help="the record's id, a lower-case slug (its file name)")
    declare_parser.add_argument("--category", choices=list(Category))
    declare_parser.add_argument("--title")
    declare_parser.add_argument("--summary")
    declare_parser.add_argument("--extends", metavar="ID")
    for key, metavar in (("refs", "#N"), ("decision", "#N"), ("surface", "ITEM")):
        declare_parser.add_argument(f"--{key}", action="append", default=[], metavar=metavar)
    selection = declare_parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--rest", action="store_true", help="every undeclared stored output")
    selection.add_argument("--labels", nargs="+", metavar="GLOB", help="the undeclared outputs whose label matches")
    selection.add_argument("--tests", nargs="+", metavar="NODEID-GLOB", help="the undeclared outputs of matching items")
    return parser.parse_args(argv)


def _judged(repo: Path, ns: argparse.Namespace) -> list[Report]:
    """Return the reports of `status` or `check`, with the gate's verdicts `--verdicts` names.

    There is one report per change as main takes them (`change_steps`), so a push of several pull requests to main is
    judged as each of them was; `--main` judges the last one, whose head main holds.
    """
    base, head, tag = _sides(repo, ns)
    sides = {base.rev: base, head.rev: head}
    verdicts = load_verdicts(ns.verdicts) if ns.verdicts else {}
    steps = change_steps(repo, base.rev or "HEAD", head.rev)
    reports = []
    for number, (old, new) in enumerate(steps, 1):
        for rev in {old, new} - sides.keys():
            sides[rev] = Side(repo, rev)
        report = evaluate(repo, sides[old], sides[new], tag, main=getattr(ns, "main", False) and number == len(steps))
        report.verdicts = verdicts
        reports.append(report)
    return reports


def _judged_by_base(repo: Path, ns: argparse.Namespace) -> int | None:
    """Run `check` with the base's copy of this check when the change touches `COPY_PATHS`; None when it does not.

    The copy is told only what every version of `check` takes: the revisions, `--tag`, `--main`, `--report-only` and the
    verdicts. A base without this script has no copy to run.
    """
    base, head = _rev(repo, ns.base), ns.head and _rev(repo, ns.head)
    if not git(repo, "diff", "--name-only", base, *([head] if head else []), "--", *COPY_PATHS):
        return None
    try:
        git(repo, "cat-file", "-e", f"{base}:{SCRIPT}")
    except subprocess.CalledProcessError:
        return None
    archive = git(repo, "archive", "--format=tar", base, "--", *COPY_PATHS)
    arguments = ["--base", ns.base, *(["--head", ns.head] if head else []), *(["--tag", ns.tag] if ns.tag else [])]
    arguments += ["--main"] if ns.main else []
    arguments += ["--report-only"] if ns.report_only else []
    arguments += ["--verdicts", str(ns.verdicts)] if ns.verdicts else []
    print(f"judged by the copy of this check in {ns.base}: the change touches its files", flush=True)
    with tempfile.TemporaryDirectory() as directory:
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(directory, filter="data")
        command = [sys.executable, str(Path(directory) / SCRIPT), "check", *arguments]
        code = subprocess.run(command, cwd=repo, check=False).returncode
    print("judged by the head's own copy of this check", flush=True)
    return code


def main(argv: list[str] | None = None, repo: Path | None = None) -> int:
    """Script entrypoint."""
    ns = _parse_args(argv)
    repo = repo or Path.cwd()
    if getattr(ns, "ci", False) and ns.verdicts and not ns.verdicts.exists():
        ns.verdicts = None
    try:
        match ns.command:
            case "declare":
                return declare(repo, ns)
            case "prune":
                return prune(repo, ns)
        base_code = _judged_by_base(repo, ns) if getattr(ns, "ci", False) else None
        reports = _judged(repo, ns)
    except CheckError as error:
        print(f"{SCRIPT}: {error}", file=sys.stderr)
        return 2
    print("".join(map(format_report, reports)), end="")
    own_code = 1 if ns.command == "check" and any(report.failed for report in reports) and not ns.report_only else 0
    return base_code or own_code


if __name__ == "__main__":
    raise SystemExit(main())
