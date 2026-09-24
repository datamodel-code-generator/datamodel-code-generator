"""Render runtime wire-rule results as text, keeping case inputs in external fixture files."""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Mapping
from decimal import Decimal
from enum import IntEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

from datamodel_code_generator._runtime.model_codecs.errors import CodecError, WireIssue, WireValidationError
from datamodel_code_generator._runtime.model_codecs.media import (
    FieldPlan,
    decode_json,
    decode_text,
    encode_json,
    media_kind,
    normalize_media_type,
)
from datamodel_code_generator._runtime.model_codecs.parameters import (
    EncodedParameterContribution,
    ParameterPlan,
    QueryStringContribution,
    RawParameters,
    decode_parameter,
    decode_parameters,
    encode_parameter,
    encode_parameters,
    raw_parameter,
)
from datamodel_code_generator._runtime.model_codecs.patterns import MatchBudget, compile_pattern, plan_pattern, search
from datamodel_code_generator._runtime.model_codecs.schema import SchemaBundle, SchemaResource
from datamodel_code_generator._runtime.model_codecs.unset import UNSET, Unset
from datamodel_code_generator._runtime.model_codecs.wire import freeze_wire, presence_of, thaw_wire

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import pytest


def failure(error: Exception) -> str:
    """Name a codec failure by its class and value-free issue codes."""
    if isinstance(error, WireValidationError):
        return f"{type(error).__name__} {','.join(item.code for item in error.issues)}"
    return f"{type(error).__name__} {error}"


def attempt(action: Callable[[], str]) -> str:
    """Run one case, rendering an expected codec, type, or value failure instead of raising it."""
    try:
        return action()
    except (CodecError, TypeError, ValueError) as error:
        return failure(error)


def _snapshot(wire: object) -> str:
    encoded = encode_json(wire)
    return (
        f"json={encoded.decode()} ascii={encode_json(wire, ascii_only=True).decode()} "
        f"presence={json.dumps(presence_of(wire).pointers())}"
    )


def json_media_report(path: Path) -> str:
    """Decode each fixture body and re-encode its wire snapshot."""
    lines = []
    for case in json.loads(path.read_text(encoding="utf-8")):
        data = bytes.fromhex(case["hex"]) if "hex" in case else case["text"].encode()
        lines.append(f"{case['name']}: {attempt(lambda data=data: _snapshot(decode_json(data)))}")
    lines.extend(
        f"{name}: {attempt(lambda depth=depth: _snapshot(decode_json(b'[' * depth + b']' * depth)))}"
        for name, depth in (("deep-nesting", 100_000), ("snapshot-nesting", 700))
    )
    return "\n".join(lines) + "\n"


class _Color(IntEnum):
    RED = 1


class _Text(str):
    __slots__ = ()


def _cyclic_list() -> list[object]:
    value: list[object] = []
    value.append(value)
    return value


def _cyclic_mapping() -> dict[str, object]:
    value: dict[str, object] = {}
    value["self"] = value
    return value


def _python_values() -> list[tuple[str, Callable[[], object]]]:
    shared = [1]
    return [
        ("ordered", lambda: OrderedDict([("b", (1, 2)), ("a", [Decimal("0.10"), 2.5, None])])),
        ("mapping-proxy", lambda: MappingProxyType({"x": {"y": [True, False]}})),
        ("shared-branches", lambda: {"left": shared, "right": shared}),
        ("large-decimal", lambda: Decimal("1E+999999")),
        ("negative-zero", lambda: [-0.0, Decimal("-0")]),
        ("nan-float", lambda: [float("nan")]),
        ("infinite-decimal", lambda: {"a": Decimal("Infinity")}),
        ("integer-key", lambda: {1: "a"}),
        ("str-subclass", lambda: [_Text("a")]),
        ("int-enum", lambda: [_Color.RED]),
        ("bytes", lambda: [b"a"]),
        ("set", lambda: {"a": {1}}),
        ("lone-surrogate-value", lambda: ["\ud800"]),
        ("lone-surrogate-key", lambda: {"\udfff": 1}),
        ("cyclic-list", _cyclic_list),
        ("cyclic-mapping", _cyclic_mapping),
        ("huge-integer", lambda: 10**5000),
    ]


def _copies(value: object) -> str:
    frozen = freeze_wire(value)
    thawed = thaw_wire(frozen)
    second = thaw_wire(frozen)
    if isinstance(thawed, (list, dict)):
        thawed.clear()
    return f"{_snapshot(frozen)} independent={thaw_wire(frozen) == second}"


def wire_value_report() -> str:
    """Freeze, thaw, and trace presence for in-memory JSON-domain values and rejected objects."""
    lines = [f"{name}: {attempt(lambda factory=factory: _copies(factory()))}" for name, factory in _python_values()]
    presence = presence_of({"a": [{"b": None}], "c": {}})
    lines.extend((
        f"child-object: {presence.child('a') is not None and presence.child('a').pointer}",
        f"child-index: {presence.child('a').child(0).pointer}",
        f"child-type-mismatch: {presence.child('a').child('0')}",
        f"child-missing: {presence.child('missing')}",
        f"presence-cycle: {attempt(lambda: str(presence_of(_cyclic_list())))}",
        f"presence-invalid: {attempt(lambda: str(presence_of([object()])))}",
    ))
    return "\n".join(lines) + "\n"


def _plan_text(source: str) -> str:
    plan_pattern(source)
    return "ok"


def _compiled(source: str) -> str:
    compile_pattern(plan_pattern(source).re2_source)
    return "ok"


def _charged(subjects: list[str]) -> str:
    budget = MatchBudget()
    plan = plan_pattern("a|é")
    return str(all(search(plan, subject, budget) for subject in subjects))


def pattern_report(path: Path) -> str:
    """Match the ECMA-262 corpus through RE2 and check every static and runtime resource limit."""
    data = json.loads(path.read_text(encoding="utf-8"))
    lines = []
    for case in data["matches"]:
        plan = plan_pattern(case["pattern"])
        lines.extend(
            f"{json.dumps(case['pattern'])} {json.dumps(subject)} -> {search(plan, subject, MatchBudget())}"
            for subject in case["subjects"]
        )
    lines.extend(f"{json.dumps(source)} -> {attempt(lambda source=source: _plan_text(source))}" for source in data["invalid"])
    mebibyte = 1 << 20
    limits = [
        ("source-4096", lambda: _plan_text("a" * 4096)),
        ("source-4097", lambda: _plan_text("a" * 4097)),
        ("source-utf8-4096", lambda: _plan_text("é" * 2048)),
        ("source-utf8-4098", lambda: _plan_text("é" * 2049)),
        ("depth-32", lambda: _plan_text("(" * 32 + ")" * 32)),
        ("depth-33", lambda: _plan_text("(" * 33 + ")" * 33)),
        ("repeat-1000", lambda: _compiled("a{1000}")),
        ("repeat-1001", lambda: _plan_text("a{1001}")),
        ("repeat-upper-1001", lambda: _plan_text("a{0,1001}")),
        ("program-size", lambda: _compiled("x{1000}y{1000}z{1000}w{1000}v{1000}")),
        ("re2-rejected", lambda: _compiled("(a{1000}){1000}")),
        ("subject-1MiB", lambda: _charged(["a" * mebibyte])),
        ("subject-1MiB+1", lambda: _charged(["a" * (mebibyte + 1)])),
        ("subject-utf8-1MiB", lambda: _charged(["é" * (mebibyte // 2)])),
        ("subject-utf8-1MiB+1", lambda: _charged(["é" * (mebibyte // 2) + "a"])),
        ("cumulative-32MiB", lambda: _charged(["a" * mebibyte] * 32)),
        ("cumulative-32MiB+1", lambda: _charged(["a" * mebibyte] * 32 + ["a"])),
    ]
    lines.extend(f"{name}: {attempt(action)}" for name, action in limits)
    compile_pattern.cache_clear()
    for index in range(130):
        compile_pattern(plan_pattern(f"a{{{index}}}").re2_source)
    info = compile_pattern.cache_info()
    lines.append(f"cache: {info.currsize}/{info.maxsize}")
    return "\n".join(lines) + "\n"


_LOGICAL = "https://dcg.invalid/inputs/"


def _issues(issues: tuple[WireIssue, ...]) -> str:
    return (
        " | ".join(
            f"{item.code} {item.instance_pointer or '/'} {item.schema_id.removeprefix(_LOGICAL)}#{item.schema_pointer} "
            f"{item.message}"
            for item in issues
        )
        or "valid"
    )


def _bundle(resources: list[tuple[str, object, tuple[str, ...]]]) -> SchemaBundle:
    return SchemaBundle(SchemaResource(uri=uri, contents=freeze_wire(contents), roots=roots) for uri, contents, roots in resources)


def schema_report(path: Path) -> str:
    """Validate fixture instances offline and render value-free issues in evaluation order."""
    fixture = decode_json(path.read_bytes())
    bundle = SchemaBundle(
        SchemaResource(uri=item["uri"], contents=item["contents"], roots=tuple(item["roots"]))
        for item in fixture["resources"]
    )
    lines = []
    for case in fixture["cases"]:
        validator = bundle.validator(case["schema"])
        lines.extend(
            f"{case['schema'].removeprefix(_LOGICAL)} [{index}] -> {_issues(validator.validate(instance))}"
            for index, instance in enumerate(case["instances"])
        )
    root = f"{_LOGICAL}root"
    other = f"{_LOGICAL}documents/0"
    failures = [
        ("duplicate-resource", lambda: _bundle([(root, {}, ("",)), (root, {}, ("",))])),
        ("scalar-root", lambda: _bundle([(root, "schema", ("",))])),
        ("unresolved-reference", lambda: _bundle([(root, {"$ref": f"{other}#/missing"}, ("",))])),
        ("missing-pointer", lambda: _bundle([(root, {"$ref": "#/$defs/missing"}, ("",))])),
        ("non-schema-target", lambda: _bundle([(root, {"a": {"$ref": "#/b"}, "b": {"type": "string"}}, ("/a",))])),
        ("dialect-pattern", lambda: _bundle([(root, {"pattern": "(?=a)"}, ("",))])),
        ("program-size-pattern", lambda: _bundle([(root, {"patternProperties": {"x{1000}y{1000}z{1000}w{1000}v{1000}": True}}, ("",))])),
        ("dynamic-reference", lambda: _bundle([(root, {"$dynamicAnchor": "a", "$dynamicRef": "#a"}, ("",))])),
        ("zero-multiple", lambda: _bundle([(root, {"multipleOf": 0}, ("",))])),
        ("boolean-multiple", lambda: _bundle([(root, {"multipleOf": True}, ("",))])),
        ("unknown-schema", lambda: bundle.validator(f"{other}#/$defs/Missing")),
        ("container-root", lambda: bundle.validator(f"{root}#")),
    ]
    lines.extend(f"{name}: {attempt(lambda action=action: str(type(action()).__name__))}" for name, action in failures)
    pet = bundle.validator(f"{root}#/components/schemas/Pet")
    lines.append(f"cached-validator: {pet is bundle.validator(f'{root}#/components/schemas/Pet')}")
    recursive = _bundle([(root, {"items": {"$ref": "#"}}, ("",))]).validator(f"{root}#")
    deep: object = []
    for _ in range(100_000):
        deep = [deep]
    lines.extend((
        f"deep-instance: {attempt(lambda: _issues(recursive.validate(deep)))}",
        f"pattern-budget: {attempt(lambda: _issues(pet.validate({'name': 'Rex'}, budget=MatchBudget(remaining=2))))}",
        f"pattern-subject: {attempt(lambda: _issues(pet.validate({'name': 'R' + 'a' * (1 << 20)})))}",
        f"bundled-boolean: {_issues(_bundle([(root, True, ('',))]).validator(f'{root}#').validate(None))}",
    ))
    return "\n".join(lines) + "\n"


def _field(data: Mapping[str, object] | None) -> FieldPlan | None:
    if data is None:
        return None
    return FieldPlan(str(data["name"]), data.get("kind", "string"), bool(data.get("repeated", False)))


def parameter_plan(data: Mapping[str, object]) -> ParameterPlan:
    """Build one runtime plan from a fixture record, keeping omitted fields at their defaults."""
    return ParameterPlan(
        location=data["location"],
        name=data["name"],
        style=data.get("style"),
        explode=bool(data.get("explode", False)),
        required=bool(data.get("required", False)),
        allow_reserved=bool(data.get("allow_reserved", False)),
        content_media_type=data.get("content_media_type"),
        shape=data.get("shape", "scalar"),
        kind=data.get("kind", "string"),
        fields=tuple(_field(item) for item in data.get("fields", ())),
        additional=_field(data.get("additional")),
        reserved_names=tuple(data.get("reserved_names", ())),
    )


def _label(plan: ParameterPlan) -> str:
    form = plan.content_media_type or f"{plan.style}{'*' if plan.explode else ''}"
    return f"{plan.location} {plan.name} {form} {plan.shape}/{plan.kind}"


def _http(contribution: EncodedParameterContribution) -> str:
    if isinstance(contribution, QueryStringContribution):
        return f"?{contribution.raw_query.decode()}"
    fragments = contribution.ordered_fragments
    match contribution.location:
        case "path":
            return f"/{fragments[0].value.decode()}"
        case "query":
            return "?" + "&".join(f"{item.name.decode()}={item.value.decode()}" for item in fragments)
        case "header":
            return " / ".join(f"{item.name.decode()}: {item.value.decode()}" for item in fragments)
        case _:
            return "Cookie: " + "; ".join(f"{item.name.decode()}={item.value.decode()}" for item in fragments)


def _raw_parameters(plan: ParameterPlan, contribution: EncodedParameterContribution) -> RawParameters:
    if isinstance(contribution, QueryStringContribution):
        return RawParameters(query=contribution.raw_query)
    fragments = contribution.ordered_fragments
    match contribution.location:
        case "path":
            return RawParameters(path={plan.name: fragments[0].value})
        case "query":
            return RawParameters(query=b"&".join(item.name + b"=" + item.value for item in fragments))
        case "header":
            return RawParameters(headers=tuple((item.name, item.value) for item in fragments))
        case _:
            return RawParameters(headers=((b"Cookie", b"; ".join(item.name + b"=" + item.value for item in fragments)),))


def _round_trip(plan: ParameterPlan, value: object) -> str:
    contribution = encode_parameter(plan, value)
    decoded = decode_parameters([plan], _raw_parameters(plan, contribution))[plan.location, plan.name]
    return f"{_http(contribution)} => {encode_json(decoded).decode()}"


def parameter_encoding_report(path: Path) -> str:
    """Encode official style examples and boundary values, then decode each contribution again."""
    fixture = decode_json(path.read_bytes())
    lines = [
        f"{_label(parameter_plan(case['plan']))}: {attempt(lambda case=case: _round_trip(parameter_plan(case['plan']), case['value']))}"
        for case in (*fixture["encode"], *fixture["failures"])
    ]
    lines.extend(f"invalid {dict(plan)}: {attempt(lambda plan=plan: str(parameter_plan(plan)))}" for plan in fixture["invalid_plans"])
    python_values = [
        ({"location": "query", "name": "i", "style": "form", "explode": True, "kind": "integer"}, 2.0),
        ({"location": "query", "name": "n", "style": "form", "explode": True, "kind": "number"}, 1.5),
        ({"location": "query", "name": "n", "style": "form", "explode": True, "kind": "number"}, 1e-07),
        ({"location": "query", "name": "i", "style": "form", "explode": True, "kind": "integer"}, 10**5000),
    ]
    lines.extend(
        f"python {_label(parameter_plan(data))}: {attempt(lambda data=data, value=value: _round_trip(parameter_plan(data), value))}"
        for data, value in python_values
    )
    required = parameter_plan({"location": "query", "name": "q", "style": "form", "explode": True, "required": True})
    optional = parameter_plan({"location": "header", "name": "X-Page", "style": "simple", "kind": "integer"})
    lines.extend((
        f"operation-present: {attempt(lambda: ' | '.join(map(_http, encode_parameters([required, optional], {('query', 'q'): 'a', ('header', 'X-Page'): UNSET}))))}",
        f"operation-missing: {attempt(lambda: str(encode_parameters([required, optional], {('header', 'X-Page'): 2})))}",
    ))
    return "\n".join(lines) + "\n"


def _bytes(value: object) -> bytes:
    return bytes.fromhex(value["hex"]) if isinstance(value, Mapping) else str(value).encode()


def _fixture_raw(data: Mapping[str, object]) -> RawParameters:
    return RawParameters(
        path={name: _bytes(value) for name, value in data.get("path", {}).items()}
        if isinstance(data.get("path"), Mapping) and "hex" not in data.get("path", {})
        else {"color": _bytes(data["path"])} if "path" in data else {},
        query=_bytes(data["query"]) if "query" in data else None,
        headers=tuple((_bytes(name), _bytes(value)) for name, value in data.get("headers", ())),
    )


def _decoded(value: object) -> str:
    return "UNSET" if value is UNSET else encode_json(value).decode()


def parameter_decoding_report(path: Path) -> str:
    """Decode raw fixture occurrences for every location, rendering UNSET, values, or issue codes."""
    fixture = decode_json(path.read_bytes())
    lines = []
    for case in fixture["cases"]:
        plan = parameter_plan(case["plan"])
        raw = _fixture_raw(case["raw"])
        lines.append(f"{_label(plan)} {encode_json(case['raw']).decode()}: {attempt(lambda plan=plan, raw=raw: _decoded(decode_parameter(plan, raw_parameter(plan, raw))))}")
    plans = [parameter_plan(item) for item in fixture["operation"]["plans"]]
    for data in fixture["operation"]["raw"]:
        raw = RawParameters(
            path={name: _bytes(value) for name, value in data["path"].items()},
            query=_bytes(data["query"]),
            headers=tuple((_bytes(name), _bytes(value)) for name, value in data["headers"]),
        )
        try:
            values = decode_parameters(plans, raw)
        except WireValidationError as error:
            lines.append("operation: " + " | ".join(f"{item.code} {item.message}" for item in error.issues))
        else:
            lines.append("operation: " + ", ".join(f"{location}.{name}={_decoded(value)}" for (location, name), value in values.items()))
    return "\n".join(lines) + "\n"


def media_report(path: Path) -> str:
    """Normalize media identities, classify builtin representations, and check shared error records."""
    fixture = json.loads(path.read_text(encoding="utf-8"))
    lines = [f"normalize {json.dumps(value)}: {attempt(lambda value=value: normalize_media_type(value))}" for value in fixture["normalize"]]
    lines.extend(f"kind {value}: {media_kind(normalize_media_type(value))}" for value in fixture["kinds"])
    lines.extend(f"text {item['hex']}: {attempt(lambda item=item: json.dumps(decode_text(bytes.fromhex(item['hex']))))}" for item in fixture["text"])
    issue = WireIssue(code="schema.type", message="Wrong type", instance_pointer="/a", schema_id="s", schema_pointer="/type")
    root_issue = WireIssue(code="schema.type", message="Wrong type", instance_pointer="", schema_id="s", schema_pointer="/type")
    lines.extend((
        f"issue-code: {attempt(lambda: str(WireIssue(code='not a code', message='', instance_pointer='', schema_id='', schema_pointer='')))}",
        f"issue-message: {attempt(lambda: str(WireIssue(code='a.b', message='x' * 1025, instance_pointer='', schema_id='', schema_pointer='')))}",
        f"error-empty: {WireValidationError(())}",
        f"error-pointer: {WireValidationError((issue, root_issue))}",
        f"error-root: {WireValidationError((root_issue,))}",
        f"unset: {UNSET!r} {bool(UNSET)} {UNSET is Unset.UNSET}",
    ))
    return "\n".join(lines) + "\n"


def missing_format_report(monkeypatch: pytest.MonkeyPatch, path: Path) -> str:
    """Remove one upstream format checker, as an absent optional dependency would, and rebuild validators."""
    import jsonschema

    from datamodel_code_generator._runtime.model_codecs import schema

    format_checker = jsonschema.Draft202012Validator.FORMAT_CHECKER
    checkers = {name: value for name, value in format_checker.checkers.items() if name != "idn-hostname"}
    monkeypatch.setattr(format_checker, "checkers", checkers)
    schema._validator_factory.cache_clear()
    fixture = decode_json(path.read_bytes())
    bundle = SchemaBundle(
        SchemaResource(uri=item["uri"], contents=item["contents"], roots=tuple(item["roots"])) for item in fixture["resources"]
    )
    try:
        return f"{attempt(lambda: str(bundle.validator(fixture['cases'][0]['schema'])))}\n"
    finally:
        monkeypatch.undo()
        schema._validator_factory.cache_clear()


def alias_hint_report() -> str:
    """Resolve the recursive wire aliases through another module's private import aliases."""
    from typing import get_type_hints

    from datamodel_code_generator._runtime.model_codecs.wire import JSONScalar, JSONValue, WireValue
    from tests.data.generation_platform.codecs.typing.annotations import snapshot

    hints = get_type_hints(snapshot, include_extras=True)
    lines = [f"snapshot: value={hints['value'] is JSONValue} return={hints['return'] is WireValue}"]
    lines.extend(f"{alias.__name__} {alias.__module__}: {alias.__value__}" for alias in (JSONScalar, JSONValue, WireValue))
    result = snapshot({"a": [1, None]})
    lines.append(f"call: {type(result).__name__} {type(result['a']).__name__} {_snapshot(result)}")
    return "\n".join(lines) + "\n"


def runtime_import_report(root: Path) -> str:
    """List every import of the embeddable runtime, which must never reach the generator package."""
    import ast

    lines = []
    for path in sorted(root.rglob("*.py")):
        imports = sorted({
            f"{'.' * node.level}{node.module or ''}" if isinstance(node, ast.ImportFrom) else alias.name
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        })
        lines.append(f"{path.relative_to(root).as_posix()}: {', '.join(imports) or '-'}")
    return "\n".join(lines) + "\n"
