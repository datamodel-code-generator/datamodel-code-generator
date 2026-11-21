"""The cells a change has to run: a catalog of the gate's cells with what each ran, and the pick of a change's cells.

The comparator's evidence (`scripts/compare_release.py`, `Evidence`) judges whether the compared cells ran each code
object a change touches (`scripts/_change_units.py`). This module chooses those cells; it never judges. The evidence
counts coverage, not equality: a cell the pick leaves out may run the changed line and differ, and a comparison of the
picked cells alone passes. So a pick only adds cells to the tier the gate runs (a replay's sweep, the PR sample); it
never stands in for them, and a run of the picked cells alone is not the gate's verdict. A cell it misses leaves the
evidence incomplete where no other compared cell ran the code.

- **The catalog** (`Catalog`) holds cells (the gate's `behaviour_diff.Cell`: kind, input, input file type, output
  model type, options) and what each ran at one revision of the package, the catalog's: every line (`line` keys,
  path below the package and line), every code object that started (`code` keys, path and ``co_qualname``) and every
  package file opened outside the import system (`file` keys). The comparator's workers record them in their `lines`
  mode (`compare_release._Recorder`, Python 3.12+): what importing the CLI ran is the catalog's ``startup`` (every
  cell runs it), the worker imports the package's modules before its first cell, and each cell runs in a fork of the
  warm worker, so what it records depends on no cell run before it (what importing a module ran counts for each cell
  that uses the module). Each key maps to its set of cells; equal sets are stored once, each as the shorter of its
  cells and the cells it lacks. On Python 3.14+ the catalog also holds keys finer than a line, for the version it
  names (``python``): the first position of each code object that started (`start` keys) and the instruction each
  branch taken led to (`arm` keys), which tell apart two code objects or two arms of one line.
- **The pick** (`pick`): each probe of a changed code object is one requirement (`requirements`); a unit counts once
  each of its probes ran in some compared cell, so covering every requirement is what the evidence asks for. A
  ``start`` probe needs the `code` key of its code object, an ``instr`` probe a `line` key of its span, a
  ``readers`` probe a `line` key of one of its readers, a ``read`` probe the `file` key; an ``unmeasurable`` probe
  needs what no run gives. A probe's lines are on its side's revision (what a change removes on the base, what it
  adds on the candidate) and translate to the catalog's (`FileMap`): an unchanged line by its offset, a line of a hunk
  to the catalog lines the hunk replaced, a line of a pure insertion to the catalog lines around it. Where the catalog
  has sub-line keys for the running interpreter's version, a probe of instructions needs one of the keys a run that
  ran them records on its way in (`compare_release.entry_places`, read off the side's code object, `Sources`), and a
  ``start`` probe the `start` key of its code object. Both kinds of keys hold for every run that ran the probe, so
  its cells are those of its line or code keys that also hold a sub-line key: the cells that run a line but not the
  probe's part of it drop out, and the pick is never wider than its line keys. That holds only where the text of
  each sub-line key is the catalog's (`FileMap.place`) and the key names one instruction or code object of the
  catalog's revision (`Sources.held`): an edit between them that drifts a key (a renamed function, a local turned
  into a closure's cell), or a place several instructions share (the operands of one `and` chain), leaves the probe
  its line or code keys alone. A requirement is met at ``startup``, by catalog ``cells`` or by ``none``. The cells
  are chosen greedily (`cover`): each requirement gets ``redundancy`` cells where the catalog has that many, ties
  broken by the cost order of `KINDS` and a stable hash, so the choice is deterministic.

Usage:
  python -m scripts._cell_catalog pick CATALOG BASE [CANDIDATE] [--redundancy N] [--json OUT] --cache-dir DIR

`pick` maps the change from BASE (a revision) to the candidate (a revision, default: the working tree) and prints the
requirements and the cells it picks. `scripts/behaviour_diff.py catalog` builds a catalog of the gate's cells
(`record`), and `gate --pick CATALOG` adds the picked cells to the cells it runs (`loaded`).
"""

from __future__ import annotations

import argparse
import contextlib
import dis
import gzip
import hashlib
import heapq
import json
import re
import shlex
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from types import CodeType
from typing import TYPE_CHECKING, NamedTuple, Protocol

from scripts import compare_release
from scripts._behaviour_classify import RunCell
from scripts._change_units import first_position

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


Record = compare_release.Record
FORMAT = 2
PACKAGE_SOURCE = f"src/{compare_release.PACKAGE}/"
KINDS = ("default", "option-set", "preset", "cli-doc", "payload", "project", "family")
"""The kinds of cells in the order the pick prefers them: the cheapest runs first."""
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
STATUSES = ("cells", "startup", "none", "unmeasurable")
"""How a requirement is met: by catalog cells, by what every cell runs, by no catalog cell, or by no run at all."""


Key = tuple[str, str, int | str]
"""A catalog key: its kind (`line`, `code`, `file`, `start`, `arm`), the path below the package, and the line, the
qualname or the place (`compare_release.instruction_place`, `compare_release.start_place`)."""
POSITIONED = ("start", "arm")
"""The kinds of sub-line keys, whose places hold lines (`compare_release.SUB_LINE`)."""


def keys_of(recorded: Iterable[Record]) -> set[Key]:
    """Return the keys of the records of a run's attempts (`compare_release._Recorder.keys`)."""
    found: set[Key] = set()
    for record in recorded:
        found.update(("line", path, line) for path, lines in record["line"].items() for line in lines)
        found.update(("code", path, name) for path, names in record["code"].items() for name in names)
        found.update(
            (kind, path, place)
            for kind in POSITIONED
            for path, places in record.get(kind, {}).items()
            for place in places
        )
        found.update(("file", path, "") for path in record["file"])
    return found


def encode(cells: Iterable[int], total: int) -> list[object]:
    """Return a set of cell indexes as the shorter of ``["+", deltas]`` (its cells) and ``["-", deltas]`` (the rest)."""
    members = sorted(set(cells))
    sign, chosen = "+", members
    if len(members) * 2 > total:
        held = set(members)
        sign, chosen = "-", [index for index in range(total) if index not in held]
    return [sign, *(index - previous for previous, index in zip([0, *chosen], chosen, strict=False))]


def decode(encoded: list[object], total: int) -> frozenset[int]:
    """Return the cell indexes of a set `encode` wrote."""
    sign, *deltas = encoded
    members, index = [], 0
    for delta in deltas:
        index += int(str(delta))
        members.append(index)
    return frozenset(members) if sign == "+" else frozenset(range(total)) - frozenset(members)


@dataclass(frozen=True)
class CatalogCell:
    """A cell of the catalog: the fields the gate names a cell by (`behaviour_diff.Cell`)."""

    kind: str
    label: str
    file_type: str
    backend: str
    options: tuple[str, ...] = ()

    @property
    def rank(self) -> tuple[int, str]:
        """The cost order of the pick: the kind (`KINDS`), then a stable hash of the cell."""
        kind = KINDS.index(self.kind) if self.kind in KINDS else len(KINDS)
        text = "\0".join((self.label, self.backend, *self.options))
        return kind, hashlib.sha256(text.encode()).hexdigest()

    def row(self) -> Record:
        """Return the cell as a JSON row."""
        return {
            "kind": self.kind,
            "label": self.label,
            "file_type": self.file_type,
            "backend": self.backend,
            "options": list(self.options),
        }


@dataclass
class Catalog:
    """The cells of a revision and the keys each ran (module docstring); `sets` stay encoded until a pick reads one."""

    revision: str
    cells: list[CatalogCell]
    startup: set[Key] = field(default_factory=set)
    failed: list[int] = field(default_factory=list)
    keys: dict[Key, int] = field(default_factory=dict)
    sets: list[list[object]] = field(default_factory=list)
    python: str | None = None
    decoded: dict[int, frozenset[int]] = field(default_factory=dict)

    @classmethod
    def of(
        cls,
        revision: str,
        cells: list[CatalogCell],
        ran: list[set[Key] | None],
        startup: set[Key],
        python: str | None = None,
    ) -> Catalog:
        """Return the catalog of cells and what each ran (None: it failed, so it can never be compared).

        `python` is the version whose sub-line keys the records hold, None for records of lines only.
        """
        holders: dict[Key, list[int]] = {}
        for index, keys in enumerate(ran):
            for key in keys or ():
                holders.setdefault(key, []).append(index)
        failed = [index for index, keys in enumerate(ran) if keys is None]
        catalog = cls(revision, cells, startup, failed, python=python)
        ids: dict[tuple[int, ...], int] = {}
        for key in sorted(holders, key=str):
            members = tuple(holders[key])
            if (found := ids.get(members)) is None:
                found = ids[members] = len(catalog.sets)
                catalog.sets.append(encode(members, len(cells)))
            catalog.keys[key] = found
        return catalog

    def cells_of(self, key: Key) -> frozenset[int]:
        """Return the cells that ran a key."""
        if (found := self.keys.get(key)) is None:
            return frozenset()
        if found not in self.decoded:
            self.decoded[found] = decode(self.sets[found], len(self.cells))
        return self.decoded[found]

    def write(self, path: Path) -> None:
        """Write the catalog as gzip JSON."""
        keys: Record = {"line": {}, "code": {}, **{kind: {} for kind in POSITIONED}, "file": {}}
        for (kind, where, item), found in sorted(self.keys.items(), key=lambda pair: str(pair[0])):
            if kind == "file":
                keys["file"][where] = found
            else:
                keys[kind].setdefault(where, []).extend([item, found])
        data = {
            "format": FORMAT,
            "revision": self.revision,
            "python": self.python,
            "cells": [cell.row() for cell in self.cells],
            "failed": self.failed,
            "startup": sorted([list(key) for key in self.startup], key=str),
            "sets": self.sets,
            "keys": keys,
        }
        with gzip.open(path, "wt", encoding="utf-8") as file:
            json.dump(data, file, separators=(",", ":"))

    @classmethod
    def read(cls, path: Path) -> Catalog:
        """Read a catalog `write` wrote."""
        with gzip.open(path, "rt", encoding="utf-8") as file:
            data = json.load(file)
        if data.get("format") != FORMAT:
            msg = f"{path}: catalog format {data.get('format')}, this script reads {FORMAT}"
            raise SystemExit(msg)
        return cls.of_data(data)

    @classmethod
    def of_data(cls, data: Record) -> Catalog:
        """Return the catalog of the JSON data `write` wrote."""
        cells = [CatalogCell(**(row | {"options": tuple(row["options"])})) for row in data["cells"]]
        keys: dict[Key, int] = {("file", where, ""): found for where, found in data["keys"]["file"].items()}
        for kind in ("line", "code", *POSITIONED):
            for where, pairs in data["keys"][kind].items():
                keys.update(((kind, where, item), found) for item, found in zip(pairs[::2], pairs[1::2], strict=True))
        startup = {(kind, where, item) for kind, where, item in data["startup"]}
        return cls(data["revision"], cells, startup, data["failed"], keys, data["sets"], data["python"])


@dataclass(frozen=True)
class FileMap:
    """How the lines of a file on one revision lie on the catalog's: its catalog path and the hunks between them.

    Each hunk is ``git diff -U0``'s (catalog start, catalog count, side start, side count); `catalog` is None for a
    file the catalog lacks. `replaced` holds the side line, the catalog's text and the side's of each line a hunk
    replaces one for one.
    """

    catalog: str | None
    hunks: tuple[tuple[int, int, int, int], ...] = ()
    replaced: tuple[tuple[int, str, str], ...] = ()

    def lines(self, line: int) -> list[int]:
        """Return the catalog lines of a side's line: by its offset, or the lines its hunk replaced or surrounds."""
        if self.catalog is None:
            return []
        shift = 0
        for old, old_count, new, new_count in self.hunks:
            if line < (new if new_count else new + 1):
                return [line + shift]
            if line < new + new_count:
                return list(range(old, old + old_count)) if old_count else [at for at in (old, old + 1) if at > 0]
            shift = (old + old_count if old_count else old + 1) - (new + new_count if new_count else new + 1)
        return [line + shift]

    def place(self, line: int, upto: int | None = None) -> int | None:
        """Return the catalog line of a side's line where its text is the catalog's, up to byte `upto` if given.

        A line outside every hunk is the catalog's whole; a line a hunk replaces one for one is the catalog line it is
        paired with up to where their texts first differ. None elsewhere, or where the text up to `upto` differs.
        """
        if self.catalog is None:
            return None
        hunk = next((hunk for hunk in self.hunks if hunk[2] <= line < hunk[2] + hunk[3]), None)
        if hunk is None:
            return self.lines(line)[0]
        before, after = next(((old, new) for at, old, new in self.replaced if at == line), ("", None))
        if after is None or upto is None:
            return None
        first, second = before.encode(), after.encode()
        same = next((at for at, pair in enumerate(zip(first, second, strict=False)) if pair[0] != pair[1]), len(first))
        return hunk[0] + line - hunk[2] if upto <= same else None


def file_maps(repo: Path, catalog: str, side: str | None) -> dict[str, FileMap]:
    """Return the maps of the package files that differ between the catalog's revision and a side's (None: the tree).

    Keyed by the side's path below the package; an unchanged file has none (its lines are the catalog's).
    """
    revisions = [catalog, *([side] if side else [])]
    diff = compare_release.git(repo, "diff", "-U0", "-M", "--no-color", "--no-ext-diff", *revisions, "--", "src")
    if diff.returncode:  # a revision the checkout lacks (a shallow clone) prints nothing: that is no unchanged file
        msg = f"git diff {' '.join(revisions)} failed ({diff.returncode}): {diff.stderr.strip()}"
        raise SystemExit(msg)
    maps: dict[str, FileMap] = {}
    old: str | None = None
    current: tuple[str | None, str | None, list[tuple[int, int, int, int]]] | None = None
    removed: list[str] = []
    added: list[str] = []
    replaced: list[tuple[int, str, str]] = []
    pending = 0
    for text in diff.stdout.splitlines():
        if pending and text[:1] in {"-", "+"}:
            (removed if text[0] == "-" else added).append(text[1:])
            pending -= 1
            if not pending and current is not None and current[1] is not None and len(removed) == len(added):
                start = current[2][-1][2]
                replaced += [(start + index, *pair) for index, pair in enumerate(zip(removed, added, strict=True))]
                maps[current[1]] = FileMap(current[0], tuple(current[2]), tuple(replaced))
        elif text.startswith("rename from "):
            old = _below_package(text[12:], "")
        elif text.startswith("rename to ") and (renamed := _below_package(text[10:], "")) is not None:
            maps[renamed] = FileMap(old)
        elif text.startswith("--- "):
            old = _below_package(text[4:], "a/")
        elif text.startswith("+++ "):
            current, replaced = (old, _below_package(text[4:], "b/"), []), []
            if current[1] is not None:
                maps[current[1]] = FileMap(old)
        elif (match := HUNK.match(text)) and current is not None:
            start, count, new, new_count = match.groups()
            current[2].append((int(start), int(count or 1), int(new), int(new_count or 1)))
            removed, added, pending = [], [], current[2][-1][1] + current[2][-1][3]
            if current[1] is not None:
                maps[current[1]] = FileMap(current[0], tuple(current[2]), tuple(replaced))
    return maps


def _below_package(name: str, prefix: str) -> str | None:
    """Return a diff header's path below the package, None for /dev/null or a path outside it."""
    path = name.removeprefix(prefix)
    return path.removeprefix(PACKAGE_SOURCE) if path.startswith(PACKAGE_SOURCE) else None


@dataclass
class Requirement:
    """A probe of a changed code object, the catalog keys that meet it, how it is met and the cells picked for it."""

    unit: Record
    probe: Record
    keys: set[Key] = field(default_factory=set)
    status: str = "none"
    cells: frozenset[int] = frozenset()
    picked: list[int] = field(default_factory=list)
    sub_line: bool = False


def _span_lines(where: str) -> list[int]:
    return sorted(compare_release.lines_of(where))


def probe_keys(probe: Record) -> list[Key]:
    """Return the keys of a probe on its own side's revision (module docstring); none for an unmeasurable one."""
    match probe["kind"]:
        case "start":
            return [("code", probe["code"][0], probe["code"][1])]
        case "instr":
            return [("line", probe["code"][0], line) for line in _span_lines(probe["where"])]
        case "readers":
            found: list[Key] = []
            for reader in probe["readers"]:
                path, _, span = str(reader["where"]).partition(" ")
                found += [("line", path, line) for line in _span_lines(span)]
            return found
        case "read":
            return [("file", probe["file"], "")]
    return []


class Sources:
    """The code objects of each side's package and of the catalog's revision, compiled once per file.

    `roots` are each side's ``src`` directory; the catalog's files are read from `repo` at the catalog's `revision`.
    A probe's ``code`` is the path below the package, ``co_qualname``, ``co_firstlineno`` and the first position
    (`_change_units.Probe`); a file is compiled as the comparator's mapping compiles it.
    """

    def __init__(self, roots: dict[str, Path], repo: Path, revision: str) -> None:
        """Find the code objects of the sides below `roots` and of the catalog's `revision` of `repo`."""
        self.roots = roots
        self.repo = repo
        self.revision = revision
        self.found: dict[tuple[str, str], dict[tuple[object, ...], CodeType]] = {}
        self.counted: dict[str, Counter[Key]] = {}

    def _codes(self, side: str, path: str) -> dict[tuple[object, ...], CodeType]:
        if (side, path) not in self.found:
            text = ""
            if side == "catalog":
                shown = compare_release.git(self.repo, "show", f"{self.revision}:{PACKAGE_SOURCE}{path}")
                text = shown.stdout if not shown.returncode else ""
            else:
                with contextlib.suppress(OSError, ValueError):
                    text = (self.roots[side] / compare_release.PACKAGE / path).read_text(encoding="utf-8")
            codes: dict[tuple[object, ...], CodeType] = {}
            with contextlib.suppress(SyntaxError, ValueError):
                pending = [compile(text, path, "exec", dont_inherit=True)]
                while pending:
                    code = pending.pop()
                    codes[(path, code.co_qualname, code.co_firstlineno, *first_position(code))] = code
                    pending += [item for item in code.co_consts if isinstance(item, CodeType)]
            self.found[side, path] = codes
        return self.found[side, path]

    def code(self, side: str, key: list[object]) -> CodeType | None:
        """Return the code object of a probe's key on a side, None where the side's file has none."""
        return self._codes(side, str(key[0])).get(tuple(key))

    def held(self, key: Key) -> int:
        """Return how many instructions or code objects of the catalog's revision have a sub-line key's place.

        1 where the key names one; 0 where an edit between the catalog and the side drifted it (a renamed function, a
        local turned into a closure's cell), more where it is shared (the operands of one `and` chain).
        """
        kind, path, _ = key
        if path not in self.counted:
            counted: Counter[Key] = Counter()
            for code in self._codes("catalog", path).values():
                counted["start", path, compare_release.start_place(code)] += 1
                counted.update(
                    ("arm", path, place)
                    for item in dis.get_instructions(code)
                    if (place := compare_release.instruction_place(code, item.offset)) is not None
                )
            self.counted[path] = counted
        return self.counted[path][key] if kind in POSITIONED else 0


def sub_line_keys(probe: Record, side: str, sources: Sources) -> list[Key] | None:
    """Return the sub-line keys of a probe on its side's revision, one of which a run that meets it records.

    A ``start`` probe needs its code object's `start` key, an ``instr`` probe or a reader one of the keys of a run's
    way into one of its instructions (`compare_release.entry_places`). None where an instruction has no such keys.
    """
    match probe["kind"]:
        case "start":
            path, qualname, _, line, column = probe["code"]
            return [("start", path, f"{line}:{column} {qualname}")]
        case "instr" | "readers":
            members = probe["readers"] if probe["kind"] == "readers" else [probe]
        case _:
            return None
    found: list[Key] = []
    for member in members:
        if (code := sources.code(side, member["code"])) is None:
            return None
        for offset in member["offsets"]:
            if (places := compare_release.entry_places(code, offset)) is None:
                return None
            found += [(kind, member["code"][0], place) for kind, place in places]
    return found


def translated(key: Key, maps: dict[str, FileMap]) -> list[Key]:
    """Return the catalog keys of a key on a side's revision (`FileMap`).

    A sub-line key's place translates only where its text is the catalog's (`FileMap.place`: up to where a span on
    one line ends, a start starts; a span over lines needs its lines whole), so its columns hold; else it has none.
    """
    kind, path, item = key
    if (found := maps.get(path)) is None:
        return [key]
    if found.catalog is None:
        return []
    if kind == "line":
        return [(kind, found.catalog, line) for line in found.lines(int(item))]
    if kind in POSITIONED:
        span, _, name = str(item).partition(" ")
        ends = [tuple(map(int, end.split(":"))) for end in span.split("-")]
        one_line = len({line for line, _ in ends}) == 1
        moved: list[str] = []
        for line, column in ends:
            if (at := found.place(line, ends[-1][1] if one_line else None)) is None:
                return []
            moved.append(f"{at}:{column}")
        return [(kind, found.catalog, " ".join(["-".join(moved), *([name] if name else [])]))]
    return [(kind, found.catalog, item)]


def _translated_all(keys: list[Key], maps: dict[str, FileMap]) -> set[Key] | None:
    """Return the catalog keys of sub-line keys, None where one of them has none (its text changed)."""
    found: set[Key] = set()
    for key in keys:
        if not (moved := translated(key, maps)):
            return None
        found.update(moved)
    return found


def requirements(
    catalog: Catalog, units: list[Record], maps: dict[str, dict[str, FileMap]], sources: Sources | None = None
) -> list[Requirement]:
    """Return a requirement per probe of the units, with how the catalog meets it; `maps` are each side's.

    With `sources` and a catalog of the running interpreter's sub-line keys, a probe's cells are those its line or
    code keys give that also hold one of its sub-line keys, where each sub-line key translates and names one
    instruction or code object of the catalog's revision (module docstring); else its line or code keys alone.
    """
    version = "{}.{}".format(*sys.version_info[:2])
    finer = sources is not None and catalog.python == version
    found = []
    for unit in units:
        for probe in unit["probes"]:
            requirement = Requirement(unit, probe)
            for key in probe_keys(probe):
                requirement.keys.update(translated(key, maps[unit["side"]]))
            requirement.cells = frozenset().union(*(catalog.cells_of(key) for key in requirement.keys))
            sub_line = sub_line_keys(probe, unit["side"], sources) if finer and sources is not None else None
            moved = _translated_all(sub_line, maps[unit["side"]]) if sub_line is not None else None
            startup = bool(requirement.keys & catalog.startup)
            if moved and sources is not None and all(sources.held(key) == 1 for key in moved):
                finer_cells = frozenset().union(*(catalog.cells_of(key) for key in moved))
                requirement.keys, requirement.sub_line = requirement.keys | moved, True
                requirement.cells &= finer_cells
                startup = startup and bool(moved & catalog.startup)
            if probe["kind"] == "unmeasurable":
                requirement.status = "unmeasurable"
            elif startup:
                requirement.status = "startup"
            elif requirement.cells:
                requirement.status = "cells"
            found.append(requirement)
    return found


def cover(catalog: Catalog, found: list[Requirement], redundancy: int) -> list[int]:
    """Pick cells greedily until each requirement met by cells has `redundancy` of its cells (or all it has).

    A pick takes the cell that most requirements still need, the cheaper one first (`CatalogCell.rank`).
    """
    open_ = [requirement for requirement in found if requirement.status == "cells"]
    need = [min(redundancy, len(requirement.cells)) for requirement in open_]
    holders: dict[int, list[int]] = {}
    for index, requirement in enumerate(open_):
        for cell in requirement.cells:
            holders.setdefault(cell, []).append(index)

    def gain(cell: int) -> int:
        return sum(need[index] > 0 for index in holders[cell])

    heap = [(-gain(cell), catalog.cells[cell].rank, cell) for cell in holders]
    heapq.heapify(heap)
    picked: list[int] = []
    while heap and any(need):
        stored, rank, cell = heapq.heappop(heap)
        if (now := gain(cell)) != -stored:
            if now:
                heapq.heappush(heap, (-now, rank, cell))
            continue
        picked.append(cell)
        for index in holders[cell]:
            if need[index] > 0:
                need[index] -= 1
                open_[index].picked.append(cell)
    return picked


@dataclass
class Pick:
    """The cells a change has to run (`pick`) and each requirement with how it is met."""

    catalog: Catalog
    found: list[Requirement]
    picked: list[int]

    def lines(self) -> list[str]:
        """Return the pick as text: the counts, the cells, and the requirements no catalog cell meets."""
        counts = {status: sum(item.status == status for item in self.found) for status in STATUSES}
        finer = sum(item.sub_line and item.status == "cells" for item in self.found)
        text = [
            (
                f"pick: catalog {self.catalog.revision[:12]}, requirements {len(self.found)}: by cells "
                f"{counts['cells']} ({finer} by sub-line keys), at startup {counts['startup']}, no catalog cell "
                f"{counts['none']}, unmeasurable {counts['unmeasurable']}; cells picked {len(self.picked)}"
            )
        ]
        for index in self.picked:
            cell = self.catalog.cells[index]
            text.append(f"pick: cell {cell.kind} {cell.label} | {cell.backend} | {' '.join(cell.options) or 'default'}")
        for item in self.found:
            if item.status in {"none", "unmeasurable"}:
                unit, probe = item.unit, item.probe
                label = "no catalog cell" if item.status == "none" else "unmeasurable"
                text.append(
                    f"pick: {label}: {unit['side']} {unit['path']} {unit['qualname']}: {probe['kind']} {probe['where']}"
                )
        return text

    def data(self) -> Record:
        """Return the pick as JSON: the picked cells (gate rows) and each requirement."""
        return {
            "catalog": self.catalog.revision,
            "cells": [self.catalog.cells[index].row() for index in self.picked],
            "requirements": [
                {
                    "side": item.unit["side"],
                    "path": item.unit["path"],
                    "qualname": item.unit["qualname"],
                    "kind": item.probe["kind"],
                    "where": item.probe["where"],
                    "status": item.status,
                    "keys": "sub-line" if item.sub_line else "line",
                    "picked": [self.catalog.cells[index].label for index in item.picked],
                }
                for item in self.found
            ],
        }


def pick(
    catalog: Catalog,
    units: list[Record],
    maps: dict[str, dict[str, FileMap]],
    redundancy: int = 2,
    sources: Sources | None = None,
) -> Pick:
    """Return the cells of the catalog that run the changed code objects `units` (module docstring)."""
    found = requirements(catalog, units, maps, sources)
    return Pick(catalog, found, cover(catalog, found, redundancy))


class ChangeSide(NamedTuple):
    """A side of a change: its revision (None: the working tree) and the ``src`` its units were mapped from."""

    revision: str | None
    src: Path | None = None


def pick_change(
    catalog: Catalog,
    repo: Path,
    units: compare_release.Units | None,
    sides: tuple[ChangeSide, ChangeSide],
    redundancy: int = 2,
) -> Pick:
    """Return the pick of a change's units (`compare_release.changed_units`) of the base and candidate sides.

    The sides' revisions are of `repo`; with the ``src`` of both, the pick reads their code objects (`Sources`), else
    it keys lines only. A release base has no change units (`units` is None): there is nothing to pick, the tier runs.
    """
    if units is None:
        msg = "pick: a release base has no change units: run the tier"
        raise SystemExit(msg)
    names = ("base", "head")
    maps = {name: file_maps(repo, catalog.revision, side.revision) for name, side in zip(names, sides, strict=True)}
    roots = {name: side.src for name, side in zip(names, sides, strict=True) if side.src is not None}
    sources = Sources(roots, repo, catalog.revision) if len(roots) == len(names) else None
    return pick(catalog, units.units, maps, redundancy, sources)


class GateCell(RunCell, Protocol):
    """A cell of the gate (`behaviour_diff.Cell`): what a catalog row names and the arguments of its generation."""

    @property
    def kind(self) -> str:
        """The kind of the cell (`KINDS`)."""

    def args(self) -> list[str]:
        """Return the generation arguments without `--output`."""


def record(runner: compare_release.Runner, cells: Sequence[GateCell], revision: str, jobs: int) -> Catalog:
    """Run each cell on the runner's side in the comparator's `lines` mode and return the catalog of what each ran."""
    runner.lines = True

    def one(cell: GateCell) -> set[Key] | None:
        with tempfile.TemporaryDirectory() as tmp:
            outcome = runner.generate(cell.args(), Path(tmp))
        return keys_of(outcome.keys or []) if outcome.ok else None

    with ThreadPoolExecutor(jobs) as pool:
        ran = list(pool.map(one, cells))
    startup = next((worker.startup for worker in runner.workers.values() if worker.startup), None)
    runner.close()
    rows = [CatalogCell(cell.kind, cell.label, cell.file_type, cell.backend, cell.options) for cell in cells]
    python = startup.get("python") if startup else None
    return Catalog.of(revision, rows, ran, keys_of([startup]) if startup else set(), python)


def option_sets(text: str) -> list[tuple[str, ...]]:
    """Return the option sets of `--options`: separated by ';', each split like a shell would ('default' is none)."""
    parts = (part.strip() for part in text.split(";"))
    return [() if part == "default" else tuple(shlex.split(part)) for part in parts]


def loaded(path: Path, repo: Path, base: str | None) -> tuple[Catalog | None, str]:
    """Return the catalog a gate against `base` picks from, or None and why the gate runs its tier alone.

    A release base (None) has no change units; the file may be missing, unreadable (cut short, not gzip JSON) or of
    another format; a catalog whose revision is no ancestor of the base (or that the checkout lacks) maps the change
    through lines it cannot follow.
    """
    if base is None:
        return None, "a release base has no change units"
    if not path.is_file():
        return None, f"no catalog at {path}"
    try:
        with gzip.open(path, "rt", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, EOFError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return None, f"{path}: unreadable catalog ({type(error).__name__})"
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        found = data.get("format") if isinstance(data, dict) else None
        return None, f"{path}: catalog format {found}, this gate reads {FORMAT}"
    if compare_release.git(repo, "merge-base", "--is-ancestor", data["revision"], base).returncode:
        return None, f"catalog {data['revision'][:12]} is no ancestor of the base {base[:12]}"
    return Catalog.of_data(data), ""


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    chosen = commands.add_parser("pick", help="print the catalog cells a change has to run")
    chosen.add_argument("catalog", type=Path, help="catalog file (`behaviour_diff.py catalog`)")
    chosen.add_argument("base", help="base revision of the change")
    chosen.add_argument("candidate", nargs="?", help="candidate revision (default: the working tree)")
    chosen.add_argument("--redundancy", type=int, default=2, help="cells per requirement where the catalog has them")
    chosen.add_argument("--json", type=Path, help="write the pick as JSON")
    chosen.add_argument("--cache-dir", type=Path, required=True, help="the comparator's cache (extracted revisions)")
    return parser.parse_args(argv)


def _pick(ns: argparse.Namespace) -> int:
    catalog = Catalog.read(ns.catalog)
    python = Path(sys.executable).absolute()
    cache_dir = ns.cache_dir.absolute()
    base = compare_release.resolve_base(ns.base, python, cache_dir)
    candidate = compare_release.commit_of(ns.candidate) if ns.candidate else None
    root = compare_release.ROOT
    head = compare_release.source_tree(candidate, cache_dir) / "src" if candidate else root / "src"
    units = compare_release.changed_units(python, root, base, candidate, head)
    sides = (ChangeSide(base.revision or ns.base, base.source), ChangeSide(candidate, head))
    chosen = pick_change(catalog, root, units, sides, ns.redundancy)
    print("\n".join(chosen.lines()))
    if ns.json:
        ns.json.write_text(json.dumps(chosen.data(), indent=1) + "\n", encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run `pick`."""
    ns = _parse_args(argv)
    with contextlib.suppress(BrokenPipeError):
        return _pick(ns)
    return 0


if __name__ == "__main__":
    sys.exit(main())
