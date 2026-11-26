"""Tests for the release behaviour diff: signatures, the classifier and the gate driver.

The signature cases and the classifier cases are the shapes the 0.86.0 audit met: alias and NotRequired spellings
that are not behaviour changes, msgspec decoders and pydantic adapters 0.86.0 could not build, the propertyNames key
change the owner accepted, and the behaviour changes the gate has to stop.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import asdict, dataclass, field, replace
from io import StringIO
from pathlib import Path
from types import FunctionType, ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, Any, TypeVar

import pytest
import tomllib

from scripts import behaviour_diff, declared_changes
from scripts._behaviour_classify import (
    SIGNATURE_FAILED,
    Allowlist,
    CellEvidence,
    Change,
    Side,
    change_lines,
    classify_base_skipped,
    classify_cell,
    covers,
    diff,
    judge,
    kinds,
    rule_b,
)
from scripts._behaviour_signature import TARGETS_FLAG, Context, _pydantic_item, canonical, example_value, meta_items
from scripts._change_records import Category, CellKind, CellSelector, Record, covers_block, read_records
from tests.capabilities import needs
from tests.conftest import assert_output
from tests.units import REFS_HEADER

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable, Generator

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
    gate = SimpleNamespace(allowlist=allowlist, loaded_allowlist=allowlist, cache=SimpleNamespace(hits=0), plan=None)
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
    gate = SimpleNamespace(allowlist=allowlist, loaded_allowlist=allowlist, cache=SimpleNamespace(hits=0), plan=None)
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
    gate = SimpleNamespace(allowlist=allowlist, loaded_allowlist=allowlist, cache=SimpleNamespace(hits=0), plan=None)
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


FAMILY_CELL_CASES = {
    "the PR tier": ("pr", ("pydantic_v2.BaseModel", "msgspec.Struct")),
    "the nightly tier": ("nightly", ("pydantic_v2.BaseModel", "msgspec.Struct")),
    "types without the targets' type": ("pr", ("msgspec.Struct",)),
}
"""The tier and the output model types of each family cell case."""


@pytest.mark.parametrize("case", sorted(FAMILY_CELL_CASES))
def test_family_cells_of_each_tier(case: str) -> None:
    """The PR tier runs a shape for its types with default options, the nightly tier the full product of each type.

    The client (its body's fields as arguments too) and the server of the shape's OpenAPI spelling run for
    pydantic_v2.BaseModel with no other option in both tiers, and not at all where the types lack it.
    """
    tier, types = FAMILY_CELL_CASES[case]
    presets = ["standard-py310-20261017", "standard-py312-20260909", "standard-py314-20261017"]
    shapes: list[behaviour_diff.FamilyCase] = [{"label": "family:f/k=v", "key": "k"}]
    pairs = behaviour_diff.tier_product(tier, types, presets)
    cells = behaviour_diff.family_cells(shapes, Path("root"), pairs)
    assert_output("".join(f"{c.input.as_posix()} | {c.backend} | {c.option_label} | {c.kind}\n" for c in cells))


FAMILY_MODE_CASES = {
    "fail": ('families = "fail"\n', "fail"),
    "report": ('families = "report"\n', "report"),
    "no key": ("", "report"),
    "no file": (None, "report"),
}
"""The text of the allowlist file of each case (None: no file) and the mode its family cells take."""


@pytest.mark.allow_direct_assert("the mode is the oracle of the key, never a stored output a fix run accepts")
@pytest.mark.parametrize("case", sorted(FAMILY_MODE_CASES))
def test_families_key_judges_or_reports_the_family_cells(case: str, tmp_path: Path) -> None:
    """The `families` key judges the family cells with `fail`; `report`, no key and no file report them only."""
    text, mode = FAMILY_MODE_CASES[case]
    path = tmp_path / "allowlist.toml"
    if text is not None:
        path.write_text(f'base = "0.1.0"\n{text}', encoding="utf-8")
    assert behaviour_diff.families_judged(path) == (mode == "fail")


def test_families_key_refuses_another_value(tmp_path: Path) -> None:
    """A `families` value other than `report` or `fail` stops the gate."""
    path = tmp_path / "allowlist.toml"
    path.write_text('families = "warn"\n', encoding="utf-8")
    with pytest.raises(SystemExit) as refused:
        behaviour_diff.families_judged(path)
    assert_output(f"{refused.value}\n".replace(str(path), "<allowlist>"))


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
            "model.py": "from stub_types._wire_base import WireModel\n\n\nclass Model(WireModel):\n    name: str\n",
            "stub_types/_wire_base.py": "from pydantic import BaseModel\n\n\nclass WireModel(BaseModel):\n    pass\n",
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


def test_guide_names_accepted_changes_by_the_records() -> None:
    """The gate names accepted changes by the titles of the change records of docs/data/migration."""
    titled = [record.id for record in read_records(behaviour_diff.MIGRATION_DATA) if record.title]
    assert_output(json.dumps({"titled": bool(titled)}))


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


def _tree_signature(files: dict[str, str], backend: str, directory: Path, *, targets: bool = False) -> dict[str, Any]:
    for name, text in files.items():
        (directory / name).parent.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(text, encoding="utf-8")
    target = directory if targets else directory / ("model.py" if "model.py" in files else "pkg")
    result = subprocess.run(
        [sys.executable, "-I", str(SIGNATURE_SCRIPT), str(target), backend, *([TARGETS_FLAG] if targets else [])],
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


TARGET_MODEL = "from pydantic import BaseModel\n\n\nclass Holder(BaseModel):\n    f: list[str | None] | None = None\n"
TARGET_RESOURCE = (
    "from __future__ import annotations\n\nimport model\n\n\nclass Resource:\n"
    '    """The operations of a stand-in client."""\n\n'
    "    def check(self, *, f: list[str | None] | None = None) -> model.Holder:\n"
    '        """Send a holder."""\n'
    "        return model.Holder(f=f)\n"
)
TARGET_TREE = {
    "model.py": TARGET_MODEL,
    "client/__init__.py": "from .resource import Resource\n",
    "client/resource.py": TARGET_RESOURCE,
}
"""A run's models and the stand-in client package the run writes beside them, which imports them as `model`."""
TARGET_TREE_CASES = {
    "a client argument whose annotation drops the None of its items": {
        **TARGET_TREE,
        "client/resource.py": TARGET_RESOURCE.replace("f: list[str | None] | None", "f: list[str] | None"),
    },
    "a client module that no longer imports": {
        **TARGET_TREE,
        "client/resource.py": TARGET_RESOURCE.replace("import model\n", "import model\n\nmodel.Missing\n"),
    },
    "the same client": TARGET_TREE,
}
"""The candidate tree of each case; the base is `TARGET_TREE`."""


@pytest.mark.parametrize("case", sorted(TARGET_TREE_CASES))
def test_classify_target_trees(case: str, tmp_path: Path) -> None:
    """A run that writes a target package beside its models is signed with it: a change of the target alone shows.

    The client's callables are part of its signature, so an argument's annotation that changes is a `signature`
    change and a client module that no longer imports an import error, while the models stay the same.
    """
    sides = [
        Side(generated=True, signature=_tree_signature(files, "pydantic_v2.BaseModel", tmp_path / name, targets=True))
        for name, files in (("base", TARGET_TREE), ("cand", TARGET_TREE_CASES[case]))
    ]
    verdict = classify_cell(*sides, input_path=case, backend="pydantic_v2.BaseModel", allowlist=Allowlist("0.86.0"))
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


def _git(*args: str, cwd: Path, dates: dict[str, str] | None = None) -> str:
    """Run git in `cwd` as a fixed author; `dates` sets the dates of the commits it writes."""
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env=os.environ | dates if dates else None,
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
    assert_output(
        text.replace(commits[base][:9], "<sha>").replace(commits[base][:7], "<sha>"), varies_with=("no-sys-monitoring",)
    )


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


PICK_CATALOG_INPUTS = ("plain.json", "deprecated.json", "warns.json")
"""The fixtures of the identity oracle's catalog: only `deprecated.json` takes the changed branch."""
PICK_CASES = {
    "the pick adds the cell that takes the change": (True, "complete"),
    "without a catalog the tier runs alone": (False, "incomplete"),
}
"""Whether the identity oracle's catalog exists, and the verdict of the plain selection with `--pick` on it."""


@pytest.mark.allow_direct_assert(
    "an oracle case fails on its own verdict, never through a stored output a fix run accepts"
)
@needs("posix")
@needs("sys-monitoring")
@pytest.mark.parametrize("case", sorted(PICK_CASES))
def test_identity_with_a_pick(case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--pick` adds the catalog's cells that run the change to the selection, which stays; without one it says so.

    The catalog (`catalog`, recorded on the base commit) holds the cells of three fixtures; the selection of the plain
    fixture alone takes no changed branch, the picked `deprecated.json` does, so the run is complete with both.
    """
    exists, wanted = PICK_CASES[case]
    argv, commit = _identity_argv(tmp_path, "tests/data/jsonschema/plain.json")
    catalog = tmp_path / "catalog.json.gz"
    if exists:
        monkeypatch.setenv(behaviour_diff.REPOSITORY_ENV, str(tmp_path / "repo"))
        selects = [arg for name in PICK_CATALOG_INPUTS for arg in ("--select", f"tests/data/jsonschema/{name}")]
        with redirect_stdout(StringIO()):
            behaviour_diff.main([
                "catalog",
                str(catalog),
                commit,
                *selects,
                "-j",
                "1",
                "--cache-dir",
                str(tmp_path / "cache"),
            ])
    stdout = StringIO()
    with redirect_stdout(stdout), redirect_stderr(StringIO()):
        code = behaviour_diff.main([*argv, "--pick", str(catalog)])
    rows = [json.loads(line) for line in (tmp_path / "cells.jsonl").read_text(encoding="utf-8").splitlines()]
    lines = [
        line
        for line in stdout.getvalue().splitlines()
        if line.startswith(("pick: cell", "pick: no", "evidence: complete", "evidence: incomplete"))
    ]
    complete = code == 0 and "evidence: complete" in lines
    incomplete = code == 1 and any(line.startswith("evidence: incomplete") for line in lines)
    verdict = "complete" if complete else "incomplete" if incomplete else f"exit={code}"
    assert verdict == wanted, stdout.getvalue()
    text = "\n".join([f"exit={code}", *lines, *sorted(row["input"] for row in rows)]) + "\n"
    assert_output(text.replace(commit[:12], "<sha>").replace(str(tmp_path), "TMP"))


FAKE_FAMILY_LIBRARY = '''"""A stand-in family library: one family, a shape per document of the stand-in generator."""

from dataclasses import dataclass

DOCUMENTS = {DOCUMENTS}


@dataclass(frozen=True)
class Shape:
    """A shape: its family, its parameters and its document."""

    family: str
    params: tuple
    document: dict

    @property
    def openapi(self):
        """The OpenAPI spelling: the same document, marked."""
        return {**self.document, "openapi": "3.1.0"}


class Family:
    """The one family of the stand-in library."""

    def shapes(self):
        """Return a shape per document."""
        return tuple(Shape("fake", (("kind", kind),), document) for kind, document in DOCUMENTS.items())


FAMILIES = {"fake": Family()}
'''
FAKE_FAMILY_DOCUMENTS = {
    "plain": {"type": "str"},
    "raw": {"type": "bytes"},
    "deprecated": {"type": "int", "deprecated": "old"},
}
"""The shapes of the stand-in family library: a plain one, one whose output the candidate of a case changes, and one
that takes the deprecation branch."""
FAKE_RAW = FAKE_FIRST.replace(
    'MODEL.format(spec["type"]),', 'MODEL.format(spec["type"]).replace(": bytes", ": bytes  # raw"),'
)
"""A candidate that changes the output of a `bytes` field only, which the stand-in family library has and no fixture."""


FAKE_UNLISTED_LIBRARY = FAKE_FAMILY_LIBRARY.replace(
    "\n    @property\n    def openapi(self):\n"
    '        """The OpenAPI spelling: the same document, marked."""\n'
    '        return {**self.document, "openapi": "3.1.0"}\n',
    "",
)
"""A stand-in library the gate cannot list: its shapes have no OpenAPI spelling, as the library had before the gate's
family cells."""


def _family_library(repo: Path, documents: dict[str, dict[str, str]], text: str = FAKE_FAMILY_LIBRARY) -> None:
    """Write a stand-in family library (`text`) into a checkout, in the packages the gate imports it from."""
    library = repo / "tests" / "main" / "shape_families"
    library.mkdir(parents=True, exist_ok=True)
    for package in (repo / "tests", repo / "tests" / "main", library):
        (package / "__init__.py").write_text("", encoding="utf-8")
    (library / "families.py").write_text(text.replace("{DOCUMENTS}", repr(documents)), encoding="utf-8")


def _family_lines(argv: list[str], cells: Path, prefixes: tuple[str, ...]) -> list[str]:
    """Run the gate and return its exit status, the lines that start with `prefixes`, and each `--json` cell."""
    stdout = StringIO()
    with redirect_stdout(stdout), redirect_stderr(StringIO()):
        code = behaviour_diff.main(argv)
    rows = [json.loads(line) for line in cells.read_text(encoding="utf-8").splitlines()]
    return [
        f"exit={code}",
        *(line for line in stdout.getvalue().splitlines() if line.startswith(prefixes)),
        *sorted(f"cell: {row['input']} | {row['file_type']} | {row['class']}" for row in rows),
    ]


FAMILY_REPORT_CASES = {
    "the family cells report only": ("report", FAKE_FAMILY_LIBRARY),
    "the family cells fail": ("fail", FAKE_FAMILY_LIBRARY),
    "a candidate without the library": ("fail", None),
    "a library the gate cannot list, reported only": ("report", FAKE_UNLISTED_LIBRARY),
    "a library the gate cannot list, judged": ("fail", FAKE_UNLISTED_LIBRARY),
}
"""The `families` key of each identity run of a candidate that changes a family cell, and the candidate's library."""


@needs("posix")
@needs("sys-monitoring")
@pytest.mark.parametrize("case", sorted(FAMILY_REPORT_CASES))
def test_identity_reports_family_cells_until_the_key_judges_them(case: str, tmp_path: Path) -> None:
    """A selection that names the family cells runs each shape of the candidate's library, its targets too.

    The candidate changes the output of the family cell that has a `bytes` field. With `families = "report"` the run
    prints that cell and the classes of the family cells apart and passes; their rows stay out of `--json`. With
    `fail` the cell is a violation of the run. The targets need options the stand-in generator lacks (`opt-in`). A
    candidate without the library has no family cells, and says so. A library the gate cannot list is one family cell
    that fails, with the listing's error: reported only, it leaves the run passing; judged, it fails the run.
    """
    mode, library = FAMILY_REPORT_CASES[case]
    repo = tmp_path / "repo"
    commits = _fake_generator(repo, ["first"], None)
    (repo / "src" / "datamodel_code_generator" / "__main__.py").write_text(FAKE_RAW, encoding="utf-8")
    if library is not None:
        _family_library(repo, FAKE_FAMILY_DOCUMENTS, library)
    allowlist = tmp_path / "allowlist.toml"
    allowlist.write_text(f'base = "0.1.0"\nfamilies = "{mode}"\n', encoding="utf-8")
    argv = ["gate", commits[0], str(repo), "--types", "pydantic_v2.BaseModel", "--candidate-python", sys.executable]
    argv += ["--cache-dir", str(tmp_path / "cache"), "--allowlist", str(allowlist), "--identity", "-j", "1"]
    argv += ["--select", "tests/data/jsonschema/plain.json", "--select", "family:*"]
    prefixes = ("family", "families", "violation:", "    ", "summary:", "evidence: complete", "evidence: incomplete")
    lines = _family_lines([*argv, "--json", str(tmp_path / "cells.jsonl")], tmp_path / "cells.jsonl", prefixes)
    assert_output("\n".join(lines).replace(str(tmp_path), "TMP").replace(commits[0][:9], "<sha>") + "\n")


UNSIGNED_FAMILY_CASES = {
    "the PR tier signs no family cell it reports only": ("pr", "report"),
    "the nightly tier signs it": ("nightly", "report"),
    "the PR tier signs it once the key judges the family cells": ("pr", "fail"),
}
"""The tier of each gate run of a candidate that changes a family cell, and the `families` key of its allowlist file."""


@needs("posix")
@needs("sys-monitoring")
@pytest.mark.parametrize("case", sorted(UNSIGNED_FAMILY_CASES))
def test_pr_tier_signs_no_family_cell_it_reports_only(case: str, tmp_path: Path) -> None:
    """The PR tier reports a family cell whose sides both generated other bytes as `differs-unsigned`.

    The candidate adds a comment to the output of the family cell that has a `bytes` field, so its signature is
    `no-behaviour` where it is signed. The nightly tier signs that cell and prints its class. With `families = "fail"`
    the cell is in the verdict, and the PR tier signs it and judges it like any other cell.
    """
    tier, mode = UNSIGNED_FAMILY_CASES[case]
    repo = tmp_path / "repo"
    commits = _fake_generator(repo, ["first"], "0.1.0")
    (repo / "src" / "datamodel_code_generator" / "__main__.py").write_text(FAKE_RAW, encoding="utf-8")
    _family_library(repo, FAKE_FAMILY_DOCUMENTS)
    projects = tmp_path / "projects.json"
    projects.write_text('{"projects": []}', encoding="utf-8")
    allowlist = tmp_path / "allowlist.toml"
    allowlist.write_text(f'base = "0.1.0"\nfamilies = "{mode}"\n', encoding="utf-8")
    argv = ["gate", commits[0], str(repo), "--types", "pydantic_v2.BaseModel", "--candidate-python", sys.executable]
    argv += ["--cache-dir", str(tmp_path / "cache"), "--allowlist", str(allowlist), "--tier", tier, "-j", "1"]
    argv += ["--projects", str(projects), "--select", "tests/data/jsonschema/plain.json", "--select", "family:*"]
    prefixes = ("family", "families", "violation:", "summary:")
    lines = _family_lines([*argv, "--json", str(tmp_path / "cells.jsonl")], tmp_path / "cells.jsonl", prefixes)
    assert_output("\n".join(lines).replace(str(tmp_path), "TMP").replace(commits[0][:9], "<sha>") + "\n")


PICK_FAMILY_CASES = {
    "a judged family cell runs the change": ("fail", FAKE_FAMILY_DOCUMENTS, "complete"),
    "a family cell the run reports only adds no evidence": ("report", FAKE_FAMILY_DOCUMENTS, "incomplete"),
    "a shape the library dropped is left out": ("fail", {"plain": {"type": "str"}}, "incomplete"),
}
"""The `families` key of each identity run with a pick, the candidate's family library and the run's verdict."""


@pytest.mark.allow_direct_assert(
    "an oracle case fails on its own verdict, never through a stored output a fix run accepts"
)
@needs("posix")
@needs("sys-monitoring")
@pytest.mark.parametrize("case", sorted(PICK_FAMILY_CASES))
def test_identity_picks_family_cells(case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A catalog records the family cells its selection names, and `--pick` reads them like the fixture cells.

    Only the family shape that takes the deprecation branch runs the change; the selection of the plain fixture does
    not. A judged family cell the pick adds completes the evidence; one the run reports only adds none, and a picked
    shape the candidate's library no longer has is left out.
    """
    mode, documents, wanted = PICK_FAMILY_CASES[case]
    argv, commit = _identity_argv(tmp_path, "tests/data/jsonschema/plain.json")
    repo = tmp_path / "repo"
    _family_library(repo, FAKE_FAMILY_DOCUMENTS)
    monkeypatch.setenv(behaviour_diff.REPOSITORY_ENV, str(repo))
    catalog = tmp_path / "catalog.json.gz"
    selects = ["--select", "tests/data/jsonschema/plain.json", "--select", "family:*"]
    with redirect_stdout(StringIO()):
        behaviour_diff.main([
            "catalog",
            str(catalog),
            commit,
            *selects,
            "-j",
            "1",
            "--cache-dir",
            str(tmp_path / "cache"),
        ])
    _family_library(repo, documents)
    allowlist = tmp_path / "allowlist.toml"
    allowlist.write_text(f'families = "{mode}"\n', encoding="utf-8")
    prefixes = ("pick: cell", "pick: no", "families", "evidence: complete", "evidence: incomplete")
    argv += ["--pick", str(catalog), "--allowlist", str(allowlist)]
    lines = _family_lines(argv, tmp_path / "cells.jsonl", prefixes)
    verdict = "complete" if "evidence: complete" in lines else "incomplete" if lines[0] == "exit=1" else lines[0]
    assert verdict == wanted, lines
    assert_output("\n".join(lines).replace(commit[:12], "<sha>").replace(str(tmp_path), "TMP") + "\n")


@needs("posix", "no-sys-monitoring")
def test_catalog_refuses_an_interpreter_without_sys_monitoring(tmp_path: Path) -> None:
    """`catalog` refuses Python 3.11, which records nothing a cell ran, before any worker: no catalog of no keys."""
    out = tmp_path / "catalog.json.gz"
    with pytest.raises(SystemExit) as refused:
        behaviour_diff.main(["catalog", str(out), "--select", "no/such/input", "--cache-dir", str(tmp_path / "cache")])
    assert_output(f"{refused.value}\nwritten: {out.exists()}\n")


BASE_ARGUMENT_CASES = {
    "verdicts and a pick": ["gate", "v1", "--verdicts", "v.json", "--pick", "c.json.gz", "--check"],
    "joined values": ["gate", "--verdicts=v.json", "--pick=c.json.gz", "-j", "2"],
    "unique prefixes": ["gate", "--verd", "v.json", "--pi", "c.json.gz", "--projects", "p.json"],
    "neither": ["gate", "v1", "--identity"],
    "a checkout named like an option after --": ["gate", "v1", "--pick", "c.json.gz", "--check", "--", "--pick"],
}
"""Arguments of a head's gate: its base's gate gets them without the head-only options."""


@pytest.mark.parametrize("case", sorted(BASE_ARGUMENT_CASES))
def test_base_gate_gets_no_head_only_option(case: str) -> None:
    """The base's gate gets the head's arguments without `--verdicts` and `--pick`, or a prefix of either, and value."""
    argv = BASE_ARGUMENT_CASES[case]
    assert_output(f"{argv} -> {behaviour_diff.base_arguments(argv)}\n")


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
    its own package instead; one whose package does not import stops it with the exit status of its worker. A
    revision BASE with `--base-python` stops it: the plan reads the revision's tree, which the installed release is
    not.
    """
    repo = tmp_path / "repo"
    _fake_generator(repo, ["first"], None)
    empty = tmp_path / "empty"
    (empty / "tests" / "data" / "jsonschema").mkdir(parents=True)
    (empty / "tests" / "data" / "jsonschema" / "plain.json").write_text("{}", encoding="utf-8")
    installed = ["--base-python", sys.executable, "--types", "pydantic_v2.BaseModel"]
    cases = (
        ("no-such-revision", str(repo), []),
        ("0.0.0", "no-such-revision", []),
        ("0.0.0", str(empty), installed),
        ("HEAD", str(repo), installed),
    )
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
    """Write one model of the schema's `type`, changed as the `CHANGES` of its `name` under the given options say.

    A preset of another target Python than `--target-python-version` is refused, as the package's.
    """
    ns = arg_parser.parse_args(argv)
    if ns.preset and ns.target_python_version and f"py{ns.target_python_version.replace('.', '')}" not in ns.preset:
        print(f"Error: --preset {ns.preset} targets another Python than {ns.target_python_version}")
        return 1
    spec = json.loads(Path(ns.input).read_text(encoding="utf-8"))
    effects = [effect for name, when, effect in CHANGES if name == spec["name"] and when in (None, *argv)]
    if "fail" in spec and "fixed" not in effects:
        print(f"Error: {spec['fail']}")
        return 1
    if "break" in effects:
        print("Error: broken")
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
CELLS_VERSION = """def get_version():
    try:
        from datamodel_code_generator._version import __version__
    except ImportError:
        return "0.0.0"
    return __version__
"""
"""The stand-in's version lookup, as the package's: the version file a revision's tree gets, else `0.0.0`."""
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
    f"{CELLS_PACKAGE}/__init__.py": CELLS_VERSION,
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
    (`orphan`). `records` also runs `declared_changes.py check` on the PR with the gate's verdicts (`--verdicts`),
    which must give the gate's verdict: it fails the harness labels the gate refuses. `shards` runs the gate as that
    many shards, one after another, with the same `--verdicts` file. `shape` names, in words, the rule the case pins or
    the passing shape it keeps. `merged` ends the subject of main's next commit with that pull request, `(#n)`, and
    `released` the release's, as a squash merge writes it. `pushed` are commits before the head, and origin/main
    points at the head: a push of several pull requests to main.
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
    shards: int = 1
    merged: str = ""
    released: str = ""
    pushed: tuple[dict[str, str | None], ...] = ()


COMMENT_PLAIN = _cells_src(("plain", None, "comment"))
COMMENT_OTHER = _cells_src(("other", None, "comment"))
FLOAT_OTHER = _cells_src(("other", None, "type:float"))
OTHER_NOTE_UNNAMED = _record("other-note", "output-text")
"""A record of main's change of `other` that names none of its cells."""
PAYLOAD_PLAIN = {
    "input": ["payload:jsonschema/plain.json"],
    "input_file_type": "jsonschema",
    "backends": "*",
    "options": "*",
}
"""A selector of the payload cells of `plain`, which the nightly and release tiers build and the PR tier does not."""


def _landed(*cells: dict[str, object], refs: tuple[str, ...] = ("#2",), extends: str = "other-note") -> dict[str, str]:
    """Return a record that names the cells of a change merged as `refs`: it extends main's record of it."""
    return _record("other-note-cells", None, *cells, extends=extends, refs=list(refs), title=None, summary=None)


OUTPUT_TEXT_OTHER = _record("other-note", "output-text", _cells("other.json"))
OTHER_REFACTOR = _cells_src(("other", None, "comment"), helper="HELPER = 1\n")
"""A source change over main's change of `other` that changes no output."""
LISTING_HARNESS = {f"{CELLS_EXPECTED}/main/jsonschema/listing.txt": "Model\nOther\n"} | _record(
    "listing-test", "harness", covers=("main/jsonschema/listing.txt",)
)
"""A changed listing a `harness` record covers: its proof fails in a src change."""
CHECKED = _cells_input("checked", type="int", validator=True)
MEMORY = _cells_input("memory", type="int", memory=True)
FAILS = _cells_input("fails", type="int", fail="boom")
PLAIN_MODULE = f"{CELLS_EXPECTED}/main/jsonschema/plain.py"
FAST_PATH_LIST = "main/cli_fast_paths/fast_paths_load_these_modules[help].txt"
FAST_PATH_REFUSAL = "main/cli_fast_paths/parsed_fast_paths_refuse_the_options_of_the_skill_installer.result.txt"
FAST_PATH_RELEASE = {
    "tests/main/test_cli_fast_paths.refs": _cells_refs({
        "test_fast_paths_load_these_modules": [f"help\t{FAST_PATH_LIST}"],
        "test_parsed_fast_paths_refuse_the_options_of_the_skill_installer": [f"\t{FAST_PATH_REFUSAL}"],
    }),
    f"{CELLS_EXPECTED}/{FAST_PATH_LIST}": '{"package": ["pkg", "pkg.arguments"]}\n',
    f"{CELLS_EXPECTED}/{FAST_PATH_REFUSAL}": '{"code": 2, "stderr": "Error: --skill-scope requires --install-skill"}\n',
}
"""The stored module list of a CLI fast path (an internal trace, #7155) and one of its refusals (a notice)."""
SECOND_MODULE = f"{CELLS_EXPECTED}/main/jsonschema/second.py"
SECOND_REFS = "tests/main/test_second.refs"
SECOND_OWN = {
    SECOND_REFS: _cells_refs({"test_second": ["\tmain/jsonschema/second.py"]}),
    SECOND_MODULE: CELLS_MODULE.format("int  # note"),
}
"""A second stored module of its own, its output the plain module with a comment."""
SECOND_SHARED = {SECOND_REFS: _cells_refs({"test_second": ["\tmain/jsonschema/second.py\t= main/jsonschema/plain.py"]})}
"""The same label, its output now the stored file of the plain module."""
SECOND_FLOAT = {SECOND_REFS: SECOND_OWN[SECOND_REFS], SECOND_MODULE: CELLS_MODULE.format("float")}
"""The second module with the other type, a file of its own."""
SECOND_VARIANTS = {
    SECOND_REFS: _cells_refs({
        "test_second": [
            "\tmain/jsonschema/second.py\t@python==3.14",
            "\tmain/jsonschema/second.py\t@python==3.15\t= main/jsonschema/plain.py",
        ]
    })
}
"""The same label under two storages: its own file under one Python, the plain module's under another."""
BASE_GATE_PROBE = """import os
import sys
from pathlib import Path

checkout = Path(os.environ.get("BEHAVIOUR_DIFF_REPOSITORY", "/nonexistent"))
sys.exit(0 if (checkout / "docs" / "notes.md").exists() else 1)
"""
"""A base's gate that passes only when it runs for the PR's checkout, which has `docs/notes.md`."""
BASE_GATE_VERDICTS = """import sys

sys.exit(any(arg.startswith("--verd") for arg in sys.argv))
"""
"""A base's gate from before `--verdicts`: it refuses the option."""
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
    "pass: output-text whose payload cases both sides refuse alike": CellsCase(
        "pass",
        "output-text, payload cases refused alike",
        _cells_src(("plain", None, "comment"), ("plain", "Payload", "break"))
        | _record("plain-note", "output-text", _cells("plain.json")),
        release=_cells_src(("plain", "Payload", "break")),
    ),
    "pass: output-text with a covered stored module that has no payloads": CellsCase(
        "pass",
        "output-text, covered stored module without payloads",
        COMMENT_PLAIN
        | {PLAIN_MODULE: "VALUE = 1  # note\n"}
        | _record("plain-note", "output-text", _cells("plain.json"), covers=("main/jsonschema/plain.py",)),
        release={PLAIN_MODULE: "VALUE = 1\n"},
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
    "pass: a selector glob ending in ** names the files below it": CellsCase(
        "pass",
        "a final ** names files on every Python",
        _cells_src(("plain", "--keyword-only", "comment"))
        | _record("plain-note", "output-text", _cells("**", options=[["--keyword-only"]])),
    ),
    "pass: a selector's inline option names the cell the tiers run": CellsCase(
        "pass",
        "inline option spelling",
        _cells_src(("plain", "--preset", "comment"))
        | _record("preset-note", "output-text", _cells("plain.json", options=[["--preset=standard-py310-20260101"]])),
    ),
    "pass: the base's gate runs for the PR's checkout": CellsCase(
        "pass",
        "oracle files, the base's gate runs for the checkout",
        {"scripts/behaviour_diff.py": BASE_GATE_PROBE + "# changed\n", "docs/notes.md": "notes\n"},
        release={"scripts/behaviour_diff.py": BASE_GATE_PROBE},
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
    "pass: the base's gate gets no --verdicts": CellsCase(
        "pass",
        "oracle files, the base's gate refuses --verdicts",
        {"scripts/behaviour_diff.py": BASE_GATE_VERDICTS + "# changed\n"},
        release={"scripts/behaviour_diff.py": BASE_GATE_VERDICTS},
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
    "pass: harness covers a modified module the proof holds for, in two shards": CellsCase(
        "pass",
        "harness, modified module the proof holds for, the gate run as two shards with one verdicts file",
        CELLS_REFACTOR
        | {PLAIN_MODULE: CELLS_MODULE.format("int  # note")}
        | _record("plain-test", "harness", covers=("main/jsonschema/plain.py",)),
        records=True,
        shards=2,
    ),
    "pass: harness covers a changed module list of a CLI fast path": CellsCase(
        "pass",
        "harness, the modules a CLI fast path imports (#7155)",
        CELLS_REFACTOR
        | {f"{CELLS_EXPECTED}/{FAST_PATH_LIST}": '{"package": ["pkg"]}\n'}
        | _record("fast-path-test", "harness", covers=(FAST_PATH_LIST,)),
        release=FAST_PATH_RELEASE,
        records=True,
    ),
    "pass: harness covers a label that becomes shared and keeps its behaviour": CellsCase(
        "pass",
        "harness, a label that becomes shared, the proof holds",
        CELLS_REFACTOR
        | SECOND_SHARED
        | {SECOND_MODULE: None}
        | _record("second-test", "harness", covers=("main/jsonschema/second.py",)),
        release=SECOND_OWN,
        records=True,
    ),
    "pass: harness covers a label that leaves a shared file and keeps its behaviour": CellsCase(
        "pass",
        "harness, a label that splits from a shared file, the proof holds",
        CELLS_REFACTOR | SECOND_OWN | _record("second-test", "harness", covers=("main/jsonschema/second.py",)),
        release=SECOND_SHARED,
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
    "fail: output-text whose payload values change under an inline preset": CellsCase(
        "fail",
        "output-text, payload values change under an inline preset",
        _cells_src(("plain", "--preset", "comment"), ("plain", "Payload", "type:float"))
        | _record("preset-note", "output-text", _cells("plain.json", options=[["--preset=standard-py310-20260101"]])),
    ),
    "fail: experimental covers no head that stops": CellsCase(
        "fail",
        "experimental, the head stops",
        _cells_src(("plain", "--generate-client", "memory"))
        | _record("client-memory", "experimental", _cells("plain.json", options=[["--generate-client"]])),
    ),
    "fail: notice outside its match": CellsCase(
        "fail",
        "notice, outside its match",
        _cells_src(("plain", None, "warn:mind the gap"))
        | _record("plain-warns", "notice", _cells("plain.json", match="other text")),
    ),
    "pass: a selector of the random tier's compositions is not judged by this run": CellsCase(
        "pass",
        "composition selector",
        COMMENT_OTHER
        | _record(
            "other-note",
            "output-text",
            _cells("other.json"),
            {"input": ["composition:*/**/tagged/**"], "input_file_type": "jsonschema", "backends": "*", "options": "*"},
        ),
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
    "fail: a change to the stored-module side runs the base's gate, which fails": CellsCase(
        "fail",
        "stored-module side changed, the base's gate fails",
        {"scripts/_stored_module.py": "# weakened\n"},
        release={"scripts/behaviour_diff.py": "import sys\n\nsys.exit(1)\n"},
    ),
    "fail: a change to the source model beside a covered change runs the base's gate": CellsCase(
        "fail",
        "source model changed beside covered cells, the base's gate fails",
        COMMENT_OTHER | OUTPUT_TEXT_OTHER | {"scripts/_source_model.py": "# weakened\n"},
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
    "fail: harness covers a label that becomes shared and changes its behaviour": CellsCase(
        "fail",
        "harness, a label that becomes shared, the proof fails",
        CELLS_REFACTOR
        | SECOND_SHARED
        | {SECOND_MODULE: None}
        | _record("second-test", "harness", covers=("main/jsonschema/second.py",)),
        release=SECOND_FLOAT,
        records=True,
    ),
    "fail: harness covers a label that leaves a shared file and changes its behaviour": CellsCase(
        "fail",
        "harness, a label that splits from a shared file with new content, the proof fails",
        CELLS_REFACTOR | SECOND_FLOAT | _record("second-test", "harness", covers=("main/jsonschema/second.py",)),
        release=SECOND_SHARED,
        records=True,
    ),
    "fail: harness covers a label that compares several stored files": CellsCase(
        "fail",
        "harness, a label with several stored outputs, the proof cannot read them",
        CELLS_REFACTOR | SECOND_VARIANTS | _record("second-test", "harness", covers=("main/jsonschema/second.py",)),
        release=SECOND_OWN,
        records=True,
    ),
    "fail: harness covers a changed refusal of a CLI fast path": CellsCase(
        "fail",
        "harness, a CLI fast path's refusal changed",
        CELLS_REFACTOR
        | {f"{CELLS_EXPECTED}/{FAST_PATH_REFUSAL}": '{"code": 0, "stderr": ""}\n'}
        | _record("fast-path-refusal", "harness", covers=(FAST_PATH_REFUSAL,)),
        release=FAST_PATH_RELEASE,
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
    "pass: a push to main of a src change, then a test-only change a harness record covers": CellsCase(
        "pass",
        "harness, modified listing, in a change without src/ pushed to main after a src change",
        {f"{CELLS_EXPECTED}/main/jsonschema/listing.txt": "Model\nOther\n"}
        | _record("listing-test", "harness", covers=("main/jsonschema/listing.txt",)),
        pushed=(CELLS_REFACTOR,),
        records=True,
    ),
    "fail: a push to main of a test-only change, then a src change a harness record covers": CellsCase(
        "fail",
        "harness, modified listing, in a src change pushed to main after a change without src/",
        CELLS_REFACTOR
        | {f"{CELLS_EXPECTED}/main/jsonschema/listing.txt": "Model\nOther\n"}
        | _record("listing-test", "harness", covers=("main/jsonschema/listing.txt",)),
        pushed=({"docs/notes.md": "notes\n"},),
        records=True,
    ),
    "pass: a push to main of a docs change, then a test-only change a harness record covers": CellsCase(
        "pass",
        "harness, modified listing, in a change without src/ pushed to main after a docs change",
        {f"{CELLS_EXPECTED}/main/jsonschema/listing.txt": "Model\nOther\n"}
        | _record("listing-test", "harness", covers=("main/jsonschema/listing.txt",)),
        pushed=({"docs/notes.md": "notes\n"},),
        records=True,
    ),
    "fail: a push to main of a src change a harness record covers, then a test-only change": CellsCase(
        "fail",
        "harness, modified listing, in a src change pushed to main before a change without src/",
        {"docs/notes.md": "notes\n"},
        pushed=(
            CELLS_REFACTOR
            | {f"{CELLS_EXPECTED}/main/jsonschema/listing.txt": "Model\nOther\n"}
            | _record("listing-test", "harness", covers=("main/jsonschema/listing.txt",)),
        ),
        records=True,
    ),
    "pass: a push to main of a src change a harness record covers, then its revert": CellsCase(
        "pass",
        "harness, modified module the proof holds for, in a src change whose revert follows in the push",
        pushed=(
            CELLS_REFACTOR
            | {PLAIN_MODULE: CELLS_MODULE.format("int  # note")}
            | _record("plain-test", "harness", covers=("main/jsonschema/plain.py",)),
        ),
        head=_cells_src(),
        records=True,
    ),
    "pass: a push to main of a src refactor, its revert, then the cells of a change merged before it": CellsCase(
        "pass",
        "records-only change, cells of a merged change, after a src change and its revert in the push",
        _landed(_cells("other.json")),
        base=COMMENT_OTHER | OTHER_NOTE_UNNAMED,
        merged="#2",
        pushed=(OTHER_REFACTOR, COMMENT_OTHER),
        records=True,
    ),
    "fail: a push to main of a src change a harness proof refuses, its revert, then merged cells": CellsCase(
        "fail",
        "harness, modified listing, in a src change of a push whose src/ ends unchanged",
        _landed(_cells("other.json")),
        base=COMMENT_OTHER | OTHER_NOTE_UNNAMED,
        merged="#2",
        pushed=(OTHER_REFACTOR | LISTING_HARNESS, COMMENT_OTHER),
        records=True,
    ),
    "fail: a push to main of a src change, its revert a harness proof refuses, then merged cells": CellsCase(
        "fail",
        "harness, modified listing, in the revert of a push whose src/ ends unchanged",
        _landed(_cells("other.json")),
        base=COMMENT_OTHER | OTHER_NOTE_UNNAMED,
        merged="#2",
        pushed=(OTHER_REFACTOR, COMMENT_OTHER | LISTING_HARNESS),
        records=True,
    ),
    "fail: a push to main of a src change, its revert, then cells of a pull request not merged": CellsCase(
        "fail",
        "records-only change, cells of an unmerged pull request, after a src change and its revert in the push",
        _landed(_cells("other.json"), refs=("#2", "#3")),
        base=COMMENT_OTHER | OTHER_NOTE_UNNAMED,
        merged="#2",
        pushed=(OTHER_REFACTOR, COMMENT_OTHER),
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
    "pass: a PR without a src change names the cells of a change merged before it": CellsCase(
        "pass",
        "records-only PR, cells of a merged change",
        _landed(_cells("other.json")),
        base=COMMENT_OTHER | OTHER_NOTE_UNNAMED,
        merged="#2",
    ),
    "fail: a PR without a src change names cells of a pull request not merged": CellsCase(
        "fail",
        "records-only PR, cells of an unmerged pull request",
        _landed(_cells("other.json"), refs=("#2", "#3")),
        base=COMMENT_OTHER | OTHER_NOTE_UNNAMED,
        merged="#2",
    ),
    "fail: a PR without a src change names cells of a pull request the release has": CellsCase(
        "fail",
        "records-only PR, cells of a released pull request",
        _landed(_cells("other.json")),
        base=COMMENT_OTHER | OTHER_NOTE_UNNAMED,
        released="#2",
    ),
    "fail: a PR without a src change names an option of a merged change the release lacks": CellsCase(
        "fail",
        "records-only PR, cells of a merged change under an unreleased option",
        _landed(_cells("other.json", options=[["--new-flag"]])),
        base=_cells_src(("other", "--new-flag", "comment"), flags=(*CELLS_FLAGS, "--new-flag")) | OTHER_NOTE_UNNAMED,
        merged="#2",
    ),
    "fail: a PR without a src change extends its own record with cells of a merged change": CellsCase(
        "fail",
        "records-only PR, a merged change's cells on a record it adds",
        _landed(_cells("other.json", match="float"), extends="other-float") | _record("other-float", "decided-change"),
        base=COMMENT_OTHER,
        merged="#2",
    ),
    "fail: a src PR judges the selectors of a record of a merged change": CellsCase(
        "fail",
        "src PR, a merged change's record judged as its own",
        _cells_src(("other", None, "comment"), ("plain", None, "comment"))
        | _record("plain-note", "output-text", _cells("plain.json"))
        | _landed(_cells("other.json")),
        base=COMMENT_OTHER | OTHER_NOTE_UNNAMED,
        merged="#2",
    ),
    "pass: the release run covers by a record of a merged change": CellsCase(
        "pass",
        "release run, a merged change's cells",
        base=COMMENT_OTHER | OTHER_NOTE_UNNAMED | _landed(_cells("other.json")),
        merged="#2",
        against="tag",
        tier="nightly",
    ),
    "fail: the release run judges the selectors of a record of a merged change": CellsCase(
        "fail",
        "release run, a merged change's stale cells",
        base=COMMENT_OTHER | OTHER_NOTE_UNNAMED | _landed(_cells("other.json"), _cells("plain.json")),
        merged="#2",
        against="tag",
        tier="nightly",
    ),
    "pass: a PR run judges no selector of the payload cells it does not build": CellsCase(
        "pass",
        "PR run, payload selector",
        COMMENT_OTHER | _record("other-note", "output-text", _cells("other.json"), PAYLOAD_PLAIN),
    ),
    "fail: the nightly judges a payload selector that matches equal cells only": CellsCase(
        "fail",
        "nightly, payload selector, equal cells",
        base=COMMENT_OTHER | _record("other-note", "output-text", _cells("other.json"), PAYLOAD_PLAIN),
        against="tag",
        tier="nightly",
    ),
    "pass: the nightly covers payload cells by a payload selector": CellsCase(
        "pass",
        "nightly, payload selector, covered cells",
        base=COMMENT_PLAIN | _record("plain-note", "output-text", _cells("plain.json"), PAYLOAD_PLAIN),
        against="tag",
        tier="nightly",
    ),
    "fail: a selector of two label spaces": CellsCase(
        "fail",
        "selector of two label spaces",
        COMMENT_OTHER
        | _record(
            "other-note",
            "output-text",
            PAYLOAD_PLAIN | {"input": [f"{CELLS_INPUTS}/other.json", "payload:jsonschema/plain.json"]},
        ),
    ),
    "fail: two records whose evidence fails on one cell": CellsCase(
        "fail",
        "two records, evidence fails on one cell",
        FLOAT_OTHER | OUTPUT_TEXT_OTHER | _record("other-warns", "notice", _cells("other.json", match="float")),
    ),
}
"""Every fail-closed rule of the cell gate and every passing shape it must keep, with the verdict it must give."""


def _cells_write(repo: Path, files: dict[str, str | None]) -> None:
    """Write the files of one commit; None removes one."""
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        with contextlib.suppress(TypeError):
            path.write_text(text, encoding="utf-8")


CELLS_DATES = {"GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000"}
"""The dates of every commit of an oracle case's history: cases whose histories agree get the same commits, so their
runs of one revision read the same version file and share their results (`SharedRuns`)."""


def _cells_commit(repo: Path, files: dict[str, str | None], message: str) -> str:
    _cells_write(repo, files)
    _git("add", "-A", ".", cwd=repo)
    _git("commit", "-q", "--allow-empty", "-m", message, cwd=repo, dates=CELLS_DATES)
    return _git("rev-parse", "HEAD", cwd=repo)


def _cells_project(case: CellsCase, repo: Path) -> dict[str, str]:
    """Commit the case's history and return the revisions `against` may name; the working tree is the PR's head."""
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    release = _cells_commit(
        repo, CELLS_RELEASE | case.release, f"release ({case.released})" if case.released else "release"
    )
    _git("tag", CELLS_TAG, cwd=repo)
    _cells_commit(repo, case.base, f"base ({case.merged})" if case.merged else "base")
    _git("switch", "-q", "-c", "layer", cwd=repo)
    base = _cells_commit(repo, case.layer, "layer")
    _git("switch", "-q", "main", cwd=repo)
    main = _cells_commit(repo, case.main, "main after the fork")
    _git("switch", "-q", "-c", "pr", "layer", cwd=repo)
    for number, pushed in enumerate(case.pushed, 1):
        _cells_commit(repo, pushed, f"pushed {number}")
    _cells_commit(repo, case.head, "head")
    if case.pushed:
        _git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=repo)
    orphan = _git("commit-tree", "-m", "another history", f"{release}^{{tree}}", cwd=repo, dates=CELLS_DATES)
    return {"base": base, "main": main, "tag": f"refs/tags/{CELLS_TAG}", "unknown": "4" * 40, "orphan": orphan}


def _cells_exit(run: Callable[[], int], log: StringIO) -> int:
    """Return a judge's exit status; a refusal by `SystemExit` is one, any other exception fails the case."""
    try:
        with redirect_stdout(log), redirect_stderr(log):
            return run()
    except SystemExit as stopped:
        log.write(f"{stopped.code}\n" * (not isinstance(stopped.code, int)))
        return stopped.code if isinstance(stopped.code, int) else 1


SHARED_SCRIPTS = ("__init__.py", "_behaviour_signature.py", "_stored_module.py", "compare_release.py")
"""The files of scripts/ the signature, stored-module and generation processes run or import."""


def _read(value: object) -> str:
    """Return an argument of a process by what the process reads: a file it is given by the digest of its bytes."""
    path = Path(value) if isinstance(value, str | Path) else None
    read = path is not None and path.is_absolute() and path.is_file()
    return hashlib.sha256(path.read_bytes()).hexdigest() if read else str(value)


def _files(tree: Path) -> dict[str, str]:
    """Return the files and directories below `tree` by their relative paths, a file with the digest of its bytes.

    Bytecode caches are left out: an interpreter writes them from the sources beside them and reads them in their place.
    """
    return {
        str(path.relative_to(tree)): hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
        for path in sorted(tree.rglob("*"))
        if "__pycache__" not in path.relative_to(tree).parts
    }


_Shared = TypeVar("_Shared")
"""What a shared call returns: a JSON value, read back from the shared file in the same shape."""


@dataclass
class SharedRuns:
    """The gate's signature, stored-module, generation and option processes of the stand-in's runs, each run once.

    `signature` returns what `scripts/_behaviour_signature.py` prints, `stored_side` what `scripts/_stored_module.py`
    writes (its draws are derandomised and use no database), `generate` what a worker of `scripts/compare_release.py`
    generates and `option_inventory` what the generator's parser offers; each reads, in a fresh process, only the
    interpreter, the files of scripts/ it runs, the files of `tree` (the directory holding the module, package or run
    it imports, or the source tree it imports the generator from), its other arguments (a file by its bytes) and the
    environment. Every oracle case signs and generates the release's cells of `plain` and `other` again, so calls of
    one function that agree on all of these share its result, in `root`: one directory of the test session, which its
    xdist workers share. A signature runs in `tree` (`-I` on absolute paths), a stored module from the checkout whose
    scripts are keyed (`-E -m`), a generation in a fresh directory of its own, and the environment differs between
    tests only in the variables of pytest and its xdist workers (`PYTEST_*`), which only they read. An empty result (a
    stopped draw), a failed signature (a timeout, a crash), a run the gate's own cache would not keep (a stopped
    generation: `Outcome.cacheable`), a result that names the directory holding `tree` or the session's directories
    (a test's own paths) and a result of a function defined inside a test (a replacement that may close over the test's
    values) are never shared. A shared stored-module result does not write its output file again: only `stored_side`
    reads it; a shared generation writes no output directory: its callers read the outcome.
    """

    root: Path
    locks: dict[str, threading.Lock] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def run(
        self,
        function: FunctionType,
        call: Callable[[], _Shared],
        tree: Path,
        *inputs: object,
    ) -> _Shared:
        """Return what `call` (a call of `function`) returns, from the first call of `function` with these inputs."""
        env = sorted(
            (name, value)
            for name, value in os.environ.items()
            if not name.startswith(("DATAMODEL_CODE_GENERATOR", "PYTEST_"))
        )
        scripts = [_read(behaviour_diff.SIGNATURE_SCRIPT.with_name(name)) for name in SHARED_SCRIPTS]
        name = f"{function.__module__}.{function.__qualname__}"
        key = hashlib.sha256(
            json.dumps([name, _files(tree), [str(value) for value in inputs], env, scripts]).encode()
        ).hexdigest()
        path = self.root / f"{key}.json"
        with self.lock:
            lock = self.locks.setdefault(key, threading.Lock())
        with lock:
            text = path.read_text(encoding="utf-8") if path.exists() else json.dumps(call())
            result = json.loads(text)
            shared = (
                bool(result)
                and not result.get(SIGNATURE_FAILED)
                and result.get("cacheable", True)
                and "<locals>" not in name
                and not any(str(directory) in text for directory in (tree.parent, self.root.parent))
            )
            if shared and not path.exists():
                written = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
                written.write_text(text, encoding="utf-8")
                written.replace(path)
        return result


@pytest.fixture(scope="session")
def shared_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Return the directory of the session's `SharedRuns`: beside the xdist workers' own directories, where they run."""
    base = tmp_path_factory.getbasetemp()
    root = (base.parent if os.environ.get("PYTEST_XDIST_WORKER") else base) / "shared-runs"
    root.mkdir(exist_ok=True)
    return root


@contextmanager
def shared_runs(root: Path) -> Generator[None, None, None]:
    """Let the gate runs inside take the processes they share (`SharedRuns`) from `root`.

    It wraps the functions installed when it starts, so a test that replaces one keeps its replacement. The second run
    of the head (`Gate.rerun`), which must show a head that generates other output each time, the runs that trace or
    record what they run (`Runner.probes`, `Runner.lines`) and those of a side with no source tree of its own always
    generate. Every option inventory of these runs reads a revision's tree on `PYTHONPATH` (`run_environment`), which
    keys it.
    """
    runs = SharedRuns(root)
    signature, stored, inventory = behaviour_diff.signature, behaviour_diff.stored_side, behaviour_diff.option_inventory
    build, generate = behaviour_diff.build_gate, behaviour_diff.compare_release.Runner.generate
    reruns: list[behaviour_diff.compare_release.Runner] = []

    def shared_signature(
        gate: behaviour_diff.Gate, target: Path, backend: str, payloads: Path | None, *, targets: bool = False
    ) -> dict[str, object]:
        call = functools.partial(signature, gate, target, backend, payloads, targets=targets)
        given = (gate.signature_python, gate.candidate_python, gate.timeout, target.name, backend, _read(payloads))
        return runs.run(signature, call, target.parent, *given, targets)

    def shared_stored(
        gate: behaviour_diff.Gate, target: Path, backend: str, values: Path | None
    ) -> behaviour_diff.StoredSide:
        call = functools.partial(stored, gate, target, backend, values)
        given = (gate.candidate_python, behaviour_diff.PAYLOAD_DRAW_TIMEOUT, target.name, backend, _read(values))
        return runs.run(stored, call, target.parent, *given)

    def shared_inventory(python: Path, env: dict[str, str] | None = None) -> dict[str, list[str] | None]:
        given = {**(env or {})}
        tree = Path(given.pop("PYTHONPATH"))
        code = behaviour_diff.OPTION_INVENTORY
        return runs.run(inventory, lambda: inventory(python, env), tree, python, code, sorted(given.items()))

    def shared_build(ns: argparse.Namespace) -> behaviour_diff.Gate:
        gate = build(ns)
        reruns.extend([gate.rerun] if gate.rerun is not None else [])
        return gate

    def shared_generate(
        runner: behaviour_diff.compare_release.Runner, args: list[str], tmp: Path
    ) -> behaviour_diff.compare_release.Outcome:
        package = runner.side.package
        if any(runner is rerun for rerun in reruns) or runner.probes is not None or runner.lines or package is None:
            return generate(runner, args, tmp)
        env = sorted((name, value.replace(str(package), "<package>")) for name, value in runner.env.items())
        inputs = [
            _files(path.parent) for path in behaviour_diff.compare_release._path_arguments(args) if path.is_file()
        ]
        side = (runner.side.name, runner.side.python, runner.import_check, runner.timeout, runner.memory_mb)
        given = (*side, env, [*map(_read, args)], inputs)
        outcome = runs.run(generate, lambda: vars(generate(runner, args, tmp)), package, *given)
        return behaviour_diff.compare_release.Outcome(**outcome)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(behaviour_diff, "signature", shared_signature)
        patch.setattr(behaviour_diff, "stored_side", shared_stored)
        patch.setattr(behaviour_diff, "option_inventory", shared_inventory)
        patch.setattr(behaviour_diff, "build_gate", shared_build)
        patch.setattr(behaviour_diff.compare_release.Runner, "generate", shared_generate)
        yield


def cells_verdict(case: CellsCase, tmp_path: Path, shared: Path) -> tuple[str, str]:
    """Run the gate (and, for `records`, the records check) as CI runs it and return the verdict and the log.

    The verdict is `split` where the records check and the gate disagree. The gate's processes are shared with the
    other cases' runs through `shared` (`SharedRuns`).
    """
    with shared_runs(shared):
        return _cells_verdict(case, tmp_path)


def _cells_verdict(case: CellsCase, tmp_path: Path) -> tuple[str, str]:
    repo = tmp_path / "repo"
    against = _cells_project(case, repo)[case.against]
    projects = tmp_path / "projects.json"
    projects.write_text('{"projects": []}', encoding="utf-8")
    allowlist = tmp_path / "allowlist.toml"
    allowlist.write_text(f'base = "{CELLS_TAG}"\n', encoding="utf-8")
    argv = ["gate", against, str(repo), "--tier", case.tier, "--check", "--candidate-python", sys.executable]
    argv += ["--cache-dir", str(tmp_path / "cache"), "--projects", str(projects), "--allowlist", str(allowlist)]
    argv += ["--verdicts", str(verdicts := tmp_path / "verdicts.json")]
    log = StringIO()
    shards = [
        _cells_exit(
            lambda index=index: behaviour_diff.main([*argv, "-j", "2", "--shard", f"{index}/{case.shards}"]), log
        )
        for index in range(1, case.shards + 1)
    ]
    codes = {"gate": max(shards)}
    if case.records:
        check = ["check", "--base", against, "--head", "HEAD", "--tag", CELLS_TAG, "--verdicts", str(verdicts)]
        codes["records"] = _cells_exit(lambda: declared_changes.main(check, repo=repo), log)
    verdicts_given = {"pass" if code == 0 else "fail" for code in codes.values()}
    verdict = verdicts_given.pop() if len(verdicts_given) == 1 else "split"
    return verdict, f"{codes}\n{log.getvalue()}"


@needs("posix")
def test_guide_runs_the_gate_of_the_release(tmp_path: Path, shared_root: Path) -> None:
    """Without `--cells`, `guide` runs the gate of the release against the previous one and writes its page."""
    repo = tmp_path / "repo"
    _cells_project(CellsCase("pass", "release run", base=COMMENT_OTHER | OUTPUT_TEXT_OTHER), repo)
    release = _git("rev-parse", CELLS_TAG, cwd=repo)
    projects = tmp_path / "projects.json"
    projects.write_text('{"projects": []}', encoding="utf-8")
    allowlist = tmp_path / "allowlist.toml"
    allowlist.write_text(f'base = "{CELLS_TAG}"\n', encoding="utf-8")
    argv = ["guide", release, "0.2.0", "--candidate", str(repo), "-j", "2"]
    argv += ["--candidate-python", sys.executable, "--cache-dir", str(tmp_path / "cache")]
    argv += ["--projects", str(projects), "--allowlist", str(allowlist)]
    with shared_runs(shared_root), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        code = behaviour_diff.main([*argv, "--docs", str(page := tmp_path / "page.json")])
    assert_output(f"exit={code}\n{page.read_text(encoding='utf-8').replace(release, 'RELEASE')}")


@pytest.mark.allow_direct_assert("the shipped table's keys are the oracle, never a stored output a fix run accepts")
def test_shipped_allowlist_accepts_nothing() -> None:
    """Change records judge every differing cell: the shipped table keeps its `base`, `[[base_skip]]` and `families`."""
    table = tomllib.loads(behaviour_diff.DEFAULT_ALLOWLIST.read_text(encoding="utf-8"))
    assert set(table) <= {"base", "base_skip", behaviour_diff.FAMILIES_KEY}, sorted(table)


@pytest.mark.allow_direct_assert(
    "an oracle case fails on its own verdict, never through a stored output a fix run accepts"
)
@needs("posix")
@pytest.mark.parametrize("case", sorted(CELLS_CASES))
def test_cells_oracle(case: str, tmp_path: Path, shared_root: Path) -> None:
    """The gate gives each oracle case the verdict of the cell gate: a case that fails, fails on that verdict."""
    verdict, log = cells_verdict(CELLS_CASES[case], tmp_path, shared_root)
    assert verdict == CELLS_CASES[case].verdict, log


REPORT_LINES = (
    "violation:",
    "    ",
    "records:",
    "covered:",
    "signature only",
    "payload cases both sides refused",
    "stored modules with no payloads",
    "uncovered",
    "[[change.cells]]",
    "input",
    "back",
    "options",
    "# ",
)


@needs("posix")
@pytest.mark.parametrize(
    "case",
    [
        "fail: an uncovered differing cell",
        "pass: decided-change inside aspects and match",
        "pass: output-text whose payload cases both sides refuse alike",
        "pass: output-text with a covered stored module that has no payloads",
    ],
)
def test_cells_report(case: str, tmp_path: Path, shared_root: Path) -> None:
    """The gate reports each violation, the records that covered cells and a block for the cells none covers.

    The block is a starting `[[change.cells]]` table per input, with the aspects and a `match` of the change lines.
    """
    _, log = cells_verdict(CELLS_CASES[case], tmp_path, shared_root)
    lines = [line for line in log.splitlines() if line.startswith(REPORT_LINES)]
    assert_output("\n".join(sorted(set(lines), key=lines.index)) + "\n")


@pytest.mark.allow_direct_assert(
    "an oracle case fails on its own verdict, never through a stored output a fix run accepts"
)
@needs("posix")
def test_cells_stored_side_that_draws_nothing_fails(
    tmp_path: Path, shared_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stored module whose head side wrote no draws (the timeout, a crash) fails its proof; it is no empty pass."""
    draw = behaviour_diff.stored_side

    def stopped(
        gate: behaviour_diff.Gate, target: Path, backend: str, values: Path | None
    ) -> behaviour_diff.StoredSide:
        return {} if values is None and target.parent.name == "head" else draw(gate, target, backend, values)

    monkeypatch.setattr(behaviour_diff, "stored_side", stopped)
    verdict, log = cells_verdict(CELLS_CASES["pass: output-text with a covered stored module"], tmp_path, shared_root)
    assert verdict == "fail", log
    assert "wrote no draws" in log, log


@pytest.mark.allow_direct_assert(
    "an oracle case fails on its own verdict, never through a stored output a fix run accepts"
)
@needs("posix")
def test_cells_selector_over_the_cell_limit_is_refused(
    tmp_path: Path, shared_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A selector that names more cells than the limit stops the gate with a message that names the record."""
    case = CELLS_CASES["pass: options * covers the preset cells"]
    monkeypatch.setattr(behaviour_diff, "SELECTOR_CELL_LIMIT", 10)
    verdict, log = cells_verdict(case, tmp_path, shared_root)
    assert verdict == "fail", log
    assert "entry preset-note: cells 1 names" in log, log
    assert "narrow its input, backends or options" in log, log


FACTS_BASE = (0, (("model.py", "a1"),), "w1", "o1")
KINDS_CASES = {
    "equal": FACTS_BASE,
    "a file's hash": (0, (("model.py", "a2"),), "w1", "o1"),
    "the file set": (0, (("model.py", "a1"), ("pkg/__init__.py", "b1")), "w1", "o1"),
    "the exit status": (1, (("model.py", "a1"),), "w1", "o1"),
    "the warnings": (0, (("model.py", "a1"),), "w2", "o1"),
    "the other facts": (0, (("model.py", "a1"),), "w1", "o2"),
    "everything": (2, (), "w2", "o2"),
}


@pytest.mark.parametrize("case", sorted(KINDS_CASES))
def test_kinds(case: str) -> None:
    """A cell's difference has the kinds of the facts that differ: output, exit, warnings and messages."""
    assert_output(json.dumps(sorted(kinds(FACTS_BASE, KINDS_CASES[case]))) + "\n")


@pytest.mark.parametrize(
    "facts",
    ['[0, [["model.py", "a1"]], "w1", "o1", "outside"]', '[0, [["model.py", "a1"]], "w1"]'],
    ids=["a fifth element", "a missing element"],
)
def test_kinds_refuses_facts_of_another_length(facts: str) -> None:
    """Facts of another length are refused: an element no kind reads would never show as a difference."""
    base = json.loads(facts)
    with pytest.raises(ValueError, match="4 elements") as raised:
        kinds(base, base)
    assert_output(f"{raised.value}\n")


TYPE_CHANGE = Change("__init__", "Model", "type", "int -> float", "a")
WARNING = ["DeprecationWarning", "mind the gap", "datamodel_code_generator.parser"]
OTHER_WARNING = ["UserWarning", "other", "datamodel_code_generator"]
Warnings = tuple[list[list[str]], list[list[str]]]
CHANGE_LINE_CASES: dict[str, tuple[list[Change], tuple[int, int], Warnings, tuple[str, str]]] = {
    "nothing differs": ([], (0, 0), ([], []), ("", "")),
    "behaviour changes, a new name left out": (
        [TYPE_CHANGE, Change("__init__", "Other", "name-added", "added")],
        (0, 0),
        ([], []),
        ("", ""),
    ),
    "the exit status": ([], (1, 0), ([], []), ("", "")),
    "a warning added and one removed": ([], (0, 0), ([WARNING, OTHER_WARNING], [WARNING, WARNING]), ("", "")),
    "a warning removed": ([], (0, 0), ([WARNING], []), ("", "")),
    "the warnings in another order": ([], (0, 0), ([WARNING, OTHER_WARNING], [OTHER_WARNING, WARNING]), ("", "")),
    "a message line added": ([], (0, 0), ([], []), ("Error: one\n", "Error: one\nError: two\nhint\n")),
    "a message line removed": ([], (0, 0), ([], []), ("Error: one\nError: two\n", "Error: one\n")),
    "a message line printed once more": ([], (0, 0), ([], []), ("Error: one\n", "Error: one\nError: one\n")),
    "the message lines in another order": ([], (0, 0), ([], []), ("a\nb\n", "b\na\n")),
}


def test_change_refuses_an_aspect_the_records_cannot_name() -> None:
    """A change's aspect is one a record's selector can name, so a new aspect is added to the records' list first."""
    with pytest.raises(ValueError, match="ASPECTS") as raised:
        Change("__init__", "Model", "colour", "red -> blue")
    assert_output(f"{raised.value}\n")


@pytest.mark.parametrize("case", sorted(CHANGE_LINE_CASES))
def test_change_lines(case: str) -> None:
    """A cell's change lines: its behaviour changes, then its exit, warning and message lines."""
    changes, exits, warnings, messages = CHANGE_LINE_CASES[case]
    lines = change_lines(changes, exits=exits, warnings=warnings, messages=messages)
    assert_output(json.dumps(lines, indent=1) + "\n")


def _selector(glob: str, backends: list[str] | None = None, options: list[list[str]] | None = None) -> CellSelector:
    """Return a selector as a `[[change.cells]]` table gives it; no `backends` or `options` is `"*"`."""
    listed = None if options is None else tuple(map(tuple, options))
    return CellSelector((glob,), None if backends is None else tuple(backends), listed)


def _lines(glob: str, aspects: tuple[str, ...] | None = None, match: str | None = None) -> CellSelector:
    """Return a selector of every backend and option set whose cells' change lines hold its aspects and match."""
    return CellSelector((glob,), aspects=aspects, match=match)


def _cell(
    label: str = "tests/data/jsonschema/plain.json",
    backend: str = "pydantic_v2.BaseModel",
    options: tuple[str, ...] = (),
    file_type: str = "jsonschema",
) -> behaviour_diff.Cell:
    return behaviour_diff.Cell(ROOT / label, label, file_type, backend, options)


COVERS_CASES: dict[str, tuple[CellSelector, behaviour_diff.Cell]] = {
    "the input file": (_selector("tests/data/jsonschema/plain.json"), _cell()),
    "another input file": (_selector("tests/data/jsonschema/other.json"), _cell()),
    "a folder's file by *": (_selector("tests/data/jsonschema/*"), _cell()),
    "not a file below a folder by *": (_selector("tests/data/*"), _cell()),
    "a file below a folder by **": (_selector("tests/data/**"), _cell()),
    "the folder itself by **": (_selector("tests/data/jsonschema/**"), _cell("tests/data/jsonschema")),
    "a part by a pattern": (_selector("tests/data/*/pl?in.[jy]son"), _cell()),
    "a listed backend": (_selector("tests/data/**", backends=["msgspec.Struct"]), _cell(backend="msgspec.Struct")),
    "an unlisted backend": (_selector("tests/data/**", backends=["msgspec.Struct"]), _cell()),
    "the default option set": (_selector("tests/data/**", options=[[], ["--use-annotated"]]), _cell()),
    "a listed option set": (
        _selector("tests/data/**", options=[["--use-annotated", "--keyword-only"]]),
        _cell(options=("--use-annotated", "--keyword-only")),
    ),
    "an option set in another order": (
        _selector("tests/data/**", options=[["--keyword-only", "--use-annotated"]]),
        _cell(options=("--use-annotated", "--keyword-only")),
    ),
    "every option set": (_selector("tests/data/**"), _cell(options=("--preset", "standard-py314-20260101"))),
    "the input file type": (replace(_selector("tests/data/**"), input_file_type="jsonschema"), _cell()),
    "another input file type": (replace(_selector("tests/data/**"), input_file_type="openapi"), _cell()),
    "no input file type, a cell of another type than its folder": (
        _selector("tests/data/**"),
        _cell("tests/data/jsonschema/plain.json", file_type="openapi"),
    ),
    "the input file type named, a cell of another type than its folder": (
        replace(_selector("tests/data/**"), input_file_type="openapi"),
        _cell("tests/data/jsonschema/plain.json", file_type="openapi"),
    ),
    "no input file type, a cell outside tests/data": (_selector("docs/**"), _cell("docs/plain.json")),
}


@pytest.mark.parametrize("case", sorted(COVERS_CASES))
def test_covers(case: str) -> None:
    """A selector covers a run cell whose input its globs name over the tree, with a listed backend and option set."""
    assert_output(json.dumps(covers(*COVERS_CASES[case])) + "\n")


MODEL_TEXT = '''"""Models."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", title="Model")
    a: int  # the count
    b: list[int] = Field(default_factory=lambda: [1])

    @field_validator("a")
    @classmethod
    def check(cls, value: int) -> int:
        return value
'''
ROOT_UNION_TEXT = """from __future__ import annotations

from typing import Optional, Union

from pydantic import BaseModel, RootModel


class Model(RootModel[Union[int, float, str]]):
    root: Union[int, float, str]


class Holder(BaseModel):
    a: Union[int, str]
    b: Optional[int] = None
    c: list[int | str | None] = []
"""
SWAP_MODEL = """

@dataclass
class Model:
    a: list[int] = field(default_factory=lambda: [1])
    b: list[int] = field(default_factory=lambda: [2])
    c: Union[int, str] = 0
    d: Union[str, int] = 0

    @property
    def e(self) -> int:
        return 1

    @e.setter
    def e(self, value: int) -> None:
        pass
"""
SWAP_OTHER = """

@dataclass
class Other:
    f: int = 0
"""
SWAP_IMPORTS = "from dataclasses import dataclass, field\nfrom typing import Union\n"
SWAP_TEXT = SWAP_IMPORTS + SWAP_MODEL + SWAP_OTHER
ALIAS_X = "X = Union[int, str]\n"
ALIAS_Y = "Y = Union[\n    str,\n    int,\n]\n"
ALIAS_TEXT = "from typing import Union\n" + ALIAS_X + ALIAS_Y
RULE_B_OLD = {
    "the members of a root model's union in another order": ROOT_UNION_TEXT,
    "the members of a field's union in another order": ROOT_UNION_TEXT,
    "a nested union flattened": ROOT_UNION_TEXT,
    "a union spelled otherwise with the same members": ROOT_UNION_TEXT,
    "an import moved past a statement it shadows": (
        "from pydantic import BaseModel\nField = 1\nfrom pydantic import Field\n"
    ),
    "two fields' default factories traded": SWAP_TEXT,
    "two fields' union orders traded": SWAP_TEXT,
    "two classes in another order": SWAP_TEXT,
    "two aliases' union orders traded": ALIAS_TEXT,
    "two aliases in another order": ALIAS_TEXT,
    "a getter's and a setter's bodies traded": SWAP_TEXT,
}
RULE_B_CASES: dict[str, tuple[str, bool]] = {
    "the members of a root model's union in another order": (
        ROOT_UNION_TEXT.replace("Union[int, float, str]", "Union[str, float, int]"),
        True,
    ),
    "the members of a field's union in another order": (
        ROOT_UNION_TEXT.replace("a: Union[int, str]", "a: Union[str, int]"),
        True,
    ),
    "a nested union flattened": (ROOT_UNION_TEXT.replace("a: Union[int, str]", "a: Union[int, Union[str]]"), True),
    "a union spelled otherwise with the same members": (
        ROOT_UNION_TEXT.replace("b: Optional[int]", "b: int | None").replace(
            "c: list[int | str | None]", "c: list[Union[int, str, None]]"
        ),
        True,
    ),
    "an import moved past a statement it shadows": (
        "from pydantic import BaseModel\nfrom pydantic import Field\nField = 1\n",
        False,
    ),
    "two fields' default factories traded": (
        SWAP_TEXT.replace("lambda: [1]", "lambda: [0]").replace("lambda: [2]", "lambda: [1]").replace("[0]", "[2]"),
        True,
    ),
    "two fields' union orders traded": (
        SWAP_TEXT
        .replace("Union[int, str]", "Union[0]")
        .replace("Union[str, int]", "Union[int, str]")
        .replace("Union[0]", "Union[str, int]"),
        True,
    ),
    "two classes in another order": (SWAP_IMPORTS + SWAP_OTHER + SWAP_MODEL, True),
    "two aliases' union orders traded": (
        ALIAS_TEXT.replace("Union[int, str]", "Union[str, int]").replace("str,\n    int,", "int,\n    str,"),
        True,
    ),
    "two aliases in another order": ("from typing import Union\n" + ALIAS_Y + ALIAS_X, True),
    "a getter's and a setter's bodies traded": (
        SWAP_TEXT
        .replace("        return 1\n", "        pass0\n")
        .replace("        pass\n", "        return 1\n")
        .replace("pass0", "pass"),
        True,
    ),
    "same text": (MODEL_TEXT, False),
    "docstrings and comments": (
        MODEL_TEXT
        .replace('"""Models."""', '"""Other models."""')
        .replace("  # the count", "")
        .replace("class Model(BaseModel):\n", 'class Model(BaseModel):\n    """A model."""\n\n'),
        False,
    ),
    "the order of the imports": (
        MODEL_TEXT.replace(
            "from typing import Annotated\n\nfrom pydantic import BaseModel, ConfigDict, Field, field_validator",
            "from pydantic import field_validator, Field, ConfigDict, BaseModel\nfrom typing import Annotated",
        ),
        False,
    ),
    "a field type, the signatures differ": (MODEL_TEXT.replace("a: int", "a: float"), False),
    "a field default spelled otherwise, the signatures equal": (
        MODEL_TEXT.replace(
            "b: list[int] = Field(default_factory=lambda: [1])",
            "b: list[int] = Field(default_factory=lambda: [1], description='d')",
        ),
        True,
    ),
    "a default factory's body, the signatures equal": (MODEL_TEXT.replace("lambda: [1]", "lambda: [2]"), True),
    "a validator's body, the signatures equal": (MODEL_TEXT.replace("return value", "return value + 1"), True),
    "a decorator, the signatures equal": (
        MODEL_TEXT.replace('@field_validator("a")', '@field_validator("a", mode="before")'),
        True,
    ),
    "a configuration key the signature does not list": (MODEL_TEXT.replace('title="Model"', 'title="Other"'), True),
    "a configuration key the signature lists": (MODEL_TEXT.replace('extra="forbid"', 'extra="allow"'), True),
    "a class keyword the signature does not list": (
        MODEL_TEXT.replace("class Model(BaseModel):", "class Model(BaseModel, defer_build=True):"),
        True,
    ),
    "a Config class key": (MODEL_TEXT + "\n    class Config:\n        title = 'x'\n", True),
    "a configuration dictionary that unpacks another": (
        MODEL_TEXT.replace('ConfigDict(extra="forbid", title="Model")', '{**BASE, "title": "Model"}'),
        True,
    ),
    "a configuration of a name": (MODEL_TEXT.replace('ConfigDict(extra="forbid", title="Model")', "BASE"), True),
    "a configuration dictionary": (
        MODEL_TEXT.replace('ConfigDict(extra="forbid", title="Model")', '{"extra": "forbid", "title": "Other"}'),
        True,
    ),
    "text that does not parse": (MODEL_TEXT + "\nclass (:\n", True),
}


@pytest.mark.parametrize("case", sorted(RULE_B_CASES))
def test_rule_b(case: str) -> None:
    """Rule (b) holds on two texts of a model module, or does not.

    It holds on equal trees apart from docstrings, comments and import order, or on equal signatures with equal bodies,
    lambdas, decorators and configuration keys the signature does not list.
    """
    new, signature_equal = RULE_B_CASES[case]
    assert_output(json.dumps(rule_b(RULE_B_OLD.get(case, MODEL_TEXT), new, signature_equal=signature_equal)) + "\n")


EVERY_CELL = _selector("tests/data/**")


def _judged(
    category: str | None, *cells: CellSelector, affects: tuple[tuple[str, tuple[str, ...]], ...] = ()
) -> Record:
    """Return a record of a category whose selectors are `cells` (by default every cell of the fixtures)."""
    category_value = Category(category) if category else None
    return Record(str(category), frozenset(), category=category_value, cells=cells or (EVERY_CELL,), affects=affects)


NO_BEHAVIOUR = CellEvidence("no-behaviour", model_files=True, rule_b=True, payloads=True, companion_equal=True)
TYPE_LINE = "[type] Model:a: int -> float"
DECIDED = _judged("decided-change", _lines("tests/data/**", aspects=("type",), match="int -> float"))
OUTPUT, EXIT, WARNINGS, MESSAGES = CellKind.OUTPUT, CellKind.EXIT, CellKind.WARNINGS, CellKind.MESSAGES
GAP_NOTICE = _judged("notice", _lines("tests/data/**", match="gap"))
TRACK_NOTICE = replace(GAP_NOTICE, id="other-notice", cells=(_lines("tests/data/**", match="track"),))
GAP_WARNING, TRACK_WARNING = "[warning] added UserWarning: mind the gap", "[warning] added UserWarning: off the track"
TWO_CHANGES = (GAP_WARNING, TRACK_WARNING, "[message] removed first attempt: off the track", "[message] added gap")
"""The warning and message lines of two changes of one release on one cell, each declared by its own notice."""
CRASH_FIXED = CellEvidence(
    "crash-fix", lines=("[exit] 2 -> 0", GAP_WARNING, TRACK_WARNING, "[message] removed handled: Error"), exits=(2, 0)
)
REMOVED_FLAG = CellEvidence("violation", lines=("[exit] 0 -> 2",), exits=(0, 2), removed=frozenset({"--old-flag"}))
OLD_FLAG_API = _judged("api", affects=(("options", ("--old-flag",)),))
JUDGE_CASES: dict[str, tuple[list[Record], set[CellKind], CellEvidence, behaviour_diff.Cell]] = {
    "no record": ([], {OUTPUT}, NO_BEHAVIOUR, _cell()),
    "no selector covers the cell": (
        [_judged("output-text", _selector("tests/data/**", backends=["msgspec.Struct"]))],
        {OUTPUT},
        NO_BEHAVIOUR,
        _cell(),
    ),
    "output-text holds": ([_judged("output-text")], {OUTPUT}, NO_BEHAVIOUR, _cell()),
    "output-text on a warning too": ([_judged("output-text")], {OUTPUT, WARNINGS}, NO_BEHAVIOUR, _cell()),
    "output-text on a behaviour change": (
        [_judged("output-text")],
        {OUTPUT},
        replace(NO_BEHAVIOUR, cls="violation", lines=(TYPE_LINE,)),
        _cell(),
    ),
    "output-text on a non-model file": (
        [_judged("output-text")],
        {OUTPUT},
        replace(NO_BEHAVIOUR, model_files=False),
        _cell(),
    ),
    "output-text without a signature": (
        [_judged("output-text")],
        {OUTPUT},
        replace(NO_BEHAVIOUR, signature_failed=True),
        _cell(),
    ),
    "output-text against rule (b)": ([_judged("output-text")], {OUTPUT}, replace(NO_BEHAVIOUR, rule_b=False), _cell()),
    "output-text with differing payload cells": (
        [_judged("output-text")],
        {OUTPUT},
        replace(NO_BEHAVIOUR, payloads=False),
        _cell(),
    ),
    "crash-fix holds": (
        [_judged("crash-fix")],
        {OUTPUT, EXIT, MESSAGES},
        CellEvidence("crash-fix", lines=("[exit] 1 -> 0",), exits=(1, 0)),
        _cell(),
    ),
    "crash-fix on a cell that now fails": (
        [_judged("crash-fix")],
        {OUTPUT, EXIT},
        CellEvidence("crash-fix", exits=(0, 1)),
        _cell(),
    ),
    "crash-fix on a cell that was no crash": ([_judged("crash-fix")], {OUTPUT}, NO_BEHAVIOUR, _cell()),
    "decided-change inside its aspects and match": (
        [DECIDED],
        {OUTPUT},
        CellEvidence("violation", lines=(TYPE_LINE,)),
        _cell(),
    ),
    "decided-change with a line outside its match": (
        [DECIDED],
        {OUTPUT},
        CellEvidence("violation", lines=(TYPE_LINE, "[default] Model:b: None -> '1'")),
        _cell(),
    ),
    "decided-change with a line outside its aspects": (
        [_judged("default-fix", _lines("tests/data/**", aspects=("default",)))],
        {OUTPUT},
        CellEvidence("violation", lines=(TYPE_LINE,)),
        _cell(),
    ),
    "decided-change with lines its two selectors match": (
        [
            _judged(
                "decided-change",
                _lines("tests/data/**", match="int -> float"),
                _lines("tests/data/*/plain.json", aspects=("default",)),
            )
        ],
        {OUTPUT},
        CellEvidence("violation", lines=(TYPE_LINE, "[default] Model:b: None -> '1'")),
        _cell(),
    ),
    "decided-change on a no-behaviour cell": ([DECIDED], {OUTPUT}, NO_BEHAVIOUR, _cell()),
    "a decision beside a failing output-text record": (
        [_judged("output-text"), DECIDED],
        {OUTPUT},
        CellEvidence("violation", lines=(TYPE_LINE,)),
        _cell(),
    ),
    "a failing output-text record beside a notice that admits nothing of the cell": (
        [_judged("output-text"), _judged("notice", _lines("tests/data/**", match="float"))],
        {OUTPUT},
        CellEvidence("violation", lines=(TYPE_LINE,), model_files=True, rule_b=True, payloads=True),
        _cell(),
    ),
    "output-text and notice on a text change with a warning": (
        [_judged("output-text"), _judged("notice", _lines("tests/data/**", match="gap"))],
        {OUTPUT, WARNINGS},
        replace(NO_BEHAVIOUR, lines=("[warning] added DeprecationWarning: mind the gap",)),
        _cell(),
    ),
    "output-text and notice on a text change with a message": (
        [_judged("output-text"), _judged("notice", _lines("tests/data/**", match="gap"))],
        {OUTPUT, MESSAGES},
        replace(NO_BEHAVIOUR, lines=("[message] added mind the gap",)),
        _cell(),
    ),
    "output-text and notice on a text change with a warning and an exit difference": (
        [_judged("output-text"), _judged("notice", _lines("tests/data/**", match="gap"))],
        {OUTPUT, WARNINGS, EXIT},
        replace(
            NO_BEHAVIOUR, lines=("[exit] 0 -> 1", "[warning] added DeprecationWarning: mind the gap"), exits=(0, 1)
        ),
        _cell(),
    ),
    "output-text and notice on a text change with a warning outside the notice's match": (
        [_judged("output-text"), _judged("notice", _lines("tests/data/**", match="gap"))],
        {OUTPUT, WARNINGS},
        replace(NO_BEHAVIOUR, lines=("[warning] added UserWarning: other",)),
        _cell(),
    ),
    "output-text and notice on a behaviour change with a warning": (
        [_judged("output-text"), _judged("notice", _lines("tests/data/**", match="gap"))],
        {OUTPUT, WARNINGS},
        replace(NO_BEHAVIOUR, cls="violation", lines=(TYPE_LINE, "[warning] added DeprecationWarning: mind the gap")),
        _cell(),
    ),
    "two decisions, each line selected by one": (
        [DECIDED, _judged("default-fix", _lines("tests/data/**", aspects=("default",)))],
        {OUTPUT},
        CellEvidence("violation", lines=(TYPE_LINE, "[default] Model:b: None -> '1'")),
        _cell(),
    ),
    "two decisions of one category, each line selected by one": (
        [DECIDED, replace(DECIDED, id="other-decided-change", cells=(_lines("tests/data/**", aspects=("default",)),))],
        {OUTPUT},
        CellEvidence("violation", lines=(TYPE_LINE, "[default] Model:b: None -> '1'")),
        _cell(),
    ),
    "two decisions and a line neither selects": (
        [DECIDED, _judged("default-fix", _lines("tests/data/**", aspects=("default",)))],
        {OUTPUT},
        CellEvidence("violation", lines=(TYPE_LINE, "[default] Model:b: None -> '1'", "[enum] Model:c: 'x' -> 'y'")),
        _cell(),
    ),
    "a decision and a notice, a warning line only the notice selects": (
        [DECIDED, _judged("notice", _lines("tests/data/**", match="gap"))],
        {OUTPUT, WARNINGS},
        CellEvidence("violation", lines=(TYPE_LINE, "[warning] added DeprecationWarning: mind the gap")),
        _cell(),
    ),
    "two decisions, a line selected only by a selector of another cell": (
        [DECIDED, _judged("default-fix", _lines("tests/data/*/other.json", aspects=("default",)))],
        {OUTPUT},
        CellEvidence("violation", lines=(TYPE_LINE, "[default] Model:b: None -> '1'")),
        _cell(),
    ),
    "experimental by its option, an equal companion": (
        [_judged("experimental", affects=(("experimental", ("cli-option.generate-client",)),))],
        {OUTPUT},
        CellEvidence("violation", lines=(TYPE_LINE,), companion_equal=True),
        _cell(options=("--generate-client", "--use-annotated")),
    ),
    "experimental by its input type": (
        [_judged("experimental", affects=(("experimental", ("input-format.avro",)),))],
        {OUTPUT, MESSAGES},
        CellEvidence("violation", lines=(TYPE_LINE,), companion_equal=True),
        _cell("tests/data/avro/plain.avsc", file_type="avro"),
    ),
    "experimental whose companion differs": (
        [_judged("experimental", affects=(("experimental", ("cli-option.generate-client",)),))],
        {OUTPUT},
        CellEvidence("violation", lines=(TYPE_LINE,)),
        _cell(options=("--generate-client", "--use-annotated")),
    ),
    "experimental without its feature in the cell": (
        [_judged("experimental", affects=(("experimental", ("cli-option.generate-client",)),))],
        {OUTPUT},
        CellEvidence("violation", lines=(TYPE_LINE,), companion_equal=True),
        _cell(options=("--use-annotated",)),
    ),
    "notice inside its match": (
        [_judged("notice", _lines("tests/data/**", match="mind the gap"))],
        {WARNINGS, MESSAGES},
        CellEvidence("same", lines=("[warning] added DeprecationWarning: mind the gap", "[message] mind the gap")),
        _cell(),
    ),
    "notice outside its match": (
        [_judged("notice", _lines("tests/data/**", match="mind the gap"))],
        {WARNINGS},
        CellEvidence("same", lines=("[warning] added UserWarning: other",)),
        _cell(),
    ),
    "notice on an exit difference too": (
        [_judged("notice", _lines("tests/data/**", match="gap")), _judged("api")],
        {EXIT, MESSAGES},
        CellEvidence("violation", lines=("[exit] 0 -> 2", "[message] gap"), exits=(0, 2)),
        _cell(),
    ),
    "api for a removed option it names": (
        [_judged("api", affects=(("options", ("--old-flag",)),))],
        {EXIT, MESSAGES},
        CellEvidence("violation", exits=(0, 2), removed=frozenset({"--old-flag"})),
        _cell(options=("--old-flag",)),
    ),
    "api for a removed option it does not name": (
        [_judged("api", affects=(("options", ("--other-flag",)),))],
        {EXIT, MESSAGES},
        CellEvidence("violation", exits=(0, 2), removed=frozenset({"--old-flag"})),
        _cell(options=("--old-flag",)),
    ),
    "no differing kind": ([_judged("output-text")], set(), NO_BEHAVIOUR, _cell()),
    "a notice for messages that show in no line": (
        [_judged("notice", _lines("tests/data/**", match="gap"))],
        {MESSAGES},
        CellEvidence("same"),
        _cell(),
    ),
    "a notice for warnings that show in no line": (
        [_judged("notice", _lines("tests/data/**", match="gap"))],
        {WARNINGS},
        CellEvidence("same"),
        _cell(),
    ),
    "a notice for a message line it matches": (
        [_judged("notice", _lines("tests/data/**", match="gap"))],
        {MESSAGES},
        CellEvidence("same", lines=("[message] mind the gap",)),
        _cell(),
    ),
    "a notice matching only the last of the message lines added": (
        [_judged("notice", _lines("tests/data/**", match="gap"))],
        {MESSAGES},
        CellEvidence(
            "same", lines=change_lines([], messages=("Error: one\n", "Error: one\nError: two\nmind the gap\n"))
        ),
        _cell(),
    ),
    "a notice when a signature could not be taken": (
        [_judged("notice", _lines("tests/data/**", match="gap"))],
        {WARNINGS},
        CellEvidence("same", lines=("[warning] added UserWarning: gap",), signature_failed=True),
        _cell(),
    ),
    "a decision for messages that show in no line": (
        [DECIDED],
        {OUTPUT, MESSAGES},
        CellEvidence("violation", lines=(TYPE_LINE,)),
        _cell(),
    ),
    "a decision for an exit status that shows in no line": (
        [_judged("default-fix", _lines("tests/data/**", aspects=("type", "exit")))],
        {OUTPUT, EXIT},
        CellEvidence("violation", lines=(TYPE_LINE,), exits=(0, 1)),
        _cell(),
    ),
    "a decision for the exit line it names": (
        [_judged("default-fix", _lines("tests/data/**", aspects=("type", "exit")))],
        {OUTPUT, EXIT},
        CellEvidence("violation", lines=(TYPE_LINE, "[exit] 0 -> 1"), exits=(0, 1)),
        _cell(),
    ),
    "a decision when both signatures failed": (
        [_judged("default-fix", _lines("tests/data/**", match="int -> float"))],
        {OUTPUT},
        CellEvidence("both-fail", signature_failed=True),
        _cell(),
    ),
    "a record without a category": ([_judged(None)], {OUTPUT}, NO_BEHAVIOUR, _cell()),
    "a harness record": ([_judged("harness")], {OUTPUT}, NO_BEHAVIOUR, _cell()),
    "two notices, each line matched by one": (
        [GAP_NOTICE, TRACK_NOTICE],
        {WARNINGS, MESSAGES},
        CellEvidence("same", lines=TWO_CHANGES),
        _cell(),
    ),
    "two notices and a warning line neither matches": (
        [GAP_NOTICE, TRACK_NOTICE],
        {WARNINGS, MESSAGES},
        CellEvidence("same", lines=(*TWO_CHANGES, "[warning] added UserWarning: other")),
        _cell(),
    ),
    "one notice and the lines of another change it does not declare": (
        [GAP_NOTICE],
        {WARNINGS, MESSAGES},
        CellEvidence("same", lines=TWO_CHANGES),
        _cell(),
    ),
    "a crash-fix on a cell that was no crash, two notices matching every line": (
        [_judged("crash-fix"), GAP_NOTICE, TRACK_NOTICE],
        {WARNINGS, MESSAGES},
        CellEvidence("same", lines=TWO_CHANGES),
        _cell(),
    ),
    "a crash-fix on a cell that was no crash, two notices and a message line neither matches": (
        [_judged("crash-fix"), GAP_NOTICE, TRACK_NOTICE],
        {WARNINGS, MESSAGES},
        CellEvidence("same", lines=(*TWO_CHANGES, "[message] added other")),
        _cell(),
    ),
    "a crash-fix on a cell that was no crash beside a notice, a warning line it does not match": (
        [_judged("crash-fix"), GAP_NOTICE],
        {WARNINGS, MESSAGES},
        CellEvidence("same", lines=("[warning] added UserWarning: other", "[message] added gap")),
        _cell(),
    ),
    "a crash-fix and two notices, each warning matched by one": (
        [_judged("crash-fix"), GAP_NOTICE, TRACK_NOTICE],
        {OUTPUT, EXIT, WARNINGS, MESSAGES},
        CRASH_FIXED,
        _cell(),
    ),
    "a crash-fix and a notice that matches the warnings only": (
        [_judged("crash-fix"), _judged("notice", _lines("tests/data/**", match="UserWarning"))],
        {OUTPUT, EXIT, WARNINGS, MESSAGES},
        CRASH_FIXED,
        _cell(),
    ),
    "a crash-fix and two notices when a signature could not be taken": (
        [_judged("crash-fix"), GAP_NOTICE, TRACK_NOTICE],
        {OUTPUT, EXIT, WARNINGS, MESSAGES},
        replace(CRASH_FIXED, signature_failed=True),
        _cell(),
    ),
    "api beside a crash-fix on a cell that now fails": (
        [OLD_FLAG_API, _judged("crash-fix")],
        {EXIT, MESSAGES},
        REMOVED_FLAG,
        _cell(options=("--old-flag",)),
    ),
    "api beside a decision that selects none of the lines": (
        [OLD_FLAG_API, DECIDED],
        {EXIT, MESSAGES},
        REMOVED_FLAG,
        _cell(options=("--old-flag",)),
    ),
    "output-text beside a crash-fix on a cell that was no crash": (
        [_judged("output-text"), _judged("crash-fix")],
        {OUTPUT},
        NO_BEHAVIOUR,
        _cell(),
    ),
    "output-text and a notice beside an experimental record whose companion differs": (
        [
            _judged("experimental", affects=(("experimental", ("cli-option.generate-client",)),)),
            _judged("output-text"),
            GAP_NOTICE,
        ],
        {OUTPUT, WARNINGS},
        replace(NO_BEHAVIOUR, companion_equal=False, lines=(GAP_WARNING,)),
        _cell(options=("--generate-client",)),
    ),
    "output-text and two notices, one matching no line": (
        [_judged("output-text"), GAP_NOTICE, TRACK_NOTICE],
        {OUTPUT, WARNINGS},
        replace(NO_BEHAVIOUR, lines=(GAP_WARNING,)),
        _cell(),
    ),
    "a decision that selects no line beside a notice, on output that shows in no line": (
        [DECIDED, GAP_NOTICE],
        {OUTPUT, WARNINGS},
        CellEvidence("violation", lines=(GAP_WARNING,)),
        _cell(),
    ),
    "a notice and a signature line while the output is equal": (
        [GAP_NOTICE],
        {WARNINGS},
        CellEvidence("same", lines=(TYPE_LINE, GAP_WARNING)),
        _cell(),
    ),
    "output-text and a decision on a no-behaviour cell": (
        [_judged("output-text"), DECIDED],
        {OUTPUT},
        NO_BEHAVIOUR,
        _cell(),
    ),
    "a decision that selects the exit line beside a notice, on output that shows in no line": (
        [_judged("default-fix", _lines("tests/data/**", aspects=("exit",))), GAP_NOTICE],
        {OUTPUT, EXIT, WARNINGS},
        CellEvidence("violation", lines=("[exit] 0 -> 1", GAP_WARNING), exits=(0, 1)),
        _cell(),
    ),
}


@pytest.mark.parametrize("case", sorted(JUDGE_CASES))
def test_judge(case: str) -> None:
    """A differing cell passes when the records that cover it admit each kind of its difference and explain it together.

    Decisions and notices explain the lines their selectors select, the other categories the kinds they admit, where
    their evidence holds; a record whose evidence fails is no failure where the others explain all it admits.
    """
    records, found, evidence, cell = JUDGE_CASES[case]
    verdict = judge(cell, frozenset(found), records, evidence)
    output = {**asdict(verdict), "accepted_by": [records[index].id for index in sorted(verdict.accepted_by)]}
    assert_output(json.dumps(output, indent=1) + "\n")
