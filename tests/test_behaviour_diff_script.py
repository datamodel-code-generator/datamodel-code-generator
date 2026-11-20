"""Tests for the release behaviour diff: signatures, the classifier and the gate driver.

The signature cases and the classifier cases are the shapes the 0.86.0 audit met: alias and NotRequired spellings
that are not behaviour changes, msgspec decoders and pydantic adapters 0.86.0 could not build, the propertyNames key
change the owner accepted, and the behaviour changes the gate has to stop.
"""

from __future__ import annotations

import contextlib
import json
import re
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, dataclass, field
from io import StringIO
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from scripts import behaviour_diff, declared_changes
from scripts._behaviour_classify import Allowlist, Side, classify_base_skipped, classify_cell, diff
from scripts._behaviour_signature import Context, _pydantic_item, canonical, example_value, meta_items
from scripts._change_records import covers_block
from tests.capabilities import needs
from tests.conftest import assert_output
from tests.units import REFS_HEADER

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).resolve().parents[1]
SIGNATURE_SCRIPT = ROOT / "scripts" / "_behaviour_signature.py"


PROPERTY_NAMES_ENTRY = {
    "input": "*property_names*",
    "aspects": ["type", "config", "parsed-value"],
    "match": "dict\\[|regex_engine|parsed value",
    "reason": "propertyNames keys",
    "decision": "#5017",
}


def _field(tp: str, *, required: bool = False, ctype: str | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "type": tp,
        "ctype": ctype or tp,
        "required": required,
        "alias": None,
        "validation_alias": None,
        "serialization_alias": None,
        "default": None if required else "None",
        **extra,
    }


def _model(fields: dict[str, Any], kind: str = "BaseModel", **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "bases": ["BaseModel"], "config": {}, "fields": fields, **extra}


def _module(items: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"names": sorted(items), "items": items, "adapters": dict.fromkeys(items, "ok"), **extra}


KEY_MAP_BEFORE = _module({
    "Key": {"kind": "RootModel", "bases": ["RootModel"], "config": {}, "root": "str", "croot": "str", "default": None},
    "KeyMap": {"kind": "RootModel", "bases": ["RootModel"], "config": {}, "root": "dict[Key, int]", "croot": "x"},
})
KEY_MAP_AFTER = _module({
    "Key": KEY_MAP_BEFORE["items"]["Key"],
    "KeyMap": {"kind": "RootModel", "bases": ["RootModel"], "config": {}, "root": "dict[str, int]", "croot": "x"},
})
UNION_ROOT_BEFORE = _module(
    {
        "Root": _model({"m": _field("Union[dict[str, Root1], dict[str, Root2]]")}, kind="msgspec.Struct"),
        "Root7": {"kind": "error", "error": "OverflowError: Python int too large to convert to C long"},
    },
    adapters={},
    decoders={"Root": "TypeError: Type unions may not contain more than one dict type"},
)
UNION_ROOT_AFTER = _module(
    {
        "Root": _model({"m": _field("dict[str, Union[Root1, Root2]]")}, kind="msgspec.Struct"),
        "Root7": _model({}, kind="msgspec.Struct"),
    },
    adapters={},
    decoders={"Root": "ok", "Root7": "ok"},
)
POINT = _model({"x": _field("int", required=True), "y": _field("int", required=True)}, kind="dataclass")
COLOR = {"kind": "Enum", "bases": ["Enum"], "enum_base": "str", "members": {"RED": "'red'", "GREEN": "'green'"}}
PAYLOADS = {"verdicts": {"v0": "accept", "v1": "accept", "i0": "reject"}, "values": {"v0": "a1", "v1": "b2"}}


def _payloads(**changes: str) -> dict[str, Any]:
    verdicts = {**PAYLOADS["verdicts"], **changes}
    return {"verdicts": verdicts, "values": {k: v for k, v in PAYLOADS["values"].items() if verdicts[k] == "accept"}}


CLASSIFY_CASES = {
    "property_names_accepted": (
        Side(generated=True, signature=KEY_MAP_BEFORE),
        Side(generated=True, signature=KEY_MAP_AFTER),
        "tests/data/jsonschema/property_names_root_model_keys.json",
        [PROPERTY_NAMES_ENTRY],
    ),
    "property_names_without_entry": (
        Side(generated=True, signature=KEY_MAP_BEFORE),
        Side(generated=True, signature=KEY_MAP_AFTER),
        "tests/data/jsonschema/property_names_root_model_keys.json",
        [],
    ),
    "property_names_entry_other_input": (
        Side(generated=True, signature=KEY_MAP_BEFORE),
        Side(generated=True, signature=KEY_MAP_AFTER),
        "tests/data/jsonschema/person.json",
        [PROPERTY_NAMES_ENTRY],
    ),
    "unbuildable_msgspec_decoder": (
        Side(generated=True, signature=UNION_ROOT_BEFORE),
        Side(generated=True, signature=UNION_ROOT_AFTER),
        "tests/data/jsonschema/additional_pattern_intersections",
        [],
    ),
    "accepted_beside_crash_fix": (
        Side(generated=True, signature=_module({**KEY_MAP_BEFORE["items"], **UNION_ROOT_BEFORE["items"]}, decoders={})),
        Side(generated=True, signature=_module({**KEY_MAP_AFTER["items"], **UNION_ROOT_AFTER["items"]})),
        "tests/data/jsonschema/property_names_mixed.json",
        [PROPERTY_NAMES_ENTRY],
    ),
    "leaked_placeholder_default": (
        Side(
            generated=True,
            signature=_module({"M": _model({"a": _field("str", default="'-datamodel-code-generator-#'")})}),
        ),
        Side(generated=True, signature=_module({"M": _model({"a": _field("str", default="'x'")})})),
        "tests/data/jsonschema/default_placeholder.json",
        [],
    ),
    "formatting_only": (
        Side(generated=True, signature=KEY_MAP_BEFORE),
        Side(generated=True, signature=KEY_MAP_BEFORE),
        "tests/data/jsonschema/alias_import_alias",
        [],
    ),
    "new_name_only": (
        Side(generated=True, signature=KEY_MAP_BEFORE),
        Side(generated=True, signature=_module({**KEY_MAP_BEFORE["items"], "Extra": _model({})})),
        "tests/data/jsonschema/new_name.json",
        [],
    ),
    "generation_fixed": (
        Side(generated=False, message="Error: unhashable type"),
        Side(generated=True, signature=KEY_MAP_AFTER),
        "tests/data/jsonschema/allof_constraint_intersections",
        [],
    ),
    "generation_now_fails": (
        Side(generated=True, signature=KEY_MAP_AFTER),
        Side(generated=False, message="Error: invalid schema"),
        "tests/data/jsonschema/person.json",
        [],
    ),
    "generation_refused_broken_output": (
        Side(
            generated=True,
            signature={"package": {"main": {"import_error": "SyntaxError: invalid syntax (main.py, line 7)"}}},
        ),
        Side(generated=False, message="Error: Cannot import the module 'class' from module 'main'"),
        "tests/data/jsonschema/keyword_directory_reference",
        [],
    ),
    "generation_refused_output_with_working_module": (
        Side(
            generated=True,
            signature={
                "package": {
                    "main": {"import_error": "SyntaxError: invalid syntax (main.py, line 7)"},
                    "other": KEY_MAP_BEFORE,
                }
            },
        ),
        Side(generated=False, message="Error: Cannot import the module 'class' from module 'main'"),
        "tests/data/jsonschema/keyword_sibling_reference",
        [],
    ),
    "generation_refused_output_working_below_keyword": (
        Side(
            generated=True,
            signature={
                "package": {
                    "main": {"import_error": "SyntaxError: invalid syntax (main.py, line 7)"},
                    "class.k": KEY_MAP_BEFORE,
                }
            },
        ),
        Side(generated=False, message="Error: Cannot import the module 'class' from module 'main'"),
        "tests/data/jsonschema/keyword_package_reference",
        [],
    ),
    "generation_refused_output_without_signature": (
        Side(generated=True, signature={"import_error": "signature timed out after 60s", "signature_failed": True}),
        Side(generated=False, message="Error: Cannot import the module 'class' from module 'main'"),
        "tests/data/jsonschema/keyword_slow_reference",
        [],
    ),
    "generation_refused_working_output": (
        Side(generated=True, signature=KEY_MAP_BEFORE),
        Side(generated=False, message="Error: Cannot import the module 'class' from module 'main'"),
        "tests/data/jsonschema/keyword_file_name_reference",
        [],
    ),
    "generation_refused_accepted": (
        Side(generated=True, signature=KEY_MAP_BEFORE),
        Side(generated=False, message="Cannot write the class 'Warn' in the root package beside the module 'Warn'"),
        "tests/data/jsonschema/module_named_like_root_class.json",
        [
            {
                "input": "*module_named_like_*",
                "aspects": ["generation"],
                "match": "^\\[generation\\] : now fails: Cannot write the class ",
                "reason": "the module replaced the class",
                "decision": "#6067",
            }
        ],
    ),
    "generation_refused_other_aspect": (
        Side(generated=True, signature=KEY_MAP_BEFORE),
        Side(generated=False, message="Cannot write the class 'Warn' in the root package beside the module 'Warn'"),
        "tests/data/jsonschema/module_named_like_root_class.json",
        [{"input": "*module_named_like_*", "aspects": ["type"], "reason": "a type change", "decision": "#6067"}],
    ),
    "both_fail": (Side(generated=False), Side(generated=False), "tests/data/jsonschema/broken.json", []),
    "import_fixed": (
        Side(generated=True, signature={"import_error": "NameError: name 'Node' is not defined"}),
        Side(generated=True, signature=KEY_MAP_AFTER),
        "tests/data/jsonschema/annotated_forward_reference.json",
        [],
    ),
    "import_now_fails": (
        Side(generated=True, signature=KEY_MAP_AFTER),
        Side(
            generated=True,
            signature={"package": {"__init__": {"import_error": "NameError: name 'inf' is not defined"}}},
        ),
        "tests/data/jsonschema/literal_constraints_undecided.yaml",
        [],
    ),
    "import_fails_both": (
        Side(generated=True, signature={"import_error": "TypeError: x"}),
        Side(generated=True, signature={"import_error": "TypeError: y"}),
        "tests/data/jsonschema/broken_import.json",
        [],
    ),
    "import_fails_new_module_only": (
        Side(
            generated=True,
            signature={"package": {"__init__": {"import_error": "E: a"}, "b": _module({"M": _model({})})}},
        ),
        Side(
            generated=True,
            signature={"package": {"__init__": {"import_error": "E: a"}, "b": {"import_error": "E: b"}}},
        ),
        "tests/data/openapi/modular",
        [],
    ),
    "import_fixed_one_module": (
        Side(
            generated=True,
            signature={"package": {"__init__": {"import_error": "E: a"}, "b": {"import_error": "E: b"}}},
        ),
        Side(
            generated=True,
            signature={"package": {"__init__": {"import_error": "E: a"}, "b": _module({"M": _model({})})}},
        ),
        "tests/data/openapi/modular",
        [],
    ),
    "entry_without_input_and_reason": (
        Side(generated=True, signature=KEY_MAP_BEFORE),
        Side(generated=True, signature=KEY_MAP_AFTER),
        "tests/data/jsonschema/property_names.json",
        [{"aspects": ["type"]}],
    ),
    "rejected_valid_payloads_with_signature_change": (
        Side(
            generated=True,
            signature=_module({"Payload": _model({"a": _field("int")})}, payloads=_payloads(v0="reject", v1="reject")),
        ),
        Side(generated=True, signature=_module({"Payload": _model({"a": _field("str")})}, payloads=PAYLOADS)),
        "payload:jsonschema/pattern.json",
        [],
    ),
    "field_changes": (
        Side(
            generated=True,
            signature=_module({"M": _model({"a": _field("int"), "b": _field("str"), "c": _field("int")})}),
        ),
        Side(
            generated=True,
            signature=_module({
                "M": _model({
                    "a": _field("int", ctype="Annotated[int, Ge(ge=0)]"),
                    "b": _field("str", required=True, alias="B"),
                    "d": _field("int", required=True),
                })
            }),
        ),
        "tests/data/jsonschema/fields.json",
        [],
    ),
    "enum_and_constructor_changes": (
        Side(
            generated=True,
            signature=_module({"Color": COLOR, "Point": {**POINT, "positional": ["x", "y"]}, "Root1": {}}),
        ),
        Side(
            generated=True,
            signature=_module({
                "Color": {**COLOR, "members": {"RED": "'Red'", "BLUE": "'blue'"}},
                "Point": {**POINT, "positional": ["y", "x"]},
            }),
        ),
        "tests/data/jsonschema/enum_order.json",
        [],
    ),
    "package_import_paths": (
        Side(
            generated=True,
            signature={
                "package": {
                    "__init__": _module({}, exports={"Pet": "pets"}),
                    "pets": _module({"Pet": _model({})}),
                    "legacy": _module({"Old": _model({})}),
                }
            },
        ),
        Side(
            generated=True,
            signature={
                "package": {"__init__": _module({}), "pets": _module({"Pet": {**_model({}), "kind": "TypedDict"}})}
            },
        ),
        "tests/data/openapi/modular",
        [],
    ),
    "submodule_binding_renamed": (
        Side(
            generated=True,
            signature={
                "package": {
                    "holder": _module({"Holder": _model({})}, exports={"list": "<module app.list>"}),
                    "app.list": _module({"Pop": _model({})}),
                }
            },
        ),
        Side(
            generated=True,
            signature={
                "package": {
                    "holder": _module({"Holder": _model({})}, exports={"list_1": "<module app.list>"}),
                    "app.list": _module({"Pop": _model({})}),
                }
            },
        ),
        "tests/data/jsonschema/module_import_named_like_builtin.json",
        [],
    ),
    "submodule_binding_with_module_removed": (
        Side(
            generated=True,
            signature={
                "package": {
                    "holder": _module({"Holder": _model({})}, exports={"list": "<module app.list>"}),
                    "app.list": _module({"Pop": _model({})}),
                }
            },
        ),
        Side(
            generated=True,
            signature={
                "package": {
                    "holder": _module({"Holder": _model({})}, exports={"list_1": "<module app.list_1>"}),
                    "app.list_1": _module({"Pop": _model({})}),
                }
            },
        ),
        "tests/data/jsonschema/module_import_named_like_builtin.json",
        [],
    ),
    "import_error_on_both_sides_beside_a_changed_module": (
        Side(
            generated=True,
            signature={
                "package": {"__init__": {"import_error": "E: a"}, "b": _module({"M": _model({"x": _field("int")})})}
            },
        ),
        Side(
            generated=True,
            signature={
                "package": {"__init__": {"import_error": "E: a"}, "b": _module({"M": _model({"x": _field("str")})})}
            },
        ),
        "tests/data/jsonschema/person.json",
        [],
    ),
    "validation_probe_missing_in_candidate": (
        Side(
            generated=True,
            signature=_module(
                {"Child": _model({"uuidValue": _field("uuid.UUID")})},
                probes={"Child": "error:TypeError: Unable to apply constraint 'min_length' to supplied value"},
            ),
        ),
        Side(generated=True, signature=_module({"Child": _model({"uuidValue": _field("str")})})),
        "tests/data/openapi/allof_no_merge_constraint_type_shape.yaml",
        [],
    ),
    "validation_probe_rejected_now": (
        Side(
            generated=True,
            signature=_module({"Child": _model({"uuidValue": _field("uuid.UUID")})}, probes={"Child": "accept"}),
        ),
        Side(
            generated=True,
            signature=_module({"Child": _model({"uuidValue": _field("uuid.UUID")})}, probes={"Child": "reject"}),
        ),
        "tests/data/openapi/allof_no_merge_constraint_type_shape.yaml",
        [],
    ),
    "validation_probe_raised_in_release": (
        Side(
            generated=True,
            signature=_module(
                {"Child": _model({"uuidValue": _field("uuid.UUID", required=True, ctype="x")})},
                probes={"Child": "error:TypeError: Unable to apply constraint 'min_length' to supplied value"},
            ),
        ),
        Side(
            generated=True,
            signature=_module(
                {"Child": _model({"uuidValue": _field("uuid.UUID", required=True)})}, probes={"Child": "accept"}
            ),
        ),
        "tests/data/openapi/allof_no_merge_constraint_type_shape.yaml",
        [],
    ),
    "validation_probe_raises_on_both_sides": (
        Side(
            generated=True,
            signature=_module(
                {"Foo": {**_model({}, kind="TypedDict"), "config": {"extra_items": "Bar"}}},
                probes={"Foo": "error:PydanticUserError: `TypeAdapter[<class 'model.Foo'>]` is not fully defined"},
            ),
        ),
        Side(
            generated=True,
            signature=_module(
                {"Foo": {**_model({}, kind="TypedDict"), "config": {"extra_items": "Bar1"}}},
                probes={"Foo": "error:PydanticUserError: `TypeAdapter[<class 'model.Foo'>]` is not fully defined"},
            ),
        ),
        "tests/data/openapi/typed_dict_extra_items_final_name/renamed.yaml",
        [],
    ),
    "validation_probe_raises_now": (
        Side(
            generated=True,
            signature=_module({"Child": _model({"uuidValue": _field("uuid.UUID")})}, probes={"Child": "reject"}),
        ),
        Side(
            generated=True,
            signature=_module(
                {"Child": _model({"uuidValue": _field("uuid.UUID")})},
                probes={"Child": "error:TypeError: Unable to apply constraint 'min_length' to supplied value"},
            ),
        ),
        "tests/data/openapi/allof_no_merge_constraint_type_shape.yaml",
        [],
    ),
    "payload_verdict_changed": (
        Side(generated=True, signature=_module({"Payload": _model({})}, payloads=PAYLOADS)),
        Side(generated=True, signature=_module({"Payload": _model({})}, payloads=_payloads(v1="reject"))),
        "payload:jsonschema/minimum.json",
        [],
    ),
    "payload_crash_in_release": (
        Side(generated=True, signature=_module({"Payload": _model({})}, payloads=_payloads(v1="error:TypeError"))),
        Side(generated=True, signature=_module({"Payload": _model({})}, payloads=PAYLOADS)),
        "payload:jsonschema/property_names_root_model_keys.json",
        [],
    ),
    "listed_input_accepted": (
        Side(generated=True, signature=KEY_MAP_BEFORE | {"payloads": PAYLOADS}),
        Side(
            generated=True,
            signature=KEY_MAP_AFTER | {"payloads": {**PAYLOADS, "values": {**PAYLOADS["values"], "v1": "c3"}}},
        ),
        "payload:jsonschema/property_names_root_model_keys.json",
        [{**PROPERTY_NAMES_ENTRY, "input": ["tests/data/a.json", "payload:*property_names*"]}],
    ),
    "parsed_value_accepted": (
        Side(generated=True, signature=KEY_MAP_BEFORE | {"payloads": PAYLOADS}),
        Side(
            generated=True,
            signature=KEY_MAP_AFTER | {"payloads": {**PAYLOADS, "values": {**PAYLOADS["values"], "v1": "c3"}}},
        ),
        "payload:jsonschema/property_names_root_model_keys.json",
        [PROPERTY_NAMES_ENTRY],
    ),
    "release_rejected_every_valid_payload": (
        Side(generated=True, signature=_module({"Payload": _model({})}, payloads=_payloads(v0="reject", v1="reject"))),
        Side(generated=True, signature=_module({"Payload": _model({})}, payloads=PAYLOADS)),
        "payload:jsonschema/pattern.json",
        [],
    ),
}


@pytest.mark.parametrize("case", sorted(CLASSIFY_CASES))
def test_classify_cell(case: str) -> None:
    """Each cell gets one class from the signatures of both sides; the changes behind it are listed."""
    base, candidate, input_path, entries = CLASSIFY_CASES[case]
    verdict = classify_cell(
        base,
        candidate,
        input_path=input_path,
        backend="pydantic_v2.BaseModel",
        allowlist=Allowlist("0.86.0", entries),
        valid_payloads=frozenset({"v0", "v1"}),
    )
    changes = [change.line() for change in diff(base.signature or {}, candidate.signature or {})]
    result = {**asdict(verdict), "accepted_by": sorted(verdict.accepted_by), "changes": changes}
    assert_output(json.dumps(result, indent=2) + "\n")


ALLOWLIST_CASES = {
    "valid": ("0.86.0", [PROPERTY_NAMES_ENTRY]),
    "missing_input": ("0.86.0", [{k: v for k, v in PROPERTY_NAMES_ENTRY.items() if k != "input"}]),
    "missing_decision": ("0.86.0", [{**PROPERTY_NAMES_ENTRY, "decision": ""}]),
    "private_decision": ("0.86.0", [{**PROPERTY_NAMES_ENTRY, "decision": "owner chat 2026-10-26"}]),
    "decisions": ("0.86.0", [{**PROPERTY_NAMES_ENTRY, "decision": "#5291, #5283"}]),
    "other_base": ("0.85.0", [PROPERTY_NAMES_ENTRY]),
    "empty_other_base": ("0.85.0", []),
    "newer_base": ("0.87.0", [PROPERTY_NAMES_ENTRY]),
    "unreleased_base": ("main", [PROPERTY_NAMES_ENTRY]),
}


@pytest.mark.parametrize("case", sorted(ALLOWLIST_CASES))
def test_allowlist_problems(case: str) -> None:
    """An entry needs public decision references; a table with entries belongs to the base release or an older one."""
    base, entries = ALLOWLIST_CASES[case]
    assert_output("\n".join(Allowlist(base, entries).problems("0.86.0")) + "\n")


REPORT_ENTRY = {**PROPERTY_NAMES_ENTRY, "input": "tests/data/*.json", "decision": "#5017"}
REPORT_CASES = {
    "pr_covered_unused": ("pr", [REPORT_ENTRY], "tests/data/a.json", "1/1"),
    "pr_covers_no_cell": ("pr", [REPORT_ENTRY], "tests/data/a.yaml", "1/1"),
    "nightly_covered_unused": ("nightly", [REPORT_ENTRY], "tests/data/a.json", "1/1"),
    "nightly_listed_unused": (
        "nightly",
        [{**REPORT_ENTRY, "input": ["tests/data/b.json", "tests/data/a.json"]}],
        "tests/data/a.json",
        "1/1",
    ),
    "release_listed_covers_no_cell": (
        "release",
        [{**REPORT_ENTRY, "input": ["tests/data/b.json"]}],
        "tests/data/a.json",
        "1/1",
    ),
    "release_covered_unused": ("release", [REPORT_ENTRY], "tests/data/a.json", "1/1"),
    "release_covers_no_cell": ("release", [REPORT_ENTRY], "tests/data/a.yaml", "1/1"),
    "release_shard_covers_no_cell": ("release", [REPORT_ENTRY], "tests/data/a.yaml", "1/2"),
    "pr_malformed_entry": ("pr", [{**REPORT_ENTRY, "decision": ""}], "tests/data/a.json", "1/1"),
}


@pytest.mark.parametrize("case", sorted(REPORT_CASES))
def test_report_stale_entries_by_tier(case: str) -> None:
    """The PR tier is a sample and judges no staleness; the full tiers do; malformed entries fail every tier."""
    tier, entries, label, shard = REPORT_CASES[case]
    allowlist = Allowlist("0.86.0", entries)
    cell = SimpleNamespace(label=label, backend="pydantic_v2.BaseModel")
    result = SimpleNamespace(
        cell=cell, verdict=SimpleNamespace(cls="same", accepted_by=set(), notes=[]), base_skip=None
    )
    gate = SimpleNamespace(allowlist=allowlist, loaded_allowlist=allowlist, cache=SimpleNamespace(hits=0))
    index, _, total = shard.partition("/")
    ns = SimpleNamespace(
        base="0.86.0",
        tier=tier,
        shard=shard,
        shard_index=int(index),
        shard_total=int(total),
        select=[],
        check=True,
        json=None,
    )
    stdout = StringIO()
    with redirect_stdout(stdout):
        code = behaviour_diff._report([result], gate, ns)
    assert_output(f"exit={code}\n{stdout.getvalue()}")


BASE_SKIP_ENTRY = {
    "input": "tests/data/*.json",
    "bases": ["<=0.86.0"],
    "reason": "0.86.0 loops on the input",
    "issue": "#5935",
}
BASE_SKIP_PROBLEM_CASES = {
    "valid": BASE_SKIP_ENTRY,
    "versions": {**BASE_SKIP_ENTRY, "bases": ["0.85.0", "0.86.0"]},
    "missing_input": {k: v for k, v in BASE_SKIP_ENTRY.items() if k != "input"},
    "missing_bases": {k: v for k, v in BASE_SKIP_ENTRY.items() if k != "bases"},
    "missing_reason": {**BASE_SKIP_ENTRY, "reason": ""},
    "missing_issue": {k: v for k, v in BASE_SKIP_ENTRY.items() if k != "issue"},
    "private_issue": {**BASE_SKIP_ENTRY, "issue": "owner chat 2026-11-04"},
    "bases_not_a_list": {**BASE_SKIP_ENTRY, "bases": "<=0.86.0"},
    "bad_version": {**BASE_SKIP_ENTRY, "bases": ["<0.86.0", "latest"]},
}


@pytest.mark.parametrize("case", sorted(BASE_SKIP_PROBLEM_CASES))
def test_base_skip_problems(case: str) -> None:
    """A `[[base_skip]]` entry needs an input, releases, a reason and public issue references."""
    allowlist = Allowlist("0.86.0", [], [BASE_SKIP_PROBLEM_CASES[case]])
    assert_output("\n".join(allowlist.problems("0.86.0")) + "\n")


BASE_SKIP_VERSIONS = ("0.85.0", "0.86.0", "0.86.1", "0.87.0", "0.100.0")
BASE_SKIP_CASES = {
    "maximum": ["<=0.86.0"],
    "exact": ["0.86.0"],
    "several": ["0.85.0", "0.87.0"],
    "two_digit_minor": ["<=0.100.0"],
    "none": [],
}


@pytest.mark.parametrize("case", sorted(BASE_SKIP_CASES))
def test_base_skip_for(case: str) -> None:
    """A release is skipped on an input when one of the entry's `bases` is it or a maximum at or above it."""
    allowlist = Allowlist("0.86.0", [], [{**BASE_SKIP_ENTRY, "bases": BASE_SKIP_CASES[case]}])
    skipped = {
        version: (allowlist.skip_for("tests/data/a.json", version), allowlist.skip_applies(0, version))
        for version in BASE_SKIP_VERSIONS
    }
    other = allowlist.skip_for("tests/data/a.yaml", "0.85.0")
    assert_output(json.dumps({"versions": skipped, "other_input": other}, indent=2) + "\n")


BASE_SKIPPED_CASES = {
    "generated": Side(generated=True, signature=_module({"Model": _model({})})),
    "generation_fails": Side(generated=False, message="ValueError: cannot resolve the pointer"),
    "import_fails": Side(generated=True, signature={"import_error": "ImportError: cannot import name 'X'"}),
    "module_import_fails": Side(
        generated=True,
        signature={"package": {"__init__": _module({}), "a": {"import_error": "NameError: name 'Y' is not defined"}}},
    ),
}


@pytest.mark.parametrize("case", sorted(BASE_SKIPPED_CASES))
def test_classify_base_skipped(case: str) -> None:
    """A cell whose release did not run is skipped when the candidate generated and imported, a violation otherwise."""
    verdict = classify_base_skipped(BASE_SKIPPED_CASES[case], note="base skipped: loops (#5935)")
    assert_output(json.dumps(asdict(verdict) | {"accepted_by": []}, indent=2) + "\n")


BASE_SKIP_REPORT_CASES = {
    "nightly_whole_used": ("nightly", "1/1", [], True, "0.86.0"),
    "nightly_whole_covers_no_cell": ("nightly", "1/1", [], False, "0.86.0"),
    "release_whole_covers_no_cell": ("release", "1/1", [], False, "0.86.0"),
    "pr_covers_no_cell": ("pr", "1/1", [], False, "0.86.0"),
    "nightly_shard_covers_no_cell": ("nightly", "2/3", [], False, "0.86.0"),
    "nightly_select_covers_no_cell": ("nightly", "1/1", ["tests/data/*.yaml"], False, "0.86.0"),
    "nightly_base_not_named": ("nightly", "1/1", [], False, "0.87.0"),
}


@pytest.mark.parametrize("case", sorted(BASE_SKIP_REPORT_CASES))
def test_report_base_skipped_cells_and_stale_entries(case: str) -> None:
    """The report counts the skipped cells per entry; a whole-tier run fails an entry that covers no cell."""
    tier, shard, select, used, base = BASE_SKIP_REPORT_CASES[case]
    allowlist = Allowlist("0.86.0", [], [BASE_SKIP_ENTRY])
    cell = SimpleNamespace(label="tests/data/a.json", backend="pydantic_v2.BaseModel", option_label="default")
    verdict = SimpleNamespace(cls="base-skipped" if used else "same", accepted_by=set(), notes=[])
    result = SimpleNamespace(cell=cell, verdict=verdict, base_skip=0 if used else None)
    gate = SimpleNamespace(allowlist=allowlist, loaded_allowlist=allowlist, cache=SimpleNamespace(hits=0))
    ns = SimpleNamespace(
        base=base,
        tier=tier,
        shard=shard,
        shard_index=int(shard.partition("/")[0]),
        shard_total=int(shard.partition("/")[2]),
        select=select,
        check=True,
        json=None,
    )
    stdout = StringIO()
    with redirect_stdout(stdout):
        code = behaviour_diff._report([result, result], gate, ns)
    assert_output(f"exit={code}\n{stdout.getvalue()}")


SHARDED_REPORT_CASES = {
    "shard_1_holds_the_entries": ("1/2", "tests/data/a.json", ["tests/data/a.json", "tests/data/a.yaml"]),
    "shard_2_holds_the_entries": ("2/2", "tests/data/a.json", ["tests/data/a.json", "tests/data/a.yaml"]),
    "shard_1_without_the_entries": ("1/2", "tests/data/a.yaml", ["tests/data/a.json", "tests/data/a.yaml"]),
    "shard_1_entries_cover_no_cell": ("1/2", "tests/data/a.yaml", ["tests/data/a.yaml"]),
    "shard_2_entries_cover_no_cell": ("2/2", "tests/data/a.yaml", ["tests/data/a.yaml"]),
}


@pytest.mark.parametrize("case", sorted(SHARDED_REPORT_CASES))
def test_report_stale_entries_of_a_shard(case: str) -> None:
    """The shard that runs the cells of an entry judges it; shard 1 also fails the entries that cover no cell."""
    shard, label, run_labels = SHARDED_REPORT_CASES[case]
    allowlist = Allowlist("0.86.0", [REPORT_ENTRY], [BASE_SKIP_ENTRY])
    cell = SimpleNamespace(label=label, backend="pydantic_v2.BaseModel", option_label="default")
    verdict = SimpleNamespace(cls="same", accepted_by=set(), notes=[])
    result = SimpleNamespace(cell=cell, verdict=verdict, base_skip=allowlist.skip_for(label, "0.86.0"))
    run = behaviour_diff.place([(name, cell.backend) for name in run_labels], allowlist, "0.86.0")
    gate = SimpleNamespace(allowlist=allowlist, loaded_allowlist=allowlist, cache=SimpleNamespace(hits=0))
    index, _, total = shard.partition("/")
    ns = SimpleNamespace(
        base="0.86.0",
        tier="nightly",
        shard=shard,
        shard_index=int(index),
        shard_total=int(total),
        select=[],
        check=True,
        json=None,
    )
    stdout = StringIO()
    with redirect_stdout(stdout):
        code = behaviour_diff._report([result], gate, ns, run)
    assert_output(f"exit={code}\n{stdout.getvalue()}")


PLACE_ENTRY = {"reason": "r", "decision": "#1"}
PLACE_PAIRS = [
    (f"tests/data/{name}", backend)
    for name in ("a.json", "b.json", "c.json", "d.json")
    for backend in ("pydantic_v2.BaseModel", "msgspec.Struct")
]
PLACE_CASES = {
    "no_entries": ([], []),
    "entry_of_two_inputs": ([{**PLACE_ENTRY, "input": ["tests/data/b.json", "tests/data/d.json"]}], []),
    "entry_of_one_backend": (
        [{**PLACE_ENTRY, "input": "tests/data/[bd].json", "backends": ["msgspec.Struct"]}],
        [],
    ),
    "entries_sharing_a_cell": (
        [
            {**PLACE_ENTRY, "input": ["tests/data/c.json", "tests/data/d.json"]},
            {**PLACE_ENTRY, "input": ["tests/data/b.json", "tests/data/c.json"], "backends": ["msgspec.Struct"]},
            {**PLACE_ENTRY, "input": "tests/data/missing.json"},
        ],
        [],
    ),
    "base_skip_beside_an_entry": (
        [{**PLACE_ENTRY, "input": "tests/data/c.json"}],
        [
            {**BASE_SKIP_ENTRY, "input": ["tests/data/b.json", "tests/data/c.json"]},
            {**BASE_SKIP_ENTRY, "input": "tests/data/d.json", "bases": ["0.85.0"]},
        ],
    ),
}


@pytest.mark.parametrize("case", sorted(PLACE_CASES))
def test_place(case: str) -> None:
    """The cells of entries that share a cell run in the shard of their first input; the others by their own input."""
    entries, skips = PLACE_CASES[case]
    run = behaviour_diff.place(PLACE_PAIRS, Allowlist("0.86.0", entries, skips), "0.86.0")
    shards = {
        f"{label} | {backend}": run.shard(behaviour_diff.Cell(Path(label), label, "jsonschema", backend), 3)
        for label, backend in PLACE_PAIRS
    }
    data = {"anchors": {" | ".join(pair): anchor for pair, anchor in sorted(run.anchors.items())}, "shards": shards}
    assert_output(json.dumps({**data, "entries": sorted(run.entries), "skips": sorted(run.skips)}, indent=2) + "\n")


OPT_IN_CASES = {
    "known": ["--input", "a.json", "--output-model-type", "msgspec.Struct", "--collapse-root-models"],
    "unknown_option": ["--input", "a.json", "--new-option"],
    "unknown_choice": ["--input-file-type", "proto3"],
    "unknown_inline_choice": ["--preset=standard-py314-20261027"],
    "flag_before_option": ["--collapse-root-models", "--use-annotated"],
}
INVENTORY = {
    "--input": None,
    "--input-file-type": ["jsonschema", "openapi"],
    "--output-model-type": ["pydantic_v2.BaseModel", "msgspec.Struct"],
    "--preset": ["standard-py314-20261017"],
    "--collapse-root-models": None,
    "--use-annotated": None,
}


@pytest.mark.parametrize("case", sorted(OPT_IN_CASES))
def test_opt_in_reason(case: str) -> None:
    """A cell is opt-in when the release lacks one of its options or option values."""
    assert_output(f"{behaviour_diff.opt_in_reason(OPT_IN_CASES[case], INVENTORY)}\n")


WITHOUT_OPTION_CASES = {
    "new_flag": (
        ["--snake-case-field", "--pin-aliases-with-alias-generator", "--target-pydantic-version", "2"],
        "--pin-aliases-with-alias-generator",
    ),
    "new_preset_value": (["--preset", "standard-py314-20261027"], "--preset standard-py314-20261027"),
    "new_valued_option": (["--use-annotated", "--naming-strategy", "full-path"], "--naming-strategy"),
    "new_inline_value": (["--naming-strategy=full-path", "--use-annotated"], "--naming-strategy"),
    "new_value_of_known_option": (
        ["--target-python-version", "3.10", "--output-datetime-class", "AwareDatetime"],
        "--output-datetime-class AwareDatetime",
    ),
}


@pytest.mark.parametrize("case", sorted(WITHOUT_OPTION_CASES))
def test_without_option(case: str) -> None:
    """The baseline of a new option's example keeps every other option of the cell."""
    options, reason = WITHOUT_OPTION_CASES[case]
    flags = {"--snake-case-field", "--pin-aliases-with-alias-generator", "--use-annotated"}
    assert_output(" ".join(behaviour_diff.without_option(options, reason, flags)) + "\n")


def test_preset_option_sets() -> None:
    """The newest released date runs for py310 and py314 with every type; older dates for py312 with two types."""
    presets = [
        f"{family}-py{python}-{date}"
        for date in ("20260909", "20261017")
        for family in ("standard", "practical")
        for python in (310, 312, 314)
    ]
    sets = behaviour_diff.preset_option_sets(presets, ["pydantic_v2.BaseModel", "typing.TypedDict", "msgspec.Struct"])
    assert_output("".join(f"{backend} {' '.join(options)}\n" for backend, options in sets))


SIGNATURE_CASES: dict[str, tuple[str, dict[str, str], list[dict[str, Any]] | None]] = {
    "alias_none_union": (
        "dataclasses.dataclass",
        {
            "model.py": (
                "from __future__ import annotations\n"
                "from dataclasses import dataclass\n"
                "from typing import Optional, TypeAlias, Union\n"
                "Version: TypeAlias = None\n"
                "@dataclass\n"
                "class Model:\n"
                "    version: Union[Version, None] = None\n"
                "    other: 'Optional[Version]' = None\n"
            )
        },
        None,
    ),
    "alias_to_enum": (
        "pydantic_v2.BaseModel",
        {
            "model.py": (
                "from enum import Enum\n"
                "from typing import TypeAlias\n"
                "class Letter(Enum):\n"
                "    a = 'a'\n"
                "LetterItem: TypeAlias = Letter\n"
                "Letters = list[LetterItem]\n"
            )
        },
        None,
    ),
    "typeddict_not_required": (
        "typing.TypedDict",
        {
            "model.py": (
                "from __future__ import annotations\n"
                "from typing import Annotated, Union\n"
                "from typing_extensions import NotRequired, ReadOnly, TypedDict\n"
                "class Model(TypedDict):\n"
                "    a: NotRequired[Union[int, str, None]]\n"
                "    b: ReadOnly[Annotated[str, 'doc']]\n"
            )
        },
        None,
    ),
    "msgspec_struct_config": (
        "msgspec.Struct",
        {
            "model.py": (
                "from __future__ import annotations\n"
                "from typing import Annotated\n"
                "from msgspec import Meta, Struct\n"
                "class Key(Struct):\n"
                "    name: str\n"
                "class Model(Struct, kw_only=True, forbid_unknown_fields=True):\n"
                "    keys: dict[Key, int]\n"
                "    small: Annotated[int, Meta(ge=0, description='x')] | None = None\n"
            )
        },
        None,
    ),
    "huge_integer_bound": (
        "pydantic_v2.BaseModel",
        {
            "model.py": (
                "from pydantic import BaseModel, Field\n"
                "class Model(BaseModel):\n"
                "    big: int = Field(..., le=10**5000, description='x')\n"
            )
        },
        None,
    ),
    "dataclass_positional_order": (
        "dataclasses.dataclass",
        {
            "model.py": (
                "from dataclasses import dataclass, field\n"
                "@dataclass\n"
                "class Point:\n"
                "    x: int\n"
                "    y: list[int] = field(default_factory=list)\n"
                "@dataclass(kw_only=True)\n"
                "class Box:\n"
                "    size: int = 1\n"
            )
        },
        None,
    ),
    "package_reexport": (
        "pydantic_v2.BaseModel",
        {
            "pkg/__init__.py": "from .pets import Pet\n",
            "pkg/pets.py": (
                "from pydantic import BaseModel, Field\n"
                "class Pet(BaseModel):\n"
                "    name: str = Field(..., alias='Name', min_length=1)\n"
            ),
            "pkg/broken.py": "raise ValueError('cannot import')\n",
        },
        None,
    ),
    "payload_verdicts": (
        "pydantic_v2.BaseModel",
        {
            "model.py": (
                "from pydantic import BaseModel, RootModel\n"
                "class Name(RootModel[str]):\n"
                "    root: str\n"
                "class Payload(BaseModel):\n"
                "    name: Name\n"
                "    tags: dict[str, int] | None = None\n"
            )
        },
        [
            {"id": "v0", "payload": {"name": "a"}},
            {"id": "v1", "payload": {"name": "b", "tags": {"x": 1}}},
            {"id": "i0", "payload": {"name": 1}},
        ],
    ),
    "msgspec_payload_verdicts": (
        "msgspec.Struct",
        {
            "model.py": (
                "from msgspec import Struct\nclass Payload(Struct):\n    count: int\n    items: list[str] = []\n"
            )
        },
        [{"id": "v0", "payload": {"count": 1, "items": ["a"]}}, {"id": "i0", "payload": {"count": "x"}}],
    ),
    "stub_beside_module": (
        "pydantic_v2.BaseModel",
        {
            "model.py": "from mcp_types._wire_base import WireModel\n\n\nclass Model(WireModel):\n    name: str\n",
            "mcp_types/_wire_base.py": "from pydantic import BaseModel\n\n\nclass WireModel(BaseModel):\n    pass\n",
        },
        None,
    ),
    "import_error": ("pydantic_v2.BaseModel", {"model.py": "from missing_module import Thing\n"}, None),
    "validation_time_constraint_errors": (
        "pydantic_v2.BaseModel",
        {
            "model.py": (
                "from __future__ import annotations\n"
                "from datetime import date\n"
                "from typing import Annotated\n"
                "from uuid import UUID\n"
                "from pydantic import AwareDatetime, BaseModel, Field, RootModel\n"
                "from typing_extensions import TypeAliasType\n"
                "class Day(RootModel[date]):\n"
                "    root: Annotated[date, Field(max_length=10)]\n"
                "StampsAdditionalProperty = TypeAliasType(\n"
                "    'StampsAdditionalProperty', Annotated[AwareDatetime, Field(min_length=20)]\n"
                ")\n"
                "class StringConstraintsOnEmittedTypes(BaseModel):\n"
                "    at: AwareDatetime\n"
                "    id: Annotated[UUID | None, Field(min_length=36)] = None\n"
                "    days: list[Day] | None = None\n"
                "class Plain(BaseModel):\n"
                "    stamps: dict[str, AwareDatetime] | None = None\n"
            )
        },
        None,
    ),
    "typeddict_payload_verdicts": (
        "typing.TypedDict",
        {
            "model.py": (
                "from typing_extensions import NotRequired, TypedDict\n"
                "class Payload(TypedDict):\n"
                "    pair: NotRequired[tuple[str, int | bool]]\n"
                "    triple: NotRequired[tuple[float, bool, str]]\n"
            )
        },
        [
            {"id": "v0", "payload": {"pair": [1, "a"]}},
            {"id": "v1", "payload": {"pair": [True, "b"], "triple": [1.5, "x", False]}},
            {"id": "i0", "payload": {"pair": ["a", 1, 2]}},
        ],
    ),
    "msgspec_validation_probe": (
        "msgspec.Struct",
        {
            "model.py": (
                "from typing import Annotated\n"
                "from msgspec import Meta, Struct\n"
                "class Item(Struct):\n"
                "    code: Annotated[str, Meta(min_length=2, pattern='^[A-Z]')]\n"
                "    count: Annotated[int, Meta(ge=1)] = 1\n"
            )
        },
        None,
    ),
}


@pytest.mark.parametrize("case", sorted(SIGNATURE_CASES))
def test_signature(case: str, tmp_path: Path) -> None:
    """The signature of a generated module is independent of how its types are spelled."""
    backend, files, payloads = SIGNATURE_CASES[case]
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    args = [str(tmp_path / ("model.py" if "model.py" in files else "pkg")), backend]
    if payloads is not None:
        (tmp_path / "payloads.json").write_text(json.dumps(payloads), encoding="utf-8")
        args.append(str(tmp_path / "payloads.json"))
    result = subprocess.run(
        [sys.executable, "-I", str(SIGNATURE_SCRIPT), *args], capture_output=True, text=True, check=True, cwd=tmp_path
    )
    assert_output(json.dumps(json.loads(result.stdout), indent=2, sort_keys=True) + "\n")


def test_guide_changes_are_built_not_read_from_the_repository() -> None:
    """The gate names accepted changes from a fresh build of the guide data; no committed or local file is needed."""
    changes = behaviour_diff.guide_changes()
    assert_output(json.dumps({"titled": bool(changes) and all(entry["id"] and entry["title"] for entry in changes)}))


def test_candidate_payload_path_does_not_import_the_guide_builder() -> None:
    """Importing the gate script, as every candidate payload subprocess does, does not load the guide builder."""
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import scripts.behaviour_diff; "
        "print('scripts.build_migration_docs' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(ROOT)], capture_output=True, text=True, check=True, cwd=ROOT
    )
    assert_output(f"builder imported: {result.stdout.strip()}\n")


@needs("posix")
def test_gate(tmp_path: Path) -> None:
    """The gate generates the selected fixtures on both sides; the PR tier judges no stale entry."""
    allowlist = tmp_path / "allowlist.toml"
    allowlist.write_text(
        'base = "0.0.0"\n[[accepted]]\ninput = "tests/data/jsonschema/person.json"\nreason = "r"\ndecision = "#1"\n',
        encoding="utf-8",
    )
    outputs = []
    for _ in range(2):
        stdout = StringIO()
        with redirect_stdout(stdout), redirect_stderr(StringIO()):
            code = behaviour_diff.main([
                "gate",
                "0.0.0",
                str(ROOT),
                "--base-python",
                sys.executable,
                "--candidate-python",
                sys.executable,
                "--cache-dir",
                str(tmp_path / "cache"),
                "--allowlist",
                str(allowlist),
                "--select",
                "tests/data/jsonschema/person.json",
                "--select",
                "tests/data/openapi/api.yaml",
                "--json",
                str(tmp_path / "cells.jsonl"),
                "--check",
                "-j",
                "2",
            ])
        outputs.append(f"exit={code}\n{stdout.getvalue()}")
    assert_output("\n".join(outputs))


@needs("posix")
def test_gate_skips_the_release_on_a_listed_input(tmp_path: Path) -> None:
    """A release that `[[base_skip]]` names does not run for the input: its cells are `base-skipped`."""
    allowlist = tmp_path / "allowlist.toml"
    allowlist.write_text(
        'base = "0.0.0"\n[[base_skip]]\ninput = "tests/data/jsonschema/person.json"\nbases = ["<=0.0.0"]\n'
        'reason = "loops"\nissue = "#5935"\n',
        encoding="utf-8",
    )
    stdout = StringIO()
    with redirect_stdout(stdout), redirect_stderr(StringIO()):
        code = behaviour_diff.main([
            "gate",
            "0.0.0",
            str(ROOT),
            "--base-python",
            sys.executable,
            "--candidate-python",
            sys.executable,
            "--cache-dir",
            str(tmp_path / "cache"),
            "--allowlist",
            str(allowlist),
            "--select",
            "tests/data/jsonschema/person.json",
            "--select",
            "tests/data/openapi/api.yaml",
            "--json",
            str(tmp_path / "cells.jsonl"),
            "--check",
            "-j",
            "2",
        ])
    rows = [json.loads(line) for line in (tmp_path / "cells.jsonl").read_text(encoding="utf-8").splitlines()]
    cells = sorted(f"{row['input']} | {row['options']} | {row['class']} | {row['notes']}" for row in rows)
    skipped = [line for line in stdout.getvalue().splitlines() if line.startswith(("base-skipped:", "summary:"))]
    assert_output(f"exit={code}\n" + "\n".join([*skipped, *cells]) + "\n")


@needs("posix")
def test_gate_shards_judge_the_stale_entries_of_an_unsharded_run(tmp_path: Path) -> None:
    """The shards of a run report the stale entries the unsharded run reports, each from the shard of its cells."""
    allowlist = tmp_path / "allowlist.toml"
    allowlist.write_text(
        'base = "0.0.0"\n[[accepted]]\ninput = ["tests/data/jsonschema/const.json", '
        '"tests/data/jsonschema/duration.json"]\nreason = "r"\ndecision = "#1"\n'
        '[[accepted]]\ninput = "tests/data/jsonschema/boolean_property.json"\nreason = "r"\ndecision = "#2"\n',
        encoding="utf-8",
    )
    outputs, problems = [], {}
    for shard in ("1/1", "1/3", "2/3", "3/3"):
        stdout = StringIO()
        with redirect_stdout(stdout), redirect_stderr(StringIO()):
            code = behaviour_diff.main([
                "gate",
                "0.0.0",
                str(ROOT),
                "--tier",
                "nightly",
                "--types",
                "pydantic_v2.BaseModel",
                "--base-python",
                sys.executable,
                "--candidate-python",
                sys.executable,
                "--cache-dir",
                str(tmp_path / "cache"),
                "--allowlist",
                str(allowlist),
                "--select",
                "tests/data/jsonschema/boolean_property.json",
                "--select",
                "tests/data/jsonschema/const.json",
                "--select",
                "tests/data/jsonschema/duration.json",
                "--shard",
                shard,
                "-j",
                "2",
                "--check",
            ])
        lines = stdout.getvalue().splitlines()
        problems[shard] = [line for line in lines if line.startswith("allowlist:")]
        cells = [line.partition(", cached")[0] for line in lines if line.startswith("cells:")]
        outputs += [f"shard {shard}: exit={code}", *problems[shard], *cells]
    union = sorted(line for name, found in problems.items() if name != "1/1" for line in found)
    assert_output("\n".join([*outputs, f"shards == unsharded: {union == sorted(problems['1/1'])}"]) + "\n")


@needs("linux")
def test_gate_memory_cap_fails_the_run_and_is_not_cached(tmp_path: Path) -> None:
    """A run over its memory cap fails on both sides; like a timeout it is not cached, so the next run tries again."""
    allowlist = tmp_path / "allowlist.toml"
    allowlist.write_text('base = "0.0.0"\n', encoding="utf-8")
    outputs = []
    for _ in range(2):
        stdout = StringIO()
        with redirect_stdout(stdout), redirect_stderr(StringIO()):
            code = behaviour_diff.main([
                "gate",
                "0.0.0",
                str(ROOT),
                "--base-python",
                sys.executable,
                "--candidate-python",
                sys.executable,
                "--cache-dir",
                str(tmp_path / "cache"),
                "--allowlist",
                str(allowlist),
                "--select",
                "tests/data/jsonschema/person.json",
                "--memory-mb",
                "1",
                "--check",
                "-j",
                "1",
            ])
        outputs.append(f"exit={code}\n{stdout.getvalue()}")
    assert_output("\n".join(outputs))


FIRST_IMPORT_TREE = {
    "pkg/__init__.py": "",
    "pkg/a/__init__.py": "from .._internal import Item\n__all__ = ['Item']\n",
    "pkg/a/c.py": (
        "from __future__ import annotations\n"
        "from pydantic import BaseModel\n"
        "class Leaf(BaseModel):\n"
        "    code: int | None = None\n"
    ),
    "pkg/d.py": (
        "from __future__ import annotations\n"
        "from pydantic import BaseModel\n"
        "from .a import c\n"
        "class Dep(BaseModel):\n"
        "    leaf: c.Leaf | None = None\n"
    ),
    "pkg/_internal.py": (
        "from __future__ import annotations\n"
        "from pydantic import BaseModel\n"
        "from . import d\n"
        "from .a import c\n"
        "class Item(BaseModel):\n"
        "    thing: Thing | None = None\n"
        "class Thing(BaseModel):\n"
        "    item: Item | None = None\n"
        "    leaf: c.Leaf | None = None\n"
        "    dep: d.Dep | None = None\n"
        "Item.model_rebuild()\n"
    ),
    "pkg/x.py": "from ._internal import Thing\n__all__ = ['Thing']\n",
}


def test_first_import_errors(tmp_path: Path) -> None:
    """A module that imports only after a sibling (0.86.0 output of a cycle below a package) fails imported first."""
    signature = _tree_signature(FIRST_IMPORT_TREE, "pydantic_v2.BaseModel", tmp_path)
    errors = {
        module: sig["import_error"].partition(":")[0] + ": " + sig["import_error"].split(": ")[1]
        for module, sig in signature["package"].items()
        if "import_error" in sig
    }
    assert_output(json.dumps(errors, indent=2, sort_keys=True) + "\n")


MODULE_NAMED_LIKE_BUILTIN = {
    "pkg/__init__.py": (
        "from __future__ import annotations\n"
        "from msgspec import UNSET, Struct, UnsetType\n"
        "from . import holder as holder_1\n"
        "class Root(Struct):\n"
        "    holder: holder_1.Holder | UnsetType = UNSET\n"
    ),
    "pkg/app/__init__.py": (
        "from __future__ import annotations\n"
        "from msgspec import UNSET, Struct, UnsetType\n"
        "from . import list\n"
        "class Thing(Struct):\n"
        "    pop: list.Pop | UnsetType = UNSET\n"
    ),
    "pkg/app/list.py": (
        "from __future__ import annotations\n"
        "from msgspec import UNSET, Struct, UnsetType\n"
        "class Pop(Struct):\n"
        "    key: str | UnsetType = UNSET\n"
    ),
    "pkg/holder.py": (
        "from __future__ import annotations\n"
        "from msgspec import UNSET, Struct, UnsetType\n"
        "from . import app\n"
        "from .app import list\n"
        "class Holder(Struct):\n"
        "    tags: list[str] | UnsetType = UNSET\n"
        "    pop: list.Pop | UnsetType = UNSET\n"
        "    thing: app.Thing | UnsetType = UNSET\n"
    ),
}
UUID_MIN_LENGTH = (
    "from __future__ import annotations\n"
    "from datetime import date\n"
    "from uuid import UUID\n"
    "from pydantic import BaseModel, Field, RootModel\n"
    "class Day(RootModel[date]):\n"
    "    root: date{day}\n"
    "class Child(BaseModel):\n"
    "    uuidValue: UUID{uuid}\n"
    "    days: list[Day] | None = None\n"
)
UUID_WITH_KEYWORDS = UUID_MIN_LENGTH.format(day=" = Field(..., max_length=10)", uuid=" = Field(..., min_length=1)")
UUID_WITHOUT_KEYWORDS = UUID_MIN_LENGTH.format(day="", uuid="")
TREE_CASES: dict[str, tuple[str, dict[str, str], dict[str, str]]] = {
    "string_keywords_on_uuid_and_date": (
        "pydantic_v2.BaseModel",
        {"model.py": UUID_WITH_KEYWORDS},
        {"model.py": UUID_WITHOUT_KEYWORDS},
    ),
    "string_keywords_written_again": (
        "pydantic_v2.BaseModel",
        {"model.py": UUID_WITHOUT_KEYWORDS},
        {"model.py": UUID_WITH_KEYWORDS},
    ),
    "module_named_like_builtin": (
        "msgspec.Struct",
        MODULE_NAMED_LIKE_BUILTIN,
        {
            **MODULE_NAMED_LIKE_BUILTIN,
            "pkg/holder.py": MODULE_NAMED_LIKE_BUILTIN["pkg/holder.py"]
            .replace("import list\n", "import list as list_1\n")
            .replace("list.Pop", "list_1.Pop"),
        },
    ),
}


def _tree_signature(files: dict[str, str], backend: str, directory: Path) -> dict[str, Any]:
    for name, text in files.items():
        (directory / name).parent.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(text, encoding="utf-8")
    target = directory / ("model.py" if "model.py" in files else "pkg")
    result = subprocess.run(
        [sys.executable, "-I", str(SIGNATURE_SCRIPT), str(target), backend],
        capture_output=True,
        text=True,
        check=True,
        cwd=directory,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("case", sorted(TREE_CASES))
def test_classify_trees(case: str, tmp_path: Path) -> None:
    """Released and candidate trees of real cases classify by their validation probes and import paths.

    String keywords on a UUID or date raise only while validating; a submodule bound as `list` is no re-export.
    """
    backend, base_files, candidate_files = TREE_CASES[case]
    sides = [
        Side(generated=True, signature=_tree_signature(files, backend, tmp_path / name))
        for name, files in (("base", base_files), ("cand", candidate_files))
    ]
    verdict = classify_cell(*sides, input_path=case, backend=backend, allowlist=Allowlist("0.86.0"))
    assert_output(json.dumps({**asdict(verdict), "accepted_by": sorted(verdict.accepted_by)}, indent=2) + "\n")


EXAMPLE_CASES: dict[str, dict[str, Any]] = {
    "ref_with_defs": {"$defs": {"Day": {"type": "string", "format": "date"}}, "$ref": "#/$defs/Day"},
    "optional_prefers_value": {"anyOf": [{"type": "null"}, {"type": "string", "format": "uuid", "minLength": 2}]},
    "null_only": {"anyOf": [{"type": "null"}]},
    "all_of_merged": {
        "definitions": {"Base": {"type": "object", "properties": {"a": {"type": "integer", "minimum": 3}}}},
        "allOf": [{"$ref": "#/definitions/Base"}, {"properties": {"b": {"type": "boolean"}}}],
    },
    "tuple_items": {"type": "array", "prefixItems": [{"type": "string"}, {"type": "integer"}]},
    "min_items": {"type": "array", "items": {"enum": ["x", "y"]}, "minItems": 3},
    "string_min_length": {"type": "string", "minLength": 4},
    "number_bounds": {"type": "number", "exclusiveMinimum": 2, "multipleOf": 0.5},
    "integer_maximum": {"type": "integer", "maximum": -3},
    "narrow_exclusive_interval": {"type": "number", "exclusiveMinimum": 0.2, "exclusiveMaximum": 0.8},
    "bounded_multiple": {"type": "integer", "minimum": 1, "maximum": 10, "multipleOf": 3},
    "properties_and_extra_values": {
        "type": "object",
        "properties": {"a": {"type": "integer"}},
        "additionalProperties": {"type": "string"},
    },
    "map_values": {"type": "object", "additionalProperties": {"type": "string", "format": "date-time"}},
    "nullable_type_list": {"type": ["null", "integer"], "minimum": 1},
    "const": {"const": "box"},
    "untyped_object": {"type": "object"},
    "recursive": {
        "$defs": {"Node": {"type": "object", "properties": {"next": {"$ref": "#/$defs/Node"}}}},
        "$ref": "#/$defs/Node",
    },
    "any": {},
}


@pytest.mark.parametrize("case", sorted(EXAMPLE_CASES))
def test_example_value(case: str) -> None:
    """The probe value of a validator's own schema reaches every property, map value and item."""
    assert_output(json.dumps(example_value(EXAMPLE_CASES[case])) + "\n")


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


PROJECT_RUN_CASES = {
    "checkout_fails": "missing-repo",
    "input_missing": "repo",
}


@pytest.mark.parametrize("case", sorted(PROJECT_RUN_CASES))
def test_project_cell_problems_are_violations(case: str, tmp_path: Path) -> None:
    """A project whose checkout fails or lacks the input is one violation, never a crash of the shard."""
    repo_name = PROJECT_RUN_CASES[case]
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    (repo / "README").write_text("x\n", encoding="utf-8")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "pinned", cwd=repo)
    manifest = tmp_path / "projects.json"
    manifest.write_text(
        json.dumps({
            "projects": [
                {
                    "name": "fake",
                    "repo": "example/fake",
                    "url": str(tmp_path / repo_name),
                    "sha": _git("rev-parse", "HEAD", cwd=repo),
                    "paths": ["README"],
                    "jobs": [
                        {
                            "name": "job",
                            "input": "schemas/pet.json",
                            "input_file_type": "jsonschema",
                            "output_model_type": "pydantic_v2.BaseModel",
                            "args": [],
                            "stubs": {"base_mod/base.py": "x = 1\n"},
                        }
                    ],
                }
            ]
        }),
        encoding="utf-8",
    )
    projects, cells = behaviour_diff.load_projects(manifest, tmp_path / "cache")
    result = behaviour_diff.run_cell(SimpleNamespace(projects=projects), cells[0])
    assert_output(
        json.dumps({
            "stubs": cells[0].stubs,
            "cls": result.verdict.cls,
            "note": result.verdict.notes[0].partition(":")[0],
        })
        + "\n"
    )


@needs("posix")
def test_gate_projects_and_option_sets(tmp_path: Path) -> None:
    """The nightly tier adds option sets, TypedDict payload cells and the projects of a manifest.

    Each project is checked out at its pinned commit: a later commit changes the schema, and the gate still runs the
    pinned one.
    """
    repo = tmp_path / "repo"
    (repo / "schemas").mkdir(parents=True)
    (repo / "schemas" / "pet.json").write_text(
        json.dumps({"title": "Pet", "type": "object", "properties": {"name": {"type": "string"}}}), encoding="utf-8"
    )
    (repo / "README").write_text("not checked out\n", encoding="utf-8")
    _git("init", "-q", cwd=repo)
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "pinned", cwd=repo)
    sha = _git("rev-parse", "HEAD", cwd=repo)
    (repo / "schemas" / "pet.json").write_text("not a schema", encoding="utf-8")
    _git("commit", "-qam", "later", cwd=repo)
    manifest = tmp_path / "projects.json"
    job = {"input": "schemas/pet.json", "input_file_type": "jsonschema", "args": ["--use-annotated"]}
    manifest.write_text(
        json.dumps({
            "projects": [
                {
                    "name": "fake",
                    "repo": "example/fake",
                    "url": str(repo),
                    "sha": sha,
                    "license": "MIT",
                    "paths": ["schemas/pet.json"],
                    "jobs": [{**job, "name": "pydantic", "output_model_type": "pydantic_v2.BaseModel"}],
                }
            ]
        }),
        encoding="utf-8",
    )
    allowlist = tmp_path / "allowlist.toml"
    allowlist.write_text('base = "0.0.0"\n', encoding="utf-8")
    stdout = StringIO()
    with redirect_stdout(stdout), redirect_stderr(StringIO()):
        code = behaviour_diff.main([
            "gate",
            "0.0.0",
            str(ROOT),
            "--tier",
            "nightly",
            "--types",
            "pydantic_v2.BaseModel,typing.TypedDict",
            "--base-python",
            sys.executable,
            "--candidate-python",
            sys.executable,
            "--cache-dir",
            str(tmp_path / "cache"),
            "--allowlist",
            str(allowlist),
            "--projects",
            str(manifest),
            "--select",
            "tests/data/jsonschema/person.json",
            "--select",
            "project:*",
            "--select",
            "payload:jsonschema/collapse_root_models_tuple_items.json",
            "--json",
            str(tmp_path / "cells.jsonl"),
            "--check",
            "-j",
            "2",
        ])
    rows = [json.loads(line) for line in (tmp_path / "cells.jsonl").read_text(encoding="utf-8").splitlines()]
    cells = sorted(
        f"{row['kind']} | {row['input']} | {row['type']} | "
        f"{'<harness arguments>' if row['kind'] == 'payload' else row['options']} | {row['class']}"
        for row in rows
        if row["kind"] in {"default", "option-set", "project", "payload"}
    )
    checkout = sorted(
        path.relative_to(tmp_path / "cache" / "projects").as_posix()
        for path in (tmp_path / "cache" / "projects").rglob("*")
        if path.is_file() and ".git" not in path.parts
    )
    assert_output(f"exit={code}\n" + "\n".join([*cells, *checkout]).replace(sha[:12], "<sha>") + "\n")


FAKE_ARGUMENTS = """import argparse

arg_parser = argparse.ArgumentParser()
for option in ("--input", "--input-file-type", "--output-model-type", "--output"):
    arg_parser.add_argument(option)
arg_parser.add_argument("--disable-timestamp", action="store_true")
arg_parser.add_argument("--formatters", nargs="*")
arg_parser.add_argument("--mode", choices=("a", "b"))
"""
FAKE_HEAD = """import json
import os
import warnings
from pathlib import Path

from datamodel_code_generator.arguments import arg_parser

MODEL = "from pydantic import BaseModel\\n\\n\\nclass Model(BaseModel):\\n    a: {}\\n"
REFUSAL = "modular references require an output directory"
"""
FAKE_FIRST = (
    FAKE_HEAD
    + """

def main(argv):
    ns = arg_parser.parse_args(argv)
    spec = json.loads(Path(ns.input).read_text(encoding="utf-8"))
    out = Path(ns.output)
    if "warn" in spec:
        warnings.warn(spec["warn"], UserWarning, stacklevel=1)
    for text in spec.get("warns", []):
        warnings.warn(text, UserWarning, stacklevel=1)
    for text in set(spec.get("names", [])):
        warnings.warn(text, UserWarning, stacklevel=1)
    if "deprecated" in spec:
        warnings.warn(spec["deprecated"], DeprecationWarning, stacklevel=1)
    if "filtered" in spec:
        warnings.warn(spec["filtered"], UserWarning, stacklevel=1)
    if "say" in spec:
        print(f"writing {out} with the templates of {Path(__file__).parent}")
    if "memory" in spec:
        raise MemoryError
    if "fail" in spec:
        try:
            raise ValueError(spec["fail"])
        finally:
            out.unlink(missing_ok=True)
    if "catch" in spec:
        try:
            raise ValueError(spec["catch"])
        except (ValueError, TypeError) as error:
            print(error)
            return 1
    if "modular" in spec:
        if out.suffix == ".py":
            print(f"Error: {REFUSAL}")
            return 1
        if "crash" in spec:
            os._exit(3)
        out.mkdir()
        out /= "__init__.py"
    out.write_text(MODEL.format(spec["type"]), encoding="utf-8")
    return 0
"""
)
FAKE_MOVED = (
    FAKE_HEAD
    + """
from datamodel_code_generator._helper import warn_for_main


def _check(spec, out):
    \"\"\"The checks of a schema, moved out of main: the warnings and the errors now come from other lines.\"\"\"
    if "warn" in spec:
        warnings.warn(spec["warn"], UserWarning, stacklevel=1)
    for text in spec.get("warns", []):
        warnings.warn(text, UserWarning, stacklevel=1)
    for text in set(spec.get("names", [])):
        warnings.warn(text, UserWarning, stacklevel=1)
    if "deprecated" in spec:
        warnings.warn(spec["deprecated"], DeprecationWarning, stacklevel=1)
    if "filtered" in spec:
        warn_for_main(spec["filtered"])
    if "say" in spec:
        print(f"writing {out} with the templates of {Path(__file__).parent}")
    if "memory" in spec:
        raise MemoryError
    if "fail" in spec:
        raise ValueError(spec["fail"])


def _caught(spec):
    try:
        raise ValueError(spec["catch"])
    except (ValueError, TypeError) as error:
        print(error)
        return 1


def _package(spec, out):
    if out.suffix == ".py":
        print(f"Error: {REFUSAL}")
        return None
    if "crash" in spec:
        os._exit(3)
    out.mkdir()
    return out / "__init__.py"


def main(argv):
    ns = arg_parser.parse_args(argv)
    spec = json.loads(Path(ns.input).read_text(encoding="utf-8"))
    out = Path(ns.output)
    _check(spec, out)
    if "catch" in spec:
        return _caught(spec)
    if "modular" in spec and (out := _package(spec, out)) is None:
        return 1
    out.write_text(MODEL.format(spec["type"]), encoding="utf-8")
    return 0
"""
)
FAKE_HELPER = """import warnings


def warn_for_main(text):
    \"\"\"Warn as the CLI module does, so the filters on that module still match.\"\"\"
    warnings.warn_explicit(text, UserWarning, __file__, 1, module="datamodel_code_generator.__main__")
"""
FAKE_CHANGED = (
    FAKE_HEAD
    + """

def main(argv):
    ns = arg_parser.parse_args(argv)
    spec = json.loads(Path(ns.input).read_text(encoding="utf-8"))
    out = Path(ns.output)
    if "warn" in spec:
        warnings.warn(spec["warn"] + "!", UserWarning, stacklevel=1)
    for text in reversed(spec.get("warns", [])):
        warnings.warn(text, UserWarning, stacklevel=1)
    for text in set(spec.get("names", [])):
        warnings.warn(text, UserWarning, stacklevel=1)
    if "filtered" in spec:
        warnings.warn_explicit(spec["filtered"], UserWarning, __file__, 1, module="datamodel_code_generator.other")
    if "say" in spec:
        print(f"writing {out} with the templates of {Path(__file__).parent}")
    if "memory" in spec:
        raise MemoryError
    if "fail" in spec:
        print(f"Error: {spec['fail']}")
        return 2
    if "catch" in spec:
        try:
            raise TypeError(spec["catch"])
        except (ValueError, TypeError) as error:
            print(error)
            return 1
    if "modular" in spec:
        if out.suffix == ".py":
            print(f"Refused: {REFUSAL}")
            return 2
        if "crash" in spec:
            os._exit(3)
        out.mkdir()
        out /= "__init__.py"
    model = MODEL.format(spec["type"])
    out.write_text(model.replace(": str", ": str  # text"), encoding="utf-8")
    return 0
"""
)
FAKE_VERSIONS = {
    "first": {"arguments.py": FAKE_ARGUMENTS, "__main__.py": FAKE_FIRST},
    "moved": {"arguments.py": FAKE_ARGUMENTS, "__main__.py": FAKE_MOVED, "_helper.py": FAKE_HELPER},
    "changed": {
        "arguments.py": FAKE_ARGUMENTS.replace('("a", "b")', '("a", "b", "c")')
        + 'arg_parser.add_argument("--strip-default-none", action="store_true")\n',
        "__main__.py": FAKE_CHANGED,
    },
}
FAKE_INPUTS = {
    "caught.json": {"type": "int", "catch": "bad schema"},
    "crashes.json": {"type": "int", "modular": True, "crash": True, "warn": "careful"},
    "deprecated.json": {"type": "int", "deprecated": "old option"},
    "fails.json": {"type": "int", "fail": "boom"},
    "filtered.json": {"type": "int", "filtered": "kept for filters"},
    "memory.json": {"type": "int", "memory": True},
    "modular.json": {"type": "int", "modular": True},
    "order.json": {"type": "int", "warns": ["one", "two"]},
    "paths.json": {"type": "int", "say": True},
    "plain.json": {"type": "str"},
    "set.json": {"type": "int", "names": ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]},
    "warns.json": {"type": "int", "warn": "careful"},
}


def _fake_generator(repo: Path, versions: list[str], tag: str | None) -> list[str]:
    """Commit a stand-in generator in `versions` order (tagging the first) and return the commits."""
    package = repo / "src" / "datamodel_code_generator"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    for name, spec in FAKE_INPUTS.items():
        path = repo / "tests" / "data" / "jsonschema" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(spec), encoding="utf-8")
    _git("init", "-q", cwd=repo)
    commits = []
    for version in versions:
        for stale in package.glob("_helper.py"):
            stale.unlink()
        for name, text in FAKE_VERSIONS[version].items():
            (package / name).write_text(text, encoding="utf-8")
        _git("add", "-A", ".", cwd=repo)
        _git("commit", "-qm", version, cwd=repo)
        commits.append(_git("rev-parse", "HEAD", cwd=repo))
        if tag and len(commits) == 1:
            _git("tag", tag, cwd=repo)
    return commits


def _gate_lines(argv: list[str], cells: Path) -> list[str]:
    """Run the gate and return its exit status, summary and allowlist lines, and each cell's class and notes."""
    stdout = StringIO()
    with redirect_stdout(stdout), redirect_stderr(StringIO()):
        code = behaviour_diff.main(argv)
    rows = [json.loads(line) for line in cells.read_text(encoding="utf-8").splitlines()]
    lines = [line for line in stdout.getvalue().splitlines() if line.startswith(("summary:", "allowlist:", "not "))]
    return [
        f"exit={code}",
        *lines,
        *sorted(f"{row['input']} | {row['options']} | {row['class']} | {row['notes']}" for row in rows),
    ]


REVISION_CASES = {
    "gate_against_a_revision": (["first", "moved", "changed"], "0.1.0", 1, ["--check"]),
    "identity_fails_on_any_difference": (
        ["first", "moved", "changed"],
        "0.1.0",
        0,
        ["--identity", "--tier", "nightly"],
    ),
    "identity_keeps_a_moved_warning_and_error": (["first", "moved"], None, 0, ["--identity"]),
}


@needs("posix")
@pytest.mark.parametrize("case", sorted(REVISION_CASES))
def test_gate_against_a_git_revision(case: str, tmp_path: Path) -> None:
    """A git revision as BASE: its `src/` runs from the candidate's interpreter, and `--identity` fails on any change.

    The gate classifies by behaviour (a comment is `no-behaviour`, warnings and messages are not compared). Identity
    fails without `--check` too; it compares output, warnings as records (category, message and the module filters
    match, deprecations included), the exceptions the CLI module handled, messages without traceback frames, exit
    status, the refusal before a modular run and the option surface, and a cell with an option only the candidate
    has is a violation. So the warnings and errors a refactor moved to other lines or modules (keeping the module of
    a warning for filters, dropping a cleanup handler) stay equal; a changed warning, its order or module, a dropped
    deprecation, another exception class behind the same message, another refusal, error or output is a violation.
    Paths of the output and the package read the same on both sides, as does the order of a set; a run the memory
    cap stopped, and an input that fails on both sides in every cell, are not compared. A retry that crashed leaves
    no record of the refused attempt. A cache directory given with `..` holds the base tree the base runs. Identity
    also fails where a changed code object ran in no compared cell: the moved error paths run only in inputs that fail
    on both sides.
    """
    versions, tag, base, mode = REVISION_CASES[case]
    repo = tmp_path / "repo"
    commits = _fake_generator(repo, versions, tag)
    allowlist = tmp_path / "allowlist.toml"
    allowlist.write_text('base = "0.1.0"\n[[accepted]]\ninput = "*"\nreason = "r"\ndecision = "#1"\n', encoding="utf-8")
    argv = ["gate", commits[base], str(repo), "--types", "pydantic_v2.BaseModel", "--candidate-python", sys.executable]
    argv += [
        "--cache-dir",
        str(tmp_path / "repo" / ".." / "cache"),
        "--allowlist",
        str(allowlist),
        "--select",
        "tests/data/jsonschema/*",
    ]
    argv += ["--json", str(tmp_path / "cells.jsonl"), "-j", "2", *mode]
    runs = [_gate_lines(argv, tmp_path / "cells.jsonl") for _ in range(2)]
    trees = sorted((tmp_path / "cache" / "trees").glob("*/src/datamodel_code_generator/_version.py"))
    rerun = (
        f"second run, from the extracted tree and the cached base outputs: {'same' if runs[1] == runs[0] else runs[1]}"
    )
    text = "\n".join([*runs[0], rerun, *(tree.read_text(encoding="utf-8") for tree in trees)])
    assert_output(text.replace(commits[base][:9], "<sha>").replace(commits[base][:7], "<sha>"))


FAKE_FAILS_AFTER_WRITING = (
    FAKE_HEAD
    + """

def main(argv):
    ns = arg_parser.parse_args(argv)
    Path(ns.output).write_text(MODEL.format("%s"), encoding="utf-8")
    print("Error: boom")
    return 2
"""
)


@needs("posix")
def test_identity_compares_the_output_of_runs_that_fail_alike(tmp_path: Path) -> None:
    """Two sides that fail with the same status and message but wrote different files are a difference."""
    repo = tmp_path / "repo"
    _fake_generator(repo, ["first"], None)
    package = repo / "src" / "datamodel_code_generator"
    commits = []
    for kind in ("int", "str"):
        (package / "__main__.py").write_text(FAKE_FAILS_AFTER_WRITING % kind, encoding="utf-8")
        _git("add", "-A", ".", cwd=repo)
        _git("commit", "-qm", kind, cwd=repo)
        commits.append(_git("rev-parse", "HEAD", cwd=repo))
    argv = ["gate", commits[0], str(repo), "--types", "pydantic_v2.BaseModel", "--candidate-python", sys.executable]
    argv += ["--cache-dir", str(tmp_path / "cache"), "--select", "tests/data/jsonschema/plain.json", "--identity"]
    argv += ["--json", str(tmp_path / "cells.jsonl"), "-j", "1"]
    assert_output("\n".join(_gate_lines(argv, tmp_path / "cells.jsonl")).replace(commits[0][:9], "<sha>") + "\n")


FAKE_DEPRECATION = FAKE_FIRST.replace(
    'warnings.warn(spec["deprecated"], DeprecationWarning', 'warnings.warn(str(spec["deprecated"]), DeprecationWarning'
)
"""The candidate of the identity oracle: the deprecation branch reads its message through `str`, as before."""
GATE_ORACLES = {
    "no_input_matches_the_selection": ("tests/data/jsonschema/no_such_input*", "incomplete"),
    "changed_branch_no_cell_takes": ("tests/data/jsonschema/plain.json", "incomplete"),
    "changed_branch_a_cell_takes": ("tests/data/jsonschema/deprecated.json", "complete"),
}
"""The selection of each identity oracle run and the verdict it must give."""


def _identity_argv(tmp_path: Path, selection: str) -> tuple[list[str], str]:
    """Return the identity gate of the working tree that reads deprecations through `str` against its commit."""
    repo = tmp_path / "repo"
    commits = _fake_generator(repo, ["first"], None)
    (repo / "src" / "datamodel_code_generator" / "__main__.py").write_text(FAKE_DEPRECATION, encoding="utf-8")
    argv = ["gate", commits[0], str(repo), "--types", "pydantic_v2.BaseModel", "--candidate-python", sys.executable]
    argv += ["--cache-dir", str(tmp_path / "cache"), "--select", selection, "--identity", "-j", "1"]
    return [*argv, "--json", str(tmp_path / "cells.jsonl")], commits[0]


@pytest.mark.allow_direct_assert(
    "an oracle case fails on its own verdict, never through a stored output a fix run accepts"
)
@needs("posix")
@needs("sys-monitoring")
@pytest.mark.parametrize("case", sorted(GATE_ORACLES))
def test_identity_evidence_oracle(case: str, tmp_path: Path) -> None:
    """Identity against a revision is complete only where a compared cell ran every changed code object.

    A selection that matches no input compared nothing; a changed branch no selected cell takes was not run.
    """
    selection, wanted = GATE_ORACLES[case]
    argv, commit = _identity_argv(tmp_path, selection)
    stdout = StringIO()
    with redirect_stdout(stdout), redirect_stderr(StringIO()):
        code = behaviour_diff.main(argv)
    lines = [line for line in stdout.getvalue().splitlines() if line.startswith(("summary:", "cells:", "evidence:"))]
    complete = code == 0 and "evidence: complete" in lines
    incomplete = code == 1 and any(line.startswith("evidence: incomplete") for line in lines)
    verdict = "complete" if complete else "incomplete" if incomplete else f"exit={code}"
    assert verdict == wanted, stdout.getvalue()
    assert_output("\n".join([f"exit={code}", *lines]).replace(commit[:9], "<sha>") + "\n")


@pytest.mark.allow_direct_assert(
    "an oracle case fails on its own verdict, never through a stored output a fix run accepts"
)
@needs("posix")
def test_identity_refuses_a_shard(tmp_path: Path) -> None:
    """A shard of an identity run cannot judge what the whole run compared: `--identity --shard 1/2` is refused."""
    argv, _ = _identity_argv(tmp_path, "tests/data/jsonschema/plain.json")
    with pytest.raises(SystemExit) as refused, redirect_stderr(stderr := StringIO()):
        behaviour_diff.main([*argv, "--shard", "1/2"])
    assert refused.value.code == 2, stderr.getvalue()
    assert_output(stderr.getvalue().partition("error: ")[2])


@needs("posix")
def test_gate_refuses_a_side_that_is_no_revision(tmp_path: Path) -> None:
    """A BASE or CANDIDATE that names no release, directory or revision stops the gate.

    BASE is a released version or a revision of the candidate's repository, CANDIDATE a directory or a revision of
    this repository. A CANDIDATE directory whose `src` holds no package stops the gate too: the interpreter would run
    its own package instead; one whose package does not import stops it with the exit status of its worker.
    """
    repo = tmp_path / "repo"
    _fake_generator(repo, ["first"], None)
    empty = tmp_path / "empty"
    (empty / "tests" / "data" / "jsonschema").mkdir(parents=True)
    (empty / "tests" / "data" / "jsonschema" / "plain.json").write_text("{}", encoding="utf-8")
    installed = ["--base-python", sys.executable, "--types", "pydantic_v2.BaseModel"]
    cases = (("no-such-revision", str(repo), []), ("0.0.0", "no-such-revision", []), ("0.0.0", str(empty), installed))
    refusals = []
    for base, candidate, extra in cases:
        with pytest.raises(SystemExit) as error, redirect_stderr(StringIO()), redirect_stdout(StringIO()):
            behaviour_diff.main([
                "gate",
                base,
                candidate,
                "--candidate-python",
                sys.executable,
                "--cache-dir",
                str(tmp_path / "cache"),
                *extra,
            ])
        refusal = re.sub(r"from \S+,", "from <package>,", str(error.value.code).replace(sys.executable, "<python>"))
        refusals.append(refusal.replace(str(tmp_path), "<tmp>").replace(str(ROOT), "<root>"))
    broken = empty / "src" / "datamodel_code_generator"
    broken.mkdir(parents=True)
    (broken / "__init__.py").write_text('raise ImportError("a broken checkout")\n', encoding="utf-8")
    gate = ["gate", "0.0.0", str(empty), "--candidate-python", sys.executable, "--cache-dir", str(tmp_path / "cache")]
    with pytest.raises(RuntimeError) as crashed, redirect_stdout(StringIO()):
        behaviour_diff.main([*gate, *installed])
    assert_output("\n".join([*refusals, f"a candidate that does not import: {crashed.value}"]) + "\n")


@needs("posix")
def test_gate_identity_of_a_candidate_revision(tmp_path: Path) -> None:
    """A CANDIDATE revision of this repository runs from its extracted `src` and `tests`.

    `HEAD` is identical to the installed checkout on a fixture.
    """
    stdout = StringIO()
    with redirect_stdout(stdout), redirect_stderr(StringIO()):
        code = behaviour_diff.main([
            "gate",
            "0.0.0",
            "HEAD",
            "--base-python",
            sys.executable,
            "--types",
            "pydantic_v2.BaseModel",
            "--cache-dir",
            str(tmp_path / "cache"),
            "--select",
            "tests/data/jsonschema/person.json",
            "--identity",
            "--check",
            "-j",
            "1",
        ])
    summary = [line for line in stdout.getvalue().splitlines() if line.startswith(("summary:", "cells:"))]
    assert_output("\n".join([f"exit={code}", *summary]) + "\n")


def test_guide_writes_the_page_of_gate_cells(tmp_path: Path) -> None:
    """`guide` reads the cells of a gate run (`--cells`) and writes the page data of the release they compare."""
    cells = tmp_path / "cells.jsonl"
    row = {"input": "tests/data/jsonschema/person.json", "file_type": "jsonschema", "type": "pydantic_v2.BaseModel"}
    row |= {"options": "default", "kind": "default", "class": "same", "notes": [], "accepted_by": []}
    cells.write_text(json.dumps(row) + "\n", encoding="utf-8")
    allowlist = tmp_path / "allowlist.toml"
    allowlist.write_text('base = "0.0.0"\n', encoding="utf-8")
    code = behaviour_diff.main([
        "guide",
        "0.0.0",
        "0.0.1",
        "--base-python",
        sys.executable,
        "--candidate-python",
        sys.executable,
        "--cache-dir",
        str(tmp_path / "cache"),
        "--allowlist",
        str(allowlist),
        "--cells",
        str(cells),
        "--docs",
        str(tmp_path / "page.json"),
        "--notes",
        str(tmp_path / "notes.md"),
    ])
    page = (tmp_path / "page.json").read_text(encoding="utf-8")
    assert_output(f"exit={code}\n{page}{(tmp_path / 'notes.md').read_text(encoding='utf-8')}")


def _tree(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


@needs("posix")
def test_record_shards_merge_into_the_unsharded_record(tmp_path: Path) -> None:
    """The records of `record --shard I/N` merge into the unsharded record; a missing shard fails the merge."""
    common = [
        "0.0.0",
        str(ROOT),
        "--python",
        sys.executable,
        "--types",
        "pydantic_v2.BaseModel,msgspec.Struct",
        "--select",
        "tests/data/jsonschema/person*",
        "--select",
        "tests/data/openapi/api.yaml",
        "-j",
        "2",
    ]
    stdout = StringIO()
    with redirect_stdout(stdout), redirect_stderr(StringIO()):
        behaviour_diff.main([
            "record",
            *common,
            "--cache-dir",
            str(tmp_path / "cache"),
            "--out",
            str(tmp_path / "full"),
        ])
        for index in (1, 2, 3):
            behaviour_diff.main([
                "record",
                *common,
                "--cache-dir",
                str(tmp_path / f"cache-{index}"),
                "--out",
                str(tmp_path / f"part-{index}"),
                "--shard",
                f"{index}/3",
            ])
        parts = [str(tmp_path / f"part-{index}") for index in (1, 2, 3)]
        behaviour_diff.main(["merge-record", "0.0.0", *parts, "--out", str(tmp_path / "merged")])
        with pytest.raises(SystemExit) as missing:
            behaviour_diff.main(["merge-record", "0.0.0", *parts[:2], "--out", str(tmp_path / "incomplete")])
    shard_cells = [
        len(json.loads((tmp_path / f"part-{index}" / "0.0.0.json").read_text(encoding="utf-8"))["cells"])
        for index in (1, 2, 3)
    ]
    full = _tree(tmp_path / "full")
    assert_output(
        f"merged == unsharded: {_tree(tmp_path / 'merged') == full}\n"
        f"files: {len(full)}\n"
        f"cells per shard: {shard_cells}\n"
        f"missing shard: {missing.value.code}\n"
    )


def test_shard_argument_and_shard_part_records(tmp_path: Path) -> None:
    """A malformed `--shard` is a usage error and `query` refuses a record that is only one shard part."""
    usage = []
    for command in (["record", "0.0.0", str(ROOT)], ["gate", "0.0.0"]):
        with pytest.raises(SystemExit) as error, redirect_stderr(StringIO()) as stderr:
            behaviour_diff.main([*command, "--shard", "2/nope"])
        usage.append(
            f"{command[0]}: {error.value.code}: {stderr.getvalue().strip().splitlines()[-1].partition('error: ')[2]}"
        )
    for version, shard in (("0.0.1", None), ("0.0.2", [2, 3])):
        record: dict[str, Any] = {"version": version, "types": [], "cells": {}}
        if shard:
            record["shard"] = shard
        (tmp_path / f"{version}.json").write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(SystemExit) as part:
        behaviour_diff.main(["query", "0.0.1", "0.0.2", "--records", str(tmp_path)])
    assert_output("\n".join(usage) + f"\nshard part: {str(part.value.code).replace(str(tmp_path), '<records>')}\n")


@dataclass(frozen=True)
class _MetaWithCallable:
    check: object = None


@pytest.mark.parametrize(
    "meta",
    [
        pytest.param(_MetaWithCallable(check=3), id="dataclass-plain"),
        pytest.param(_MetaWithCallable(check=len), id="builtin"),
        pytest.param(_MetaWithCallable(check=lambda: 1), id="dataclass-address-dropped"),
        pytest.param(_MetaWithCallable(), id="dataclass-empty"),
        pytest.param(object(), id="repr-address-dropped"),
    ],
)
def test_meta_items_drop_memory_addresses(meta: object) -> None:
    """Metadata whose text carries a memory address is dropped, whether it is a dataclass or not."""
    assert_output(json.dumps(meta_items(meta)) + "\n")


CONSTRAINT_SPELLING_NAMESPACE = (
    "from decimal import Decimal\n"
    "from typing import Annotated, Optional, Union\n"
    "from annotated_types import Ge, Interval, Len, MaxLen\n"
    "from msgspec import Meta\n"
    "from pydantic import BaseModel, Field, StringConstraints, condecimal, confloat, conint, constr\n"
)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        pytest.param("Union[conint(ge=1), None, bool]", "Union[Annotated[int, Field(ge=1)], None, bool]", id="conint"),
        pytest.param("Annotated[int, Ge(1)]", "Annotated[int, Field(ge=1)]", id="ge-class"),
        pytest.param("Annotated[int, Interval(ge=0, le=9)]", "Annotated[int, Field(le=9, ge=0)]", id="interval"),
        pytest.param(
            "Union[constr(max_length=4), int]", "Union[Annotated[str, Field(max_length=4)], int]", id="constr"
        ),
        pytest.param(
            "Optional[Union[Annotated[str, StringConstraints(pattern='^a', min_length=1)], int]]",
            "Optional[Union[Annotated[str, Field(min_length=1, pattern='^a')], int]]",
            id="string-constraints",
        ),
        pytest.param("Annotated[str, Len(1, 4)]", "Annotated[str, Field(min_length=1, max_length=4)]", id="len"),
        pytest.param(
            "condecimal(ge=Decimal('1'), multiple_of=Decimal('0.01'), max_digits=5)",
            "Annotated[Decimal, Field(ge=Decimal('1'), multiple_of=Decimal('0.01'), max_digits=5)]",
            id="condecimal",
        ),
        pytest.param(
            "confloat(gt=0.5, strict=True, allow_inf_nan=False)",
            "Annotated[float, Field(gt=0.5, strict=True, allow_inf_nan=False)]",
            id="confloat-flags",
        ),
        pytest.param(
            "Annotated[str, StringConstraints(max_length=4)]", "Annotated[str, Field(max_length=5)]", id="changed-value"
        ),
        pytest.param("Union[conint(ge=1), str]", "Union[Annotated[int, Field(le=1)], str]", id="changed-bound"),
        pytest.param(
            "Union[constr(min_length=1, max_length=4), int]",
            "Union[Annotated[str, Field(max_length=4)], int]",
            id="dropped-constraint",
        ),
        pytest.param("conint(ge=1, strict=True)", "Annotated[int, Field(ge=1)]", id="dropped-strict"),
        pytest.param("constr(to_lower=True)", "str", id="dropped-flag"),
    ],
)
def test_constraint_spellings(before: str, after: str) -> None:
    """con*(), Field() and the constraint classes spell one constraint alike; a changed or dropped one differs.

    Each annotation is read as a runtime type, as the string annotation postponed evaluation leaves, and as a pydantic
    model field (whose top-level metadata pydantic moves to the field).
    """
    module = ModuleType("generated")
    exec(CONSTRAINT_SPELLING_NAMESPACE, module.__dict__)
    namespace, ctx = module.__dict__, Context(module)

    def forms(text: str) -> dict[str, str]:
        model = eval(f"type('Model', (BaseModel,), {{'__annotations__': {{'x': {text}}}}})", namespace)
        return {
            "runtime": canonical(eval(text, namespace), ctx, meta=True),
            "string": canonical(text, ctx, meta=True),
            "model-field": (_pydantic_item(model, ctx) or {})["fields"]["x"]["ctype"],
        }

    old, new = forms(before), forms(after)
    lines = [f"{form}: {'same' if old[form] == new[form] else 'differs'}: {old[form]} | {new[form]}" for form in old]
    assert_output("\n".join(lines) + "\n")


@pytest.mark.parametrize(
    "annotation",
    [
        pytest.param("Union[undefined_call(ge=1), int]", id="unresolvable-call"),
        pytest.param("Union[len('ab'), int]", id="call-not-annotated"),
        pytest.param("Annotated[int, undefined_meta]", id="unresolvable-metadata"),
        pytest.param("Annotated[Union[int, str], Field(discriminator='kind')]", id="discriminator"),
        pytest.param("Annotated[int, Meta(ge=1, description='doc')]", id="msgspec-meta"),
        pytest.param("Literal['b', 'a', undefined_value]", id="literal-values"),
    ],
)
def test_annotation_text_of_other_metadata(annotation: str) -> None:
    """Metadata that is no constraint class keeps its own text; a call or value that cannot be resolved its source."""
    module = ModuleType("generated")
    exec(CONSTRAINT_SPELLING_NAMESPACE, module.__dict__)
    assert_output(canonical(annotation, Context(module), meta=True) + "\n")


CELLS_TAG = "0.1.0"
CELLS_PACKAGE = "src/datamodel_code_generator"
CELLS_INPUTS = "tests/data/jsonschema"
CELLS_FRAGMENTS = "docs/data/migration/unreleased"
CELLS_EXPECTED = "tests/data/expected"
CELLS_PRESETS = ("standard-py310-20260101", "standard-py312-20260101", "standard-py314-20260101")
CELLS_UNRELEASED_PRESETS = (*CELLS_PRESETS, "standard-py310-20260201", "standard-py314-20260201")
CELLS_FLAGS = (
    "--disable-timestamp",
    "--strip-default-none",
    "--keyword-only",
    "--use-default-factory-for-optional-nested-models",
    "--use-annotated",
    "--generate-client",
    "--old-flag",
)
"""The flags of the stand-in generator at the release: `OPTION_SETS`, one released option, the experimental client and
one option a case removes."""
CELLS_ARGUMENTS = """import argparse

from datamodel_code_generator._behaviour import FLAGS
from datamodel_code_generator.preset_names import PresetName, get_args

arg_parser = argparse.ArgumentParser()
for option in ("--input", "--input-file-type", "--output", "--output-model-type", "--target-python-version"):
    arg_parser.add_argument(option)
arg_parser.add_argument("--class-name", default="Model")
arg_parser.add_argument("--formatters", nargs="*")
arg_parser.add_argument("--preset", choices=get_args(PresetName))
for flag in FLAGS:
    arg_parser.add_argument(flag, action="store_true")
"""
CELLS_MAIN = '''import json
import random
import warnings
from pathlib import Path

from datamodel_code_generator._behaviour import CHANGES
from datamodel_code_generator.arguments import arg_parser

try:
    from datamodel_code_generator._version import __version__
except ImportError:
    __version__ = "0.0.0"

CLASSES = {
    "pydantic_v2.BaseModel": ("from pydantic import BaseModel", "class {}(BaseModel):"),
    "pydantic_v2.dataclass": ("from pydantic.dataclasses import dataclass", "@dataclass\\nclass {}:"),
    "dataclasses.dataclass": ("from dataclasses import dataclass", "@dataclass\\nclass {}:"),
    "typing.TypedDict": ("from typing import TypedDict", "class {}(TypedDict):"),
    "msgspec.Struct": ("from msgspec import Struct", "class {}(Struct):"),
}
BODIES = {
    "": "return value",
    "body": "return value + 0",
    "reject-big": "if value > 5:\\n            raise ValueError(value)\\n        return value",
}
VALIDATOR = """

    @field_validator("a")
    @classmethod
    def check_a(cls, value):
        {}
"""


def main(argv):
    """Write one model of the schema's `type`, changed as the `CHANGES` of its `name` under the given options say."""
    ns = arg_parser.parse_args(argv)
    spec = json.loads(Path(ns.input).read_text(encoding="utf-8"))
    effects = [effect for name, when, effect in CHANGES if name == spec["name"] and when in (None, *argv)]
    if "fail" in spec and "fixed" not in effects:
        print(f"Error: {spec['fail']}")
        return 1
    if ("memory" in spec and "fixed" not in effects) or "memory" in effects:
        raise MemoryError
    for effect in effects:
        if effect.startswith("warn:"):
            warnings.warn(effect.removeprefix("warn:"), UserWarning, stacklevel=1)
    kind = next((effect.removeprefix("type:") for effect in effects if effect.startswith("type:")), spec["type"])
    note = "  # note" * ("comment" in effects) + f"  # {random.random()}" * ("random" in effects)
    backend = ns.output_model_type or "pydantic_v2.BaseModel"
    imports, header = CLASSES[backend]
    validated = spec.get("validator", False) and backend.startswith("pydantic")
    imports += "\\nfrom pydantic import field_validator" * validated
    text = f"{imports}\\n\\n\\n{header.format(ns.class_name)}\\n    a: {kind}{note}\\n"
    body = next((BODIES[effect] for effect in effects if effect in BODIES), BODIES[""])
    text += VALIDATOR.format(body) * validated + "a = (\\n" * ("broken" in effects)
    text = f"#   version:   {__version__}\\n" * spec.get("header", False) + text
    out = Path(ns.output)
    out.write_text(text, encoding="utf-8")
    if "file" in effects:
        (out.parent / "notes.txt").write_text("notes\\n", encoding="utf-8")
    return 0
'''
CELLS_HARNESS = {
    "tests/__init__.py": "",
    "tests/main/__init__.py": "",
    "tests/main/payload_validation/__init__.py": """import json
from dataclasses import dataclass
from pathlib import Path

from hypothesis import strategies as st

ROOT = Path(__file__).resolve().parents[3]
PAYLOAD_INPUTS = ("plain.json", "checked.json")


@dataclass(frozen=True)
class SchemaCase:
    id: str
    codegen_schema: dict
    input_file_type: str = "jsonschema"


@dataclass(frozen=True)
class Mutation:
    payload: object
    constraint: str


class Validator:
    def is_valid(self, payload):
        return type(payload.get("a")) is int


SCHEMA_CASES = [
    SchemaCase(f"jsonschema/{name}", json.loads(path.read_text(encoding="utf-8")))
    for name in PAYLOAD_INPUTS
    if (path := ROOT / "tests" / "data" / "jsonschema" / name).exists()
]


def source_schema_validator(case):
    return Validator()


def payload_strategy(case):
    return st.fixed_dictionaries({"a": st.integers(0, 9)})


def invalid_payload_mutations(case, payload):
    return [Mutation({"a": str(payload["a"])}, "type")]
""",
    "tests/main/payload_validation/codegen.py": """import json


def _write_input_schema(case, directory):
    path = directory / "input.json"
    path.write_text(json.dumps(case.codegen_schema), encoding="utf-8")
    return path


def _payload_codegen_args(case, input_path, output_path, backend):
    return [
        "--input", str(input_path), "--input-file-type", case.input_file_type, "--output", str(output_path),
        "--output-model-type", backend.output_model_type, "--target-python-version", "3.14",
        "--class-name", "Payload", "--disable-timestamp", "--formatters", "builtin",
    ]
""",
    "tests/main/payload_validation/models.py": """from enum import Enum


class PayloadBackend(Enum):
    PYDANTIC_V2 = "pydantic_v2.BaseModel"
    PYDANTIC_V2_DATACLASS = "pydantic_v2.dataclass"
    MSGSPEC = "msgspec.Struct"
    DATACLASSES = "dataclasses.dataclass"

    @property
    def output_model_type(self):
        return self.value
""",
    "tests/main/payload_validation/native_numeric.py": (
        "def native_float_multiple_errors(case, payload):\n    return False\n"
    ),
}
"""The payload-validation harness of the stand-in project: one case per payload input, `a` drawn from 0..9."""
CELLS_MODULE = "from pydantic import BaseModel\n\n\nclass Model(BaseModel):\n    a: {}\n"
CELLS_COMPANION = 'valid: {"a": 1}\ninvalid {"a": "x"}: rejected (int_parsing)\n'
CELLS_REFS = {
    "test_plain": ["\tmain/jsonschema/plain.py"],
    "test_plain_payloads": ["\tmain/jsonschema/plain.txt"],
    "test_listing": ["\tmain/jsonschema/listing.txt"],
}


def _cells_refs(sections: dict[str, list[str]]) -> str:
    return REFS_HEADER + "".join(
        f"[{test}]\n" + "".join(f"{line}\n" for line in lines) for test, lines in sections.items()
    )


def _cells_src(
    *changes: tuple[str, str | None, str],
    flags: tuple[str, ...] = CELLS_FLAGS,
    presets: tuple[str, ...] = CELLS_PRESETS,
    helper: str = "",
) -> dict[str, str]:
    """Return the stand-in's changing source: its flags, its presets and its output changes.

    A change is the input's `name`, the option that shows it (None: every cell) and its effect; `helper` changes the
    source and no output.
    """
    names = "".join(f'    "{name}",\n' for name in presets)
    return {
        f"{CELLS_PACKAGE}/_behaviour.py": f"FLAGS = {flags!r}\nCHANGES = {changes!r}\n{helper}",
        f"{CELLS_PACKAGE}/preset_names.py": "from typing import Literal, TypeAlias, get_args\n\n"
        f"PresetName: TypeAlias = Literal[\n{names}]\n",
    }


def _cells_input(name: str, **spec: object) -> dict[str, str]:
    return {f"{CELLS_INPUTS}/{name}.json": json.dumps({"name": name, **spec})}


CELLS_REFACTOR = _cells_src(helper="HELPER = 1\n")
"""A source change that changes no output."""
CELLS_RELEASE = {
    f"{CELLS_PACKAGE}/__init__.py": "",
    f"{CELLS_PACKAGE}/arguments.py": CELLS_ARGUMENTS,
    f"{CELLS_PACKAGE}/__main__.py": CELLS_MAIN,
    **_cells_src(),
    **CELLS_HARNESS,
    **_cells_input("plain", type="int"),
    **_cells_input("other", type="str"),
    "docs/data/migration/releases.toml": f'[[release]]\nversion = "{CELLS_TAG}"\ndate = "2026-01-01"\n',
    "tests/main/test_models.refs": _cells_refs(CELLS_REFS),
    "tests/test_tool_script.refs": _cells_refs({"test_tool": ["\tscripts/tool.txt"]}),
    f"{CELLS_EXPECTED}/main/jsonschema/plain.py": CELLS_MODULE.format("int"),
    f"{CELLS_EXPECTED}/main/jsonschema/plain.txt": CELLS_COMPANION,
    f"{CELLS_EXPECTED}/main/jsonschema/listing.txt": "Model\n",
    f"{CELLS_EXPECTED}/scripts/tool.txt": "ran\n",
}
"""The tree of the release tag: the stand-in generator, its payload harness, two inputs (`plain` has a payload case,
`other` none), stored outputs of the models, of a payload companion, of a listing and of a script's test."""
CELLS_GUIDE = {"source": "release-note", "refs": ["#1"]}
CELLS_CATEGORY_KEYS: dict[str, dict[str, object]] = {
    "output-text": {"kind": "output-text", "action": "none", **CELLS_GUIDE},
    "crash-fix": {"kind": "default-output", "action": "none", **CELLS_GUIDE},
    "decided-change": {
        "kind": "default-output",
        "action": "review",
        **CELLS_GUIDE,
        "decision": ["#1"],
        "keep": {"note": "pin the release"},
    },
    "experimental": {
        "kind": "experimental",
        "action": "none",
        **CELLS_GUIDE,
        "decision": ["#1"],
        "affects": {"experimental": ["cli-option.generate-client"]},
    },
    "notice": {"kind": "warning", "action": "warn", **CELLS_GUIDE},
    "api": {
        "kind": "removal",
        "action": "required",
        **CELLS_GUIDE,
        "decision": ["#1"],
        "affects": {"options": ["--old-flag"]},
    },
    "harness": {},
}
"""The keys each category's record needs besides `id`, `title` and `summary`."""


def _toml(value: object) -> str:
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{key} = {_toml(item)}" for key, item in value.items()) + " }"
    return json.dumps(value)


def _cells(*inputs: str, backends: object = "*", options: object = "*", **more: object) -> dict[str, object]:
    """Return one `[[change.cells]]` selector of fixture inputs (names below tests/data/jsonschema, globs allowed)."""
    return {"input": [f"{CELLS_INPUTS}/{name}" for name in inputs], "backends": backends, "options": options, **more}


def _record(
    entry_id: str,
    category: str | None,
    *cells: dict[str, object],
    covers: tuple[str, ...] = (),
    file: str = "",
    **keys: object,
) -> dict[str, str]:
    """Return the fragment of one record (in `file`, by default `<id>.toml`), its `covers` as `declare` writes them."""
    fields = {"id": entry_id, "category": category, "title": "t", "summary": "s"} | CELLS_CATEGORY_KEYS.get(
        category, {}
    )
    lines = [f"{key} = {_toml(value)}" for key, value in (fields | keys).items() if value is not None]
    tables = "".join(
        "\n[[change.cells]]\n" + "".join(f"{key} = {_toml(value)}\n" for key, value in cell.items()) for cell in cells
    )
    text = "[[change]]\n" + "\n".join(lines) + "\n" + covers_block(covers) * bool(covers) + tables
    return {f"{CELLS_FRAGMENTS}/{file or entry_id}.toml": text}


@dataclass(frozen=True)
class CellsCase:
    """One oracle case of the cell gate: it judges differing cells by the records' `[[change.cells]]` selectors.

    The release is tagged; `base` is main's next commit, `layer` a lower stack layer on it (the PR's base), `head` the
    PR; `main` is a commit main takes after the PR forked. `against` names the gate's base: the PR's `base` (the
    layer's tip), main's tip, the release tag, a commit no history has (`unknown`) or one of another history
    (`orphan`). `records` also runs `declared_changes.py check` on the PR, which fails the harness labels the
    gate refuses. `shape` names, in words, the rule the case pins or the passing shape it keeps.
    """

    verdict: str
    shape: str
    head: dict[str, str | None] = field(default_factory=dict)
    release: dict[str, str] = field(default_factory=dict)
    base: dict[str, str | None] = field(default_factory=dict)
    layer: dict[str, str | None] = field(default_factory=dict)
    main: dict[str, str | None] = field(default_factory=dict)
    against: str = "base"
    tier: str = "pr"
    records: bool = False


COMMENT_PLAIN = _cells_src(("plain", None, "comment"))
COMMENT_OTHER = _cells_src(("other", None, "comment"))
FLOAT_OTHER = _cells_src(("other", None, "type:float"))
OUTPUT_TEXT_OTHER = _record("other-note", "output-text", _cells("other.json"))
CHECKED = _cells_input("checked", type="int", validator=True)
MEMORY = _cells_input("memory", type="int", memory=True)
FAILS = _cells_input("fails", type="int", fail="boom")
PLAIN_MODULE = f"{CELLS_EXPECTED}/main/jsonschema/plain.py"
CELLS_CASES: dict[str, CellsCase] = {
    "pass: no src change": CellsCase("pass", "no src change, the shortcut", {"docs/notes.md": "notes\n"}),
    "pass: a refactor, every cell equal": CellsCase(
        "pass", "refactor, both sides stop", CELLS_REFACTOR, release=FAILS | MEMORY
    ),
    "pass: output-text with a payload case": CellsCase(
        "pass", "output-text, payload case", COMMENT_PLAIN | _record("plain-note", "output-text", _cells("plain.json"))
    ),
    "pass: output-text without a payload case": CellsCase(
        "pass", "output-text, signature only", COMMENT_OTHER | OUTPUT_TEXT_OTHER
    ),
    "pass: output-text with a covered stored module": CellsCase(
        "pass",
        "output-text, covered stored module",
        COMMENT_PLAIN
        | {PLAIN_MODULE: CELLS_MODULE.format("int  # note")}
        | _record("plain-note", "output-text", _cells("plain.json"), covers=("main/jsonschema/plain.py",)),
    ),
    "pass: output-text with an added stored module": CellsCase(
        "pass",
        "output-text, added module",
        COMMENT_PLAIN
        | {
            "tests/main/test_models.refs": _cells_refs(CELLS_REFS | {"test_note": ["\tmain/jsonschema/note.py"]}),
            f"{CELLS_EXPECTED}/main/jsonschema/note.py": CELLS_MODULE.format("int  # note"),
        }
        | _record("plain-note", "output-text", _cells("plain.json"), covers=("main/jsonschema/note.py",)),
    ),
    "pass: options * covers the preset cells": CellsCase(
        "pass",
        "options * covers the preset cells",
        _cells_src(("plain", "--preset", "comment")) | _record("preset-note", "output-text", _cells("plain.json")),
    ),
    "pass: crash-fix": CellsCase(
        "pass",
        "crash-fix",
        _cells_src(("fails", None, "fixed")) | _record("fails-fixed", "crash-fix", _cells("fails.json")),
        release=FAILS,
    ),
    "pass: decided-change inside aspects and match": CellsCase(
        "pass",
        "decided-change",
        FLOAT_OTHER
        | _record("other-float", "decided-change", _cells("other.json", aspects=["type"], match=r"str -> float")),
    ),
    "pass: experimental with an equal companion": CellsCase(
        "pass",
        "experimental, equal companion",
        _cells_src(("plain", "--generate-client", "type:float"))
        | _record("client-float", "experimental", _cells("plain.json", options=[["--generate-client"]])),
    ),
    "pass: notice inside match": CellsCase(
        "pass",
        "notice",
        _cells_src(("plain", None, "warn:mind the gap"))
        | _record("plain-warns", "notice", _cells("plain.json", match="mind the gap")),
    ),
    "pass: api for a removed option": CellsCase(
        "pass",
        "api",
        _cells_src(flags=CELLS_FLAGS[:-1]) | _record("old-flag", "api", _cells("plain.json", options=[["--old-flag"]])),
    ),
    "pass: the version header line is normalised": CellsCase(
        "pass", "version header normalised", CELLS_REFACTOR, release=_cells_input("header", type="int", header=True)
    ),
    "pass: a lagging branch is judged from its merge base": CellsCase(
        "pass",
        "lagging branch, judged from its merge base",
        {"docs/notes.md": "notes\n"},
        main=FLOAT_OTHER,
        against="main",
    ),
    "pass: a stack layer extends the record below": CellsCase(
        "pass",
        "stack layer extends the record below",
        _cells_src(("plain", None, "comment"), ("other", None, "comment"))
        | _record("other-note", None, _cells("other.json"), extends="plain-note", title=None, summary=None),
        layer=COMMENT_PLAIN | _record("plain-note", "output-text", _cells("plain.json")),
    ),
    "pass: a renamed fragment is no PR record": CellsCase(
        "pass",
        "renamed fragment",
        _cells_src(("other", None, "comment"), helper="HELPER = 1\n")
        | {f"{CELLS_FRAGMENTS}/other-note.toml": None}
        | _record("other-note", "output-text", _cells("other.json"), file="renamed"),
        base=COMMENT_OTHER | OUTPUT_TEXT_OTHER,
    ),
    "pass: the release run covers by unreleased records": CellsCase(
        "pass", "release run, unreleased records", base=COMMENT_OTHER | OUTPUT_TEXT_OTHER, against="tag", tier="nightly"
    ),
    "pass: a decision record beside an output-text record": CellsCase(
        "pass",
        "decision beside output-text",
        FLOAT_OTHER
        | OUTPUT_TEXT_OTHER
        | _record("other-float", "decided-change", _cells("other.json", aspects=["type"], match=r"str -> float")),
    ),
    "pass: an oracle-file change runs the base's gate, which passes": CellsCase(
        "pass",
        "oracle files, the base's gate passes",
        {"scripts/behaviour_diff.py": "import sys\n\nsys.exit(0)  # changed\n"},
        release={"scripts/behaviour_diff.py": "import sys\n\nsys.exit(0)\n"},
    ),
    "pass: the base stops, the head generates": CellsCase(
        "pass",
        "base stops, head generates",
        _cells_src(("memory", None, "fixed")) | _record("memory-fixed", "crash-fix", _cells("memory.json")),
        release=MEMORY,
    ),
    "pass: harness covers an internal label": CellsCase(
        "pass",
        "harness, internal label",
        CELLS_REFACTOR
        | {f"{CELLS_EXPECTED}/scripts/tool.txt": "ran twice\n"}
        | _record("tool", "harness", covers=("scripts/tool.txt",)),
        records=True,
    ),
    "pass: harness covers a modified module the proof holds for": CellsCase(
        "pass",
        "harness, modified module the proof holds for",
        CELLS_REFACTOR
        | {PLAIN_MODULE: CELLS_MODULE.format("int  # note")}
        | _record("plain-test", "harness", covers=("main/jsonschema/plain.py",)),
        records=True,
    ),
    "pass: harness covers an added label": CellsCase(
        "pass",
        "harness, added label",
        CELLS_REFACTOR
        | {
            "tests/main/test_models.refs": _cells_refs(CELLS_REFS | {"test_note": ["\tmain/jsonschema/note.py"]}),
            f"{CELLS_EXPECTED}/main/jsonschema/note.py": CELLS_MODULE.format("int"),
        }
        | _record("note-test", "harness", covers=("main/jsonschema/note.py",)),
        records=True,
    ),
    "fail: an uncovered differing cell": CellsCase("fail", "uncovered differing cell", FLOAT_OTHER),
    "fail: a kind no covering record admits": CellsCase(
        "fail",
        "kind no covering record admits",
        _cells_src(("other", None, "comment"), ("other", None, "warn:mind the gap")) | OUTPUT_TEXT_OTHER,
    ),
    "fail: output-text with a signature change": CellsCase(
        "fail", "output-text, signature change", FLOAT_OTHER | OUTPUT_TEXT_OTHER
    ),
    "fail: output-text with a differing non-model file": CellsCase(
        "fail", "output-text, differing non-model file", _cells_src(("other", None, "file")) | OUTPUT_TEXT_OTHER
    ),
    "fail: output-text whose output does not import": CellsCase(
        "fail", "output-text, output does not import", _cells_src(("other", None, "broken")) | OUTPUT_TEXT_OTHER
    ),
    "fail: output-text against rule (b)": CellsCase(
        "fail",
        "output-text, validator body changed",
        _cells_src(("checked", None, "body")) | _record("checked-body", "output-text", _cells("checked.json")),
        release=CHECKED,
    ),
    "fail: output-text whose payload verdicts change": CellsCase(
        "fail",
        "output-text, payload verdicts change",
        _cells_src(("checked", None, "reject-big")) | _record("checked-big", "output-text", _cells("checked.json")),
        release=CHECKED,
    ),
    "fail: crash-fix on a cell that was no crash": CellsCase(
        "fail", "crash-fix, cell was no crash", COMMENT_PLAIN | _record("plain-note", "crash-fix", _cells("plain.json"))
    ),
    "fail: decided-change outside its match": CellsCase(
        "fail",
        "decided-change, outside its match",
        FLOAT_OTHER | _record("other-float", "decided-change", _cells("other.json", aspects=["type"], match=r"-> int")),
    ),
    "fail: experimental whose companion differs": CellsCase(
        "fail",
        "experimental, companion differs",
        _cells_src(("plain", "--use-annotated", "type:float"))
        | _record(
            "client-float", "experimental", _cells("plain.json", options=[["--generate-client", "--use-annotated"]])
        ),
    ),
    "fail: notice outside its match": CellsCase(
        "fail",
        "notice, outside its match",
        _cells_src(("plain", None, "warn:mind the gap"))
        | _record("plain-warns", "notice", _cells("plain.json", match="other text")),
    ),
    "fail: a selector that matches no input": CellsCase(
        "fail",
        "selector matches no input",
        COMMENT_OTHER | _record("other-note", "output-text", _cells("other.json"), _cells("missing.json")),
    ),
    "fail: a selector that matches equal cells only": CellsCase(
        "fail",
        "selector matches equal cells only",
        COMMENT_OTHER | _record("other-note", "output-text", _cells("other.json"), _cells("plain.json")),
    ),
    "fail: the release run's stale selector": CellsCase(
        "fail",
        "release run, stale selector",
        base=CELLS_REFACTOR | _record("plain-note", "output-text", _cells("plain.json")),
        against="tag",
        tier="nightly",
    ),
    "fail: output-text without cells": CellsCase(
        "fail", "output-text without cells", COMMENT_OTHER | _record("other-note", "output-text")
    ),
    "fail: a decision selector without aspects or match": CellsCase(
        "fail",
        "decision selector without aspects or match",
        FLOAT_OTHER | _record("other-float", "decided-change", _cells("other.json")),
    ),
    "fail: cells in a harness record": CellsCase(
        "fail", "cells in a harness record", COMMENT_OTHER | _record("other-test", "harness", _cells("other.json"))
    ),
    "fail: a selector naming an unreleased preset": CellsCase(
        "fail",
        "selector names an unreleased preset",
        _cells_src(("plain", "standard-py314-20260201", "comment"), presets=CELLS_UNRELEASED_PRESETS)
        | _record(
            "preset-note", "output-text", _cells("plain.json", options=[["--preset", "standard-py314-20260201"]])
        ),
    ),
    "fail: a selector naming an unreleased option": CellsCase(
        "fail",
        "selector names an unreleased option",
        _cells_src(("plain", "--new-flag", "comment"), flags=(*CELLS_FLAGS, "--new-flag"))
        | _record("flag-note", "output-text", _cells("plain.json", options=[["--new-flag"]])),
    ),
    "fail: a nondeterministic head": CellsCase(
        "fail", "nondeterministic head", _cells_src(("other", None, "random")) | OUTPUT_TEXT_OTHER
    ),
    "fail: the head stops where the base generates": CellsCase(
        "fail", "head stops, base generates", _cells_src(("plain", None, "memory"))
    ),
    "fail: an unknown base": CellsCase("fail", "unknown base", CELLS_REFACTOR, against="unknown"),
    "fail: a base without a merge base": CellsCase(
        "fail", "base without a merge base", CELLS_REFACTOR, against="orphan"
    ),
    "fail: cells without a src change": CellsCase(
        "fail", "cells without a src change", _record("plain-note", "output-text", _cells("plain.json"))
    ),
    "fail: an oracle-file change runs the base's gate, which fails": CellsCase(
        "fail",
        "oracle files, the base's gate fails",
        {"scripts/behaviour_diff.py": "import sys\n\nsys.exit(0)\n"},
        release={"scripts/behaviour_diff.py": "import sys\n\nsys.exit(1)\n"},
    ),
    "fail: selectors added to a record of the base": CellsCase(
        "fail",
        "selectors added to a record of the base",
        _cells_src(("other", None, "comment"), ("plain", None, "comment"))
        | _record("other-note", "output-text", _cells("other.json"), _cells("plain.json")),
        base=COMMENT_OTHER | OUTPUT_TEXT_OTHER,
    ),
    "fail: a fragment of the tag covers nothing": CellsCase(
        "fail",
        "fragment of the tag covers nothing",
        release=OUTPUT_TEXT_OTHER,
        base=COMMENT_OTHER,
        against="tag",
        tier="nightly",
    ),
    "fail: output-text whose covered module fails the proof": CellsCase(
        "fail",
        "output-text, covered module fails the proof",
        COMMENT_PLAIN
        | {PLAIN_MODULE: CELLS_MODULE.format("float")}
        | _record("plain-note", "output-text", _cells("plain.json"), covers=("main/jsonschema/plain.py",)),
    ),
    "fail: output-text whose covered payload companion changed": CellsCase(
        "fail",
        "output-text, covered payload companion changed",
        COMMENT_PLAIN
        | {f"{CELLS_EXPECTED}/main/jsonschema/plain.txt": 'valid: {"a": 1}\nvalid: {"a": "x"}\n'}
        | _record("plain-note", "output-text", _cells("plain.json"), covers=("main/jsonschema/plain.txt",)),
    ),
    "fail: harness covers a modified module the proof fails for": CellsCase(
        "fail",
        "harness, modified module the proof fails for",
        CELLS_REFACTOR
        | {PLAIN_MODULE: CELLS_MODULE.format("float")}
        | _record("plain-test", "harness", covers=("main/jsonschema/plain.py",)),
        records=True,
    ),
    "fail: harness covers a modified listing": CellsCase(
        "fail",
        "harness, modified listing",
        CELLS_REFACTOR
        | {f"{CELLS_EXPECTED}/main/jsonschema/listing.txt": "Model\nOther\n"}
        | _record("listing-test", "harness", covers=("main/jsonschema/listing.txt",)),
        records=True,
    ),
    "fail: a fragment that does not parse": CellsCase(
        "fail",
        "fragment does not parse",
        COMMENT_OTHER | OUTPUT_TEXT_OTHER | {f"{CELLS_FRAGMENTS}/broken.toml": "[[change]\nid = \n"},
    ),
    "fail: an unknown key in cells": CellsCase(
        "fail",
        "unknown key in cells",
        COMMENT_OTHER | _record("other-note", "output-text", _cells("other.json", colour="red")),
    ),
    "fail: an empty input list": CellsCase(
        "fail",
        "empty input list",
        COMMENT_OTHER | _record("other-note", "output-text", {"input": [], "backends": "*", "options": "*"}),
    ),
    "fail: an unknown backend": CellsCase(
        "fail",
        "unknown backend",
        COMMENT_OTHER | _record("other-note", "output-text", _cells("other.json", backends=["pydantic.BaseModel"])),
    ),
    "fail: malformed options": CellsCase(
        "fail",
        "malformed options",
        COMMENT_OTHER | _record("other-note", "output-text", _cells("other.json", options=["--use-annotated"])),
    ),
    "fail: an unknown aspect": CellsCase(
        "fail",
        "unknown aspect",
        FLOAT_OTHER | _record("other-float", "decided-change", _cells("other.json", aspects=["colour"], match="float")),
    ),
    "fail: a match that does not compile": CellsCase(
        "fail",
        "match does not compile",
        FLOAT_OTHER | _record("other-float", "decided-change", _cells("other.json", aspects=["type"], match="(float")),
    ),
    "fail: two records whose evidence fails on one cell": CellsCase(
        "fail",
        "two records, evidence fails on one cell",
        FLOAT_OTHER | OUTPUT_TEXT_OTHER | _record("other-warns", "notice", _cells("other.json", match="float")),
    ),
}
"""Every fail-closed rule of the cell gate and every passing shape it must keep, with the verdict it must give."""
ALLOWLIST_ONLY = "fails: a behaviour change needs an allowlist entry; a revision base reads none"
REPLAYED = "passes: the records check sends harness labels to its replay list, which it never fails"
CELLS_NOT_BUILT: dict[str, str] = {
    "fail: a base without a merge base": "passes: it compares the given revision, whatever its history",
    "fail: a fragment of the tag covers nothing": "passes: records are not read",
    "fail: a fragment that does not parse": "passes: records are not read",
    "fail: a kind no covering record admits": "passes: warnings are not compared, and the comment is no behaviour",
    "fail: a nondeterministic head": "passes: it does not run the head twice",
    "fail: a selector naming an unreleased option": "passes: records are not read",
    "fail: a selector naming an unreleased preset": "passes: records are not read",
    "fail: a selector that matches equal cells only": "passes: records are not read",
    "fail: a selector that matches no input": "passes: records are not read",
    "fail: an empty input list": "passes: records are not read",
    "fail: an oracle-file change runs the base's gate, which fails": "passes: it does not run the base's gate",
    "fail: an unknown backend": "passes: records are not read",
    "fail: an unknown key in cells": "passes: records are not read",
    "fail: cells in a harness record": "passes: records are not read",
    "fail: cells without a src change": "passes: records are not read",
    "fail: crash-fix on a cell that was no crash": "passes: records are not read; the comment is no behaviour",
    "fail: experimental whose companion differs": "passes: the PR tier runs neither option",
    "fail: harness covers a modified listing": REPLAYED,
    "fail: harness covers a modified module the proof fails for": REPLAYED,
    "fail: malformed options": "passes: records are not read",
    "fail: notice outside its match": "passes: warnings are not compared",
    "fail: output-text against rule (b)": "passes: the signatures are equal; rule (b) does not exist yet",
    "fail: output-text whose covered module fails the proof": "passes: records are not read",
    "fail: output-text whose covered payload companion changed": "passes: records are not read",
    "fail: output-text whose payload verdicts change": "passes: a fixture cell runs no payload cells",
    "fail: output-text with a differing non-model file": "passes: the differing file holds no behaviour",
    "fail: output-text without cells": "passes: records are not read",
    "fail: selectors added to a record of the base": "passes: records are not read",
    "fail: the release run's stale selector": "passes: records are not read",
    "pass: a decision record beside an output-text record": ALLOWLIST_ONLY,
    "pass: a lagging branch is judged from its merge base": "fails: it compares main's tip, not the merge base",
    "pass: decided-change inside aspects and match": ALLOWLIST_ONLY,
}
"""The cases the gate does not judge yet as the cell gate must, with what it does instead.

They are not collected: the change that builds a case's check moves it out of this table. The collected cases give
the cell gate's verdict on the gate as it is, many only because it reads no records yet.
"""


def _cells_write(repo: Path, files: dict[str, str | None]) -> None:
    """Write the files of one commit; None removes one."""
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        with contextlib.suppress(TypeError):
            path.write_text(text, encoding="utf-8")


def _cells_commit(repo: Path, files: dict[str, str | None], message: str) -> str:
    _cells_write(repo, files)
    _git("add", "-A", ".", cwd=repo)
    _git("commit", "-q", "--allow-empty", "-m", message, cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo)


def _cells_project(case: CellsCase, repo: Path) -> dict[str, str]:
    """Commit the case's history and return the revisions `against` may name; the working tree is the PR's head."""
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    release = _cells_commit(repo, CELLS_RELEASE | case.release, "release")
    _git("tag", CELLS_TAG, cwd=repo)
    _cells_commit(repo, case.base, "base")
    _git("switch", "-q", "-c", "layer", cwd=repo)
    base = _cells_commit(repo, case.layer, "layer")
    _git("switch", "-q", "main", cwd=repo)
    main = _cells_commit(repo, case.main, "main after the fork")
    _git("switch", "-q", "-c", "pr", "layer", cwd=repo)
    _cells_commit(repo, case.head, "head")
    orphan = _git("commit-tree", "-m", "another history", f"{release}^{{tree}}", cwd=repo)
    return {"base": base, "main": main, "tag": f"refs/tags/{CELLS_TAG}", "unknown": "4" * 40, "orphan": orphan}


def _cells_exit(run: Callable[[], int], log: StringIO) -> int:
    """Return a judge's exit status; a refusal by `SystemExit` is one, any other exception fails the case."""
    try:
        with redirect_stdout(log), redirect_stderr(log):
            return run()
    except SystemExit as stopped:
        return stopped.code if isinstance(stopped.code, int) else 1


def cells_verdict(case: CellsCase, tmp_path: Path) -> tuple[str, str]:
    """Run the gate (and, for `records`, the records check) as CI runs it and return the verdict and the log."""
    repo = tmp_path / "repo"
    against = _cells_project(case, repo)[case.against]
    projects = tmp_path / "projects.json"
    projects.write_text('{"projects": []}', encoding="utf-8")
    allowlist = tmp_path / "allowlist.toml"
    allowlist.write_text(f'base = "{CELLS_TAG}"\n', encoding="utf-8")
    argv = ["gate", against, str(repo), "--tier", case.tier, "--check", "--candidate-python", sys.executable]
    argv += ["--cache-dir", str(tmp_path / "cache"), "--projects", str(projects), "--allowlist", str(allowlist)]
    log = StringIO()
    codes = {"gate": _cells_exit(lambda: behaviour_diff.main([*argv, "-j", "2"]), log)}
    if case.records:
        check = ["check", "--base", against, "--head", "HEAD", "--tag", CELLS_TAG]
        codes["records"] = _cells_exit(lambda: declared_changes.main(check, repo=repo), log)
    verdict = "pass" if set(codes.values()) == {0} else "fail"
    return verdict, f"{codes}\n{log.getvalue()}"


@pytest.mark.allow_direct_assert(
    "an oracle case fails on its own verdict, never through a stored output a fix run accepts"
)
@needs("posix")
@pytest.mark.parametrize("case", sorted(CELLS_CASES.keys() - CELLS_NOT_BUILT.keys()))
def test_cells_oracle(case: str, tmp_path: Path) -> None:
    """The gate gives each oracle case the verdict of the cell gate: a case that fails, fails on that verdict."""
    verdict, log = cells_verdict(CELLS_CASES[case], tmp_path)
    assert verdict == CELLS_CASES[case].verdict, log
