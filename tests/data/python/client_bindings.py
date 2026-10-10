"""Render model fixtures as client packages and report the model modules and model codecs each package ships.

Every component schema becomes the body and response of one operation, so the client binds every model. A change
edits the generated models through a custom formatter, as a user formatter may, and the report shows what the
edit changes in the shipped models and codecs. A rewrite instead changes the staged models after generation,
as another process writing to the staging directory would.

A render the generator refuses reports the error instead of a package, and the generator's own warnings are reported
before what the render ships. A case that pins a known bug names the internal exception it lets escape in "raises".
Each render that ships a package also generates its models the ordinary way, for the models it captured to equal.
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

import pytest
import yaml

import datamodel_code_generator
from datamodel_code_generator import generate
from tests.data.python.client_generation import client_cyclic_metadata_report, model_config, render_client

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from tests.data.python.client_generation import Modules

DATA = Path(__file__).parents[1]
CASES = DATA / "generation_platform" / "client" / "bindings.json"
FORMATTER = "tests.data.python.custom_formatters.replace_text"
CLIENT = {"default_base_url": "https://bindings.invalid"}
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
STDLIB = frozenset({"dataclasses.dataclass", "typing.TypedDict"})


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


def binding_model_options(values: dict[str, Any], root: Path) -> dict[str, Any]:
    """Prepare model options with fixture templates copied under the test root."""
    if "extra_template_data" in values:
        values = {**values, "extra_template_data": defaultdict(dict, values["extra_template_data"])}
    if "custom_template_dir" in values:
        values = {
            **values,
            "custom_template_dir": shutil.copytree(
                values["custom_template_dir"], root / "templates", dirs_exist_ok=True
            ),
        }
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


def _models(modules: Modules) -> Modules:
    """Return the model modules of a rendered package's files."""
    return {parts: text for parts, text in modules.items() if parts[0] != PACKAGE}


def _shipped(diagnostics: list[str], modules: Modules, *, maps: bool, sources: bool) -> list[str]:
    """Return the refusal or diagnostics, the model modules, and the codecs and field maps of a rendered package.

    With sources, each model module is followed by its lines, for an edit to show what it changes in the models.
    """
    lines = [f"diagnostic {item}" for item in diagnostics]
    for parts, text in _models(modules).items():
        lines.extend(["/".join(parts), *(text.splitlines() if sources else ())])
    if BINDINGS in modules:
        lines.append("model_bindings.py")
        lines.extend(_codecs(modules[BINDINGS], maps=maps))
    return lines


def _codecs(text: str, *, maps: bool) -> Iterator[str]:
    """Yield the type each codec reads and writes, then the field map of each dataclass or TypedDict model."""
    models: list[str] = []
    modules: dict[str, str] = {}
    for node in ast.parse(text).body:
        match node:
            case ast.Import():
                modules.update((alias.asname or alias.name, alias.name) for alias in node.names)
            case ast.ImportFrom(level=0):
                modules.update((alias.asname or alias.name, f"{node.module}.{alias.name}") for alias in node.names)
            case ast.AnnAssign(
                target=ast.Name(id=name), annotation=ast.Subscript(slice=ast.Subscript(slice=type_))
            ) if name.startswith("codec_"):
                yield f"{name} {ast.unparse(type_)}"
            case ast.AnnAssign(target=ast.Name(id="MODELS"), value=ast.Dict(keys=keys, values=values)) if maps:
                for key, value in zip(keys, values, strict=True):
                    module, _, model = ast.unparse(key).rpartition(".")
                    pairs = ", ".join(
                        f"{ast.literal_eval(field.args[0])}={ast.literal_eval(field.args[1])}"
                        for field in value.args[0].elts
                    )
                    models.append(f"map {modules.get(module, module)}:{model} {pairs}".rstrip())
            case _:
                pass
    yield from sorted(models)


def _difference(before: list[str], after: list[str]) -> list[str]:
    changed = [
        line for line in difflib.unified_diff(before, after, lineterm="", n=0) if not line.startswith(("---", "+++"))
    ]
    return changed or ["unchanged"]


def _renders(
    case: dict[str, Any], root: Path
) -> Iterator[tuple[str, Callable[..., tuple[list[str], list[str], Modules]], Callable[[str], Path]]]:
    """Yield the label of each backend and variant of a case, and functions that render it or its models in a directory.

    A render returns its report lines without and with the model sources, and the Python modules of the package it
    ships, and the ordinary generation returns the directory that holds its models.
    """
    source = _document(DATA / case["source"], root, case)
    config = {**CLIENT, **case.get("config", {})}
    models = case.get("models", "models.py")
    raises = case.get("raises")
    for count, (backend, (variant, options)) in enumerate(
        product(case.get("backends", ["pydantic_v2.BaseModel"]), case.get("variants", {"default": {}}).items())
    ):
        model = binding_model_options(
            {
                **MODEL,
                **case.get("model", {}),
                **case.get("backend_model", {}).get(backend, {}),
                **options,
            },
            root,
        )

        def render(
            name: str,
            extra: dict[str, Any] | None = None,
            *,
            backend: str = backend,
            model: dict = model,
        ) -> tuple[list[str], list[str], Modules]:
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
                    )
                except Exception as error:
                    if not isinstance(error, datamodel_code_generator.Error) and type(error).__name__ != raises:
                        raise
                    diagnostics, modules = [f"{type(error).__name__}: {error}"], {}
            reported = _warnings(caught)
            return (
                [*reported, *_shipped(diagnostics, modules, maps=backend in STDLIB, sources=False)],
                [*reported, *_shipped(diagnostics, modules, maps=backend in STDLIB, sources=True)],
                modules,
            )

        def ordinary(name: str, *, backend: str = backend, model: dict = model) -> Path:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                generate(source, config=model_config(root / name / models, backend, model))
            _warnings(caught)
            return root / name

        yield f"{count} {backend} {variant}", render, ordinary


def _edits(case: dict[str, Any], key: str) -> dict[str, list[dict[str, str]]]:
    edits: dict[str, list[dict[str, str]]] = {}
    for edit in case.get(key, ()):
        edits.setdefault(edit["render"], []).append(edit)
    return edits


def _report(case_name: str, lines: list[str], unused: dict[str, list[dict[str, str]]], root: Path) -> str:
    if unused:
        lines.append(f"edits naming no render: {sorted(unused)}")
    return "\n".join([f"# {case_name}", *lines]).replace(root.resolve().as_posix(), "<root>") + "\n"


def client_binding_report(case_name: str, root: Path) -> tuple[str, list[tuple[Modules, Path]]]:
    """Render one fixture for each backend and variant, then again with each formatter change of a render.

    Each render that ships a package is returned with its models, and the directory where ordinary generation
    wrote the models of the same options.
    """
    case = json.loads(CASES.read_text(encoding="utf-8"))[case_name]
    if case_name == "session-cyclic-metadata":
        return client_cyclic_metadata_report(_document(DATA / case["source"], root, case), root, MODEL), []
    changes = _edits(case, "changes")
    lines: list[str] = []
    packages: list[tuple[Modules, Path]] = []
    for key, render, ordinary in _renders(case, root):
        count, label = key.split(" ", 1)
        listed, shipped, modules = render(count)
        if modules:
            packages.append((_models(modules), ordinary(f"ordinary-{count}")))
        lines.extend((f"render {label}", *(f"  {line}" for line in listed)))
        for change in changes.pop(label, ()):
            edit = {key: change[key] for key in ("old", "new", "append") if key in change}
            _, edited, _ = render(change["id"], {"custom_formatters": [FORMATTER], "custom_formatters_kwargs": edit})
            lines.extend((f"change {label} {change['id']}", *(f"  {line}" for line in _difference(shipped, edited))))
    return _report(case_name, lines, changes, root), packages


@pytest.mark.abnormal_path("Another process rewriting the staged models cannot be timed from outside.")
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
    """Render each rewritten render of a fixture again, with its staged models rewritten after generation."""
    case = json.loads(CASES.read_text(encoding="utf-8"))[case_name]
    rewrites = _edits(case, "rewrites")
    lines: list[str] = []
    for key, render, _ in _renders(case, root):
        count, label = key.split(" ", 1)
        if not (selected := rewrites.pop(label, ())):
            continue
        _, shipped, _ = render(count)
        for rewrite in selected:
            with _rewritten_stage(rewrite["old"], rewrite["new"]):
                _, edited, _ = render(rewrite["id"])
            lines.extend((f"rewrite {label} {rewrite['id']}", *(f"  {line}" for line in _difference(shipped, edited))))
    return _report(case_name, lines, rewrites, root)
