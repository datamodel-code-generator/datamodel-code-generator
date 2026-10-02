"""Render model fixtures as client packages and report the models and model bindings each package ships.

Every component schema becomes the body and response of one operation, so the client binds every model. A change
edits the generated models through a custom formatter, as a user formatter may, and the report shows what the
edit changes in the shipped models and bindings. A rewrite instead changes the staged models after generation,
as another process writing to the staging directory would.

A render the generator refuses reports the error instead of a package, and the generator's own warnings are reported
before what the render ships.
"""

from __future__ import annotations

import ast
import difflib
import json
import shutil
import warnings
from collections import defaultdict
from contextlib import contextmanager
from itertools import product
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import yaml

import datamodel_code_generator
from tests.data.python.client_generation import render_client

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

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
GENERATOR = Path(datamodel_code_generator.__file__).parent
PACKAGE = "client"
BINDINGS = (PACKAGE, "_generated", "model_bindings.py")
STAGING = ".datamodel-codegen-"


def _document(source: Path, root: Path, case: dict[str, Any]) -> Path:
    """Copy a model fixture and the documents it references beside it.

    A fixture becomes JSON with one more operation that sends and returns each component schema the case does not
    skip. A verbatim fixture keeps its own text, YAML aliases included, and declares its operations itself.
    """
    (inputs := root / "inputs").mkdir(parents=True, exist_ok=True)
    for reference in case.get("references", ()):
        shutil.copy2(source.parent / reference, inputs / reference)
    if case.get("verbatim"):
        return Path(shutil.copy2(source, inputs / source.name))
    text = source.read_text(encoding="utf-8")
    document = {**(json.loads(text) if source.suffix == ".json" else yaml.safe_load(text)), **case.get("document", {})}
    skipped = case.get("skip", ())
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
    path = inputs / f"{source.stem}.json"
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _options(values: dict[str, Any]) -> dict[str, Any]:
    if "extra_template_data" in values:
        return {**values, "extra_template_data": defaultdict(dict, values["extra_template_data"])}
    return values


def _warnings(caught: list[warnings.WarningMessage]) -> list[str]:
    """Return the generator's own warnings as report lines, and warn again of any other warning."""
    lines: list[str] = []
    for item in caught:
        if item.category.__module__.startswith(GENERATOR.name) or Path(item.filename).is_relative_to(GENERATOR):
            lines.append(f"warning {item.category.__name__}: {item.message}")
        else:
            warnings.warn_explicit(item.message, item.category, item.filename, item.lineno, source=item.source)
    return lines


def _shipped(diagnostics: list[str], modules: Modules) -> list[str]:
    """Return the refusal or diagnostics, the models, and each model binding and codec of a rendered package."""
    lines = [f"diagnostic {item}" for item in diagnostics]
    for parts, text in modules.items():
        if parts[0] != PACKAGE:
            lines.extend(["/".join(parts), *text.splitlines()])
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


def _renders(case: dict[str, Any], root: Path) -> Iterator[tuple[str, Callable[..., list[str]]]]:
    """Yield the label of each backend and variant of a case, and a function that renders it under a directory."""
    source = _document(DATA / case["source"], root, case)
    config = {**CLIENT, **case.get("config", {})}
    models = case.get("models", "models.py")
    for count, (backend, (variant, options)) in enumerate(
        product(case.get("backends", ["pydantic_v2.BaseModel"]), case.get("variants", {"default": {}}).items())
    ):
        model = _options({
            **MODEL,
            **case.get("model", {}),
            **case.get("backend_model", {}).get(backend, {}),
            **options,
        })

        def render(
            name: str,
            extra: dict[str, Any] | None = None,
            *,
            binding_diagnostics: bool = False,
            backend: str = backend,
            model: dict = model,
        ) -> list[str]:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                try:
                    diagnostics, modules = render_client(
                        source,
                        root / name,
                        backend,
                        {**model, **(extra or {})},
                        config,
                        models=models,
                        binding_diagnostics=binding_diagnostics,
                    )
                except datamodel_code_generator.Error as error:
                    diagnostics, modules = [f"{type(error).__name__}: {error}"], {}
            return [*_warnings(caught), *_shipped(diagnostics, modules)]

        yield f"{count} {backend} {variant}", render


def _edits(case: dict[str, Any], key: str) -> dict[str, list[dict[str, str]]]:
    edits: dict[str, list[dict[str, str]]] = {}
    for edit in case.get(key, ()):
        edits.setdefault(edit["render"], []).append(edit)
    return edits


def _report(case_name: str, lines: list[str], unused: dict[str, list[dict[str, str]]], root: Path) -> str:
    if unused:
        lines.append(f"edits naming no render: {sorted(unused)}")
    return "\n".join([f"# {case_name}", *lines]).replace(root.resolve().as_posix(), "<root>") + "\n"


def client_binding_report(case_name: str, root: Path) -> str:
    """Render one fixture for each backend and variant, then again with each formatter change of a render."""
    case = json.loads(CASES.read_text(encoding="utf-8"))[case_name]
    changes = _edits(case, "changes")
    lines: list[str] = []
    for key, render in _renders(case, root):
        count, label = key.split(" ", 1)
        shipped = render(count)
        lines.extend((f"render {label}", *(f"  {line}" for line in shipped)))
        for change in changes.pop(label, ()):
            edit = {key: change[key] for key in ("old", "new", "append") if key in change}
            edited = render(change["id"], {"custom_formatters": [FORMATTER], "custom_formatters_kwargs": edit})
            lines.extend((f"change {label} {change['id']}", *(f"  {line}" for line in _difference(shipped, edited))))
    return _report(case_name, lines, changes, root)


@contextmanager
def _rewritten_stage(old: str, new: str) -> Iterator[None]:
    """Replace text in staged models whenever they are read, as a process rewriting the staging directory would."""
    read_bytes = Path.read_bytes

    def rewrite(path: Path) -> bytes:
        content = read_bytes(path)
        if path.suffix == ".py" and any(part.startswith(STAGING) for part in path.parts):
            return content.replace(old.encode(), new.encode())
        return content

    with patch.object(Path, "read_bytes", rewrite):
        yield


def client_binding_rewrite_report(case_name: str, root: Path) -> str:
    """Render each rewritten render of a fixture again, with its staged models rewritten after generation.

    Both renders also report the diagnostics of the model binding batch, because packages drop them (#4299).
    """
    case = json.loads(CASES.read_text(encoding="utf-8"))[case_name]
    rewrites = _edits(case, "rewrites")
    lines: list[str] = []
    for key, render in _renders(case, root):
        count, label = key.split(" ", 1)
        if not (selected := rewrites.pop(label, ())):
            continue
        shipped = render(count, binding_diagnostics=True)
        for rewrite in selected:
            with _rewritten_stage(rewrite["old"], rewrite["new"]):
                edited = render(rewrite["id"], binding_diagnostics=True)
            lines.extend((f"rewrite {label} {rewrite['id']}", *(f"  {line}" for line in _difference(shipped, edited))))
    return _report(case_name, lines, rewrites, root)
