"""Tests for the output-change pages: release summaries, record comparisons and the committed pages.

The gate rows are cells of the 0.86.0 vs 0.87.0 release-tier run the 0.87.0 page is built from.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts._behaviour_report import compare_records, error_name, redact, summarize_gate
from scripts.build_behaviour_docs import render_comparison, render_release
from tests.conftest import assert_output

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_behaviour_docs.py"


def _row(input_path: str, backend: str, cls: str, notes: list[str], **extra: Any) -> dict[str, Any]:
    return {
        "input": input_path,
        "file_type": "jsonschema",
        "type": backend,
        "options": "default",
        "kind": "default",
        "class": cls,
        "notes": notes,
        "accepted_by": [],
        **extra,
    }


ROWS = {
    "same": _row("tests/data/jsonschema/person.json", "pydantic_v2.BaseModel", "same", []),
    "no_behaviour": _row("tests/data/jsonschema/all_of_ref_self.json", "dataclasses.dataclass", "no-behaviour", []),
    "both_fail": _row("tests/data/jsonschema/allof_inheritance_errors", "pydantic_v2.BaseModel", "both-fail", []),
    "unbuildable_decoder": _row(
        "tests/data/jsonschema/additional_pattern_intersections",
        "msgspec.Struct",
        "crash-fix",
        [
            (
                "crash-fix [type] anchored_disjoint:Root: Union[dict[str, Root1], dict[str, Root2]] -> "
                "dict[str, Union[Root1, Root2]]"
            )
        ],
    ),
    "pydantic_validation_error": _row(
        "tests/data/jsonschema/allof_constraint_intersections",
        "pydantic_v2.BaseModel",
        "crash-fix",
        ["generation fixed:     For further information visit https://errors.pydantic.dev/2.14/v/float_type"],
    ),
    "ref_not_found": _row(
        "tests/data/jsonschema/allof_inherited_ref_other_directory",
        "pydantic_v2.BaseModel",
        "crash-fix",
        [
            (
                "generation fixed: $ref file not found: "
                "/home/user/.cache/datamodel-code-generator/compare-release/trees/87f898ca9cccd333b137ff439360e69f"
            )
        ],
    ),
    "unhashable": _row(
        "tests/data/jsonschema/allof_type_list_member.json",
        "pydantic_v2.BaseModel",
        "crash-fix",
        ["generation fixed: TypeError: cannot use 'tuple' as a set element (unhashable type: 'list')"],
    ),
    "forward_reference": _row(
        "tests/data/jsonschema/annotated_forward_reference.json",
        "typing.TypedDict",
        "crash-fix",
        ["import fixed: NameError: name 'Node' is not defined"],
    ),
    "accepted_anchor": _row(
        "tests/data/jsonschema/external_anchor",
        "pydantic_v2.BaseModel",
        "accepted",
        [
            (
                "accepted (spec-correct $anchor target; 0.86.0 depended on file read or) [type] "
                "root:ExternalAnchor:anchored: Union[NestedChild, None] -> Union[AnchoredChild, None]"
            )
        ],
        accepted_by=[0],
    ),
    "new_option": _row(
        "tests/data/jsonschema/alias_generator.json",
        "pydantic_v2.dataclass",
        "opt-in",
        ["the release has no --pin-aliases-with-alias-generator"],
        options="--snake-case-field --pin-aliases-with-alias-generator --target-pydantic-version 2",
        kind="cli-doc",
    ),
}


ACCEPTED_ENTRY = {
    "input": "tests/data/jsonschema/external_anchor",
    "aspects": ["type"],
    "match": "AnchoredChild",
    "reason": "spec-correct $anchor target; 0.86.0 depended on file read order",
    "decision": "#5088",
    "release_note": "an external $anchor resolves to the anchored schema regardless of file read order",
}
ACCEPTED_EXAMPLE = {
    "input": "tests/data/jsonschema/external_anchor",
    "type": "dataclasses.dataclass",
    "options": "default",
    "diff": "--- pkg/root.py\n+++ pkg/root.py\n@@ -11,6 +11,6 @@\n @dataclass\n class ExternalAnchor:\n"
    "-    anchored: child.NestedChild | None = None\n+    anchored: child.AnchoredChild | None = None\n",
}


def _signature(**fields: str) -> dict[str, Any]:
    model = {
        "kind": "BaseModel",
        "bases": ["BaseModel"],
        "config": {},
        "fields": {
            name: {"type": tp, "ctype": tp, "required": False, "alias": None, "default": "None"}
            for name, tp in fields.items()
        },
    }
    return {"names": ["Model"], "items": {"Model": model}, "adapters": {"Model": "ok"}}


SIGNATURES = {
    "int": _signature(count="int"),
    "int-reformatted": _signature(count="int"),
    "str": _signature(count="str"),
    "unbuildable": {**_signature(count="int"), "adapters": {"Model": "PydanticSchemaGenerationError"}},
}
OK_INT = {"gen": "ok", "sig": "int"}
OK_STR = {"gen": "ok", "sig": "str"}
FAILED = {"gen": "fail", "error": "TypeError: unhashable type: 'list'"}
UNSUPPORTED = {"gen": "unsupported", "error": "the release has no --output-model-type msgspec.Struct"}
KEY = "tests/data/jsonschema/{}|pydantic_v2.BaseModel|default"
CHAIN_CASES = {
    "same": [OK_INT, OK_INT, OK_INT],
    "reformatted_only": [OK_INT, {"gen": "ok", "sig": "int-reformatted"}, {"gen": "ok", "sig": "int-reformatted"}],
    "type_changed_in_middle_release": [OK_INT, OK_STR, OK_STR],
    "changed_and_changed_back": [OK_INT, OK_STR, OK_INT],
    "crash_fixed_in_last_release": [FAILED, FAILED, OK_INT],
    "fails_since_middle_release": [OK_INT, FAILED, FAILED],
    "unusable_validator_fixed": [{"gen": "ok", "sig": "unbuildable"}, OK_STR, OK_STR],
    "unsupported_backend": [UNSUPPORTED, OK_INT, OK_INT],
    "failed_then_fixed": [OK_INT, FAILED, OK_INT],
    "type_changed_then_failed": [OK_INT, OK_STR, FAILED],
    "failed_then_type_changed": [FAILED, OK_INT, OK_STR],
}


def test_compare_records() -> None:
    """Each cell is compared between the oldest and newest record and attributed to the records that changed it."""
    versions = ("0.70.0", "0.80.0", "0.87.0")
    records = {
        version: {"cells": {KEY.format(case): chain[index] for case, chain in CHAIN_CASES.items()}}
        for index, version in enumerate(versions)
    }
    comparison = compare_records(records, "0.70.0", "0.87.0", SIGNATURES.__getitem__)
    assert_output(json.dumps(comparison, indent=2) + "\n" + render_comparison(comparison) + "\n")


ERROR_MESSAGES = {
    "exception": "generation fixed: TypeError: cannot use 'tuple' as a set element (unhashable type: 'list')",
    "pydantic_link": "generation fixed:     For further information visit https://errors.pydantic.dev/2.14/v/float_type",
    "no_exception": "generation fixed: allOf contains a boolean false schema that makes the schema unsatisfiable",
    "cached_tree_path": ROWS["ref_not_found"]["notes"][0],
    "fixture_path": "generation fixed: FileNotFoundError: /Users/runner/work/dcg/tests/data/jsonschema/a.json",
    "windows_path": r"import fixed: ImportError: C:\Users\runner\AppData\Local\Temp\tmpab\model.py",
}


@pytest.mark.parametrize("case", sorted(ERROR_MESSAGES))
def test_error_name_and_redact(case: str) -> None:
    """Crash fixes are grouped by the exception they name; machine paths are dropped from the page."""
    message = ERROR_MESSAGES[case]
    assert_output(f"{error_name(message)}\n{redact(message)}\n")


RELEASE_CASES = {
    "fixes_only": [name for name in ROWS if name not in {"new_option", "accepted_anchor"}],
    "every_class": list(ROWS),
    "pr_tier": [name for name in ROWS if name not in {"new_option", "accepted_anchor"}],
}


@pytest.mark.parametrize("case", sorted(RELEASE_CASES))
def test_render_release(case: str) -> None:
    """A release page sums the gate cells per class and output model type and lists each group's examples."""
    data = summarize_gate([ROWS[name] for name in RELEASE_CASES[case]], [ACCEPTED_ENTRY])
    data = {"version": "0.87.0", "previous": "0.86.0", "tier": "pr" if case == "pr_tier" else "release", **data}
    for group in data["accepted"]:
        group["restored_by"] = []
        group["example"] = ACCEPTED_EXAMPLE
    assert_output(json.dumps(data, indent=2) + "\n" + render_release(data))


def test_build_behaviour_docs_check() -> None:
    """The committed output-change pages match their summaries."""
    subprocess.run([sys.executable, str(SCRIPT), "--check"], check=True)
