"""Summaries of classified cells for the output-change pages, for scripts/behaviour_diff.py.

`summarize_gate` turns the cells of one `behaviour_diff.py gate --json` run into the data of one release page;
`compare_records` compares two behaviour records (`behaviour_diff.py record`) cell by cell and attributes each change
to the recorded releases between them. Pure functions only: signatures come in through a loader callable.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from itertools import pairwise
from typing import TYPE_CHECKING, Any

from scripts._behaviour_classify import Allowlist, Side, classify_cell

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

PAGE_SCHEMA_VERSION = 1
ERROR_NAME = re.compile(r"\b([A-Z][A-Za-z]*(?:Error|Exception|Warning))\b")
NOTE_PREFIX = re.compile(r"^(?:generation fixed|import fixed|generation now fails|import now fails): *|^crash-fix ")
ASPECT = re.compile(r"^(?:crash-fix |accepted \(.*?\) )?\[([a-z-]+)\]")
ABSOLUTE_FIXTURE = re.compile(r"(?:[A-Za-z]:)?[/\\][^\s'\"]*?[/\\](tests[/\\]data[/\\])")
ABSOLUTE_PATH = re.compile(r"(?<![\w.])(?:/(?:home|Users|tmp|var|private)|[A-Za-z]:\\)[^\s'\":]*")
QUERY_CLASSES = ("same", "no-behaviour", "crash-fix", "behaviour-change", "now-fails", "both-fail", "unsupported")
CHANGED = frozenset({"crash-fix", "behaviour-change", "now-fails"})
GATE_ORDER = ("same", "no-behaviour", "crash-fix", "accepted", "violation", "both-fail", "opt-in")
MAX_EXAMPLES = 3
MAX_DETAIL = 160


def version_key(version: str) -> tuple[int, ...]:
    """Return a sort key for a release version such as 0.86.0."""
    return tuple(int(part) for part in re.findall(r"\d+", version))


def redact(text: str) -> str:
    """Drop machine-specific path prefixes from an error message."""
    return ABSOLUTE_PATH.sub("<path>", ABSOLUTE_FIXTURE.sub(r"\1", text)).replace("\\", "/")


def error_name(message: str) -> str:
    """Return the exception class a generation or import error names, or `no exception name`.

    A pydantic validation error's last line is only its documentation link.
    """
    if match := ERROR_NAME.search(message):
        return match.group(1)
    return "ValidationError" if "errors.pydantic.dev" in message else "no exception name"


def aspects_of(notes: Iterable[str]) -> list[str]:
    """Return the behaviour aspects named in a cell's notes, in order, without repeats."""
    return list(dict.fromkeys(match.group(1) for note in notes if (match := ASPECT.search(note))))


def _detail(note: str) -> str:
    note = redact(NOTE_PREFIX.sub("", note))
    return note if len(note) <= MAX_DETAIL else note[: MAX_DETAIL - 3] + "..."


def _example(row: dict[str, Any], detail: str) -> dict[str, str]:
    return {"input": row["input"], "type": row["type"], "options": row["options"], "detail": _detail(detail)}


def _crash_group(row: dict[str, Any]) -> tuple[str, str]:
    note = row["notes"][0] if row["notes"] else ""
    if note.startswith("generation fixed:"):
        return "generation", error_name(note)
    if note.startswith("import fixed:"):
        return "import", error_name(note)
    if note.startswith("the release rejected every valid payload"):
        return "validation", "every valid payload rejected"
    return "validation", "no validator or unusable value"


def summarize_gate(rows: list[dict[str, Any]], entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the data of a release page from the cells of one gate run and the allowlist entries it used."""
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    kinds: Counter[str] = Counter()
    crash: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    accepted: dict[int, list[dict[str, Any]]] = defaultdict(list)
    opt_in: dict[str, list[dict[str, Any]]] = defaultdict(list)
    numbered: list[dict[str, str]] = []
    violations: list[dict[str, str]] = []
    for row in rows:
        cls = row["class"]
        counts[row["type"]][cls] += 1
        kinds[row["kind"]] += 1
        numbered += [_example(row, note) for note in row["notes"] if "[numbered-name-removed]" in note]
        match cls:
            case "crash-fix":
                crash[_crash_group(row)].append(row)
            case "accepted":
                for index in row.get("accepted_by", []):
                    accepted[index].append(row)
            case "opt-in":
                opt_in[row["notes"][0].removeprefix("the release has no ")].append(row)
            case "violation":
                violations.append(_example(row, "; ".join(row["notes"][:2])))
    return {
        "schema_version": PAGE_SCHEMA_VERSION,
        "types": sorted(counts),
        "classes": [cls for cls in GATE_ORDER if any(counts[t][cls] for t in counts)],
        "counts": {backend: dict(sorted(by_class.items())) for backend, by_class in sorted(counts.items())},
        "kinds": dict(sorted(kinds.items())),
        "crash_fixes": [
            {
                "stage": stage,
                "error": error,
                "cells": len(group),
                "inputs": len({row["input"] for row in group}),
                "types": sorted({row["type"] for row in group}),
                "examples": [_example(row, row["notes"][0] if row["notes"] else "") for row in _spread(group)],
            }
            for (stage, error), group in sorted(crash.items(), key=lambda item: (-len(item[1]), item[0]))
        ],
        "accepted": [
            {
                "entry": entries[index].get("input", ""),
                "reason": entries[index].get("reason", ""),
                "decision": entries[index].get("decision", ""),
                "release_note": entries[index].get("release_note", ""),
                "cells": len(group),
                "inputs": sorted({row["input"] for row in group}),
                "types": sorted({row["type"] for row in group}),
                "option_sets": sorted({row["options"] for row in group}),
                "aspects": aspects_of(note for row in group for note in row["notes"]),
            }
            for index, group in sorted(accepted.items())
        ],
        "opt_in": [
            {
                "option": option,
                "cells": len(group),
                "inputs": len({row["input"] for row in group}),
                "types": sorted({row["type"] for row in group}),
            }
            for option, group in sorted(opt_in.items())
        ],
        "numbered_names": numbered[: MAX_EXAMPLES * 5],
        "violations": violations[: MAX_EXAMPLES * 5],
    }


def _spread(group: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return up to MAX_EXAMPLES rows of distinct inputs."""
    seen: dict[str, dict[str, Any]] = {}
    for row in sorted(group, key=lambda row: (row["options"] != "default", row["input"], row["type"], row["options"])):
        seen.setdefault(row["input"], row)
    return list(seen.values())[:MAX_EXAMPLES]


def _side(entry: dict[str, Any], load: Callable[[str], dict[str, Any]]) -> Side:
    if entry["gen"] == "ok":
        return Side(generated=True, signature=load(entry["sig"]))
    return Side(generated=False, message=entry.get("error", ""))


def pair_class(
    old: dict[str, Any], new: dict[str, Any], load: Callable[[str], dict[str, Any]], *, key: str
) -> tuple[str, tuple[str, ...]]:
    """Classify one cell between two recorded releases: a `QUERY_CLASSES` name and the change lines."""
    if "unsupported" in {old["gen"], new["gen"]}:
        return "unsupported", ()
    if old["gen"] == new["gen"] == "ok" and old["sig"] == new["sig"]:
        return "same", ()
    input_path, backend, _ = key.split("|")
    verdict = classify_cell(
        _side(old, load),
        _side(new, load),
        input_path=input_path,
        backend=backend,
        allowlist=Allowlist(),
        valid_payloads=frozenset(new.get("valid", [])),
    )
    if verdict.cls != "violation":
        return verdict.cls, verdict.notes
    failed = verdict.notes[0].startswith(("generation now fails", "import now fails"))
    return ("now-fails" if failed else "behaviour-change"), verdict.notes


def compare_records(
    records: dict[str, dict[str, Any]], old: str, new: str, load: Callable[[str], dict[str, Any]]
) -> dict[str, Any]:
    """Compare the cells of two recorded releases and attribute each change to the recorded releases between them.

    `records` maps versions to records; every recorded version in [old, new] takes part in the attribution. The counts
    classify the two ends; `changed_in` classifies each pair of adjacent recorded releases on its own, so a change
    that was made and later undone shows under both releases.
    """
    chain = sorted((v for v in records if version_key(old) <= version_key(v) <= version_key(new)), key=version_key)
    before, after = records[old]["cells"], records[new]["cells"]
    counts: Counter[str] = Counter()
    by_type: dict[str, Counter[str]] = defaultdict(Counter)
    changed_in: dict[str, Counter[str]] = defaultdict(Counter)
    aspects: Counter[str] = Counter()
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for key in sorted(set(before) & set(after), key=lambda key: (key.startswith("payload:"), key)):
        cls, notes = pair_class(before[key], after[key], load, key=key)
        counts[cls] += 1
        by_type[key.split("|")[1]][cls] += 1
        releases = []
        for previous, current in pairwise(chain):
            if key not in records[previous]["cells"] or key not in records[current]["cells"]:
                continue
            step, _ = pair_class(records[previous]["cells"][key], records[current]["cells"][key], load, key=key)
            if step in CHANGED:
                changed_in[current][step] += 1
                releases.append(current)
        if cls not in CHANGED:
            continue
        aspects.update(aspects_of(notes))
        input_path, backend, _ = key.split("|")
        if len(examples[cls]) < MAX_EXAMPLES and all(example["input"] != input_path for example in examples[cls]):
            examples[cls].append({
                "input": input_path,
                "type": backend,
                "changed_in": releases,
                "changes": [_detail(note) for note in notes[:2]],
            })
    return {
        "from": old,
        "to": new,
        "releases": chain,
        "types": sorted(by_type),
        "counts": {cls: counts[cls] for cls in QUERY_CLASSES if counts[cls]},
        "by_type": {backend: dict(sorted(c.items())) for backend, c in sorted(by_type.items())},
        "changed_in": {release: dict(sorted(changed_in[release].items())) for release in chain if changed_in[release]},
        "aspects": dict(aspects.most_common()),
        "examples": {cls: examples[cls] for cls in QUERY_CLASSES if examples[cls]},
    }
