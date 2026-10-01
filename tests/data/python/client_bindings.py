"""Render model fixtures as client packages and report the models and model bindings each package ships.

Every component schema becomes the body and response of one operation, so the client binds every model. A change
edits the generated models through a custom formatter, as a user formatter may, and the report shows what the
edit changes in the shipped models and bindings.
"""

from __future__ import annotations

import ast
import difflib
import json
from collections import defaultdict
from itertools import product
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from tests.data.python.client_generation import render_client

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.data.python.client_generation import Modules

DATA = Path(__file__).parents[1]
CASES = DATA / "generation_platform" / "client" / "bindings.json"
FORMATTER = "tests.data.python.custom_formatters.replace_text"
CLIENT = {"default_base_url": "https://bindings.invalid", "validation": {"response": "schema"}}
MODEL = {
    "formatters": [],
    "use_union_operator": False,
    "use_standard_collections": False,
    "field_extra_keys": {"default_factory"},
}
BINDINGS = ("client", "_generated", "model_bindings.py")
MODELS = ("models.py",)


def _document(source: Path, root: Path, skipped: list[str]) -> Path:
    """Copy a model fixture as JSON, adding one operation that sends and returns each component schema not skipped."""
    text = source.read_text(encoding="utf-8")
    document = json.loads(text) if source.suffix == ".json" else yaml.safe_load(text)
    schemas = [name for name in document.get("components", {}).get("schemas", {}) if name not in skipped]
    document["paths"] = {
        **document.get("paths", {}),
        **{
            f"/bindings/{index}": {
                "post": {
                    "requestBody": {"content": {"application/json": {"schema": {"$ref": reference}}}},
                    "responses": {
                        "200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": reference}}}}
                    },
                }
            }
            for index, name in enumerate(schemas)
            for reference in (f"#/components/schemas/{name.replace('~', '~0').replace('/', '~1')}",)
        },
    }
    (path := root / "inputs" / f"{source.stem}.json").parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _options(values: dict[str, Any]) -> dict[str, Any]:
    if "extra_template_data" in values:
        return {**values, "extra_template_data": defaultdict(dict, values["extra_template_data"])}
    return values


def _shipped(diagnostics: list[str], modules: Modules) -> list[str]:
    """Return the refusal or diagnostics, the models, and each model binding and codec of a rendered package."""
    lines = [f"diagnostic {item}" for item in diagnostics]
    if MODELS in modules:
        lines.extend(["models.py", *modules[MODELS].splitlines()])
    if BINDINGS in modules:
        lines.append("model_bindings.py")
        lines.extend(_bindings(modules[BINDINGS]))
    return lines


def _bindings(text: str) -> Iterator[str]:
    """Yield each model binding on one line with one line per field binding, then the type of each codec."""
    for node in ast.parse(text).body:
        match node:
            case ast.FunctionDef(body=[ast.Return(value=ast.Call() as call)]) if node.name.startswith("_model_"):
                yield f"{node.name} " + ", ".join(
                    f"{item.arg}={ast.unparse(item.value)}" for item in call.keywords if item.arg != "fields"
                )
                for item in call.keywords:
                    if item.arg == "fields" and isinstance(item.value, ast.Tuple):
                        yield from (f"  {ast.unparse(field)}" for field in item.value.elts)
            case ast.FunctionDef(returns=ast.expr() as returns) if node.name.startswith("codec_"):
                yield f"{node.name} -> {ast.unparse(returns)}"
            case _:
                pass


def _difference(before: list[str], after: list[str]) -> list[str]:
    changed = [
        line for line in difflib.unified_diff(before, after, lineterm="", n=0) if not line.startswith(("---", "+++"))
    ]
    return changed or ["unchanged"]


def client_binding_report(case_name: str, root: Path) -> str:
    """Render one fixture for each backend and variant, then once more for each change of a render."""
    case = json.loads(CASES.read_text(encoding="utf-8"))[case_name]
    source = _document(DATA / case["source"], root, case.get("skip", []))
    changes: dict[str, list[dict[str, str]]] = {}
    for change in case.get("changes", ()):
        changes.setdefault(change["render"], []).append(change)
    lines = [f"# {case_name}"]
    for count, (backend, (variant, options)) in enumerate(
        product(case.get("backends", ["pydantic_v2.BaseModel"]), case.get("variants", {"default": {}}).items())
    ):
        label = f"{backend} {variant}"
        backend_model = case.get("backend_model", {}).get(backend, {})
        model = _options({**MODEL, **case.get("model", {}), **backend_model, **options})
        shipped = _shipped(*render_client(source, root / str(count), backend, model, CLIENT))
        lines.extend((f"render {label}", *(f"  {line}" for line in shipped)))
        for change in changes.pop(label, ()):
            edit = {key: change[key] for key in ("old", "new", "append") if key in change}
            edited = _shipped(
                *render_client(
                    source,
                    root / f"{count}-{change['id']}",
                    backend,
                    {**model, "custom_formatters": [FORMATTER], "custom_formatters_kwargs": edit},
                    CLIENT,
                )
            )
            lines.extend((f"change {label} {change['id']}", *(f"  {line}" for line in _difference(shipped, edited))))
    if changes:
        msg = f"Changes name no render: {sorted(changes)}"
        raise ValueError(msg)
    return "\n".join(lines).replace(root.resolve().as_posix(), "<root>") + "\n"
