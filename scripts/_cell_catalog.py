"""The cells a change has to run: a catalog of the gate's cells with what each ran, and the pick of a change's cells.

The comparator's evidence (`scripts/compare_release.py`, `Evidence`) judges whether the compared cells ran each code
object a change touches (`scripts/_change_units.py`). This module chooses those cells; it never judges: a cell it
misses leaves the evidence incomplete, it never lets a change pass.

- **The catalog** (`Catalog`) holds cells (the gate's `behaviour_diff.Cell`: kind, input, input file type, output
  model type, options) and what each ran at one revision of the package, the catalog's: every line (`line` keys,
  path below the package and line), every code object that started (`code` keys, path and ``co_qualname``) and every
  package file opened outside the import system (`file` keys). The comparator's workers record them in their `lines`
  mode (`compare_release._Recorder`, Python 3.12+): what importing the CLI ran is the catalog's ``startup`` (every
  cell runs it), the worker imports the package's modules before its first cell, and each cell runs in a fork of the
  warm worker, so what it records depends on no cell run before it (what importing a module ran counts for each cell
  that uses the module). Each key maps to its set of cells; equal sets are stored once, each as the shorter of its
  cells and the cells it lacks.
- **The pick** (`pick`): each probe of a changed code object is one requirement (`requirements`); a unit counts once
  each of its probes ran in some compared cell, so covering every requirement is what the evidence asks for. A
  ``start`` probe needs the `code` key of its code object, an ``instr`` probe a `line` key of its span, a
  ``readers`` probe a `line` key of one of its readers, a ``read`` probe the `file` key; an ``unmeasurable`` probe
  needs what no run gives. A probe's lines are on its side's revision (what a change removes on the base, what it
  adds on the candidate) and translate to the catalog's (`FileMap`): an unchanged line by its offset, a line of a hunk
  to the catalog lines the hunk replaced, a line of a pure insertion to the catalog lines around it. A requirement is
  met at ``startup``, by catalog ``cells`` or by ``none``. The cells are chosen greedily (`cover`): each requirement
  gets ``redundancy`` cells where the catalog has that many, ties broken by the cost order of `KINDS` and a stable
  hash, so the choice is deterministic.

Usage:
  python -m scripts._cell_catalog build OUT [REVISION] --cache-dir DIR [--types T1,T2] [--options 'default;--x']
      [--select GLOB] [-j N]
  python -m scripts._cell_catalog pick CATALOG BASE [CANDIDATE] [--redundancy N] [--json OUT] --cache-dir DIR

`build` records the fixtures of this checkout (`behaviour_diff.fixture_inputs`) x types x option sets on a revision
(default: HEAD); `pick` maps the change from BASE (a revision) to the candidate (a revision, default: the working
tree) and prints the requirements and the cells it picks.
"""

from __future__ import annotations

import argparse
import contextlib
import fnmatch
import gzip
import hashlib
import heapq
import json
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from scripts import behaviour_diff, compare_release

if TYPE_CHECKING:
    from collections.abc import Iterable


Record = compare_release.Record
FORMAT = 1
PACKAGE_SOURCE = f"src/{compare_release.PACKAGE}/"
KINDS = ("default", "option-set", "preset", "cli-doc", "payload", "project")
"""The kinds of cells in the order the pick prefers them: the cheapest runs first."""
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
STATUSES = ("cells", "startup", "none", "unmeasurable")
"""How a requirement is met: by catalog cells, by what every cell runs, by no catalog cell, or by no run at all."""


Key = tuple[str, str, int | str]
"""A catalog key: its kind (`line`, `code`, `file`), the path below the package, and the line or the qualname."""


def keys_of(recorded: Iterable[Record]) -> set[Key]:
    """Return the keys of the records of a run's attempts (`compare_release._Recorder.keys`)."""
    found: set[Key] = set()
    for record in recorded:
        found.update(("line", path, line) for path, lines in record["line"].items() for line in lines)
        found.update(("code", path, name) for path, names in record["code"].items() for name in names)
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
    decoded: dict[int, frozenset[int]] = field(default_factory=dict)

    @classmethod
    def of(cls, revision: str, cells: list[CatalogCell], ran: list[set[Key] | None], startup: set[Key]) -> Catalog:
        """Return the catalog of cells and what each ran (None: it failed, so it can never be compared)."""
        holders: dict[Key, list[int]] = {}
        for index, keys in enumerate(ran):
            for key in keys or ():
                holders.setdefault(key, []).append(index)
        catalog = cls(revision, cells, startup, [index for index, keys in enumerate(ran) if keys is None])
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
        keys: Record = {"line": {}, "code": {}, "file": {}}
        for (kind, where, item), found in sorted(self.keys.items(), key=lambda pair: str(pair[0])):
            if kind == "file":
                keys["file"][where] = found
            else:
                keys[kind].setdefault(where, []).extend([item, found])
        data = {
            "format": FORMAT,
            "revision": self.revision,
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
        cells = [CatalogCell(**(row | {"options": tuple(row["options"])})) for row in data["cells"]]
        keys: dict[Key, int] = {("file", where, ""): found for where, found in data["keys"]["file"].items()}
        for kind in ("line", "code"):
            for where, pairs in data["keys"][kind].items():
                keys.update(((kind, where, item), found) for item, found in zip(pairs[::2], pairs[1::2], strict=True))
        startup = {(kind, where, item) for kind, where, item in data["startup"]}
        return cls(data["revision"], cells, startup, data["failed"], keys, data["sets"])


@dataclass(frozen=True)
class FileMap:
    """How the lines of a file on one revision lie on the catalog's: its catalog path and the hunks between them.

    Each hunk is ``git diff -U0``'s (catalog start, catalog count, side start, side count); `catalog` is None for a
    file the catalog lacks.
    """

    catalog: str | None
    hunks: tuple[tuple[int, int, int, int], ...] = ()

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


def file_maps(repo: Path, catalog: str, side: str | None) -> dict[str, FileMap]:
    """Return the maps of the package files that differ between the catalog's revision and a side's (None: the tree).

    Keyed by the side's path below the package; an unchanged file has none (its lines are the catalog's).
    """
    revisions = [catalog, *([side] if side else [])]
    diff = compare_release.git(repo, "diff", "-U0", "-M", "--no-color", "--no-ext-diff", *revisions, "--", "src")
    maps: dict[str, FileMap] = {}
    old: str | None = None
    current: tuple[str | None, str | None, list[tuple[int, int, int, int]]] | None = None
    for text in diff.stdout.splitlines():
        if text.startswith("rename from "):
            old = _below_package(text[12:], "")
        elif text.startswith("rename to ") and (renamed := _below_package(text[10:], "")) is not None:
            maps[renamed] = FileMap(old)
        elif text.startswith("--- "):
            old = _below_package(text[4:], "a/")
        elif text.startswith("+++ "):
            current = (old, _below_package(text[4:], "b/"), [])
            if current[1] is not None:
                maps[current[1]] = FileMap(old)
        elif (match := HUNK.match(text)) and current is not None and current[1] is not None:
            start, count, new, new_count = match.groups()
            current[2].append((int(start), int(count or 1), int(new), int(new_count or 1)))
            maps[current[1]] = FileMap(current[0], tuple(current[2]))
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


def translated(key: Key, maps: dict[str, FileMap]) -> list[Key]:
    """Return the catalog keys of a key on a side's revision (`FileMap`)."""
    kind, path, item = key
    if (found := maps.get(path)) is None:
        return [key]
    if found.catalog is None:
        return []
    if kind == "line":
        return [(kind, found.catalog, line) for line in found.lines(int(item))]
    return [(kind, found.catalog, item)]


def requirements(catalog: Catalog, units: list[Record], maps: dict[str, dict[str, FileMap]]) -> list[Requirement]:
    """Return a requirement per probe of the units, with how the catalog meets it; `maps` are each side's."""
    found = []
    for unit in units:
        for probe in unit["probes"]:
            requirement = Requirement(unit, probe)
            for key in probe_keys(probe):
                requirement.keys.update(translated(key, maps[unit["side"]]))
            requirement.cells = frozenset().union(*(catalog.cells_of(key) for key in requirement.keys))
            if probe["kind"] == "unmeasurable":
                requirement.status = "unmeasurable"
            elif requirement.keys & catalog.startup:
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
        text = [
            (
                f"pick: catalog {self.catalog.revision[:12]}, requirements {len(self.found)}: by cells "
                f"{counts['cells']}, at startup {counts['startup']}, no catalog cell {counts['none']}, unmeasurable "
                f"{counts['unmeasurable']}; cells picked {len(self.picked)}"
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
                    "picked": [self.catalog.cells[index].label for index in item.picked],
                }
                for item in self.found
            ],
        }


def pick(catalog: Catalog, units: list[Record], maps: dict[str, dict[str, FileMap]], redundancy: int = 2) -> Pick:
    """Return the cells of the catalog that run the changed code objects `units` (module docstring)."""
    found = requirements(catalog, units, maps)
    return Pick(catalog, found, cover(catalog, found, redundancy))


def pick_change(
    catalog: Catalog,
    repo: Path,
    units: compare_release.Units | None,
    sides: tuple[str, str | None],
    redundancy: int = 2,
) -> Pick:
    """Return the pick of a change's units (`compare_release.changed_units`) of the base and candidate revisions.

    `sides` are the base revision and the candidate's, None for the working tree of `repo`.
    """
    maps = {
        side: file_maps(repo, catalog.revision, revision)
        for side, revision in zip(("base", "head"), sides, strict=True)
    }
    return pick(catalog, units.units if units else [], maps, redundancy)


def record(runner: compare_release.Runner, cells: list[behaviour_diff.Cell], revision: str, jobs: int) -> Catalog:
    """Run each cell on the runner's side in the comparator's `lines` mode and return the catalog of what each ran."""
    runner.lines = True

    def one(cell: behaviour_diff.Cell) -> set[Key] | None:
        with tempfile.TemporaryDirectory() as tmp:
            outcome = runner.generate(cell.args(), Path(tmp))
        return keys_of(outcome.keys or []) if outcome.ok else None

    with ThreadPoolExecutor(jobs) as pool:
        ran = list(pool.map(one, cells))
    startup = next((worker.startup for worker in runner.workers.values() if worker.startup), None)
    runner.close()
    rows = [CatalogCell(cell.kind, cell.label, cell.file_type, cell.backend, cell.options) for cell in cells]
    return Catalog.of(revision, rows, ran, keys_of([startup]) if startup else set())


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="record the catalog of the fixtures on a revision")
    build.add_argument("out", type=Path, help="catalog file to write (gzip JSON)")
    build.add_argument("revision", nargs="?", default="HEAD", help="revision of this repository (default: HEAD)")
    build.add_argument("--types", default="pydantic_v2.BaseModel", help="comma-separated --output-model-type values")
    build.add_argument("--options", default="default", help="option sets separated by ';' ('default' = none)")
    build.add_argument("--select", action="append", default=[], help="only fixtures whose path matches this glob")
    build.add_argument("-j", type=int, default=2, help="parallel cells")
    build.add_argument("--timeout", type=int, default=300, help="seconds before one generation fails")
    build.add_argument("--cache-dir", type=Path, required=True, help="the comparator's cache (extracted revisions)")
    chosen = commands.add_parser("pick", help="print the catalog cells a change has to run")
    chosen.add_argument("catalog", type=Path, help="catalog file (`build`)")
    chosen.add_argument("base", help="base revision of the change")
    chosen.add_argument("candidate", nargs="?", help="candidate revision (default: the working tree)")
    chosen.add_argument("--redundancy", type=int, default=2, help="cells per requirement where the catalog has them")
    chosen.add_argument("--json", type=Path, help="write the pick as JSON")
    chosen.add_argument("--cache-dir", type=Path, required=True, help="the comparator's cache (extracted revisions)")
    return parser.parse_args(argv)


def _build(ns: argparse.Namespace) -> int:
    python = Path(sys.executable).absolute()
    base = compare_release.resolve_base(ns.revision, python, ns.cache_dir.absolute())
    revision = base.revision or ns.revision
    candidate = behaviour_diff.Candidate(
        compare_release.ROOT, python, compare_release.run_environment(compare_release.ROOT / "src"), None
    )
    inputs = behaviour_diff.fixture_inputs(candidate, revision)
    inputs = [item for item in inputs if not ns.select or any(fnmatch.fnmatch(item[0], glob) for glob in ns.select)]
    sets = [() if opts == "default" else tuple(opts.split()) for opts in (o.strip() for o in ns.options.split(";"))]
    kinds = ["default" if not options else "option-set" for options in sets]
    cells = [
        behaviour_diff.Cell(compare_release.ROOT / name, name, file_type, backend, options, kind)
        for options, kind in zip(sets, kinds, strict=True)
        for backend in ns.types.split(",")
        for name, file_type in inputs
    ]
    runner = compare_release.Runner(
        compare_release.Side("catalog", base.python, base.package), base.env(), import_check=False, timeout=ns.timeout
    )
    catalog = record(runner, cells, revision, ns.j)
    catalog.write(ns.out)
    counts = (len(cells), len(catalog.failed), len(catalog.keys), len(catalog.sets))
    print("catalog: {} cells, {} failed, {} keys, {} sets".format(*counts))
    return 0


def _pick(ns: argparse.Namespace) -> int:
    catalog = Catalog.read(ns.catalog)
    python = Path(sys.executable).absolute()
    cache_dir = ns.cache_dir.absolute()
    base = compare_release.resolve_base(ns.base, python, cache_dir)
    candidate = compare_release.commit_of(ns.candidate) if ns.candidate else None
    root = compare_release.ROOT
    head = compare_release.source_tree(candidate, cache_dir) / "src" if candidate else root / "src"
    units = compare_release.changed_units(python, root, base, candidate, head)
    chosen = pick_change(catalog, root, units, (base.revision or ns.base, candidate), ns.redundancy)
    print("\n".join(chosen.lines()))
    if ns.json:
        ns.json.write_text(json.dumps(chosen.data(), indent=1) + "\n", encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run `build` or `pick`."""
    ns = _parse_args(argv)
    with contextlib.suppress(BrokenPipeError):
        return _build(ns) if ns.command == "build" else _pick(ns)
    return 0


if __name__ == "__main__":
    sys.exit(main())
