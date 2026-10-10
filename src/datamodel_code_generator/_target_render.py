"""Render the pieces every target shares: runtime copies, plan literals, and the dependencies of what they import."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

    from datamodel_code_generator._runtime.model_codecs.media import FieldPlan
    from datamodel_code_generator._runtime.model_codecs.parameters import ParameterPlan

RUNTIME: Final = Path(__file__).parent / "_runtime"
MODEL_DEPENDENCIES: Final = (
    ("pydantic.EmailStr", "email-validator>=2.3"),
    ("pydantic.NameEmail", "email-validator>=2.3"),
    ("pydantic.networks.EmailStr", "email-validator>=2.3"),
    ("pydantic.networks.NameEmail", "email-validator>=2.3"),
    ("ulid", "python-ulid>=3.2.1"),
    ("pendulum", "pendulum>=3.2"),
)
_PLAN_DEFAULTS: Final[dict[str, object]] = {
    "style": None,
    "explode": False,
    "required": False,
    "allow_reserved": False,
    "content_media_type": None,
    "shape": "scalar",
    "kind": "string",
}


@dataclass(frozen=True, slots=True)
class Parameter:
    """One parameter of a generated signature, with its default expression or none."""

    name: str
    annotation: str
    default: str = ""


@dataclass(frozen=True, slots=True)
class Keyword:
    """One argument of a generated call: its keyword, empty for a positional one, and its value expression."""

    keyword: str
    value: str


@dataclass(frozen=True, slots=True)
class Plan:
    """A module-level plan constant: its name, its annotation, and the constructor call of its value."""

    name: str
    annotation: str
    constructor: str
    arguments: tuple[Keyword, ...]


def display(values: Iterable[str]) -> str:
    """Return the tuple display of expressions, with the comma a lone item needs."""
    items = tuple(values)
    return f"({', '.join(items)}{',' if len(items) == 1 else ''})"


def call(head: str, arguments: Iterable[str]) -> str:
    """Return a call of `head` with its arguments, each already spelled with its keyword when it has one."""
    return f"{head}({', '.join(arguments)})"


def runtime_sources(modules: Iterable[str]) -> Iterator[tuple[PurePosixPath, str]]:
    """Yield the declared runtime modules and their packages' initializers, in ascending path order.

    A target declares every module its capabilities need; the copy never follows the modules' imports.
    """
    declared = set(modules)
    packages = {(PurePosixPath(module).parent / "__init__.py").as_posix() for module in declared}
    for path in sorted({"__init__.py", *packages, *declared}):
        yield PurePosixPath("_runtime", path), (RUNTIME / path).read_text(encoding="utf-8")


def field_plan(local: Callable[[str, str], str], field: FieldPlan) -> str:
    """Return the FieldPlan constructor of one form or object member, naming it through `local`."""
    repeated = ", repeated=True" if field.repeated else ""
    return f"{local('_runtime.model_codecs.media', 'FieldPlan')}({field.name!r}, {field.kind!r}{repeated})"


def parameter_plan(local: Callable[[str, str], str], plan: ParameterPlan) -> str:
    """Return the plan constructor of one parameter or header, naming it through `local`."""
    entries = [f"location={plan.location!r}", f"name={plan.name!r}"]
    entries.extend(
        f"{name}={value!r}" for name, default in _PLAN_DEFAULTS.items() if (value := getattr(plan, name)) != default
    )
    if plan.fields:
        entries.append(f"fields={display(field_plan(local, item) for item in plan.fields)}")
    if plan.additional is not None:
        entries.append(f"additional={field_plan(local, plan.additional)}")
    if plan.reserved_names:
        entries.append(f"reserved_names={plan.reserved_names!r}")
    return call(local("_runtime.model_codecs.parameters", type(plan).__name__), entries)


def model_dependencies(imported: frozenset[str]) -> tuple[str, ...]:
    """Return the requirements of the optional libraries that the imported module and member names need, in order."""
    return tuple(
        dict.fromkeys(
            requirement
            for name, requirement in MODEL_DEPENDENCIES
            if any(item == name or item.startswith(f"{name}.") for item in imported)
        )
    )
