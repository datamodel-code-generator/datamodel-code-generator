"""Pure projection of captured type recipes without evaluating graph getters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._binding_literals import UnsupportedBindingValueError, freeze_argument
from datamodel_code_generator._generation_contract import (
    AnnotatedType,
    BindingCaptureError,
    BoundType,
    BuiltinType,
    ConstructorType,
    GeneratedSymbolType,
    GenericType,
    ImportedType,
    LiteralScalar,
    LiteralType,
    MetadataCall,
    NoneType,
    TypeProjection,
    UnionType,
)
from datamodel_code_generator.imports import IMPORT_ANY, Import

if TYPE_CHECKING:
    from collections.abc import Mapping

    from datamodel_code_generator._generation_contract import (
        FinalPythonType,
        GeneratedEnumMember,
        GraphObjectId,
        SymbolId,
        TypeProjectionReason,
    )
    from datamodel_code_generator.parser.openapi_contract import PreexistingNullObservation
    from datamodel_code_generator.parser.openapi_contract_store import TypeRecipe


_TYPE_UNSUPPORTED: Final = "BND_TYPE_EXPRESSION_UNSUPPORTED"
_SYMBOL_NOT_EMITTED: Final = "BND_SYMBOL_NOT_EMITTED"


class _UnsupportedTypeError(Exception):
    """Carry a value diagnostic without marking an ordinary parse attempt as failed."""

    def __init__(
        self,
        reason: TypeProjectionReason,
    ) -> None:
        self.reason: TypeProjectionReason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class ReferenceTypeBinding:
    """Supply final symbol policy as values rather than model getter callbacks."""

    symbol: SymbolId
    nullable: bool
    is_alias: bool
    serialize_as_any: bool


def _builtin_type(name: str) -> BuiltinType:
    match name:
        case (
            "bool"
            | "bytes"
            | "complex"
            | "float"
            | "int"
            | "str"
            | "object"
            | "list"
            | "set"
            | "frozenset"
            | "dict"
            | "tuple"
        ):
            return BuiltinType(name)
        case _:
            raise _UnsupportedTypeError(_TYPE_UNSUPPORTED)


def _project_atom(atom: str) -> FinalPythonType:
    match atom:
        case "None":
            return NoneType()
        case "Any":
            return ImportedType(IMPORT_ANY)
        case _:
            return _builtin_type(atom)


def _imported_type(import_: Import, atom: str | None) -> ImportedType:
    binding = import_.alias or (import_.import_.partition(".")[0] if import_.from_ is None else import_.import_)
    if atom is None or atom in {binding, import_.import_}:
        return ImportedType(import_)
    suffix = tuple(atom[len(binding) + 1 :].split("."))
    if atom.startswith(binding + ".") and all(part.isidentifier() for part in suffix):
        return ImportedType(import_, suffix)
    raise _UnsupportedTypeError(_TYPE_UNSUPPORTED)


def _ordered_union(members: tuple[FinalPythonType, ...], *, preserve_order: bool) -> FinalPythonType:
    flattened = tuple(
        member for value in members for member in (value.members if isinstance(value, UnionType) else (value,))
    )
    unique = tuple(dict.fromkeys(flattened))
    if len(unique) == 1:
        return unique[0]
    if not unique:
        raise _UnsupportedTypeError(_TYPE_UNSUPPORTED)
    return UnionType(unique, preserve_order)


class FinalTypeProjector:
    """Read finite recipes and final identities, preserving imports and type structure."""

    def __init__(
        self,
        references: Mapping[GraphObjectId, ReferenceTypeBinding],
        enum_members: Mapping[GraphObjectId, tuple[GeneratedEnumMember, ...]],
    ) -> None:
        """Borrow only value mappings established from actual final reference ownership."""
        self._references = references
        self._enum_members = enum_members
        self._active: set[GraphObjectId] = set()

    def project(self, recipe: TypeRecipe) -> TypeProjection:
        """Return a finite unsupported-type diagnostic without changing model acceptance."""
        try:
            return TypeProjection(self._project(recipe))
        except (_UnsupportedTypeError, UnsupportedBindingValueError) as error:
            return TypeProjection(None, error.reason)

    def preexisting_null(self, observation: PreexistingNullObservation) -> bool | None:
        """Resolve only captured top-level null evidence against final reference policy."""
        if observation.explicit:
            return True
        unknown = observation.opaque
        for reference in observation.references:
            if (binding := self._references.get(reference)) is None:
                unknown = True
            elif binding.nullable:
                if not binding.is_alias:
                    return True
                unknown = True
        return None if unknown else False

    def _project(self, recipe: TypeRecipe) -> FinalPythonType:
        if recipe.node in self._active:
            msg = "A final type recipe contains a structural cycle"
            raise BindingCaptureError(msg)
        self._active.add(recipe.node)
        try:
            projected, inferred_optional = self._project_base(recipe)
            projected = self._wrap_container(recipe, projected)
            reference = self._references.get(recipe.reference) if recipe.reference is not None else None
            nullable_reference = reference is not None and reference.nullable and not reference.is_alias
            if (
                "optional" in recipe.modifiers or inferred_optional or nullable_reference
            ) and projected != ImportedType(IMPORT_ANY):
                return _ordered_union(
                    (projected, NoneType()), preserve_order="preserve_union_order" in recipe.modifiers
                )
            return projected
        finally:
            self._active.remove(recipe.node)

    def _project_base(self, recipe: TypeRecipe) -> tuple[FinalPythonType | None, bool]:
        if recipe.bound is not None:
            return BoundType(recipe.bound), False
        if recipe.atom is not None:
            return self._project_atomic(recipe), False
        if recipe.children or "tuple" in recipe.modifiers:
            return self._project_structural(recipe)
        if recipe.reference is not None and not (recipe.enum_members or recipe.literals):
            return self._project_reference(recipe.reference, recipe), False
        return self._project_atomic(recipe), False

    def _project_reference(self, reference: GraphObjectId, recipe: TypeRecipe) -> FinalPythonType:
        if (binding := self._references.get(reference)) is None:
            raise _UnsupportedTypeError(_SYMBOL_NOT_EMITTED)
        result: FinalPythonType = GeneratedSymbolType(binding.symbol)
        if "serialize_as_any" in recipe.modifiers and binding.serialize_as_any and recipe.alias is None:
            result = GenericType(ImportedType(Import(import_="SerializeAsAny", from_="pydantic")), (result,))
        return result

    def _project_atomic(self, recipe: TypeRecipe) -> FinalPythonType | None:
        if recipe.enum_members:
            if (members := self._enum_members.get(recipe.node)) is None:
                raise _UnsupportedTypeError(_TYPE_UNSUPPORTED)
            return LiteralType(members)
        if recipe.literals:
            return LiteralType(tuple(_freeze_literal_atom(value=value) for value in recipe.literals))
        if recipe.import_ is not None:
            imported = _imported_type(recipe.import_, recipe.atom)
            if "function" in recipe.modifiers and recipe.kwargs:
                return ConstructorType(imported, tuple((name, freeze_argument(value)) for name, value in recipe.kwargs))
            return imported
        if recipe.atom is not None:
            return _project_atom(recipe.atom)
        return None

    def _project_structural(self, recipe: TypeRecipe) -> tuple[FinalPythonType, bool]:
        if "tuple" in recipe.modifiers:
            if recipe.children and recipe.children[-1].atom == "...":
                return (
                    GenericType(
                        BuiltinType("tuple"), tuple(self._project(child) for child in recipe.children[:-1]), "ellipsis"
                    ),
                    False,
                )
            arguments = tuple(self._project(child) for child in recipe.children)
            if recipe.tuple_item_count is not None:
                arguments = (arguments[0] if arguments else ImportedType(IMPORT_ANY),) * recipe.tuple_item_count
            return GenericType(BuiltinType("tuple"), arguments, "fixed"), False
        if len(recipe.children) == 1:
            return self._project(recipe.children[0]), False
        preserve_order = "preserve_union_order" in recipe.modifiers
        members = tuple(self._project(child) for child in recipe.children)
        inferred_optional = False
        if not preserve_order:
            flattened = tuple(
                member for value in members for member in (value.members if isinstance(value, UnionType) else (value,))
            )
            members = tuple(member for member in flattened if not isinstance(member, NoneType))
            inferred_optional = len(members) != len(flattened)
        union = _ordered_union(members, preserve_order=preserve_order) if members else ImportedType(IMPORT_ANY)
        if recipe.discriminator is not None:
            return (
                AnnotatedType(
                    union,
                    (
                        MetadataCall(
                            Import(import_="Field", from_="pydantic"),
                            (("discriminator", LiteralScalar("str", recipe.discriminator)),),
                        ),
                    ),
                ),
                inferred_optional,
            )
        return union, inferred_optional

    def _wrap_container(self, recipe: TypeRecipe, value: FinalPythonType | None) -> FinalPythonType:
        for modifier in ("frozen_set", "set", "sequence", "list", "mapping", "dict"):
            if modifier not in recipe.modifiers:
                continue
            generic = "generic_container" in recipe.modifiers
            container_module = "collections.abc" if "standard_collections" in recipe.modifiers else "typing"
            match modifier:
                case "frozen_set":
                    base: FinalPythonType = BuiltinType("frozenset")
                case "set":
                    base = BuiltinType("frozenset" if generic else "set")
                case "sequence" | "list" if modifier == "sequence" or generic:
                    base = ImportedType(Import(import_="Sequence", from_=container_module))
                case "list":
                    base = BuiltinType("list")
                case "mapping" | "dict" if modifier == "mapping" or generic:
                    base = ImportedType(Import(import_="Mapping", from_=container_module))
                case "dict":
                    base = BuiltinType("dict")
                case _:
                    raise _UnsupportedTypeError(_TYPE_UNSUPPORTED)
            if modifier in {"mapping", "dict"} and (recipe.dict_key is not None or value is not None):
                key = self._project(recipe.dict_key) if recipe.dict_key is not None else BuiltinType("str")
                return GenericType(base, (key, value if value is not None else ImportedType(IMPORT_ANY)))
            return GenericType(base, (value,) if value is not None else ())
        if value is None:
            raise _UnsupportedTypeError(_TYPE_UNSUPPORTED)
        return value


def _freeze_literal_atom(*, value: bool | int | str) -> LiteralScalar:
    """Project the finite builtin DataType literal domain without equality coercion."""
    match value:
        case bool():
            return LiteralScalar("bool", value)
        case int():
            return LiteralScalar("int", value)
        case str():
            return LiteralScalar("str", value)
