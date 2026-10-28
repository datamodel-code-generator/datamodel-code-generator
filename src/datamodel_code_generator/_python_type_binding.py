"""Immutable binding between Python type expressions and generated imports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from datamodel_code_generator._python_type_annotation import (
    PythonTypeBoundName,
    PythonTypeExpr,
    PythonTypeRuntimeSymbol,
    rewrite_python_type_expr,
)

if TYPE_CHECKING:
    from datamodel_code_generator.imports import Import


@dataclass(frozen=True, slots=True)
class BoundPythonType:
    """A semantic annotation and the ordered imports that bind its names.

    Rendered text is intentionally not cached here. The expression remains the
    source of truth until the final DataType string boundary, preventing a later
    code-generation phase from reparsing output produced by an earlier phase.
    """

    expression: PythonTypeExpr
    imports: tuple[Import, ...]

    def __deepcopy__(self, _memo: dict[int, object]) -> BoundPythonType:
        """Share the immutable binding across copied model graphs."""
        return self


def python_type_import_key(import_: Import) -> tuple[str | None, str]:
    """Return the stable identity used when an import receives an alias."""
    return import_.from_, import_.import_


def python_type_import_name(import_: Import) -> str:
    """Return the identifier currently bound by an import statement."""
    return import_.binding_name


def alias_bound_python_type(
    bound_type: BoundPythonType,
    aliases: dict[tuple[str | None, str], Import],
) -> BoundPythonType:
    """Alias imports and their semantic leaves together without a text round trip."""
    module_aliases: dict[str, str] = {}
    imports: list[Import] = []
    changed = False
    for import_ in bound_type.imports:
        if (aliased_import := aliases.get(python_type_import_key(import_))) is None:
            imports.append(import_)
            continue
        changed = True
        imports.append(aliased_import)
        if import_.from_ is None:
            module_aliases[import_.import_] = aliased_import.alias or aliased_import.import_

    if not changed:
        return bound_type

    def alias_leaf(expression: PythonTypeExpr) -> PythonTypeExpr:
        match expression:
            case PythonTypeBoundName() as bound_name:
                if aliased_import := aliases.get((bound_name.import_from, bound_name.import_name)):
                    return PythonTypeBoundName(
                        aliased_import.alias or aliased_import.import_,
                        bound_name.import_from,
                        bound_name.import_name,
                    )
            case PythonTypeRuntimeSymbol() as runtime_symbol:
                if alias := module_aliases.get(runtime_symbol.module):
                    return PythonTypeRuntimeSymbol(alias, runtime_symbol.qualname_parts)
        return expression

    return BoundPythonType(
        rewrite_python_type_expr(bound_type.expression, alias_leaf),
        tuple(imports),
    )


__all__ = [
    "BoundPythonType",
    "alias_bound_python_type",
    "python_type_import_key",
    "python_type_import_name",
]
