"""Tests for the pure helpers of the change records: the stamp-pending rule, the `covers` block and the cells."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import TYPE_CHECKING

import pytest

from scripts._change_records import (
    CellSelector,
    Facts,
    owner_of,
    parse_entry,
    read_records,
    resolved_record,
    rule_problems,
    selector_surface,
    stamp_pending,
    with_covers,
    without_cells,
)
from tests.conftest import assert_output

if TYPE_CHECKING:
    from pathlib import Path

FRAGMENT = '[[change]]\nid = "x"\ncategory = "harness"\ntitle = "t"\nsummary = "s"\n'
FRAGMENTS = {
    "no covers yet": FRAGMENT,
    "no newline at the end": FRAGMENT.rstrip("\n"),
    "covers replaced": FRAGMENT + 'covers = [\n    "old.txt",\n]\n',
    "a sub-table": '[[change]]\nid = "x"\nkind = "output-text"\nsource = "run"\n\n[change.verify]\nschema = "{}"\n',
    "a header-like line in a string": '[[change]]\nid = "x"\nsummary = """\n[not a table]\n"""\n',
    "a comment first": '# moved\n\n[[change]]\nid = "x"\n',
    "covers in another form": FRAGMENT + 'covers = ["old.txt"]\n',
}
LABELS = {"labels": ['main/b "quoted".py', "main/a.py", "main/a.py", "main/é/"], "no labels": []}
STAMPS = {
    "listed": ("0.97.0", ["0.96.0", "0.97.0"]),
    "older tag": ("0.96.0", ["0.96.0", "0.97.0"]),
    "tag newer than every row": ("0.98.0", ["0.96.0", "0.97.0"]),
    "no rows": ("0.1.0", []),
}


def _covered(text: str, labels: list[str]) -> str:
    try:
        return with_covers(text, labels)
    except ValueError as error:
        return f"ValueError: {error}\n"


def test_change_record_helpers() -> None:
    """A `covers` block is written after the keys, before a sub-table, or after the header where a string hides one.

    Labels are sorted, unique and JSON-quoted; no labels drop the block; a hand-written `covers` is refused. A tag
    newer than every row of the release table is a pending stamp.
    """
    output = [
        f"=== {fragment}, {labels} ===\n{_covered(text, values)}"
        for fragment, text in FRAGMENTS.items()
        for labels, values in LABELS.items()
    ]
    output.append(json.dumps({case: stamp_pending(*args) for case, args in STAMPS.items()}, indent=1))
    assert_output("\n".join(output))


def _entry(category: str | None = "output-text", *cells: dict[str, object], **keys: object) -> dict[str, object]:
    """Return a `[[change]]` table of a category with `[[change.cells]]` tables, as tomllib reads a fragment."""
    entry: dict[str, object] = {"id": "x", "title": "t", "summary": "s", "refs": ["#1"]}
    if category is not None:
        entry["category"] = category
    return entry | ({"cells": list(cells)} if cells else {}) | keys


SELECTOR = {"input": ["tests/data/jsonschema/plain.json"], "backends": "*", "options": "*"}
SELECTOR_CASES: dict[str, dict[str, object]] = {
    "every backend and option set": _entry("output-text", SELECTOR),
    "listed backends and option sets": _entry(
        "decided-change",
        SELECTOR
        | {
            "backends": ["msgspec.Struct", "pydantic_v2.BaseModel"],
            "options": [[], ["--use-annotated"]],
            "input_file_type": "openapi",
            "aspects": ["type", "exit"],
            "match": r"^\[type\]",
        },
    ),
    "two selectors": _entry("output-text", SELECTOR, SELECTOR | {"input": ["tests/data/jsonschema/*", "a/**"]}),
    "cells are no tables": _entry("output-text", cells="x"),
    "a cell is no table": _entry("output-text", cells=["x"]),
    "unknown key": _entry("output-text", SELECTOR | {"colour": "red"}),
    "missing keys": _entry("output-text", {"aspects": ["type"]}),
    "empty input": _entry("output-text", SELECTOR | {"input": []}),
    "input of no strings": _entry("output-text", SELECTOR | {"input": [1]}),
    "input as a string": _entry("output-text", SELECTOR | {"input": "tests/data/jsonschema/plain.json"}),
    "unknown backend": _entry("output-text", SELECTOR | {"backends": ["pydantic.BaseModel", "msgspec.Struct"]}),
    "empty backends": _entry("output-text", SELECTOR | {"backends": []}),
    "backends as another string": _entry("output-text", SELECTOR | {"backends": "all"}),
    "options of strings": _entry("output-text", SELECTOR | {"options": ["--use-annotated"]}),
    "empty options": _entry("output-text", SELECTOR | {"options": []}),
    "options as another string": _entry("output-text", SELECTOR | {"options": "default"}),
    "input file type of no string": _entry("output-text", SELECTOR | {"input_file_type": 1}),
    "unknown aspect": _entry("decided-change", SELECTOR | {"aspects": ["colour", "type"]}),
    "empty aspects": _entry("decided-change", SELECTOR | {"aspects": []}),
    "match that does not compile": _entry("decided-change", SELECTOR | {"match": "(float"}),
    "match of no string": _entry("decided-change", SELECTOR | {"match": 1}),
    "payload cells with their input file type": _entry(
        "output-text", SELECTOR | {"input": ["payload:jsonschema/*.json"], "input_file_type": "jsonschema"}
    ),
    "payload cells without an input file type": _entry("output-text", SELECTOR | {"input": ["payload:jsonschema/*"]}),
    "project and family cells": _entry(
        "output-text",
        SELECTOR | {"input": ["project:a/*"], "input_file_type": "openapi"},
        SELECTOR | {"input": ["family:*"], "input_file_type": "jsonschema"},
    ),
    "cells of two label spaces": _entry(
        "output-text",
        SELECTOR | {"input": ["tests/data/jsonschema/plain.json", "payload:jsonschema/plain.json"]},
    ),
    "a glob outside every label space is a path": _entry("output-text", SELECTOR | {"input": ["**/plain.json"]}),
}


@pytest.mark.parametrize("case", sorted(SELECTOR_CASES))
def test_parse_entry_cells(case: str) -> None:
    """`[[change.cells]]` tables become selectors (`"*"` is None); each that is none is refused with every reason."""
    record, problems = parse_entry(SELECTOR_CASES[case], "unreleased/x.toml")
    cells = [asdict(selector) for selector in record.cells] if record else None
    assert_output(json.dumps({"cells": cells, "problems": problems}, indent=1) + "\n")


FACTS = Facts(
    new_surface=frozenset({
        "--new-flag",
        "--output-model-type new.Type",
        "preset:20260301",
        "--input-file-type newtype",
    })
)
RULE_CASES: dict[str, tuple[dict[str, object], str]] = {
    "output-text without cells, added": (_entry("output-text"), "added"),
    "output-text without cells, older": (_entry("output-text"), ""),
    "crash-fix without cells, added": (_entry("crash-fix"), "added"),
    "notice without cells, added": (_entry("notice", kind="warning", action="warn", source="run"), "added"),
    "decided-change without cells, added": (
        _entry("decided-change", decision=["#1"], keep={"note": "n"}),
        "added",
    ),
    "harness with cells": (_entry("harness", SELECTOR, refs=[]), ""),
    "new-surface with cells": (_entry("new-surface", SELECTOR, surface=["--new-flag"], refs=[]), ""),
    "internal with cells": (_entry("internal", SELECTOR, refs=[]), ""),
    "docs-text with cells": (_entry("docs-text", SELECTOR, refs=[]), ""),
    "decided-change selector without aspects or match": (
        _entry("decided-change", SELECTOR, SELECTOR | {"match": "x"}, decision=["#1"], keep={"note": "n"}),
        "",
    ),
    "default-fix selector with aspects only": (
        _entry("default-fix", SELECTOR | {"aspects": ["type"]}, decision=["#1"]),
        "",
    ),
    "notice selector with aspects only": (_entry("notice", SELECTOR | {"aspects": ["warning"]}), ""),
    "notice selector with match": (_entry("notice", SELECTOR | {"match": "gap"}), ""),
    "notice selector with an empty match": (_entry("notice", SELECTOR | {"match": ""}), ""),
    "decided-change selector with an empty match": (
        _entry("decided-change", SELECTOR | {"match": ""}, decision=["#1"], keep={"note": "n"}),
        "",
    ),
    "decided-change selector with aspects and an empty match": (
        _entry("decided-change", SELECTOR | {"aspects": ["type"], "match": ""}, decision=["#1"], keep={"note": "n"}),
        "",
    ),
    "api selector": (_entry("api", SELECTOR, decision=["#1"]), ""),
    "experimental selector": (
        _entry("experimental", SELECTOR, decision=["#1"], affects={"experimental": ["input-format.avro"]}),
        "",
    ),
    "released entry with cells": (_entry("output-text", SELECTOR), "released"),
    "released guide entry with cells": (_entry(None, SELECTOR), "released"),
    "derived entry with cells": (_entry(None, SELECTOR, id="option:--new-flag", refs=[]), ""),
    "extending record with cells": (
        {"id": "x", "extends": "y", "refs": ["#1"], "cells": [SELECTOR | {"options": [["--new-flag"]]}]},
        "facts",
    ),
    "extending record without facts": ({"id": "x", "extends": "y", "cells": [SELECTOR]}, ""),
    "extending record with a title": ({"id": "x", "extends": "y", "title": "t", "cells": [SELECTOR]}, ""),
    "cells naming unreleased surface": (
        _entry(
            "output-text",
            SELECTOR
            | {
                "options": [
                    ["--new-flag", "--use-annotated"],
                    ["--output-model-type", "new.Type"],
                    ["--output-model-type=new.Type"],
                    ["--preset", "standard-py314-20260301"],
                    ["--output-model-type", "msgspec.Struct", "--new-flag"],
                ]
            },
        ),
        "facts",
    ),
    "cells naming an unreleased input file type": (
        _entry("output-text", SELECTOR | {"input_file_type": "newtype"}),
        "facts",
    ),
    "cells naming a released input file type": (
        _entry("output-text", SELECTOR | {"input_file_type": "openapi"}),
        "facts",
    ),
    "cells naming released surface": (
        _entry("output-text", SELECTOR | {"options": [["--preset", "standard-py314-20260101"], ["--keyword-only"]]}),
        "facts",
    ),
}


@pytest.mark.parametrize("case", sorted(RULE_CASES))
def test_rule_problems_cells(case: str) -> None:
    """A category's cells are required (of an added record), optional or forbidden, with the keys its selectors need.

    No released entry, derived entry or record other than a fragment names cells; an extending record may; with the
    facts, cells that name an option, value or preset date the latest release lacks are refused.
    """
    entry, mode = RULE_CASES[case]
    record, problems = parse_entry(entry, "unreleased/x.toml")
    facts = FACTS if mode == "facts" else None
    found = (
        rule_problems(record, fragment=mode != "released", facts=facts, added=mode == "added") if record else problems
    )
    assert_output(json.dumps(found, indent=1) + "\n")


def _extending(entry_id: str, *cells: dict[str, object], extends: str = "y", **keys: object) -> dict[str, object]:
    """Return a record that extends another by id, with `[[change.cells]]` tables."""
    return {"id": entry_id, "extends": extends, "refs": ["#2"]} | ({"cells": list(cells)} if cells else {}) | keys


RESOLVE_CASES: dict[str, tuple[list[dict[str, object]], str, bool]] = {
    "a crash-fix target needs cells, added": ([_entry("crash-fix", id="y"), _extending("x")], "x", True),
    "a crash-fix target needs cells, older": ([_entry("crash-fix", id="y"), _extending("x")], "x", False),
    "a crash-fix target with the cells": ([_entry("crash-fix", id="y"), _extending("x", SELECTOR)], "x", True),
    "a default-fix target may name none": (
        [_entry("default-fix", id="y", decision=["#1"]), _extending("x")],
        "x",
        True,
    ),
    "an output-text target needs cells, added": ([_entry("output-text", id="y"), _extending("x")], "x", True),
    "a notice target needs cells, added": (
        [_entry("notice", id="y", kind="warning", action="warn", source="run"), _extending("x")],
        "x",
        True,
    ),
    "a decided-change target may name none": (
        [_entry("decided-change", id="y", decision=["#1"], keep={"note": "n"}), _extending("x")],
        "x",
        True,
    ),
    "a harness target names none": ([_entry("harness", id="y"), _extending("x", SELECTOR)], "x", True),
    "a selector missing its keys": (
        [_entry("notice", id="y", kind="warning", action="warn", source="run"), _extending("x", SELECTOR)],
        "x",
        True,
    ),
    "a target that does not exist": ([_extending("x")], "x", True),
    "a chain of extends": (
        [_entry("crash-fix", id="z"), _extending("y", SELECTOR, extends="z"), _extending("x", SELECTOR, extends="y")],
        "x",
        True,
    ),
    "a derived target": (
        [_entry(None, id="option:--new-flag", refs=[]), _extending("x", extends="option:--new-flag")],
        "x",
        True,
    ),
    "a target without a category": ([_entry(None, id="y"), _extending("x")], "x", True),
    "an extending record with a title": (
        [_entry("crash-fix", id="y"), _extending("x", SELECTOR, title="t")],
        "x",
        True,
    ),
    "a record that extends nothing": ([_entry("crash-fix", id="y")], "y", True),
}


@pytest.mark.parametrize("case", sorted(RESOLVE_CASES))
def test_resolved_record_and_its_rule(case: str) -> None:
    """An extending record takes its target's category: its own covers and cells follow that category's rule.

    The target must be an unreleased record of its own with a category: a chain, a derived entry, a missing or an
    uncategorized one is refused with one message, wherever the record is read.
    """
    entries, name, added = RESOLVE_CASES[case]
    records = {record.id: record for entry in entries if (record := parse_entry(entry, "x.toml")[0])}
    record = records[name]
    resolved, why = resolved_record(record, records)
    found = rule_problems(record, fragment=True, added=added, records=records)
    shown = (
        None if resolved is None else {"id": resolved.id, "category": resolved.category, "cells": len(resolved.cells)}
    )
    assert_output(json.dumps({"resolved": shown, "why": why, "problems": found}, indent=1, default=str) + "\n")


CELLS_TABLE = '\n[[change.cells]]\ninput = ["a.json"]\nbackends = "*"\noptions = "*"\n'
STAMPED_CASES = {
    "no cells": FRAGMENT,
    "two cells tables": FRAGMENT + CELLS_TABLE + "# the second\n" + CELLS_TABLE.replace("a.json", "b.json"),
    "cells before a sub-table": FRAGMENT + CELLS_TABLE + '\n[change.verify]\nschema = "{}"\n',
    "spaced header": FRAGMENT + CELLS_TABLE.replace("[[change.cells]]", "[[ change . cells ]]  # spaced"),
    "inline cells": FRAGMENT + 'cells = [{ input = ["a.json"], backends = "*", options = "*" }]\n',
    "a header-like line in a string": FRAGMENT.replace('summary = "s"', 'summary = """\n[[change.cells]]\n"""'),
    "option sets at the start of their lines": FRAGMENT
    + CELLS_TABLE.replace('options = "*"', 'options = [\n["--use-annotated"],\n[]\n]'),
    "an indented header": FRAGMENT + CELLS_TABLE.replace("[[change.cells]]", "  [[change.cells]]"),
}


@pytest.mark.parametrize("case", sorted(STAMPED_CASES))
def test_without_cells(case: str) -> None:
    """The stamp drops each `[[change.cells]]` table up to the next table; cells written otherwise are refused."""
    try:
        output = without_cells(STAMPED_CASES[case])
    except ValueError as error:
        output = f"ValueError: {error}\n"
    assert_output(output)


SURFACE_CASES: dict[str, CellSelector] = {
    "every option set": CellSelector(("a.json",)),
    "flags": CellSelector(("a.json",), options=(("--use-annotated", "--keyword-only"),)),
    "a value as the next item": CellSelector(
        ("a.json",), options=(("--output-model-type", "msgspec.Struct", "--use-annotated"),)
    ),
    "a value after =": CellSelector(("a.json",), options=(("--output-model-type=msgspec.Struct",),)),
    "a preset": CellSelector(
        ("a.json",), options=(("--preset", "standard-py314-20260201"), ("--preset=practical-py310-20260101",))
    ),
    "default options": CellSelector(("a.json",), options=((),)),
    "an input file type": CellSelector(("a.json",), options=(("--use-annotated",),), input_file_type="openapi"),
}


@pytest.mark.parametrize("case", sorted(SURFACE_CASES))
def test_selector_surface(case: str) -> None:
    """A selector's option sets name each option, each value and each preset's date as surface items."""
    assert_output(json.dumps(selector_surface(SURFACE_CASES[case])) + "\n")


READ_CASES = {
    "released and unreleased records": {
        "changes/0.2.0.toml": '[[change]]\nid = "b"\ntitle = "B"\n\n[[change]]\nid = "a"\ntitle = "A"\n',
        "unreleased/c.toml": FRAGMENT.replace('"x"', '"c"') + CELLS_TABLE,
        "unreleased/notes.txt": "not a record\n",
    },
    "a table that holds no record": {
        "changes/0.2.0.toml": '[[change]]\nid = "B"\n',
        "unreleased/c.toml": '[[change]]\nid = "c"\ncells = "x"\n',
    },
}


@pytest.mark.parametrize("case", sorted(READ_CASES))
def test_read_records(case: str, tmp_path: Path) -> None:
    """The records of a migration data folder are the releases' changes, then the fragments.

    A table that holds no record refuses the read with every reason.
    """
    for name, text in READ_CASES[case].items():
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    try:
        output = json.dumps([[record.id, record.title, len(record.cells)] for record in read_records(tmp_path)])
    except ValueError as error:
        output = f"ValueError: {error}"
    assert_output(output + "\n")


PAYLOAD_NODE = "tests/main/test_payload_validation.py::"
OWNER_CASES: dict[str, tuple[str, str]] = {
    "the count of the discovered schema files is a listing": (
        PAYLOAD_NODE + "test_payload_validation_cases_cover_discovered_schema_files",
        "main/payload_validation/cases_cover_discovered_schema_files.txt",
    ),
    "the other payload outputs stay model output": (
        PAYLOAD_NODE + "test_generated_models_decide_value_neighbours_like_the_schema[a.json]",
        "main/payload_validation/generated_models_decide_value_neighbours_like_the_schema.txt",
    ),
    "a payload classification stays model output": (
        PAYLOAD_NODE + "test_allof_payload_strategy_classification",
        "main/payload_validation/allof_payload_strategy_classification.txt",
    ),
    "a Jinja oracle of the builtin templates is internal": (
        "tests/model/test_compiled_templates.py::test_target_templates_match_jinja_oracle[client]",
        "model/compiled_templates/target_templates_match_jinja_oracle[client].rendered.txt",
    ),
    "the list of the builtin templates is a listing": (
        "tests/model/test_compiled_templates.py::test_template_inventory_is_derived_from_all_builtin_sources[client]",
        "model/compiled_templates/template_inventory_is_derived_from_all_builtin_sources[client].txt",
    ),
    "the rendering of real models through the compiled templates stays model output": (
        "tests/model/test_compiled_templates.py::test_model_rendering_uses_generated_renderers_with_real_model_objects",
        "model/compiled_templates/model_rendering_uses_generated_renderers_with_real_model_objects.txt",
    ),
}


@pytest.mark.parametrize("case", sorted(OWNER_CASES))
def test_owner_of(case: str) -> None:
    """A stored output is told from model output by its test node and label."""
    nodeid, label = OWNER_CASES[case]
    assert_output(f"{owner_of(nodeid, label).value}\n")
