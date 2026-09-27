"""Render the pieces every target shares: runtime copies, plan literals, and the dependencies of what they import."""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import cache
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._python_layout import Doc, Group

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

    from datamodel_code_generator._openapi_artifacts import ModelArtifact
    from datamodel_code_generator._openapi_wire_plan import WirePlan
    from datamodel_code_generator._runtime.model_codecs.media import FieldPlan
    from datamodel_code_generator._runtime.model_codecs.parameters import ParameterPlan
    from datamodel_code_generator._runtime.model_codecs.wire import WireValue

RUNTIME: Final = Path(__file__).parent / "_runtime"
_RUNTIME_IMPORT: Final = re.compile(r"^from \.+_runtime\.(\w+)\.(\w+) import", re.MULTILINE)
_RELATIVE_IMPORT: Final = re.compile(r"^\s*from (\.+)(\w+(?:\.\w+)*) import", re.MULTILINE)
PATTERNS: Final = "google-re2>=1.1.20251105"
MODEL_DEPENDENCIES: Final = (
    ("pydantic.EmailStr", "email-validator>=2.3"),
    ("pydantic.NameEmail", "email-validator>=2.3"),
    ("pydantic.networks.EmailStr", "email-validator>=2.3"),
    ("pydantic.networks.NameEmail", "email-validator>=2.3"),
    ("ulid", "python-ulid>=3.2.1"),
    ("pendulum", "pendulum>=3.2"),
)
_PATTERN_KEYWORDS: Final = frozenset({"pattern", "patternProperties"})
_IMPORT: Final = re.compile(
    r"^(?:from[ \t]+([\w.]+)[ \t]+import[ \t]+(\([^)]*\)|[^\n]*)|import[ \t]+([^\n]*))", re.MULTILINE
)
_COMMENT: Final = re.compile(r"#[^\n]*")
_PLAN_DEFAULTS: Final[dict[str, object]] = {
    "style": None,
    "explode": False,
    "required": False,
    "allow_reserved": False,
    "content_media_type": None,
    "shape": "scalar",
    "kind": "string",
}


def items(values: Iterable[Doc]) -> tuple[tuple[str, Doc], ...]:
    """Return group items without prefixes."""
    return tuple(("", value) for value in values)


@cache
def _relative_imports(module: str) -> tuple[str, ...]:
    """Return the runtime modules that one runtime module imports relatively."""
    parents = PurePosixPath(module).parents
    return tuple(
        f"{parents[len(dots) - 1].joinpath(*target.split('.')).as_posix()}.py"
        for dots, target in _RELATIVE_IMPORT.findall((RUNTIME / module).read_text(encoding="utf-8"))
    )


def runtime_sources(texts: Iterable[str]) -> Iterator[tuple[PurePosixPath, str]]:
    """Yield the runtime modules that sources import, with their own imports, in ascending path order."""
    pending = [f"{package}/{module}.py" for text in texts for package, module in _RUNTIME_IMPORT.findall(text)]
    needed: set[str] = set()
    while pending:
        if (module := pending.pop()) not in needed:
            needed.add(module)
            pending.extend(_relative_imports(module))
    packages = {f"{PurePosixPath(module).parent}/__init__.py" for module in needed}
    for path in sorted({"__init__.py", *packages, *needed}):
        yield PurePosixPath("_runtime", path), (RUNTIME / path).read_text(encoding="utf-8")


def field_plan(local: Callable[[str, str], str], field: FieldPlan) -> str:
    """Return the FieldPlan constructor of one form or object member, naming it through `local`."""
    repeated = ", repeated=True" if field.repeated else ""
    return f"{local('_runtime.model_codecs.media', 'FieldPlan')}({field.name!r}, {field.kind!r}{repeated})"


def parameter_plan(local: Callable[[str, str], str], plan: ParameterPlan) -> Group:
    """Return the ParameterPlan constructor of one parameter or header, naming it through `local`."""
    entries: list[tuple[str, Doc]] = [("location=", repr(plan.location)), ("name=", repr(plan.name))]
    entries.extend(
        (f"{name}=", repr(value))
        for name, default in _PLAN_DEFAULTS.items()
        if (value := getattr(plan, name)) != default
    )
    if plan.fields:
        entries.append(("fields=", Group("(", items(field_plan(local, item) for item in plan.fields), ")", ",")))
    if plan.additional is not None:
        entries.append(("additional=", field_plan(local, plan.additional)))
    if plan.reserved_names:
        entries.append(("reserved_names=", repr(plan.reserved_names)))
    return Group(f"{local('_runtime.model_codecs.parameters', 'ParameterPlan')}(", tuple(entries), ")")


def _imported(models: tuple[ModelArtifact, ...]) -> frozenset[str]:
    """Return every module and imported name that the top-level import statements of the models name."""
    names: set[str] = set()
    for artifact in models:
        for module, members, plain in _IMPORT.findall(artifact.content.decode(artifact.encoding)):
            entries = [
                item.split()[0] for item in _COMMENT.sub("", members or plain).strip("() \n").split(",") if item.split()
            ]
            names.update((module, *(f"{module}.{item}" for item in entries)) if module else entries)
    return frozenset(names)


def model_dependencies(models: tuple[ModelArtifact, ...]) -> tuple[str, ...]:
    """Return the requirements of the optional libraries the models import, in declaration order."""
    imported = _imported(models)
    return tuple(
        dict.fromkeys(
            requirement
            for name, requirement in MODEL_DEPENDENCIES
            if any(item == name or item.startswith(f"{name}.") for item in imported)
        )
    )


def _patterned(value: WireValue) -> bool:
    if isinstance(value, tuple):
        return any(_patterned(item) for item in value)
    if isinstance(value, Mapping):
        return any(key in _PATTERN_KEYWORDS or _patterned(item) for key, item in value.items())
    return False


def patterned(wire: WirePlan) -> bool:
    """Return whether a schema the wire plan bundles declares a pattern, which the runtime matches with RE2."""
    return any(_patterned(resource.contents) for resource in wire.resources)
