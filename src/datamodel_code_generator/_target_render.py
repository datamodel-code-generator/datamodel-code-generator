"""Render the pieces every target shares: runtime copies, plan literals, and the dependencies of what they import."""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Final

from datamodel_code_generator._python_syntax import call_text, tuple_display
from datamodel_code_generator._requirements import REQUIREMENTS, pydantic_target_meets
from datamodel_code_generator.enums import TargetPydanticVersion, _pydantic_version_key

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
PYDANTIC_TARGET_MINIMUM: Final = REQUIREMENTS["pydantic.api_target_runtime"].minimum
PYDANTIC_FLOOR: Final = f"{PYDANTIC_TARGET_MINIMUM}.0"
PYDANTIC_BACKENDS: Final = frozenset({"pydantic_v2.BaseModel", "pydantic_v2.dataclass"})
PYDANTIC_IMPORT_MINIMUMS: Final = tuple(
    (form, row.minimum) for row in REQUIREMENTS.values() if row.library == "pydantic" and (form := row.form) is not None
)


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
    """Return the plan constructor of one parameter or header, naming it through `local`; defaults are left out."""

    def spelled(name: str, value: Any) -> str:
        match name:
            case "fields":
                return tuple_display(field_plan(local, item) for item in value)
            case "additional":
                return field_plan(local, value)
        return repr(value)

    entries = [
        f"{item.name}={spelled(item.name, value)}"
        for item in fields(plan)
        if (value := getattr(plan, item.name)) != item.default
    ]
    return call_text(local("_runtime.model_codecs.parameters", type(plan).__name__), entries)


def model_dependencies(imported: frozenset[str]) -> tuple[str, ...]:
    """Return the requirements of the optional libraries that the imported module and member names need, in order."""
    return tuple(
        dict.fromkeys(
            requirement
            for name, requirement in MODEL_DEPENDENCIES
            if any(item == name or item.startswith(f"{name}.") for item in imported)
        )
    )


def pydantic_target_problem(
    backend: str, target: TargetPydanticVersion | None, *, sentinel: bool, implied: bool = False
) -> str | None:
    """Say what a package that requires pydantic needs of its target pydantic version, or None.

    An explicit target older than the floor is refused, and so is an unset one whose models use the MISSING sentinel:
    the models stay as a model-only run writes them, which imports MISSING from pydantic for a target of the floor. A
    sentinel the target `implied` for the backend is named as such.
    """
    if backend not in PYDANTIC_BACKENDS or (
        pydantic_target_meets("pydantic.api_target_runtime", target)
        and (not sentinel or pydantic_target_meets("pydantic.missing_sentinel", target))
    ):
        return None
    if sentinel:
        reason = f", which it sets for {backend!r} models," if implied else ","
        return (
            f"needs model_config.target_pydantic_version {REQUIREMENTS['pydantic.missing_sentinel'].minimum} for "
            f"--use-missing-sentinel{reason} "
            "so that the models import MISSING from pydantic"
        )
    allowed = " or ".join(
        repr(item.value) for item in TargetPydanticVersion if pydantic_target_meets("pydantic.api_target_runtime", item)
    )
    return f"needs a target pydantic version of {PYDANTIC_TARGET_MINIMUM} or later; leave it unset or use {allowed}"


def pydantic_dependency(imported: frozenset[str]) -> str:
    """Return the pydantic requirement: the tested floor, raised to the newest minimum of what the models import."""
    minimums = (minimum for name, minimum in PYDANTIC_IMPORT_MINIMUMS if name in imported)
    return f"pydantic>={max((PYDANTIC_FLOOR, *minimums), key=_pydantic_version_key)}"
