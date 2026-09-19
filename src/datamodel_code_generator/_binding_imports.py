"""Bind projected types to actual module imports after ordinary import remapping."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._binding_literals import UnsupportedBindingValueError
from datamodel_code_generator._generation_contract import (
    AnnotatedType,
    BoundType,
    ConstructorType,
    GenericType,
    ImportedExpression,
    ImportedType,
    MetadataCall,
    TypeProjection,
    UnionType,
)
from datamodel_code_generator._python_type_annotation import (
    PythonTypeBoundName,
    PythonTypeRuntimeSymbol,
    rewrite_python_type_expr,
)
from datamodel_code_generator._python_type_binding import BoundPythonType

_UNSUPPORTED: Final = "BND_TYPE_EXPRESSION_UNSUPPORTED"


class FinalImportResolver:
    """Resolve retained producer imports against one actual final module namespace."""

    def __init__(self, imports: tuple[Import, ...], overrides: Mapping[str, str] | None) -> None:
        """Index final identities once, retaining independent alias ownership."""
        self._imports: dict[tuple[str | None, str, bool], list[Import]] = {}
        for import_ in imports:
            self._imports.setdefault((import_.from_, import_.import_, import_.keep_unaliased), []).append(import_)
        self._overrides = overrides or {}
        self._cache: dict[Import, Import] = {}
        self._needs_remap = bool(overrides) or any(import_.alias for import_ in imports)

    def resolve(self, import_: Import) -> Import:
        """Keep the actual emitted module/alias without changing the producer or graph."""
        if (resolved := self._cache.get(import_)) is not None:
            return resolved
        module = self._overrides.get(import_.import_, import_.from_) if import_.from_ != "__future__" else import_.from_
        candidates: Sequence[Import] = self._imports.get((module, import_.import_, import_.keep_unaliased), ())
        if len(candidates) != 1:
            candidates = tuple(candidate for candidate in candidates if candidate.alias == import_.alias)
        if len(candidates) != 1:
            raise UnsupportedBindingValueError(_UNSUPPORTED)
        actual = candidates[0]
        resolved = (
            import_
            if (import_.from_, import_.alias, import_.keep_unaliased)
            == (actual.from_, actual.alias, actual.keep_unaliased)
            else replace(import_, from_=actual.from_, alias=actual.alias, keep_unaliased=actual.keep_unaliased)
        )
        self._cache[import_] = resolved
        return resolved

    def project(self, projection: TypeProjection) -> TypeProjection:
        """Preserve unsupported values without turning them into generation failures."""
        if projection.value is None or not self._needs_remap:
            return projection
        try:
            return TypeProjection(self._type(projection.value))
        except UnsupportedBindingValueError as error:
            return TypeProjection(None, error.reason)

    def _argument(self, value: TypeArgument) -> TypeArgument:
        if isinstance(value, ImportedExpression):
            return replace(value, import_=self.resolve(value.import_))
        return value

    def _bound(self, value: BoundPythonType) -> BoundPythonType:
        resolved = tuple(self.resolve(import_) for import_ in value.imports)
        names = {
            (original.from_, original.import_, original.binding_name): actual
            for original, actual in zip(value.imports, resolved, strict=True)
        }
        modules = {
            original.import_: actual
            for original, actual in zip(value.imports, resolved, strict=True)
            if original.from_ is None
        }

        def leaf(expression: PythonTypeExpr) -> PythonTypeExpr:
            match expression:
                case PythonTypeBoundName(name, module, symbol):
                    if (actual := names.get((module, symbol, name))) is not None:
                        return PythonTypeBoundName(actual.binding_name, actual.from_, actual.import_)
                case PythonTypeRuntimeSymbol(module, parts):
                    if (actual := modules.get(module)) is not None:
                        return PythonTypeRuntimeSymbol(
                            f"{actual.from_}.{actual.import_}" if actual.from_ else actual.import_, parts
                        )
                case _:
                    pass
            return expression

        return BoundPythonType(rewrite_python_type_expr(value.expression, leaf), resolved)

    def _type(  # ruff: ignore[too-many-return-statements] -- Preserve each finite immutable type alternative.
        self, value: FinalPythonType
    ) -> FinalPythonType:
        match value:
            case ImportedType(import_, suffix):
                return ImportedType(self.resolve(import_), suffix)
            case BoundType(bound):
                return BoundType(self._bound(bound))
            case GenericType(base, arguments, tuple_form):
                return GenericType(self._type(base), tuple(self._type(item) for item in arguments), tuple_form)
            case UnionType(members, preserve_order):
                return UnionType(tuple(self._type(item) for item in members), preserve_order)
            case ConstructorType(callable_, keywords):
                callee = (
                    ImportedType(self.resolve(callable_.import_), callable_.qualified_suffix)
                    if isinstance(callable_, ImportedType)
                    else BoundType(self._bound(callable_.binding))
                )
                return ConstructorType(callee, tuple((name, self._argument(item)) for name, item in keywords))
            case AnnotatedType(base, metadata):
                return AnnotatedType(
                    self._type(base),
                    tuple(
                        MetadataCall(
                            self.resolve(call.import_),
                            tuple((name, self._argument(item)) for name, item in call.keywords),
                        )
                        for call in metadata
                    ),
                )
            case _:
                pass
        return value


if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from datamodel_code_generator._generation_contract import FinalPythonType, TypeArgument
    from datamodel_code_generator._python_type_annotation import PythonTypeExpr
    from datamodel_code_generator.imports import Import
