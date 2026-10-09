"""Render runtime codec results as text, keeping case inputs in external fixture files."""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal
from typing import TYPE_CHECKING

from datamodel_code_generator._runtime.model_codecs.errors import CodecError
from datamodel_code_generator._runtime.model_codecs.media import (
    FieldPlan,
    decode_text,
    json_bytes,
    media_kind,
    normalize_media_type,
    plain,
)
from datamodel_code_generator._runtime.model_codecs.parameters import (
    ParameterPlan,
    RawParameters,
    decode_parameter,
    pairs,
    path_text,
    querystring,
    raw_parameter,
)
from datamodel_code_generator._runtime.model_codecs.unset import UNSET, Unset

if TYPE_CHECKING:
    import ast
    from collections.abc import Callable
    from pathlib import Path


def failure(error: Exception) -> str:
    """Name a codec failure by its class and value-free message."""
    return f"{type(error).__name__} {error}"


def attempt(action: Callable[[], str]) -> str:
    """Run one case, rendering an expected codec, type, or value failure instead of raising it."""
    try:
        return action()
    except (CodecError, TypeError, ValueError) as error:
        return failure(error)


def _json(value: object) -> str:
    return json_bytes(value).decode()


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


def _http(plan: ParameterPlan, value: object) -> tuple[str, RawParameters]:
    if plan.location == "querystring":
        query = querystring(plan, value)
        return f"?{query}", RawParameters(query=query.encode())
    written = [(key or "", text) for key, text in pairs(plan, value)]
    match plan.location:
        case "path":
            text = written[0][1]
            return f"/{text}", RawParameters(path={plan.name: text.encode()})
        case "query":
            query = "&".join(f"{key}={text}" for key, text in written)
            return f"?{query}", RawParameters(query=query.encode())
        case "header":
            headers = tuple((key.encode(), text.encode()) for key, text in written)
            return " / ".join(f"{key}: {text}" for key, text in written), RawParameters(headers=headers)
        case _:
            cookie = "; ".join(f"{key}={text}" for key, text in written)
            return f"Cookie: {cookie}", RawParameters(headers=((b"Cookie", cookie.encode()),))


def _round_trip(plan: ParameterPlan, value: object) -> str:
    http, raw = _http(plan, value)
    return f"{http} => {_decoded(decode_parameter(plan, raw_parameter(plan, raw)))}"


def parameter_encoding_report(path: Path) -> str:
    """Encode official style examples and boundary values, keeping exact number lexemes, then decode them again."""
    fixture = json.loads(path.read_bytes(), parse_float=Decimal)
    lines = [
        f"{_label(parameter_plan(case['plan']))}: {attempt(lambda case=case: _round_trip(parameter_plan(case['plan']), case['value']))}"
        for case in (*fixture["encode"], *fixture["failures"])
    ]
    lines.extend(
        f"invalid {dict(plan)}: {attempt(lambda plan=plan: str(parameter_plan(plan)))}"
        for plan in fixture["invalid_plans"]
    )
    lines.extend(
        f"path text {_label(parameter_plan(case['plan']))}: {attempt(lambda case=case: path_text(parameter_plan(case['plan']), case['value']))}"
        for case in fixture["path_texts"]
    )
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
    return "\n".join(lines) + "\n"


def _bytes(value: object) -> bytes:
    return bytes.fromhex(value["hex"]) if isinstance(value, Mapping) else str(value).encode()


def _fixture_raw(data: Mapping[str, object]) -> RawParameters:
    return RawParameters(
        path={name: _bytes(value) for name, value in data.get("path", {}).items()}
        if isinstance(data.get("path"), Mapping) and "hex" not in data.get("path", {})
        else {"color": _bytes(data["path"])}
        if "path" in data
        else {},
        query=_bytes(data["query"]) if "query" in data else None,
        headers=tuple((_bytes(name), _bytes(value)) for name, value in data.get("headers", ())),
    )


def _decoded(value: object) -> str:
    return "UNSET" if value is UNSET else _json(plain(value))


def parameter_decoding_report(path: Path) -> str:
    """Decode raw fixture occurrences for every location, rendering UNSET, values, or failures."""
    fixture = json.loads(path.read_bytes())
    lines = []
    for case in fixture["cases"]:
        plan = parameter_plan(case["plan"])
        raw = _fixture_raw(case["raw"])
        lines.append(
            f"{_label(plan)} {_json(case['raw'])}: {attempt(lambda plan=plan, raw=raw: _decoded(decode_parameter(plan, raw_parameter(plan, raw))))}"
        )
    return "\n".join(lines) + "\n"


def media_report(path: Path) -> str:
    """Normalize media identities, classify builtin representations, and decode text bodies."""
    fixture = json.loads(path.read_text(encoding="utf-8"))
    lines = [
        f"normalize {json.dumps(value)}: {attempt(lambda value=value: normalize_media_type(value))}"
        for value in fixture["normalize"]
    ]
    lines.extend(f"kind {value}: {media_kind(normalize_media_type(value))}" for value in fixture["kinds"])
    lines.extend(
        f"text {item['hex']}: {attempt(lambda item=item: json.dumps(decode_text(bytes.fromhex(item['hex']))))}"
        for item in fixture["text"]
    )
    lines.append(f"unset: {UNSET!r} {bool(UNSET)} {UNSET is Unset.UNSET}")
    return "\n".join(lines) + "\n"


def alias_hint_report() -> str:
    """Resolve the recursive JSON aliases through another module's private import aliases."""
    from typing import get_type_hints

    from datamodel_code_generator._runtime.model_codecs.media import JSONScalar, JSONValue
    from tests.data.generation_platform.codecs.typing.annotations import encoded

    hints = get_type_hints(encoded, include_extras=True)
    lines = [f"encoded: value={hints['value'] is JSONValue} scalar={hints['scalar'] is JSONScalar}"]
    lines.extend(f"{alias.__name__} {alias.__module__}: {alias.__value__}" for alias in (JSONScalar, JSONValue))
    lines.append(f"call: {encoded({'a': [1, None]}, 'é').decode()}")
    return "\n".join(lines) + "\n"


def _imported_name(path: Path, node: ast.ImportFrom, name: str) -> str:
    """Name a relative import by the submodule it imports from a package, or else by the module it imports from."""
    module = f"{'.' * node.level}{node.module or ''}"
    package = path.parents[node.level - 1].joinpath(*(node.module or "").split(".")) if node.level else None
    if package is not None and (package / f"{name}.py").is_file():
        return f"{module}.{name}" if node.module else f"{module}{name}"
    return module


def runtime_import_report(root: Path) -> str:
    """List every import of the embeddable runtime, which must never reach the generator package."""
    import ast

    lines = []
    for path in sorted(root.rglob("*.py")):
        imports = sorted({
            _imported_name(path, node, alias.name) if isinstance(node, ast.ImportFrom) else alias.name
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        })
        lines.append(f"{path.relative_to(root).as_posix()}: {', '.join(imports) or '-'}")
    return "\n".join(lines) + "\n"
