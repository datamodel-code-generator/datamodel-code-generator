"""Print the review items a diff triggers: the written rules of the repository that no check enforces.

usage: python scripts/review_checklist.py [--base REV] [--head REV] [--table PATH]

The items are the rows of ``scripts/review_checklist.toml``: each one is a rule that AGENTS.md or a page of ``docs/``
states and no check, test or hook enforces, with the words that state it, the question a reviewer asks and the public
pull requests behind it, and triggers (paths, and patterns of added lines or of removed lines that the same hunk
replaces). The diff runs from the merge base of ``--base`` (default ``origin/main``) and HEAD to the working tree,
untracked files included, or to ``--head`` when one is given. The script prints the items the diff triggers, at most
ten in the order of the table, each with the first lines that triggered it, and exits with 0. It exits with 2 when the
table is not TOML or invalid: every item states its rule in words its file holds, names two pull requests and has a
trigger.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import tomllib

if TYPE_CHECKING:
    from collections.abc import Iterator

TABLE = Path(__file__).with_name("review_checklist.toml")
MAX_ITEMS = 10
MIN_PULLS = 2
SHOWN_LINES = 3
TEXT_WIDTH = 100
FIELDS = frozenset({"id", "title", "rule", "words", "question", "pulls", "trigger"})
HUNK = re.compile(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass(frozen=True)
class Line:
    """An added line, or a removed one (``replaced`` when its hunk also adds lines), of a changed file."""

    path: str
    number: int
    text: str
    added: bool
    replaced: bool = False


@dataclass(frozen=True)
class Trigger:
    """The paths a trigger watches and the patterns its added or replaced lines match."""

    paths: tuple[re.Pattern[str], ...]
    added: tuple[re.Pattern[str], ...]
    removed: tuple[re.Pattern[str], ...]

    def fires(self, line: Line) -> bool:
        """Return whether ``line`` triggers: a watched path, and a matching line (any added one without patterns)."""
        if not any(path.fullmatch(line.path) for path in self.paths):
            return False
        if line.added:
            return any(pattern.search(line.text) for pattern in self.added) or not (self.added or self.removed)
        return line.replaced and any(pattern.search(line.text) for pattern in self.removed)


@dataclass(frozen=True)
class Item:
    """A written rule no check enforces, with its question, the pull requests behind it and its triggers."""

    id: str
    title: str
    rule: str
    words: str
    question: str
    pulls: tuple[int, ...]
    triggers: tuple[Trigger, ...]


def _glob(pattern: str) -> re.Pattern[str]:
    """Compile a path glob: ``**/`` any directories, ``**`` anything, ``*`` and ``?`` within one path segment."""
    parts = re.split(r"(\*\*/|\*\*|\*|\?)", pattern)
    wildcards = {"**/": "(?:.*/)?", "**": ".*", "*": "[^/]*", "?": "[^/]"}
    return re.compile("".join(wildcards.get(part, re.escape(part)) for part in parts))


def _words(text: str) -> str:
    return " ".join(text.split())


def _pattern(item: str, value: str, problems: list[str]) -> re.Pattern[str] | None:
    try:
        return re.compile(value)
    except re.error as error:
        problems.append(f"{item}: pattern {value!r} does not compile: {error}")
        return None


def _patterns(item: str, values: list[str], problems: list[str]) -> tuple[re.Pattern[str], ...]:
    return tuple(pattern for value in values if (pattern := _pattern(item, value, problems)) is not None)


def _is_list_of(value: object, kind: type) -> bool:
    return isinstance(value, list) and all(
        isinstance(element, kind) and not isinstance(element, bool) for element in value
    )


def _shape_problem(entry: dict) -> str | None:
    """Return what is wrong with the types of an item's fields, or ``None`` when every field has its type."""
    for field in ("id", "title", "rule", "words", "question"):
        if not isinstance(entry[field], str):
            return f"{field} must be a string"
    if not _is_list_of(entry["pulls"], int):
        return "pulls must be a list of integers"
    if not _is_list_of(entry["trigger"], dict):
        return "trigger must be a list of tables"
    for trigger in entry["trigger"]:
        if not _is_list_of(trigger.get("paths"), str):
            return "a trigger's paths must be a list of strings"
        for field in ("added", "removed"):
            if not _is_list_of(trigger.get(field, []), str):
                return f"a trigger's {field} must be a list of strings"
    return None


def load_items(table: Path, root: Path) -> tuple[list[Item], list[str]]:
    """Read the items of ``table`` and the problems that make it invalid, reading each rule's file below ``root``."""
    items: list[Item] = []
    problems: list[str] = []
    try:
        entries = tomllib.loads(table.read_text(encoding="utf-8")).get("item", [])
    except ValueError as error:
        return items, [f"{table.name} is not valid TOML: {error}"]
    if not _is_list_of(entries, dict):
        return items, [f"{table.name}: item must be an array of tables"]
    for entry in entries:
        name = entry["id"] if isinstance(entry.get("id"), str) else "an item without an id"
        if missing := sorted(FIELDS - entry.keys()):
            problems.append(f"{name}: missing {', '.join(missing)}")
            continue
        if shape := _shape_problem(entry):
            problems.append(f"{name}: {shape}")
            continue
        source = root / entry["rule"]
        if _words(entry["words"]) not in _words(source.read_text(encoding="utf-8") if source.is_file() else ""):
            problems.append(f"{name}: {entry['rule']} does not hold the words {entry['words']!r}")
        if len(entry["pulls"]) < MIN_PULLS:
            problems.append(f"{name}: names {len(entry['pulls'])} pull request(s), an item needs {MIN_PULLS}")
        if not entry["trigger"]:
            problems.append(f"{name}: has no trigger")
        triggers = tuple(
            Trigger(
                tuple(_glob(path) for path in trigger["paths"]),
                _patterns(name, trigger.get("added", []), problems),
                _patterns(name, trigger.get("removed", []), problems),
            )
            for trigger in entry["trigger"]
        )
        items.append(
            Item(
                name, entry["title"], entry["rule"], entry["words"], entry["question"], tuple(entry["pulls"]), triggers
            )
        )
    return items, problems


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=root, capture_output=True, check=False)
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        msg = f"git {' '.join(args)} failed: {message}"
        raise SystemExit(msg)
    return result.stdout.decode("utf-8", errors="replace")


def _hunk(path: str, removed: list[tuple[int, str]], added: list[tuple[int, str]]) -> Iterator[Line]:
    yield from (Line(path, number, text, added=False, replaced=bool(added)) for number, text in removed)
    yield from (Line(path, number, text, added=True) for number, text in added)


def parse_diff(diff: str) -> Iterator[Line]:
    """Yield the added and removed lines of a ``git diff -U0`` patch, numbered in the new and the old file."""
    path, old, new = "", 0, 0
    removed: list[tuple[int, str]] = []
    added: list[tuple[int, str]] = []
    in_hunk = False
    for raw in diff.splitlines():
        if raw.startswith("diff --git "):
            yield from _hunk(path, removed, added)
            removed, added, in_hunk = [], [], False
        elif not in_hunk and raw.startswith(("--- a/", "+++ b/")):
            path = raw[6:].rstrip("\t")
        elif match := HUNK.match(raw):
            yield from _hunk(path, removed, added)
            old, new, removed, added, in_hunk = int(match[1]), int(match[2]), [], [], True
        elif in_hunk and raw.startswith("-"):
            removed.append((old + len(removed), raw[1:]))
        elif in_hunk and raw.startswith("+"):
            added.append((new + len(added), raw[1:]))
    yield from _hunk(path, removed, added)


def diff_lines(root: Path, base: str, head: str | None) -> list[Line]:
    """Return the changed lines from the merge base of ``base`` and ``head`` (or HEAD) to ``head`` or the work tree."""
    fork = _git(root, "merge-base", base, head or "HEAD").strip()
    patch = ["diff", "--no-color", "--no-ext-diff", "--no-textconv", "-U0", "-M", "--src-prefix=a/", "--dst-prefix=b/"]
    lines = list(parse_diff(_git(root, *patch, fork, *([head] if head else []))))
    if head is None:
        for name in filter(None, _git(root, "ls-files", "-z", "--others", "--exclude-standard").split("\0")):
            path = root / name
            raw = str(path.readlink()).encode() if path.is_symlink() else path.read_bytes()
            text = raw.decode("utf-8", errors="replace")
            lines += [Line(name, number, line, added=True) for number, line in enumerate(text.splitlines(), 1)]
    return lines


def _site(line: Line) -> str:
    text = line.text.strip()
    shown = text if len(text) <= TEXT_WIDTH else f"{text[: TEXT_WIDTH - 3]}..."
    return f"{line.path}:{line.number} {'adds' if line.added else 'replaces'} `{shown}`"


def report(items: list[Item], lines: list[Line], against: str) -> list[str]:
    """Return the report of the items ``lines`` trigger, at most ``MAX_ITEMS`` in table order."""
    triggered = [
        (item, sites)
        for item in items
        if (sites := [line for line in lines if any(trigger.fires(line) for trigger in item.triggers)])
    ]
    triggered = [(item, sorted(sites, key=lambda line: line.added)) for item, sites in triggered]
    if not triggered:
        return [f"Review checklist against {against}: the diff triggers none of the {len(items)} items."]
    output = [f"Review checklist against {against}: {len(triggered)} of {len(items)} items."]
    for index, (item, sites) in enumerate(triggered[:MAX_ITEMS], 1):
        more = f" (and {len(sites) - SHOWN_LINES} more lines)" if len(sites) > SHOWN_LINES else ""
        output += [
            "",
            f"{index}. {item.title} [{item.id}]",
            f"   Rule ({item.rule}): {_words(item.words)}",
            f"   Ask: {_words(item.question)}",
            f"   Triggered by{more}:",
            *(f"     {_site(line)}" for line in sites[:SHOWN_LINES]),
            f"   Pull requests: {', '.join(f'#{number}' for number in item.pulls)}",
        ]
    if rest := triggered[MAX_ITEMS:]:
        output += ["", f"{len(rest)} more items triggered: {', '.join(item.id for item, _ in rest)}"]
    return output


def main(argv: list[str] | None = None) -> int:
    """Print the review items the diff triggers, or the problems of an invalid table."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base", default="origin/main", help="git revision whose merge base with HEAD the diff starts at"
    )
    parser.add_argument("--head", help="git revision the diff ends at (default: the working tree)")
    parser.add_argument("--table", type=Path, default=TABLE, help="the item table (default: %(default)s)")
    args = parser.parse_args(argv)
    root = Path(_git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
    items, problems = load_items(args.table, root)
    if problems:
        print(f"{args.table.name} is invalid:", *problems, sep="\n  ")
        return 2
    against = f"{args.base} up to {args.head}" if args.head else args.base
    print("\n".join(report(items, diff_lines(root, args.base, args.head), against)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
