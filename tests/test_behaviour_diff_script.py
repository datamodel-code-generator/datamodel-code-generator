"""Tests for the release behaviour diff: signatures, the classifier and the gate driver.

The signature cases and the classifier cases are the shapes the 0.86.0 audit met: alias and NotRequired spellings
that are not behaviour changes, msgspec decoders and pydantic adapters 0.86.0 could not build, the propertyNames key
change the owner accepted, and the behaviour changes the gate has to stop.
"""

from __future__ import annotations

import json
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import behaviour_diff
from scripts._behaviour_classify import Allowlist, Side, classify_cell, diff
from tests.conftest import assert_output

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
}


@pytest.mark.parametrize("case", sorted(ALLOWLIST_CASES))
def test_allowlist_problems(case: str) -> None:
    """An entry needs public decision references, and a table with entries belongs to one base release."""
    base, entries = ALLOWLIST_CASES[case]
    assert_output("\n".join(Allowlist(base, entries).problems("0.86.0")) + "\n")


REPORT_ENTRY = {**PROPERTY_NAMES_ENTRY, "input": "tests/data/*.json", "decision": "#5017"}
REPORT_CASES = {
    "pr_covered_unused": ("pr", [REPORT_ENTRY], "tests/data/a.json", "1/1"),
    "pr_covers_no_cell": ("pr", [REPORT_ENTRY], "tests/data/a.yaml", "1/1"),
    "nightly_covered_unused": ("nightly", [REPORT_ENTRY], "tests/data/a.json", "1/1"),
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
    result = SimpleNamespace(cell=cell, verdict=SimpleNamespace(cls="same", accepted_by=set(), notes=[]))
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
    "import_error": ("pydantic_v2.BaseModel", {"model.py": "from missing_module import Thing\n"}, None),
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


@pytest.mark.skipif(sys.platform == "win32", reason="behaviour_diff.py forks its workers")
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
