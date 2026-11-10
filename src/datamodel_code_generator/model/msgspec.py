"""msgspec.Struct model generator.

Generates Python models using msgspec.Struct for high-performance serialization.
"""

from __future__ import annotations

from ast import literal_eval
from fractions import Fraction
from functools import partial, wraps
from operator import is_not, itemgetter
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Literal, NamedTuple, Optional, TypeVar, cast

from typing_extensions import Sentinel, final, override

from datamodel_code_generator import Error
from datamodel_code_generator import _constraint_applicability as _applicability
from datamodel_code_generator._container_kinds import ARRAYS, MAPPINGS
from datamodel_code_generator._document_reader import as_mapping
from datamodel_code_generator._graph import reach
from datamodel_code_generator._graph_links import clear_parents, link_members
from datamodel_code_generator._mro import resolve_members
from datamodel_code_generator._python_syntax import holds_lone_surrogate, keyword_argument, subscript_text
from datamodel_code_generator._records import record
from datamodel_code_generator._reference_graph import EdgeKind, GraphSnapshot, walk_graph
from datamodel_code_generator._target_unicode import is_identifier
from datamodel_code_generator.imports import IMPORT_UNION, Import
from datamodel_code_generator.model import DataModel, DataModelFieldBase, _rebuild_model_with_datamodel_namespace
from datamodel_code_generator.model._constraints import PatternConstraints as _Constraints
from datamodel_code_generator.model._decisions.assignment import (
    MsgspecWritten,
    requires_value,
    struct_assigned,
    struct_construction_assigned,
    written_data,
)
from datamodel_code_generator.model._decisions.literal_default import decide_literal_default
from datamodel_code_generator.model._decisions.tag_value import (
    struct_discriminator_tag,
    struct_tag_options,
    struct_tag_refused,
    struct_union_refusal,
)
from datamodel_code_generator.model._decisions.union_assembly import join_union
from datamodel_code_generator.model._render_plan import (
    ANNOTATED_ANNOTATION,
    BARE_DEFAULT,
    DEFAULT_VALUE,
    EXPLICIT_ALIAS,
    MUTABLE_FACTORY,
    NESTED_MODEL_FACTORY,
    PLAIN_ANNOTATION,
    REQUIRED_FIELD,
    TYPE_OVERRIDE,
    UNTAKEN_BY_TYPE,
    CallArguments,
    CallPlan,
    FieldDefault,
    FieldRenderPlan,
    Kwarg,
    Omitted,
    RuleId,
    Why,
    _FieldCallWriter,
    written_argument_names,
    written_text,
)
from datamodel_code_generator.model.base import (
    UNDEFINED,
    BaseClassDataType,
    BaseLayoutView,
    _apply_literal_default,
    _default_factory_type_name,
    _default_state,
    _field_source_name,
    _find_base_classes,
    _forbids_unknown_fields_marked,
    _has_opaque_custom_base,
    _keeps_stripped_place,
    _model_key_of_model,
    _ModelKeys,
    _nested_model_factory,
    _NestedModelFactory,
    _normalized_constraint_kwargs,
    _ValueFactory,
    linearize_data_models,
)
from datamodel_code_generator.model.field_name import MsgspecFieldNameResolver
from datamodel_code_generator.model.imports import (
    IMPORT_MSGSPEC_CONVERT,
    IMPORT_MSGSPEC_FIELD,
    IMPORT_MSGSPEC_META,
    IMPORT_MSGSPEC_STRUCT,
    IMPORT_MSGSPEC_UNSET,
    IMPORT_MSGSPEC_UNSETTYPE,
)
from datamodel_code_generator.model.type_alias import TypeAliasBase, _alias_value
from datamodel_code_generator.model.types import DataTypeManager as _DataTypeManager
from datamodel_code_generator.python_literal import (
    PythonCode,
    _normalized_safe_python_name,
    represent_python_value,
    represent_untrusted_python_value,
    stable_repr,
)
from datamodel_code_generator.reference import FieldNameResolver, ModelType, field_origin_of, named_schema_location
from datamodel_code_generator.types import (
    NONE,
    DataType,
    _copy_data_type,
    _is_value_literal,
    _qualified_type_path,
    _takes_json_scalar,
    build_union,
    chain_as_tuple,
    union_members,
)


class _UNSET:
    @override
    def __str__(self) -> str:
        return "UNSET"

    __repr__ = __str__


UNSET = _UNSET()


_ANNOTATED_CONSTRAINTS_CONTEXT: object = Sentinel("_ANNOTATED_CONSTRAINTS_CONTEXT")
_UNCONSTRAINED_UNION_MEMBERS_KEY = "_unconstrained_union_members"
_NO_UNCONSTRAINED_MEMBERS: tuple[frozenset[Any], ...] = (frozenset(), frozenset(), frozenset(), frozenset())
"""The kinds, keywords, integer keywords and implied constraints of a field without union members left unconstrained."""
_BOUND_SIDES: Mapping[str, str] = MappingProxyType({"ge": "ge", "gt": "ge", "le": "le", "lt": "le"})
"""The side of each ``Meta`` bound, which the integer check of a union reads at the bounds the schema writes."""


def _integer_bounds(
    constraints: tuple[tuple[str, Any], ...], arithmetic: Literal["exact", "float"]
) -> tuple[int | None, int | None]:
    """Return the least and the greatest integer that numeric constraints admit, None where they set no bound.

    Each bound is read with ``arithmetic`` (``_applicability.union_bound_arithmetic``).
    """
    lowers: list[int] = []
    uppers: list[int] = []
    for name, value in constraints:
        if (bound := _applicability.integer_bound(name, value, arithmetic=arithmetic)) is not None:
            (lowers if bound[0] == "ge" else uppers).append(bound[1])
    return max(lowers, default=None), min(uppers, default=None)


def _admits_integers_of(
    constraints: tuple[tuple[str, Any], ...],
    other: tuple[tuple[str, Any], ...],
    arithmetic: Literal["exact", "float"],
) -> bool:
    """Return whether numeric constraints admit every integer that the other numeric constraints admit.

    The integer multiples of a divisor p/q in lowest terms are the multiples of p.
    """
    lower, upper = _integer_bounds(constraints, arithmetic)
    other_lower, other_upper = _integer_bounds(other, arithmetic)
    if other_lower is not None and other_upper is not None and other_lower > other_upper:
        return True
    if lower is not None and (other_lower is None or other_lower < lower):
        return False
    if upper is not None and (other_upper is None or other_upper > upper):
        return False
    other_steps = [Fraction(str(value)).numerator for name, value in other if name == "multiple_of"]
    return all(
        step == 1 or any(not other_step % step for other_step in other_steps)
        for step in (Fraction(str(value)).numerator for name, value in constraints if name == "multiple_of")
    )


_LOOSEST_LENGTHS: Mapping[str, tuple[Callable[[Iterable[Any]], Any], Callable[[Iterable[Any]], Any]]] = (
    MappingProxyType({"min_length": (min, max), "max_length": (max, min)})
)
"""The length keywords of ``Meta`` with the functions that pick the loosest and the tightest of several values."""
_ROUTED_KINDS: Mapping[str, tuple[str, ...]] = MappingProxyType({"float": ("int", "Decimal")})
"""The other kinds whose values msgspec decodes as a kind: a JSON float such as ``-1.0``, valid for an integer member,
goes to the float member, and so does every number a Decimal member, which takes no ``Meta`` bound, admits."""


def _meta_keywords_text(constraints: Iterable[tuple[str, Any]]) -> str:
    """Return the ``Meta`` keyword arguments of ``constraints`` as written, sorted by their text."""
    return ", ".join(sorted(keyword_argument(name, represent_python_value(value)) for name, value in constraints))


def _bound_tightness(constraint: tuple[str, Any]) -> tuple[Any, bool]:
    """Return how tight a ``Meta`` bound is, greater when tighter: its value toward the inside, then exclusive first."""
    name, value = constraint
    return (value if _BOUND_SIDES[name] == "ge" else -value, name in {"gt", "lt"})


def _loosest_shared(constraint_sets: Sequence[tuple[tuple[str, Any], ...]]) -> tuple[tuple[str, Any], ...]:
    """Return the ``Meta`` constraints that every one of ``constraint_sets`` implies, as loose as the loosest set.

    A side that every set bounds keeps the loosest of their bounds (``ge`` over ``gt`` at one value), ``min_length``
    the least and ``max_length`` the greatest where every set writes one, a set that writes one several times by the
    tightest of them, and any other keyword the value every set writes alike. A set without a keyword leaves it out.
    """
    kept: list[tuple[str, Any]] = []
    for side in ("ge", "le"):
        bounds = [
            max((c for c in constraints if _BOUND_SIDES.get(c[0]) == side), key=_bound_tightness, default=None)
            for constraints in constraint_sets
        ]
        if None not in bounds:
            kept.append(min(cast("list[tuple[str, Any]]", bounds), key=_bound_tightness))
    for name, (loosest, tightest) in _LOOSEST_LENGTHS.items():
        if all(values := [[value for key, value in constraints if key == name] for constraints in constraint_sets]):
            kept.append((name, loosest(map(tightest, values))))
    first, *others = constraint_sets
    kept.extend(
        constraint
        for constraint in first
        if constraint[0] not in _BOUND_SIDES
        and constraint[0] not in _LOOSEST_LENGTHS
        and all(constraint in constraints for constraints in others)
    )
    return tuple(kept)


_CLASS_VAR = Why(RuleId("D7.tag.class-var"), keyword="discriminator")
"""A tag field of a tagged Struct: msgspec writes the tag itself, and the built-in template skips the field."""
_FIELD_CALL = Why(RuleId("D11.msgspec.field-call"))
"""The field writes ``field(...)``: a name, a default factory, or a default beside them needs one."""
_META_CALL = Why(RuleId("D11.msgspec.meta"), option="--use-annotated")
_FIELD_ARGUMENT = RuleId("D11.msgspec.field-argument")
_META_ARGUMENT = RuleId("D11.msgspec.meta-argument")
_UNSET_DEFAULT = Why(RuleId("D4.default.msgspec-unset"), keyword="required")
"""An optional field without a default writes ``UNSET``, or ``None`` when it is nullable: a Struct always writes one."""
_NONE_DEFAULT = Why(RuleId("D4.default.msgspec-none"), keyword="nullable")
_STRUCT_CONVERSION = Why(RuleId("D4.default.msgspec-convert"), keyword="default")
"""A default holding Structs is written as a factory that converts it with ``msgspec.convert``."""
_IMPORT_SIMPLE_NAMESPACE = Import.from_full_path("types.SimpleNamespace")
"""The object a default factory converts from its attributes to a Struct that msgspec reads from an array."""
_CONSTRAINT = RuleId("D2.msgspec.constraint")
_NON_FINITE = RuleId("D2.msgspec.non-finite-bound")
_MULTIPLE_OF_ONE = RuleId("D2.msgspec.integer-multiple-of-one")
_STRONGER_BOUND = RuleId("D2.msgspec.stronger-bound")
_ITEMS_AS_LENGTH = RuleId("D2.msgspec.items-as-length")
_LITERAL_DECIDED = RuleId("D2.msgspec.literal-decided")
"""The values of the field's ``Literal`` decide the constraint (``_applicability.literal_check``)."""
_INEXACT = RuleId("D2.msgspec.inexact-float-divisor")
_INT64_LIMIT = RuleId("D2.msgspec.int64-limit")
"""msgspec takes the number constraints of an int only within the 64-bit range (``MSGSPEC_OUTPUT.int_values``)."""
_NONE_VALUE = Why(RuleId("D11.msgspec.none-unset"))
_NEEDS_ANNOTATED = Why(RuleId("D11.msgspec.meta-needs-annotated"), option="--use-annotated")


def _renamed_field_name_refused(name: str) -> bool:
    """Return whether msgspec refuses a renamed field name.

    It refuses one that holds a backslash, a double quote, a control character or a lone surrogate.
    """
    return any(char in '\\"' or char < " " for char in name) or holds_lone_surrogate(name)


@final
@record(eq=False)
class _StructConversion:
    """The default factory that converts a default holding Structs: ``lambda: convert({...}, type=Model)``.

    ``of_list`` converts to a list of ``target``; ``container`` is the type, holding ``target``, that it converts to as
    its hint writes it, a quoted reference named directly. Its text names the models as the module finally names them.
    A default that holds an object for a Struct msgspec reads from an array is converted from attributes
    (``from_attributes``, ``_attribute_default``).
    """

    method: str
    default: object
    target: DataType
    container: DataType | None = None
    of_list: bool = False

    @property
    def _converted(self) -> object:
        """Return the default as the factory converts it, its objects for array_like Structs as their attributes."""
        if self.of_list and isinstance(self.default, list):
            items = [_attribute_default(self.target, item) for item in self.default]
            return items if any(map(is_not, items, self.default)) else self.default
        return _attribute_default(self.container or self.target, self.default)

    @property
    def from_attributes(self) -> bool:
        """Return whether the factory converts an object of the default from its attributes."""
        return self._converted is not self.default

    @override
    def __str__(self) -> str:
        """Return the factory as the field's call writes it."""
        if self.of_list:
            type_text = subscript_text("list", (_default_factory_type_name(self.target),))
        elif self.container is not None:
            type_text = self.container.type_hint
            for nested in self.container.all_data_types:
                if nested._is_quoted_reference:  # noqa: SLF001
                    type_text = type_text.replace(cast("str", nested.alias), _default_factory_type_name(nested))
        else:
            type_text = _default_factory_type_name(self.target)
        converted = self._converted
        options = "" if converted is self.default else ", from_attributes=True"
        return f"lambda: {self.method}({represent_python_value(converted)}, type={type_text}{options})"


def _meta_call_text(call: CallPlan) -> str:
    """Write a ``Meta(...)`` call: its arguments sorted by their text."""
    return f"Meta({_meta_keywords_text((k.name, k.value) for k in call.kwargs)})"


class _MetaAnnotatedType(DataType):
    """The ``Annotated`` type of a msgspec field: a copy of the field's type, its one member, with its ``Meta()`` call.

    It writes its text when it is read, so a field's render plan holds it with the names inside it written as they are
    then; import collection reads the typing names of that text.
    """

    meta: Any = None

    @property
    @override
    def type_hint(self) -> str:
        """Write ``Annotated`` over the hint of the copied type, ``None`` when it writes nothing, and the ``Meta()``."""
        return subscript_text("Annotated", (self.data_types[0].type_hint or NONE, _meta_call_text(self.meta)))

    @property
    @override
    def _annotation_texts(self) -> tuple[str | None, ...]:
        return (self.type_hint,)


class _MetaType(DataType):
    """A type that msgspec checks by the keywords of its ``Meta()`` call: ``Annotated[str, Meta(...)]``.

    A string dict key under --use-msgspec-meta-for-map-keys, and the member of a union that writes the constraints
    every member of its type implies.
    """

    meta_keywords: Any = None

    @property
    @override
    def type_hint(self) -> str:
        """Write ``Annotated`` over the type and the ``Meta()`` call, its arguments sorted by their text."""
        return subscript_text(
            "Annotated", (super().type_hint, f"Meta({_meta_keywords_text(self.meta_keywords.items())})")
        )

    @property
    @override
    def _annotation_texts(self) -> tuple[str | None, ...]:
        return (self.type_hint,)

    @property
    @override
    def imports(self) -> Iterator[Import]:
        """Yield ``Meta`` too; the typing name ``Annotated`` is read from the hint."""
        yield from super().imports
        yield IMPORT_MSGSPEC_META


_META_KEY_KEYWORDS: Mapping[str, str] = MappingProxyType({
    "pattern": "pattern",
    "minLength": "min_length",
    "maxLength": "max_length",
})
"""The ``Meta`` keyword of each key constraint of a propertyNames or patternProperties map."""


class _UnionLeaf(NamedTuple):
    """A union member msgspec decodes as one type: the alias members spread to reach it, its type and constraints.

    ``schema_constraints`` are its ``Meta`` constraints with each bound read as the schema bounds it comes from
    (``DataModelField._union_meta_constraints``). A member written without its constraints may be written with
    ``meta`` instead, the ones every member of its type implies, or left out (``covered``) beside the member that is.
    """

    spread: tuple[tuple[DataType, DataModelField], ...]
    member: DataType
    kind: str
    base: DataType
    constraints: tuple[tuple[str, Any], ...]
    schema_constraints: tuple[tuple[str, Any], ...]
    meta: tuple[tuple[str, Any], ...] = ()
    covered: bool = False

    def read(self, arithmetic: Literal["exact", "float"]) -> tuple[tuple[str, Any], ...]:
        """Return the constraints the integer check reads: the schema's bounds when it reads them ``exact``."""
        return self.schema_constraints if arithmetic == "exact" else self.constraints

    @property
    def keywords(self) -> frozenset[str]:
        """Return the Meta keywords of the member."""
        return frozenset(name for name, _ in self.constraints)


class _LeafRead(NamedTuple):
    """A union member ``_union_leaves`` reads, with the alias members spread to reach it, constraints and aliases."""

    member: DataType
    spread: tuple[tuple[DataType, DataModelField], ...]
    constraints: tuple[tuple[str, Any], ...]
    seen: frozenset[int]
    schema_constraints: tuple[tuple[str, Any], ...]


if TYPE_CHECKING:
    from collections import defaultdict
    from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
    from pathlib import Path

    from datamodel_code_generator._constraint_applicability import ConstraintOptions
    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator.model._decisions.assignment import AssignmentRow
    from datamodel_code_generator.model._decisions.hashability import SetItemHash
    from datamodel_code_generator.model._render_plan import OmitReason
    from datamodel_code_generator.reference import Reference
    from datamodel_code_generator.types import UnionIntFloat


def _takes_default(  # noqa: PLR0911
    data_type: DataType, default: object, seen: frozenset[int] = frozenset(), *, attributes: bool = False
) -> bool:
    """Return whether ``msgspec.convert`` takes a default for a type, by the JSON kind of the default at every level.

    A Struct takes an object that holds the Struct's tag where it holds its tag field, or, where msgspec reads it from
    an array (``_reads_arrays``), an array that starts with its tag where it has one, and with ``attributes`` also such
    an object, which the factory converts from its attributes (``_attribute_default``); a type alias, what the type it
    names takes besides itself (``seen`` holds the aliases followed since the last value read), and None where it
    writes None; a union, what one of its members takes; an array type, an array (or the set a default writes for one)
    whose items its item types take; a mapping, an object whose values its value types take; a Literal, its values;
    str, int, float, bool and None, values of their JSON kind. An optional type takes None too, and any other type any
    value.
    """
    if default is None and data_type.is_optional:
        return True
    if (reference := data_type.reference) is not None:
        if isinstance(source := reference.source, TypeAliasBase):
            return (
                bool(fields := source.fields)
                and id(source) not in seen
                and (
                    (
                        default is None
                        and isinstance(alias := fields[0], DataModelField)
                        and alias._field_has_top_level_none()  # noqa: SLF001
                    )
                    or _takes_default(fields[0].data_type, default, seen | {id(source)}, attributes=attributes)
                )
            )
        return not isinstance(source, Struct) or _struct_takes_default(source, default, attributes=attributes)
    if (array := data_type in ARRAYS) or data_type in MAPPINGS:
        values: Iterable[object] = ()
        match default:
            case list() | set() | frozenset() if array:
                values = default
            case dict() if not array:
                values = default.values()
            case _:
                return False
        return not data_type.data_types or all(
            any(_takes_default(item, value, attributes=attributes) for item in data_type.data_types) for value in values
        )
    if data_type.data_types:
        return any(_takes_default(member, default, seen, attributes=attributes) for member in data_type.data_types)
    return _takes_json_scalar(data_type, default)


def _struct_takes_default(struct: Struct, default: object, *, attributes: bool) -> bool:
    """Return whether ``msgspec.convert`` takes a default for a Struct, by its JSON kind (``_takes_default``)."""
    tag = struct.discriminator_tag
    if _reads_arrays(struct) and not (attributes and isinstance(default, dict)):
        return isinstance(default, list) and (tag is None or (bool(default) and default[0] == tag[1]))
    return isinstance(default, dict) and (tag is None or default.get(tag[0], tag[1]) == tag[1])


def _attribute_default(data_type: DataType, value: object, seen: frozenset[int] = frozenset()) -> object:
    """Return ``value`` with each object that ``data_type`` reads as a Struct msgspec reads from an array as attributes.

    msgspec reads such a Struct (``_reads_arrays``) from an array, or, under ``from_attributes``, from the attributes of
    an object: the object is written as ``SimpleNamespace(**{...})`` (``_struct_attribute_default``). A type alias
    reads the value by the type it names (``seen`` holds the aliases followed since the last value read), a union by
    its first member that takes it (``_first_attribute_default``), an array type its items and a mapping its values by
    the first of their types that take them. A value with no such object is returned as it is, as is the value of a
    tuple, whose positions ``_takes_default`` reads as alternatives.
    """
    if (reference := data_type.reference) is not None:
        match source := reference.source:
            case TypeAliasBase() if source.fields and id(source) not in seen:
                return _attribute_default(source.fields[0].data_type, value, seen | {id(source)})
            case Struct() if isinstance(value, dict):
                return _struct_attribute_default(source, value)
        return value
    if (array := data_type in ARRAYS) or data_type in MAPPINGS:
        entries = (
            dict(enumerate(value))
            if array and not data_type.is_tuple and isinstance(value, list)
            else value
            if not array and isinstance(value, dict)
            else {}
        )
        read = {key: _first_attribute_default(data_type.data_types, item) for key, item in entries.items()}
        if all(read[key] is item for key, item in entries.items()):
            return value
        return list(read.values()) if array else read
    return _first_attribute_default(data_type.data_types, value, seen)


def _struct_attribute_default(struct: Struct, value: dict[str, object]) -> object:
    """Return an object a Struct reads as ``_attribute_default`` writes it: as attributes where it reads arrays.

    Its values are read by the types of the fields of their schema names, those of its bases included.
    """
    fields: dict[str, DataModelFieldBase] = {}
    for current in _struct_chain(struct, order="listed"):
        for field in filter(_is_instance_field, current.fields):
            fields.setdefault(cast("str", _field_source_name(field)), field)
    read = {
        key: item if (field := fields.get(key)) is None else _attribute_default(field.data_type, item)
        for key, item in value.items()
    }
    if all(read[key] is item for key, item in value.items()):
        read = value
    return PythonCode(f"SimpleNamespace(**{represent_python_value(read)})") if _reads_arrays(struct) else read


def _default_takers(members: Sequence[DataType], value: object, seen: frozenset[int] = frozenset()) -> list[DataType]:
    """Return the members that take ``value`` as msgspec reads JSON, else, where none does, from attributes.

    msgspec reads an object as a member that reads objects, so only where no member takes the value as JSON is it read
    as the attributes of a Struct msgspec reads from an array (``_takes_default``).
    """
    for attributes in (False, True):
        if takers := [member for member in members if _takes_default(member, value, seen, attributes=attributes)]:
            return takers
    return []


def _first_attribute_default(members: Sequence[DataType], value: object, seen: frozenset[int] = frozenset()) -> object:
    """Return ``value`` as the first member of ``members`` that takes it (``_default_takers``) reads it, else as it is.

    The member reads it as ``_attribute_default`` does.
    """
    return _attribute_default(takers[0], value, seen) if (takers := _default_takers(members, value, seen)) else value


def _reads_arrays(struct: Struct) -> bool:
    """Return whether msgspec reads a struct from an array, not an object (``array_like``).

    The option is that of the first struct that sets it in the chain of order ``listed`` (``_struct_chain``).
    """
    return _first_struct_flag(
        map(_user_struct_options, _struct_chain(struct, order="listed")), "array_like", default=False
    )


def _struct_chain(struct: Struct, *, order: Literal["listed", "graph"]) -> list[Struct]:
    """Return the struct and its generated bases, each once before its bases, in ``listed`` or ``graph`` order."""
    if order == "listed":
        return reach((struct,), lambda current: reversed(_struct_bases(current)), key=id)
    walk = walk_graph(GraphSnapshot.SET_ITEM_CLASSES, EdgeKind.BASE, (struct,), _model_key_of_model, _struct_bases)
    return [walk.node_of[key] for key in walk.graph.preorder(walk.starts[0], EdgeKind.BASE)]


def _first_struct_flag(options: Iterable[Mapping[str, Any]], name: str, *, default: bool) -> bool:
    """Return whether the first of the struct options ``options`` that sets ``name`` turns it on, else ``default``."""
    return next((value in {True, "True"} for option in options if (value := option.get(name)) is not None), default)


def _names_structs(data_type: DataType, aliases: frozenset[int], *, containers: bool = True) -> bool:
    """Return whether a default converts to a type: whether it names a Struct, also through the type aliases it names.

    A type that holds a union of a mapping beside other members, or of Structs msgspec refuses (``_refused_structs``),
    converts to none: msgspec takes neither, and the default stays the value, as beside a dict member of the field's
    union. The positions of a tuple, which a type alias holds, are no union, so msgspec refuses none of their Structs,
    but a mapping among them converts it to none too, as the default reads them as alternatives (``_takes_default``).
    ``aliases`` holds the aliases, by id, whose values are read already. Without ``containers`` the items and values
    of arrays and mappings are left out, as an empty default holds none.
    """
    pending, followed, found = [data_type], set(aliases), False
    while pending:
        if (reference := (current := pending.pop()).reference) is not None:
            if isinstance(reference.source, Struct):
                found = True
            elif (aliased := _alias_value(current)) is not None and id(aliased[0]) not in followed:
                followed.add(id(aliased[0]))
                pending.append(aliased[1].data_type)
        elif containers or not (current in ARRAYS or current in MAPPINGS):
            if len(members := current.data_types) > 1 and (
                any(member in MAPPINGS for member in members) or (not current.is_tuple and _refused_structs(members))
            ):
                return False
            pending.extend(members)
    return found


def _refused_structs(members: Sequence[DataType]) -> bool:
    """Return whether msgspec refuses the Structs that union members hold as a type (``struct_union_refusal``).

    msgspec flattens the unions and the type aliases among the members, so their Structs are members too
    (``_union_leaves``).
    """
    structs = {
        id(source): source
        for leaf in reach(members, _union_leaves, key=id)
        if leaf.reference is not None
        and isinstance(source := leaf.reference.source, DataModel)
        and source.REQUIRES_TAGGED_UNION_DISCRIMINATOR
    }
    return struct_union_refusal([struct.discriminator_tag for struct in structs.values()]) is not None


def _union_leaves(data_type: DataType) -> Sequence[DataType]:
    """Return what a union member spreads to where msgspec flattens it: a union its members, an alias its value."""
    if members := union_members(data_type):
        return members
    if (aliased := _alias_value(data_type)) is not None:
        return (aliased[1].data_type,)
    return ()


def _is_instance_field(field: DataModelFieldBase) -> bool:
    """Return whether a field takes a slot of its struct, unlike the class variable a msgspec tag field becomes."""
    return not field.is_class_var


def _same_model(model: DataModel) -> DataModel:
    """Return the model itself as the model whose layout it has."""
    return model


def _is_never_opaque(model: DataModel) -> bool:  # noqa: ARG001
    """Return that only a configured base, which the layout check finds itself, hides the slots of a model."""
    return False


_FINAL_LAYOUT_VIEW = BaseLayoutView(_is_instance_field, _same_model, _is_never_opaque)


def _user_struct_option_items(model: DataModel) -> Iterable[tuple[str, Any]]:
    return _user_struct_options(model).items()


def _user_struct_options(model: DataModel) -> dict[str, Any]:
    """Return the struct options that template data gives a model, which builtin templates read only from a dict."""
    return as_mapping(model.extra_template_data.get("base_class_kwargs"))


def has_field_assignment(field: DataModelFieldBase) -> bool:
    """Return whether a msgspec field renders with a default assignment.

    A Struct writes a default for every optional field, ``UNSET`` or ``None``, with ``--strip-default-none`` too.
    """
    return struct_assigned(required=requires_value(field))


def _construction_order_key(field: DataModelFieldBase) -> bool:
    """Return whether a Struct is built with the field among the fields with a default.

    Under ``--strip-default-none`` a field whose ``None`` default is stripped keeps its schema place among the fields
    without one, unless ``--order-constructor-fields-by written-defaults`` is given.
    """
    return struct_construction_assigned(
        required=requires_value(field),
        keeps_stripped_place=_keeps_stripped_place(field),
    )


def get_field_default_info(field: DataModelFieldBase) -> tuple[bool, bool]:
    """Return msgspec constructor-default semantics."""
    return field._get_constructor_default_info()  # noqa: SLF001  # output-owned field policy hook


DataModelFieldBaseT = TypeVar("DataModelFieldBaseT", bound=DataModelFieldBase)


def import_extender(cls: type[DataModelFieldBaseT]) -> type[DataModelFieldBaseT]:
    """Extend imports property with msgspec-specific imports."""
    original_getter: Callable[[DataModelFieldBaseT], tuple[Import, ...]] = cast("Any", cls.imports.fget)

    @wraps(original_getter)
    def new_imports(self: DataModelFieldBaseT) -> tuple[Import, ...]:
        if self.is_class_var:
            return ()
        extra_imports: tuple[Import, ...] = ()
        if isinstance(self, DataModelField):
            extra_imports = tuple(import_ for import_, _ in self._render_plan().symbol_imports)
        imports = original_getter(self)
        return chain_as_tuple(imports, extra_imports)

    cls.imports = property(new_imports)  # ty: ignore[invalid-assignment]
    return cls


class Struct(DataModel):
    """DataModel implementation for msgspec.Struct."""

    TEMPLATE_FILE_PATH: ClassVar[str] = "msgspec.jinja2"
    _FREEZES_FIELD_RENDER_PLANS: ClassVar[bool] = True
    RESOLVES_STRING_ANNOTATIONS_FROM_MODULE: ClassVar[bool] = True
    WRITES_QUOTED_NAMES_IN_TYPING_GENERICS: ClassVar[bool] = True
    BASE_CLASS: ClassVar[str] = "msgspec.Struct"
    BASE_CLASS_NAME: ClassVar[str] = "Struct"
    BASE_CLASS_ALIAS: ClassVar[str] = "_Struct"
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = ()
    FIELD_ASSIGNMENT_CHECKER = staticmethod(has_field_assignment)
    FIELD_CONSTRUCTION_ORDER_KEY = staticmethod(_construction_order_key)
    FIELD_DEFAULT_CLASSIFIER = staticmethod(get_field_default_info)
    _ASSIGNMENT_ROW: ClassVar[AssignmentRow | None] = "msgspec"
    FIELD_NAME_MODEL_TYPE: ClassVar[ModelType] = ModelType.MSGSPEC
    FIELD_NAME_RESOLVER_CLASS: ClassVar[type[FieldNameResolver]] = MsgspecFieldNameResolver
    SUPPORTS_DISCRIMINATOR: ClassVar[bool] = True
    SUPPORTS_INHERITED_DISCRIMINATOR_ENUM: ClassVar[bool] = True
    SUPPORTS_KW_ONLY: ClassVar[bool] = True
    REQUIRES_MODEL_LEVEL_KW_ONLY: ClassVar[bool] = True
    SUPPORTS_BOOLEAN_LITERAL: ClassVar[bool] = False
    SUPPORTS_NULL_ENUM_MEMBER: ClassVar[bool] = False
    _DECODES_MIXED_ENUM_VALUES: ClassVar[bool] = False
    _DECODES_VALUE_UNIONS: ClassVar[bool] = False
    _LITERAL_HOLDS_ENUM_MEMBERS: ClassVar[bool] = False
    _LAYERS_REPEATED_CONSTRAINTS: ClassVar[bool] = False
    _CHECKS_CONSTRAINTS: ClassVar[bool] = True
    _READ_BY_MSGSPEC: ClassVar[bool] = True
    REQUIRES_TAGGED_UNION_DISCRIMINATOR: ClassVar[bool] = True
    REQUIRES_UNIQUE_FIELD_ALIASES: ClassVar[bool] = True
    FLATTENS_CONFLICTING_BASE_LAYOUTS: ClassVar[bool] = True
    SUPPORTS_ANNOTATED_CONSTRAINTS: ClassVar[bool] = True
    ANNOTATED_CONSTRAINTS_CONTEXT: ClassVar[object | None] = _ANNOTATED_CONSTRAINTS_CONTEXT
    REQUIRES_EXPLICIT_DEFERRED_ANNOTATIONS_FOR_FORWARD_REFS: ClassVar[bool] = True
    CONFIG_MAPPING: ClassVar[dict[tuple[str, Any], tuple[str, Any] | None]] = {
        ("allow_mutation", False): ("frozen", True),
        ("extra_fields", "forbid"): ("forbid_unknown_fields", True),
        ("extra_fields", "allow"): None,
        ("extra_fields", "ignore"): None,
        ("allow_extra_fields", True): None,
        ("allow_population_by_field_name", True): None,
        ("use_attribute_docstrings", True): None,
    }

    def __init__(  # noqa: PLR0913
        self,
        *,
        reference: Reference,
        fields: list[DataModelFieldBase],
        decorators: list[str] | None = None,
        base_classes: list[Reference] | None = None,
        custom_base_class: str | list[str] | None = None,
        custom_template_dir: Path | None = None,
        extra_template_data: defaultdict[str, dict[str, Any]] | None = None,
        methods: list[str] | None = None,
        path: Path | None = None,
        description: str | None = None,
        default: Any = UNDEFINED,
        nullable: bool = False,
        keyword_only: bool = False,
        treat_dot_as_module: bool | None = None,
    ) -> None:
        """Initialize msgspec Struct with fields sorted by field assignment requirement."""
        super().__init__(
            reference=reference,
            fields=self.order_fields_for_construction(fields),
            decorators=decorators,
            base_classes=base_classes,
            custom_base_class=custom_base_class,
            custom_template_dir=custom_template_dir,
            extra_template_data=extra_template_data,
            methods=methods,
            path=path,
            description=description,
            default=default,
            nullable=nullable,
            keyword_only=keyword_only,
            treat_dot_as_module=treat_dot_as_module,
        )
        self.extra_template_data.setdefault("base_class_kwargs", {})
        self._set_internal_template_data("base_class_kwargs", {})
        if self.keyword_only:
            self.add_base_class_kwarg("kw_only", "True")
        if self._forbids_unknown_fields():
            self.add_base_class_kwarg("forbid_unknown_fields", "True")

    def _forbids_unknown_fields(self) -> bool:
        """Return whether the struct rejects unknown fields: its schema admits no property but its own.

        The parser marks such a model under --use-msgspec-forbid-unknown-fields, and every model with --extra-fields
        forbid beside it; --extra-fields allow or ignore, --allow-extra-fields and a forbid_unknown_fields of the
        struct's own options keep their choice.
        """
        data = self.extra_template_data
        return bool(
            _forbids_unknown_fields_marked(data)
            and data.get("extra_fields") not in {"allow", "ignore"}
            and not data.get("allow_extra_fields")
            and "forbid_unknown_fields" not in _user_struct_options(self)
        )

    @classmethod
    @override
    def render_module_code(cls, models: list[DataModel]) -> str:
        """Reject incompatible generated Struct layouts before rendering a module."""
        for model in models:
            if isinstance(model, cls):
                cls._validate_renamed_field_names(model)
                if len(model.base_classes) > 1:
                    cls._validate_base_layouts(model)
        return ""

    @staticmethod
    def _layout_parents(model: DataModel, view: BaseLayoutView) -> list[DataModel]:
        """Return the generated Struct bases of a model as the view of the layouts sees them."""
        return [view.layout_model(parent) for parent in _struct_bases(model)]

    @staticmethod
    def _has_unknown_layout(model: DataModel, view: BaseLayoutView) -> bool:
        """Return whether a configured base or the view hides the slots a model has."""
        return _has_opaque_custom_base(model, Struct.BASE_CLASS) or view.is_opaque(model)

    @staticmethod
    def _find_layout_conflict(
        bases: Sequence[DataModel], view: BaseLayoutView = _FINAL_LAYOUT_VIEW
    ) -> tuple[DataModel, DataModel] | None:
        """Find independent slot owners with linear storage for compatible graphs.

        Field introduction depths distinguish inherited slots from extensions.
        Encounter ranks retain the original diagnostic order independently of
        the parent-first traversal needed to establish each model's layout.
        The view tells the fields that take slots and the model whose layout a model has. Models are told apart by
        model key.
        """
        pending = [(view.layout_model(base), False) for base in bases]
        keys = _ModelKeys()
        visited: dict[ModelKey, int] = {}
        owner_depths: dict[ModelKey, int] = {}
        field_depths: dict[str | None, int] = {}
        owners: list[DataModel] = []
        while pending:
            base, complete = pending.pop()
            if not complete and keys.of(base) in visited:
                continue
            parents = Struct._layout_parents(base, view)
            if not complete:
                visited[keys.of(base)] = len(visited)
                if Struct._has_unknown_layout(base, view):
                    return None
                pending.append((base, True))
                pending.extend((parent, False) for parent in reversed(parents))
                continue
            parent_depth = max((owner_depths.get(keys.of(parent), 0) for parent in parents), default=0)
            if new_names := [
                field.name
                for field in base.fields
                if view.is_slot(field) and field_depths.get(field.name, len(owners) + 1) > parent_depth
            ]:
                if parent_depth != len(owners):
                    other = min(owners[parent_depth:], key=lambda owner: visited[keys.of(owner)])
                    return Struct._select_layout_conflict(
                        (other, base), pending, owners, owner_depths, visited, view, keys
                    )
                owners.append(base)
                for name in new_names:
                    field_depths[name] = len(owners)
                parent_depth = len(owners)
            owner_depths[keys.of(base)] = parent_depth
        return None

    @staticmethod
    def _select_layout_conflict(  # noqa: PLR0913, PLR0917
        conflict: tuple[DataModel, DataModel],
        pending: list[tuple[DataModel, bool]],
        owners: list[DataModel],
        owner_depths: dict[ModelKey, int],
        encounter_ranks: dict[ModelKey, int],
        view: BaseLayoutView,
        keys: _ModelKeys,
    ) -> tuple[DataModel, DataModel] | None:
        """Prefer an earlier active slot owner without repeating the layout proof.

        The compact witness establishes incompatibility. An active descendant
        may occur earlier in the emitted bases' preorder, or inherit an opaque
        layout that prevents diagnosing the generated class at all.
        """
        for base, complete in reversed(pending):
            if not complete:
                continue
            parents = Struct._layout_parents(base, view)
            ancestor_keys: set[ModelKey] = set()
            inherited_names: set[str | None] = set()
            parent_depth = 0
            while parents:
                parent = parents.pop()
                if (parent_key := keys.of(parent)) in ancestor_keys:
                    continue
                if Struct._has_unknown_layout(parent, view):
                    return None
                ancestor_keys.add(parent_key)
                parent_depth = max(parent_depth, owner_depths.get(parent_key, 0))
                inherited_names.update(field.name for field in parent.fields if view.is_slot(field))
                parents.extend(Struct._layout_parents(parent, view))
            if parent_depth < len(owners) and any(
                field.name not in inherited_names and view.is_slot(field) for field in base.fields
            ):
                other = min(owners[parent_depth:], key=lambda owner: encounter_ranks[keys.of(owner)])
                conflict = other, base
        return conflict

    def _generated_layout_bases(self) -> list[Struct]:
        """Return the generated Struct bases in the order the layout of the bases is diagnosed in."""
        return _struct_bases(self)[::-1]

    @override
    def flatten_conflicting_base_layouts(self, view: BaseLayoutView, flatten: Callable[[DataModel], None]) -> None:
        """Flatten the struct where msgspec cannot combine the layouts of its generated bases, as the view sees them."""
        if not self._uses_custom_root_template and self._find_layout_conflict(self._generated_layout_bases(), view):
            flatten(self)

    @override
    def adopt_base_options(self) -> None:
        """Make the struct options of the generated bases the struct's own.

        msgspec makes a struct take each option from the first class in its method resolution order that sets it; an
        option the generator wrote on that class keeps the syntax it wrote (``add_base_class_kwarg``).
        """
        options = _user_struct_options(self)
        written = self._written_struct_options
        for owner, (name, value) in resolve_members(
            linearize_data_models([self])[1:], _user_struct_option_items, itemgetter(0)
        ).values():
            if name in options:
                continue
            options[name] = value
            if isinstance(owner, Struct) and name in (owned := owner._written_struct_options):  # noqa: SLF001
                written[name] = owned[name]

    @override
    def flatten_base_layouts(
        self, adopt_field: Callable[[DataModelFieldBase, bool], DataModelFieldBase]
    ) -> list[DataModelFieldBase]:
        """Return the fields in the order the constructor needs."""
        return self.order_fields_for_construction(super().flatten_base_layouts(adopt_field))

    def _validate_base_layouts(self) -> None:
        """Allow empty mixins and shared layouts, but reject independent slot extensions."""
        if self._uses_custom_root_template:
            return
        if conflict := self._find_layout_conflict(self._generated_layout_bases()):
            other, base = conflict
            msg = (
                f"msgspec.Struct model {stable_repr(self.class_name)} has incompatible layouts from generated bases "
                f"{stable_repr(other.class_name)} and {stable_repr(base.class_name)}."
            )
            raise Error(msg)

    def _validate_renamed_field_names(self) -> None:
        """Refuse a field that renames to a name msgspec refuses (``_renamed_field_name_refused``), naming where it is.

        The generated module could not be imported: ``field(name=...)`` raises ``ValueError``, or
        ``UnicodeEncodeError`` for a lone surrogate.
        """
        for field in self.fields:
            if field.alias is not None and not field.is_class_var and _renamed_field_name_refused(field.alias):
                origin = field_origin_of(field)
                location = named_schema_location(None if origin is None else origin.location, self.reference)
                msg = (
                    f"msgspec.Struct model {stable_repr(self.class_name)} cannot express its field "
                    f"{stable_repr(field.name)} named {stable_repr(field.alias)} at {location}: msgspec refuses a "
                    "renamed field name that contains a backslash, a double quote, a control character or a lone "
                    "surrogate. Rename the property or use another --output-model-type."
                )
                raise Error(msg)

    @property
    def _written_struct_options(self) -> dict[str, str]:
        """Return the struct options the generator wrote, by name, with the syntax it wrote them in."""
        return self._internal_template_data["base_class_kwargs"]

    def add_base_class_kwarg(self, name: str, value: str) -> None:
        """Add keyword argument to base class constructor."""
        _user_struct_options(self)[name] = value
        self._internal_template_data["base_class_kwargs"][name] = value

    @override
    def _builtin_template_data(self) -> dict[str, Any]:
        """Serialize user msgspec options while retaining generator-owned syntax."""
        template_data = super()._builtin_template_data()
        base_class_kwargs: dict[str, str] = {}
        for key, value in _user_struct_options(self).items():
            if (normalized_key := _normalized_safe_python_name(key, self.target_python_version)) is not None:
                base_class_kwargs[normalized_key] = represent_untrusted_python_value(value)
        base_class_kwargs.update(self._internal_template_data["base_class_kwargs"])
        return {**template_data, "base_class_kwargs": base_class_kwargs}

    @override
    def _custom_template_data(self) -> dict[str, Any]:
        """Preserve legacy raw msgspec options for trusted custom templates."""
        template_data = super()._custom_template_data()
        raw_base_class_kwargs = self.extra_template_data.get("base_class_kwargs")
        internal_base_class_kwargs = self._internal_template_data["base_class_kwargs"]
        if not isinstance(raw_base_class_kwargs, dict):
            return {**template_data, "base_class_kwargs": raw_base_class_kwargs}
        return {
            **template_data,
            "base_class_kwargs": {**raw_base_class_kwargs, **internal_base_class_kwargs},
        }

    @override
    def apply_discriminator_tag(self, field: DataModelFieldBase, field_name: str, value: Any) -> bool:
        """Configure msgspec's tag; the discriminator leaves the instance fields.

        A tag msgspec refuses (``struct_tag_refused``) is left out, and the discriminator stays a field.
        """
        if struct_tag_refused(value):
            return False
        self.add_base_class_kwarg("tag_field", stable_repr(field_name))
        self.add_base_class_kwarg("tag", stable_repr(value))
        return True

    @override
    def opt_out_of_inherited_tag(self) -> None:
        """Clear the tag that msgspec makes the struct inherit, or that it has."""
        self.add_base_class_kwarg("tag", "False")

    def _own_tag_options(self, bases: list[tuple[Any, Any]]) -> tuple[Any, Any]:
        """Return the tag and tag_field options of the struct, given those of its generated bases."""
        options = {
            **_user_struct_options(self),
            **{
                key: literal_eval(value)
                for key, value in self._internal_template_data["base_class_kwargs"].items()
                if key in {"tag", "tag_field"}
            },
        }
        return struct_tag_options(own_tag=options.get("tag"), own_tag_field=options.get("tag_field"), bases=bases)

    def _tag_options(self) -> tuple[Any, Any]:
        """Return the tag and tag_field options of the struct and its generated bases (``struct_tag_options``).

        Each struct is answered after its bases, from the snapshot of the structs reached through base classes, a
        component of structs that inherit from one another in a cycle as one unit: the bases a struct takes options
        from are those outside its component. The generated code cannot define such a cycle, which L6 reports.
        """
        walk = walk_graph(GraphSnapshot.STRUCT_TAGS, EdgeKind.BASE, (self,), _model_key_of_model, _struct_bases)
        graph = walk.graph
        options: dict[ModelKey, tuple[Any, Any]] = {}
        for component in graph.components((EdgeKind.BASE,)):
            for key in component:
                options[key] = walk.node_of[key]._own_tag_options([  # noqa: SLF001
                    options[base] for base in graph.ordered_targets(key, EdgeKind.BASE) if base not in component
                ])
        return options[walk.starts[0]]

    @property
    @override
    def discriminator_tag(self) -> tuple[Any, Any] | None:  # ty: ignore[invalid-property-type-override]
        """Return the tag field and the tag msgspec tells the struct apart by (``struct_discriminator_tag``)."""
        return struct_discriminator_tag(*self._tag_options(), class_name=self.class_name)

    @override
    def _class_hash(self, generic_base_config: Mapping[str, Any] | None) -> SetItemHash:
        """Return how the struct hashes: by value where it compares by value (``eq`` on, the default) and is frozen.

        Each option is the one of the first struct of the chain of order ``graph`` (``_struct_chain``), then the generic
        base class all of them inherit from, that sets it.
        """
        chain = _struct_chain(self, order="graph")
        options = [_user_struct_options(struct) for struct in chain]
        if generic_base_config is not None and self.SUPPORTS_GENERIC_BASE_CLASS:
            options.append({key: str(value) for key, value in self._mapped_struct_options(generic_base_config)})
        compares_by_value = _first_struct_flag(options, "eq", default=True)
        frozen = _first_struct_flag(options, "frozen", default=False)
        hashes = {struct._compared_class_hash(compares_by_value=compares_by_value, frozen=frozen) for struct in chain}  # noqa: SLF001
        return next(kind for kind in ("identity", "never", "value") if kind in hashes)

    @override
    def has_keyword_only_definition(self) -> bool:
        """Return whether msgspec's class declaration already enables keyword-only fields."""
        if self._internal_template_data["base_class_kwargs"].get("kw_only") == "True":
            return True
        return _user_struct_options(self).get("kw_only") in {True, "True"}

    @override
    def enable_model_keyword_only(self) -> None:
        """Enable msgspec's class-level keyword-only option."""
        self.add_base_class_kwarg("kw_only", "True")

    @classmethod
    def _mapped_struct_options(cls, config: Mapping[str, Any]) -> Iterator[tuple[str, Any]]:
        """Yield the struct options that the configuration of the generic base class maps to (``CONFIG_MAPPING``)."""
        for item in config.items():
            if (mapped := cls.CONFIG_MAPPING.get(item)) is not None:
                yield mapped

    @classmethod
    @override
    def create_base_class_model(
        cls,
        config: dict[str, Any],
        reference: Reference,
        custom_template_dir: Path | None = None,
        keyword_only: bool = False,
        treat_dot_as_module: bool | None = None,
    ) -> Struct | None:
        """Create a shared base class model for DRY configuration.

        Creates a Struct that inherits from msgspec.Struct (aliased as _Struct)
        with the specified configuration. Updates the reference path and name in place.
        """
        reference.path = f"#/{cls.BASE_CLASS_NAME}"
        reference.name = cls.BASE_CLASS_NAME

        base_model = cls(
            reference=reference,
            fields=[],
            custom_template_dir=custom_template_dir,
            keyword_only=keyword_only,
            treat_dot_as_module=treat_dot_as_module,
        )

        base_model.base_classes = [BaseClassDataType(type=cls.BASE_CLASS_ALIAS)]

        for mapped_key, mapped_value in cls._mapped_struct_options(config):
            base_model.add_base_class_kwarg(mapped_key, str(mapped_value))

        base_model._additional_imports.append(
            Import(from_=IMPORT_MSGSPEC_STRUCT.from_, import_=IMPORT_MSGSPEC_STRUCT.import_, alias=cls.BASE_CLASS_ALIAS)
        )

        return base_model


def _struct_bases(model: DataModel) -> list[Struct]:
    """Return the generated structs that ``model`` inherits from, in the order its base classes list them."""
    return [base for base in _find_base_classes(model) if isinstance(base, Struct)]


class Constraints(_Constraints):
    """Constraint model for msgspec fields."""


@import_extender
class DataModelField(_FieldCallWriter, DataModelFieldBase):
    """Field implementation for msgspec Struct models."""

    _FIELD_ASSIGNMENT_ROW: ClassVar[AssignmentRow] = "msgspec"
    _FREEZES_RENDER_PLAN: ClassVar[bool] = True
    VALIDATES_STRUCTURED_DEFAULTS: ClassVar[bool] = True

    SUPPORTS_ANNOTATED_CONSTRAINTS: ClassVar[bool] = True
    ANNOTATED_CONSTRAINTS_CONTEXT: ClassVar[object | None] = _ANNOTATED_CONSTRAINTS_CONTEXT
    _FIELD_KEYS: ClassVar[set[str]] = {
        "default",
        "default_factory",
    }
    _META_FIELD_KEYS: ClassVar[set[str]] = {
        "title",
        "description",
        "gt",
        "ge",
        "lt",
        "le",
        "multiple_of",
        "min_items",
        "max_items",
        "min_length",
        "max_length",
        "pattern",
        "examples",
        # 'unique_items', # not supported by msgspec
    }
    _PARSE_METHOD = "convert"
    _COMPARE_EXPRESSIONS: ClassVar[set[str]] = set(_applicability.NUMBER_FIELD_KEYWORDS)
    constraints: Optional[Constraints] = None  # noqa: UP045

    @override
    def process_const(self) -> None:
        """Process const field constraint; its default and nullability are those of ``decide_literal_default``."""
        if "const" not in self.extras:
            return
        self.const = True
        const = self.extras["const"]
        _apply_literal_default(self, decide_literal_default("msgspec-const", const, required=self.required))
        if self.data_type.type == "str" and isinstance(const, str):  # pragma: no cover
            self.replace_data_type(self.data_type.__class__(literals=[const]), clear_old_parent=False)

    def _unset_type_data_type(self) -> DataType:
        return self.data_type.__class__.from_import(IMPORT_MSGSPEC_UNSETTYPE)

    def _ordered_union_data_type(self, members: list[DataType], *, null: bool, unset: bool) -> DataType:
        """Return the union a field hint writes: ``members``, then None and the ``UNSET`` sentinel (row ``ordered``)."""
        data_type_class = self.data_type.__class__
        none_members = [data_type_class(type=NONE)] if null else []
        unset_members = [self._unset_type_data_type()] if unset else []
        return build_union(
            data_type_class,
            [*members, *none_members, *unset_members],
            "ordered",
            roles=(*("member",) * len(members), *("null",) * len(none_members), *("sentinel",) * len(unset_members)),
            use_union_operator=self._use_union_operator,
        )

    def _data_type_has_top_level_none(self, data_type: DataType) -> bool:
        """Return whether the type writes None at the top; row ``legacy-msgspec-top-level-null-as-written``.

        It reads ``is_optional`` as written before render preparation, without the None render preparation wrote.
        """
        if data_type._optional_before_render_preparation() or self._data_type_renders_none(data_type):  # noqa: SLF001
            return True
        if not data_type.is_union:
            return False
        return any(self._data_type_renders_none(child) for child in data_type.data_types)

    def _field_has_top_level_none(self) -> bool:
        return self._top_level_none_of(self)

    def _top_level_none_of(self, field: DataModelFieldBase) -> bool:
        """Return whether ``field`` writes None at the top: nullable, with a null type, or a type that writes None."""
        return any((
            field.nullable is True,
            field.type_has_null is True,
            self._data_type_has_top_level_none(field.data_type),
        ))

    @staticmethod
    def _data_type_is_plain_union(data_type: DataType) -> bool:
        return bool(union_members(data_type))

    def _data_type_spreads_members(self, data_type: DataType) -> bool:
        """Return whether a field's unset union writes the members of its union type one by one.

        A union none of whose members writes anything renders ``Any`` whole, as every other hint of it does.
        """
        return any(self._data_type_writes_hint(member) for member in union_members(data_type))

    _copy_data_type = staticmethod(partial(_copy_data_type, register_references=False, link_parents=False))
    """Copy a data type tree whose copies keep no parents and register no references."""

    def _copy_data_type_without_top_level_none(self, data_type: DataType) -> DataType:
        """Copy a data type without its top-level None, for a hint that writes None itself.

        The None is the type's own when only the nullable keyword of the field declares it.
        """
        data_type = self._copy_data_type(data_type)
        data_type._strip_null(own=self.nullable is True and not self.type_has_null)  # noqa: SLF001
        if data_type.is_union:
            data_type.data_types = [child for child in data_type.data_types if not self._data_type_renders_none(child)]
        return data_type

    def _unset_union_data_type(self) -> DataType:
        """Return the union of the field's type with ``UnsetType``, and None where the field writes it.

        The None is left out where the type without its own None still writes one: that of the nullable model it names.
        """
        if self._data_type_renders_none(self.data_type):
            return self._ordered_union_data_type([], null=True, unset=True)

        has_none = self._field_has_top_level_none()
        if not has_none and self._data_type_spreads_members(self.data_type):
            data_types = [self._copy_data_type(child) for child in self.data_type.data_types]
        elif self._data_type_has_renderable_structure(
            base_data_type := self._annotated_base_data_type()
        ) and not self._data_type_renders_none(base_data_type):
            data_types = [base_data_type]
            has_none = has_none and not base_data_type.admits_none
        else:
            data_types = []
        return self._ordered_union_data_type(data_types, null=has_none, unset=True)

    def _annotated_base_data_type(self) -> DataType:
        if not self._field_has_top_level_none():
            return self._copy_data_type(self.data_type)
        return self._copy_data_type_without_top_level_none(self.data_type)

    def _annotated_union_data_type(self, meta: CallPlan, *, include_unset: bool) -> DataType:
        """Return the ``Annotated`` field type, over None where the type writes nothing, as the field hint does."""
        data_type = self.data_type
        annotated = _MetaAnnotatedType(
            meta=meta,
            data_types=[self._annotated_base_data_type()],
            python_version=data_type.python_version,
            use_standard_collections=data_type.use_standard_collections,
            use_generic_container=data_type.use_generic_container,
            use_union_operator=data_type.use_union_operator,
            treat_dot_as_module=data_type.treat_dot_as_module,
            use_serialize_as_any=data_type.use_serialize_as_any,
        )
        return self._ordered_union_data_type([annotated], null=self._field_has_top_level_none(), unset=include_unset)

    def _written_type(self, meta: CallPlan | None) -> DataType | None:
        """Return the copy of the field's type that its annotation writes, or None when it writes the type itself.

        It is the ``Annotated`` type with ``meta``, with ``UnsetType`` unless the field is required or nullable, or
        the union with ``UnsetType`` of a field its struct may leave unset. Like the copies it holds, it keeps no
        parents, so render plans compare by value.
        """
        if meta is not None:
            written = self._annotated_union_data_type(meta, include_unset=not (self.required or self.nullable))
        elif self._not_required and not self.nullable:
            written = self._unset_union_data_type()
        else:
            return None
        clear_parents(written)
        return written

    def _get_simple_unset_base_type(self) -> str | None:  # noqa: PLR0911
        """Return the existing leaf hint when direct unset rendering is equivalent."""
        parent = self.parent
        data_type = self.data_type
        if type(self) is not DataModelField or type(parent) is not Struct:
            return None
        if self.required or self.nullable or self.use_annotated:
            return None
        if data_type.data_types or data_type.dict_key or data_type.reference or data_type.python_type:
            return None
        if (type_hint := data_type.type) is None or not is_identifier(type_hint, data_type.python_version):
            return None
        if data_type.literals or data_type.enum_member_literals or data_type.kwargs or data_type.alias:
            return None
        if data_type._is_container:  # noqa: SLF001
            return None
        data_type_class = type(data_type)
        if data_type_class is not DataType and data_type_class is not data_type._context_class(  # noqa: SLF001
            "ContextDataType", DataType
        ):
            return None
        if (
            data_type.is_custom_type
            or data_type.discriminator is not None
            or data_type.is_optional
            or data_type.is_func
        ):
            return None
        if self._has_explicit_typing_import_requirements(data_type) or self.type_has_null is True:
            return None
        if type_hint == NONE:
            return None
        if type_hint == "UnsetType":
            return None
        return type_hint

    def _get_simple_unset_type_hint(self) -> str | None:
        """Render a standard unset union without copying its data type graph."""
        if (type_hint := self._get_simple_unset_base_type()) is None:
            return None

        return join_union((type_hint, "UnsetType"), union_operator=self._use_union_operator)

    def _imports_data_type(self, plan: FieldRenderPlan) -> DataType:
        """Return the type imports collect from: the one the annotation of ``plan`` writes, else the field's own.

        The ``Annotated`` type of a field neither required, nullable nor in a struct is not.
        """
        if plan.annotated_call is not None and not (self.required or self.nullable or self._not_required):
            return self.data_type
        return cast("DataType | None", plan.written_type) or self.data_type

    @property
    @override
    def field(self) -> str | None:
        """For backwards compatibility."""
        return self._plan_text(self._render_plan()) or None

    def _field_call_arguments(self, omitted: list[Omitted]) -> tuple[CallArguments, bool]:
        """Build the ``field(...)`` arguments in the order the call writes them, and whether one converts Structs."""
        args = CallArguments(omitted)
        for key, value in self.extras.items():
            if key in self._FIELD_KEYS:
                args.put(key, value, Why(_FIELD_ARGUMENT, key), (f"extra:{key}",))
        converts = False
        if self.alias is not None:
            args.put("name", self.alias, EXPLICIT_ALIAS, ("alias",))

        if _default_state(self.default) == "value":
            args.put("default", self.default, DEFAULT_VALUE)
        elif self._not_required and "default_factory" not in args:
            args.put("default", None if self.nullable else UNSET, _NONE_DEFAULT if self.nullable else _UNSET_DEFAULT)

        if requires_value(self):
            args.pop("default", "factory-not-applicable", REQUIRED_FIELD)
            args.pop("default_factory", "factory-not-applicable", REQUIRED_FIELD)
        elif (
            _default_state(self.default) == "value"
            and "default_factory" not in args
            and (conversion := self._get_default_as_struct_model()) is not None
        ):
            default = cast("Kwarg", args.take("default"))
            args.put("default_factory", conversion, _STRUCT_CONVERSION, default.items)
            converts = True

        if (
            (entry := args.entry("default")) is not None
            and isinstance(entry.value, (list, dict, set))
            and "default_factory" not in args
        ):
            args.take("default")
            args.put("default_factory", _ValueFactory(entry.value), MUTABLE_FACTORY, entry.items)

        if (
            self._takes_nested_model_factory()
            and "default_factory" not in args
            and (nested := self._get_default_factory_for_optional_nested_model()) is not None
        ):
            args.put("default_factory", nested, NESTED_MODEL_FACTORY)
            args.pop("default", "superseded", NESTED_MODEL_FACTORY)
        return args, converts

    @override
    def _has_default_for_nested_model_factory(self) -> bool:
        """Preserve the UNSET default emitted for optional Struct fields."""
        return not self.required or super()._has_default_for_nested_model_factory()

    @override
    def _written_assignment(self) -> MsgspecWritten:
        """Return the ``field()`` arguments the field's render plan writes and whether its call renders."""
        data = written_data(written_argument_names(self._render_plan()))
        return MsgspecWritten(data=data, text=written_text(self, writes=data.any))

    @override
    def _build_render_plan(self) -> FieldRenderPlan:
        """Build the plan: ``Meta(...)`` inside ``Annotated``, then ``field(...)``, the default alone, or nothing.

        A tag field (a class variable) keeps both for templates that read them; the built-in template skips it.
        """
        omitted: list[Omitted] = []
        args, converts = self._field_call_arguments(omitted)
        meta = self._build_meta_call(omitted)
        kwargs = args.kwargs()
        call: CallPlan | None = None
        default = FieldDefault(self.default, missing_sentinel=False, why=DEFAULT_VALUE)
        bare_default = False
        if len(kwargs) == 1 and kwargs[0].name == "default":
            default = FieldDefault(kwargs[0].value, missing_sentinel=False, why=BARE_DEFAULT, items=kwargs[0].items)
            bare_default = True
        elif kwargs:
            call = CallPlan(None, None, kwargs, default_keyword=False, why=_FIELD_CALL, callee="field")
        symbol_imports: list[tuple[Import, Why]] = []
        if not self.is_class_var:
            if call is not None:
                symbol_imports.append((IMPORT_MSGSPEC_FIELD, call.why))
            if converts:
                symbol_imports.append((IMPORT_MSGSPEC_CONVERT, _STRUCT_CONVERSION))
                if cast("_StructConversion", args.get("default_factory")).from_attributes:
                    symbol_imports.append((_IMPORT_SIMPLE_NAMESPACE, _STRUCT_CONVERSION))
            if meta is not None:
                symbol_imports.append((IMPORT_MSGSPEC_META, meta.why))
            if args.get("default") is UNSET:
                symbol_imports.append((IMPORT_MSGSPEC_UNSET, _UNSET_DEFAULT))
        form, why = (
            ("classvar", _CLASS_VAR)
            if self.is_class_var
            else ("plain", PLAIN_ANNOTATION)
            if meta is None
            else ("annotated", ANNOTATED_ANNOTATION)
        )
        return FieldRenderPlan(
            annotation_form=form,
            call=call,
            default=default,
            has_default_factory_in_field="default_factory" in args,
            omitted=tuple(omitted),
            symbol_imports=tuple(symbol_imports),
            why=why,
            annotated_call=meta,
            bare_default=bare_default,
            written_type=self._written_type(meta),
        )

    def _render_plan_text(self, plan: FieldRenderPlan) -> tuple[object, ...]:
        """Return what the field writes from ``plan``, to compare a frozen plan with a rebuilt one."""
        return (
            self._plan_text(plan),
            None if plan.annotated_call is None else _meta_call_text(plan.annotated_call),
            plan.annotation_form,
            tuple(import_ for import_, _ in plan.symbol_imports),
            tuple((omitted.item, omitted.reason, omitted.warns) for omitted in plan.omitted),
        )

    def _declared_render_items(self) -> frozenset[str]:
        """Return the items the field declares that its plan must write or omit.

        They are its ``field()`` and ``Meta()`` metadata, its alias, its constraints and the constraints that leave with
        the type ``--type-overrides`` replaces.
        """
        items = {f"extra:{key}" for key in self.extras if key in self._FIELD_KEYS or key in self._META_FIELD_KEYS}
        if self.alias is not None:
            items.add("alias")
        if self.constraints is not None:
            items.update(
                f"constraint:{key}" for key, value in self.constraints.model_dump().items() if value is not None
            )
        if self.overridden_constraints:
            items.add("override")
        return frozenset(items)

    @property
    @override
    def type_hint(self) -> str:
        """Return the type hint, using UnsetType for non-required non-nullable fields."""
        if (simple_unset_type_hint := self._get_simple_unset_type_hint()) is not None:
            return simple_unset_type_hint
        if self._not_required and not self.nullable:
            plan = self._render_plan()
            written = plan.written_type if plan.annotated_call is None else self._unset_union_data_type()
            return cast("DataType", written).type_hint
        return super().type_hint

    def _hint_meta(self) -> tuple[bool, str | None]:
        """Return whether the hint renders the field's own type, and the ``Meta()`` it annotates the type with.

        An unset union, and an ``Annotated`` type of a required or nullable field, render a copy of the type.
        """
        meta = self._get_meta_string() if self.use_annotated else None
        copied = (self._not_required and not self.nullable) or (meta is not None and (self.required or self.nullable))
        return not copied, meta

    @override
    def _hint_renders_own_type(self) -> bool:
        return self._hint_meta()[0]

    @override
    def _import_state_data_type(self) -> DataType | None:
        """Return the field's own data type, or None when ``imports`` collects from a copy of it."""
        own, meta = self._hint_meta()
        return self._import_state_target(needs_annotated=meta is not None, data_type=self.data_type) if own else None

    @property
    @override
    def imports(self) -> tuple[Import, ...]:
        """Get imports from the structurally rendered msgspec annotation."""
        if self._get_simple_unset_base_type() is not None:
            imports = self._collect_field_imports(needs_annotated=False, data_type=self.data_type)
            if IMPORT_MSGSPEC_UNSETTYPE not in imports:
                imports = (*imports, IMPORT_MSGSPEC_UNSETTYPE)
            if not self._use_union_operator and IMPORT_UNION not in imports:
                imports = (*imports, IMPORT_UNION)
            return imports
        plan = self._render_plan()
        return self._collect_field_imports(
            needs_annotated=plan.annotated_call is not None,
            data_type=self._imports_data_type(plan),
        )

    @property
    def _not_required(self) -> bool:
        return not self.required and isinstance(self.parent, Struct)

    @property
    @override
    def fall_back_to_nullable(self) -> bool:
        """Return whether to fall back to nullable type instead of UnsetType."""
        return not self._not_required

    def _meta_keywords(self, data_type: DataType) -> frozenset[str]:
        """Return the constraint keywords msgspec.Meta takes on a type.

        Numbers take bounds, sized types take lengths and str takes a pattern, as the msgspec type table lists;
        a mapping and a mapping class msgspec decodes as a dict (``DataTypeManager.MAPPING_CLASS_PATHS``, also
        subscripted, as ``counts_keys`` reads it) take lengths, which count their keys, a fixed tuple and a union take
        none, and an unclassified type keeps them all. A referenced type alias takes what its type takes
        (``constrained_value_type``); any other referenced model, such as an enum or a Struct, takes none.
        """
        if (target := self.constrained_value_type(data_type)) is None:
            return _applicability.NO_KEYWORDS
        if (keywords := self._shape_meta_keywords(target)) is not None:
            return keywords
        version = self.target_version_key(target)
        return _applicability.meta_keywords(
            DataTypeManager.constraint_keywords(target, version)
            | DataTypeManager.constraint_keywords(target, version, counts_keys=True)
        )

    def _shape_meta_keywords(self, data_type: DataType) -> frozenset[str] | None:
        """Return the keywords a mapping, a fixed tuple or a union takes, or None for a type of another shape."""
        return _applicability.shape_meta_keywords(
            mapping=data_type in MAPPINGS,
            unconstrained=data_type.is_tuple or self._data_type_is_plain_union(data_type),
        )

    @override
    def _stops_constraint_walk(self, data_type: DataType) -> bool:
        """Return whether ``data_type`` is a mapping, a fixed tuple or a union: it takes the keywords of its shape."""
        return super()._stops_constraint_walk(data_type) or self._shape_meta_keywords(data_type) is not None

    @override
    def _alias_value_admits_null(self, alias_field: DataModelFieldBase) -> bool:
        """Return whether the root of an alias writes None, so the alias takes no constraint."""
        return super()._alias_value_admits_null(alias_field) or self._top_level_none_of(alias_field)

    def _union_meta_constraints(self) -> tuple[tuple[tuple[str, Any], ...], tuple[tuple[str, Any], ...]]:
        """Return the ``Meta`` constraints of the type, and them as the integer check of a union reads them exactly.

        Read exactly, each side that ``Meta`` bounds is every schema bound of that side it takes, written or
        superseded, at the value the schema writes: the ``ge`` that ``minimum`` and ``exclusiveMinimum`` come to reads
        as both, so the check reads the integers they admit together. A field extra that a schema bound superseded is
        no bound of the schema, and ``Meta`` does not write it.
        """
        omitted: list[Omitted] = []
        args, _ = self._meta_call_arguments(omitted)
        written = tuple(
            (k, v) for k, v in args.data().items() if v is not None and k in _applicability.META_SCHEMA_KEYWORDS
        )
        sides = {_BOUND_SIDES[k] for k, _ in written if k in _BOUND_SIDES}
        taken = (
            *((item, entry.value) for entry in args.kwargs() for item in entry.items),
            *(
                (entry.item, entry.value)
                for entry in omitted
                if entry.reason == "superseded" and entry.item.startswith("constraint:")
            ),
        )
        exact = [(k, v) for k, v in written if k not in _BOUND_SIDES]
        for item, value in taken:
            source, _, keyword = item.partition(":")
            if _BOUND_SIDES.get(keyword) in sides:
                written_value: UnionIntFloat | None = (
                    getattr(self.constraints, keyword) if source == "constraint" else None
                )
                exact.append((keyword, value if written_value is None else written_value.value))
        return written, tuple(exact)

    def _meta_call_arguments(self, omitted: list[Omitted]) -> tuple[CallArguments, frozenset[str]]:
        """Return the ``Meta(...)`` arguments msgspec takes on the type, and the constraints it leaves out.

        The constraints it does not take go to ``omitted``, warning unless the values of the field's ``Literal`` decide
        them; so does a float ``multiple_of`` that msgspec checks inexactly.
        """
        args = self._declared_meta_arguments(omitted)
        data = args.data()
        untaken, inexact = self._dropped_meta_keywords(data)
        dropped = (untaken | {"multiple_of"}) if inexact else untaken
        decided = self._literal_decided_meta_keywords(data) if untaken else frozenset()
        for name in args.names():
            if name not in dropped:
                continue
            entry = cast("Kwarg", args.take(name))
            if name in untaken:
                literal = name in decided
                reason: OmitReason = "consumed-by-type" if literal else "untaken-by-type"
                why = Why(_LITERAL_DECIDED if literal else UNTAKEN_BY_TYPE, name)
                warns = not literal
            else:
                reason, why, warns = "inexact-float-divisor", Why(_INEXACT, name), True
            omitted.extend(Omitted(item, entry.value, reason, why, warns=warns) for item in entry.items)
        return args, dropped

    def _declared_meta_arguments(self, omitted: list[Omitted]) -> CallArguments:
        """Return the ``Meta(...)`` arguments of the field's metadata and constraints, before the type decides them.

        Number bounds are normalized for the field's numeric type, the weaker of two bounds of one side goes, and
        item counts are written as lengths.
        """
        args = CallArguments(omitted)
        for key, value in self.extras.items():
            if key in self._META_FIELD_KEYS:
                args.put(key, value, Why(_META_ARGUMENT, key), (f"extra:{key}",))
        unset = [cast("Kwarg", args.take(entry.name)) for entry in args.kwargs() if entry.value is None]
        omitted.extend(Omitted(item, None, "none-value", _NONE_VALUE) for entry in unset for item in entry.items)
        if (overridden := self.overridden_constraints) is not None:
            replaced, count = overridden
            omitted.append(
                Omitted("override", count, "override-type-does-not-take", TYPE_OVERRIDE, warns=True, detail=replaced)
            )
        if self.constraints is not None:
            declared = {k: v for k, v in self.constraints.model_dump().items() if v is not None}
            if (blocked := self._constraints_blocked_by()) is not None:
                omitted.extend(Omitted(f"constraint:{k}", v, blocked, Why(_CONSTRAINT, k)) for k, v in declared.items())
            else:
                for key, argument in self._normalized_meta_constraints(declared, omitted).items():
                    args.put(key, argument.value, argument.why, argument.items)

        if (gt := args.get("gt")) is not None and (ge := args.get("ge")) is not None:
            args.pop("ge" if gt >= ge else "gt", "superseded", Why(_STRONGER_BOUND, "gt"))
        if (lt := args.get("lt")) is not None and (le := args.get("le")) is not None:
            args.pop("le" if lt <= le else "lt", "superseded", Why(_STRONGER_BOUND, "lt"))

        for items_key, length_key in (("min_items", "min_length"), ("max_items", "max_length")):
            if (entry := args.take(items_key)) is not None:
                args.put(length_key, entry.value, Why(_ITEMS_AS_LENGTH, items_key), entry.items)
        return args

    def _normalized_meta_constraints(self, declared: dict[str, Any], omitted: list[Omitted]) -> dict[str, Kwarg]:
        """Return the constraints ``Meta(...)`` writes, normalized for the field's numeric type, merging collisions.

        A constraint ``Meta`` has no argument for, a non-finite bound, an integer ``multiple_of`` of one and, on an
        int (``_names_int``), a number beyond the 64-bit range go to ``omitted``; that last one warns, also where the
        number has no finite float.
        """
        has_integer_constraints = any(key in self._COMPARE_EXPRESSIONS for key in declared)
        is_float_type = has_integer_constraints and self._has_numeric_data_type("float")
        is_int_type = has_integer_constraints and not is_float_type and self._has_numeric_data_type("int")
        annotates_int = (
            has_integer_constraints
            and self.use_annotated
            and (target := self.constrained_value_type(self._annotated_base_data_type())) is not None
            and self._names_int(target)
        )

        def drop(item: str, k: str, v: Any, normalized: tuple[str, Any] | None) -> Omitted | None:
            if k not in self._META_FIELD_KEYS:
                return Omitted(item, v, "backend-cannot-express", Why(_CONSTRAINT, k))
            if annotates_int and _applicability.int_constraint_beyond_values(
                _applicability.MSGSPEC_OUTPUT, *(constraint := normalized or (k, v))
            ):
                return Omitted(
                    item, constraint[1], "backend-cannot-express", Why(_INT64_LIMIT, constraint[0]), warns=True
                )
            if normalized is not None:
                return None
            if _applicability.is_non_finite(v):
                return Omitted(item, v, "backend-cannot-express", Why(_NON_FINITE, k))
            return Omitted(item, v, "consumed-by-type", Why(_MULTIPLE_OF_ONE, k))

        return _normalized_constraint_kwargs(
            declared,
            omitted,
            _applicability.MSGSPEC_OUTPUT,
            _CONSTRAINT,
            drop,
            is_float_type=is_float_type,
            is_int_type=is_int_type,
        )

    def _names_int(self, data_type: DataType) -> bool:
        """Return whether a type is int, also where an annotation names the int that Annotated or Final qualifies."""
        if (python_type := data_type.python_type) is None:
            return data_type.type == "int"
        return _qualified_type_path(python_type.expression, self.target_version_key(data_type)) == "int"

    def _dropped_meta_keywords(self, data: dict[str, Any]) -> tuple[frozenset[str], bool]:
        """Return the constraints msgspec.Meta does not take on the type, and whether a float multiple_of is inexact.

        msgspec checks `multiple_of` on a float inexactly (``_applicability.inexact_float_divisor``).
        """
        if not (
            constrained := {k for k, v in data.items() if v is not None and k in _applicability.META_SCHEMA_KEYWORDS}
        ):
            return frozenset(), False
        untaken = frozenset(constrained - self._meta_keywords(self._annotated_base_data_type()))
        inexact = (
            "multiple_of" in constrained - untaken
            and self._has_numeric_data_type("float")
            and _applicability.inexact_float_divisor(data["multiple_of"])
        )
        return untaken, inexact

    def _literal_decided_meta_keywords(self, data: dict[str, Any]) -> frozenset[str]:
        """Return the constraints the values of a const's or an enum's ``Literal`` decide, which it leaves out silently.

        The parser keeps only the values that meet them (``_applicability.literal_check``), or warns that none does. A
        constraint that only some of the values meet stays reported: the values that fail it are kept, as without
        --filter-literal-values, and the ``Meta`` keyword would have rejected them.
        """
        if not _is_value_literal(base := self._annotated_base_data_type()):
            return frozenset()
        return _applicability.meta_keywords(
            _applicability.literal_met_keywords(
                base.literals,
                {
                    schema: data[meta]
                    for meta, schema in _applicability.META_SCHEMA_KEYWORDS.items()
                    if schema in _applicability.LITERAL_DECIDABLE_KEYWORDS and data.get(meta) is not None
                },
                unmet_too=True,
            )
        )

    def _union_leaves(
        self,
        member: DataType,
        spread: tuple[tuple[DataType, DataModelField], ...] = (),
        constraints: tuple[tuple[str, Any], ...] = (),
        seen: frozenset[int] = frozenset(),
        schema_constraints: tuple[tuple[str, Any], ...] = (),
    ) -> Iterator[_UnionLeaf]:
        """Yield the types msgspec decodes a union member as, with their types without aliases and Meta constraints.

        A member is an alias chain ending in a scalar or a Literal of one type; msgspec spreads an alias of a union,
        or of a type and None, into the union that names it, so the members of such an alias count as its own.
        Other members yield nothing. The members of the aliases are walked with a stack of their own, in order, so
        a chain of aliases of any length is read.
        """
        pending = [_LeafRead(member, spread, constraints, seen, schema_constraints)]
        while pending:
            inner_reads, leaves = self._union_leaf_step(*pending.pop())
            yield from leaves
            pending.extend(reversed(inner_reads))

    def _union_leaf_step(
        self,
        member: DataType,
        spread: tuple[tuple[DataType, DataModelField], ...],
        constraints: tuple[tuple[str, Any], ...],
        seen: frozenset[int],
        schema_constraints: tuple[tuple[str, Any], ...],
    ) -> tuple[list[_LeafRead], tuple[_UnionLeaf, ...]]:
        """Return the members of the alias of a union that ``member`` names, to read in its place, or its leaves.

        The alias chain of ``member`` ends in the alias that msgspec spreads, whose members are read with the
        constraints of the chain where it has one member, or in the type the member decodes as (``_union_leaves``).
        """
        data_type = member
        while (reference := data_type.reference) is not None:
            source = reference.source
            if (
                data_type.is_optional
                or not isinstance(source, TypeAliasBase)
                or len(source.fields) != 1
                or id(source) in seen
                or not isinstance(alias_field := source.fields[0], DataModelField)
            ):
                return [], ()
            seen |= {id(source)}
            if alias_field.use_annotated:
                written, exact = alias_field._union_meta_constraints()  # noqa: SLF001
                constraints += written
                schema_constraints += exact
            if (
                alias_field._field_has_top_level_none()  # noqa: SLF001
                or self._data_type_is_plain_union(alias_field.data_type)
                or (alias_field.data_type.data_types and alias_field.data_type._writes_nothing_of_its_own)  # noqa: SLF001
            ):
                members = self._non_none_members(alias_field.data_type)
                single = len(members) == 1
                return [
                    _LeafRead(
                        inner,
                        (*spread, (member, alias_field)),
                        constraints if single else (),
                        seen,
                        schema_constraints if single else (),
                    )
                    for inner in members
                ], ()
            data_type = alias_field.data_type
        if (
            data_type.data_types or data_type.is_optional or data_type.enum_member_literals or data_type._is_container  # noqa: SLF001
        ):
            return [], ()
        if isinstance(data_type, _MetaType):
            constraints += (written := tuple(data_type.meta_keywords.items()))
            schema_constraints += written
        kinds = {type(literal).__name__ for literal in data_type.literals} or {data_type.type}
        return [], tuple(
            _UnionLeaf(spread, member, kind, data_type, constraints, schema_constraints)
            for kind in kinds
            if kind and len(kinds) == 1
        )

    def _non_none_members(self, data_type: DataType) -> list[DataType]:
        """Return the members of a type other than None, spreading the unions and wrappers that render as them."""
        if data_type.data_types and data_type._writes_nothing_of_its_own:  # noqa: SLF001
            return [member for child in data_type.data_types for member in self._non_none_members(child)]
        return [] if self._data_type_renders_none(data_type) else [data_type]

    def _union_members(self, data_type: DataType) -> Iterator[tuple[DataType, int, DataType]]:
        """Yield each member of a union with its owner and index, spreading member unions that render as one."""
        for index, member in enumerate(data_type.data_types):
            if member.type is None and member.reference is None and self._data_type_is_plain_union(member):
                yield from self._union_members(member)
            else:
                yield data_type, index, member

    def _unconstrained_members(
        self,
        member: DataType,
        dropped: Mapping[int, _UnionLeaf],
        spread: Mapping[int, DataModelField],
    ) -> list[DataType]:
        """Return a union member written with the dropped members as their types without Meta.

        An alias spread to reach a dropped member is written as its members, so the alias itself keeps its Meta. A
        dropped member that carries the constraints every member of its type implies (``meta``) is written as its type
        with them.
        """
        if (leaf := dropped.get(id(member))) is not None:
            if leaf.covered:
                return []
            return [self._meta_member(leaf.base, leaf.meta) if leaf.meta else self._bare_member(leaf.base)]
        if (alias_field := spread.get(id(member))) is None:
            copied = self._copy_data_type(member)
            for nested in copied.all_data_types:
                nested.register_reference()
                link_members(nested)
            return [copied]
        members = [
            unconstrained
            for inner in self._non_none_members(alias_field.data_type)
            for unconstrained in self._unconstrained_members(inner, dropped, spread)
        ]
        if alias_field._field_has_top_level_none():  # noqa: SLF001
            members.append(self.data_type.__class__(type=NONE))
        return members

    def _bare_member(self, base: DataType) -> DataType:
        """Return a union member written as the type of ``base`` without ``Meta``."""
        if isinstance(base, _MetaType):
            return self.data_type.__class__(type=base.type, import_=base.import_)
        return self._copy_data_type(base)

    @staticmethod
    def _meta_member(base: DataType, meta: tuple[tuple[str, Any], ...]) -> DataType:
        """Return a union member written as the type of ``base`` with the ``Meta`` constraints ``meta``."""
        return base._context_class("MsgspecMetaMemberType", _MetaType)(  # noqa: SLF001
            type=base.type, import_=base.import_, meta_keywords=dict(meta)
        )

    @staticmethod
    def _conflicting_leaves(
        leaves: Mapping[str, list[_UnionLeaf]], arithmetic: Literal["exact", "float"]
    ) -> tuple[dict[int, _UnionLeaf], set[int], frozenset[tuple[str, Any]]]:
        """Return the constrained members of a union that would reject values another member accepts, by identity.

        Each such member comes with the constraints it loses. msgspec checks the constraints of a member of one type on
        every member of that type, so a member whose constraints are the ones every member of that type shares keeps
        them, and the other members lose all of theirs. Otherwise the constraints every member implies
        (``_loosest_shared``), also every member of the kinds msgspec decodes as that type (``_ROUTED_KINDS``), stay,
        written once on the first member (``meta``), and the other members of the type are left out (``covered``), so
        no alias of a member is named where it was not. Also return the int members that
        would reject an integer a float member accepts, since msgspec decodes every integer as the int member;
        constraints that admit every such integer stay, and the constraints an int member carries for every member
        stay only where they admit every integer the float and Decimal members admit. The bounds of that check are read
        with ``arithmetic`` (``_applicability.union_bound_arithmetic``). Last return the constraints that stay this way.
        """
        dropped: dict[int, _UnionLeaf] = {}
        implied: dict[str, frozenset[tuple[str, Any]]] = {}
        for kind, same_kind in leaves.items():
            if len(same_kind) < 2:  # noqa: PLR2004
                continue
            constraint_sets = [frozenset(leaf.constraints) for leaf in same_kind]
            if (kept := frozenset.intersection(*constraint_sets)) in constraint_sets:
                dropped.update(
                    (id(leaf.member), leaf._replace(constraints=tuple(c for c in leaf.constraints if c not in kept)))
                    for leaf, constraints in zip(same_kind, constraint_sets, strict=True)
                    if constraints != kept
                )
                continue
            routed = [*same_kind, *(leaf for other in _ROUTED_KINDS.get(kind, ()) for leaf in leaves.get(other, ()))]
            implied[kind] = kept = frozenset(_loosest_shared([leaf.constraints for leaf in routed]))
            keeper = id(same_kind[0].member) if kept else None
            dropped.update(
                (
                    id(leaf.member),
                    leaf._replace(
                        constraints=tuple(c for c in leaf.constraints if c not in kept),
                        meta=tuple(kept) if id(leaf.member) == keeper else (),
                        covered=keeper is not None and id(leaf.member) != keeper,
                    ),
                )
                for leaf in same_kind
            )
        floats = [
            () if id(leaf.member) in dropped else leaf.read(arithmetic)
            for leaf in leaves.get("float", ())
            if not leaf.base.literals
        ]
        if "int" in implied and not all(
            _admits_integers_of(_loosest_shared([leaf.read(arithmetic) for leaf in leaves["int"]]), other, arithmetic)
            for other in (*floats, *(leaf.read(arithmetic) for leaf in leaves.get("Decimal", ())))
        ):
            del implied["int"]
            dropped.update((id(leaf.member), leaf) for leaf in leaves["int"])
        integers = [
            leaf
            for leaf in leaves.get("int", ())
            if leaf.constraints
            and id(leaf.member) not in dropped
            and not all(_admits_integers_of(leaf.read(arithmetic), other, arithmetic) for other in floats)
        ]
        dropped.update((id(leaf.member), leaf) for leaf in integers)
        return dropped, {id(leaf.member) for leaf in integers}, frozenset().union(*implied.values())

    @override
    def unconstrain_union_members(self, options: ConstraintOptions | None = None) -> bool:
        """Write the members of a union that msgspec decodes as one type without their Meta constraints.

        msgspec checks every member of a type in a union against the constraints of all of them, and decodes every
        integer as the int member when the union has a float member too, so a constraint of such a member would reject
        values another member accepts; the union accepts the values of every member. ``options`` say how the bounds of
        that integer check are read. Return whether the field changed.
        """
        arithmetic = _applicability.union_bound_arithmetic(options or _applicability.ConstraintOptions())
        changed = False
        kinds: set[str] = set()
        keywords: set[str] = set()
        integer_keywords: set[str] = set()
        implied: set[tuple[str, Any]] = set()
        for data_type in tuple(self.data_type.all_data_types):
            if data_type.is_tuple or len(data_type.data_types) < 2:  # noqa: PLR2004
                continue
            slots = list(self._union_members(data_type))
            leaves: dict[str, list[_UnionLeaf]] = {}
            for _, _, member in slots:
                for leaf in self._union_leaves(member):
                    leaves.setdefault(leaf.kind, []).append(leaf)
            dropped, integers, kept = self._conflicting_leaves(leaves, arithmetic)
            implied |= kept
            for member_id, leaf in dropped.items():
                if member_id in integers:
                    integer_keywords.update(leaf.keywords)
                else:
                    kinds.add(leaf.kind)
                    keywords.update(leaf.keywords)
            spread = {id(member): alias_field for leaf in dropped.values() for member, alias_field in leaf.spread}
            for owner, index, member in reversed(slots):
                if id(member) in dropped or id(member) in spread:
                    changed = True
                    member.unregister_reference()
                    owner.data_types[index : index + 1] = members = self._unconstrained_members(member, dropped, spread)
                    for unconstrained in members:
                        unconstrained.parent = owner
        self._record_unconstrained_union_members(kinds, keywords, integer_keywords, implied)
        return changed or bool(kinds or integer_keywords)

    @override
    def inherit_unconstrained_union_members(self, source: DataModelFieldBase) -> None:
        """Take over the union members written without constraints from a field whose type this field inlines.

        Collapsing a root model copies its field's type into this field, so this field warns about its members.
        """
        if (recorded := source.__dict__.get(_UNCONSTRAINED_UNION_MEMBERS_KEY)) is not None:
            self._record_unconstrained_union_members(*recorded)

    def _record_unconstrained_union_members(
        self,
        kinds: Collection[str],
        keywords: Collection[str],
        integer_keywords: Collection[str],
        implied: Collection[tuple[str, Any]] = (),
    ) -> None:
        """Record the kinds and the Meta keywords of the union members this field is written without.

        ``implied`` are the constraints that the union keeps for every member of a kind in their place.
        """
        if not kinds and not integer_keywords:
            return
        recorded_kinds, recorded_keywords, recorded_integer_keywords, recorded_implied = self.__dict__.get(
            _UNCONSTRAINED_UNION_MEMBERS_KEY, _NO_UNCONSTRAINED_MEMBERS
        )
        self.__dict__[_UNCONSTRAINED_UNION_MEMBERS_KEY] = (
            recorded_kinds | frozenset(kinds),
            recorded_keywords | frozenset(keywords),
            recorded_integer_keywords | frozenset(integer_keywords),
            recorded_implied | frozenset(implied),
        )
        self.invalidate_semantic_caches()

    @property
    @override
    def dropped_constraints_warning(self) -> str | None:
        """Return a warning naming the constraints left out because msgspec.Meta does not take or check them exactly.

        Those of the field's own ``Meta(...)`` are the omitted items of its render plan that warn, with the counts of
        the array or map ``--type-overrides`` replaces that leave with it.
        """
        omitted = self._render_plan().omitted
        reasons: list[str] = []
        overridden = next((o for o in omitted if o.reason == "override-type-does-not-take"), None)
        if overridden is not None:
            reasons.append(
                f"--type-overrides writes {self._annotated_base_data_type().type_hint} in place of {overridden.detail}"
            )
        untaken = frozenset(cast("str", o.why.keyword) for o in omitted if o.reason == "untaken-by-type" and o.warns)
        inexact = next((o for o in omitted if o.reason == "inexact-float-divisor" and o.warns), None)
        beyond = {cast("str", o.why.keyword): o.value for o in omitted if o.why.rule == _INT64_LIMIT}
        if untaken:
            reasons.append(
                f"msgspec.Meta does not take {', '.join(sorted(untaken))} on "
                f"{self._annotated_base_data_type().type_hint}"
            )
        if beyond:
            reasons.append(
                "msgspec.Meta takes the number constraints of an int only within the 64-bit range, not "
                + _meta_keywords_text(beyond.items())
            )
        if inexact is not None:
            reasons.append(
                "msgspec checks multiple_of on float by a floating-point remainder, which rejects multiples of "
                f"{represent_python_value(inexact.value)} that floats only approximate"
            )
        kinds, union_keywords, integer_keywords, implied = self.__dict__.get(
            _UNCONSTRAINED_UNION_MEMBERS_KEY, _NO_UNCONSTRAINED_MEMBERS
        )
        if kinds:
            reasons.append(
                f"msgspec checks all {', '.join(sorted(kinds))} members of a union against the constraints of each "
                f"of them, so {', '.join(sorted(union_keywords))} of one member would reject values another "
                "member accepts"
                + (f", and it keeps {_meta_keywords_text(implied)}, which every member implies" if implied else "")
            )
        if integer_keywords:
            reasons.append(
                "msgspec decodes every integer as the int member of a union that has a float member too, so "
                f"{', '.join(sorted(integer_keywords))} of the int member would reject integers the float member "
                "accepts"
            )
        if not reasons:
            return None
        return self._generated_without_constraints(
            "; ".join(reasons),
            len(untaken | frozenset(beyond) | union_keywords | integer_keywords)
            + (inexact is not None)
            + (0 if overridden is None else cast("int", overridden.value)),
        )

    def _get_meta_string(self) -> str | None:
        """Return the ``Meta(...)`` the field writes inside ``Annotated``, or None (always without --use-annotated)."""
        return None if (meta := self._meta_call()) is None else _meta_call_text(meta)

    def _meta_call(self) -> CallPlan | None:
        """Return the ``Meta(...)`` call of the field's render plan, the frozen one or before B2 the live one."""
        return self._render_plan().annotated_call

    def _build_meta_call(self, omitted: list[Omitted]) -> CallPlan | None:
        """Build the ``Meta(...)`` call written inside ``Annotated``: its arguments, under --use-annotated."""
        if not self.use_annotated:
            args = self._declared_meta_arguments(omitted)
            for argument in args.kwargs():
                args.omit_entry(argument, "meta-needs-annotated", _NEEDS_ANNOTATED)
            return None
        if not (kwargs := self._meta_call_arguments(omitted)[0].kwargs()):
            return None
        return CallPlan(None, None, kwargs, default_keyword=False, why=_META_CALL, callee="Meta")

    @property
    @override
    def annotated(self) -> str | None:
        """Get Annotated type hint with Meta constraints.

        For ClassVar fields (discriminator tag_field), ClassVar is required
        regardless of use_annotated setting.
        """
        if self.extras.get("is_classvar"):  # pragma: no cover
            meta = self._get_meta_string()
            if self.use_annotated and meta:
                return subscript_text("ClassVar", (subscript_text("Annotated", (self.type_hint, meta)),))
            return subscript_text("ClassVar", (self.type_hint,))

        if not self.use_annotated or (plan := self._render_plan()).annotated_call is None:
            return None
        return cast("DataType", plan.written_type).type_hint

    @property
    @override
    def needs_annotated_import(self) -> bool:
        """Check if this field requires the Annotated import.

        ClassVar fields with Meta need Annotated only when use_annotated is True.
        ClassVar fields without Meta don't need Annotated.
        """
        return self._meta_call() is not None

    def _get_default_as_struct_model(self) -> _StructConversion | None:
        """Return the factory that converts the default to the Structs of the field's type, or None.

        A field of several types converts it to one member (``_union_default_as_struct_model``), a field typed as a list
        itself by its items (``_list_items_conversion``) and any other field to its one member (``_member_conversion``).
        """
        if self._uses_empty_builtin_container_factory():
            return None
        members = self._non_none_members(self.data_type)
        if self.data_type.is_union or len(members) > 1:
            return self._union_default_as_struct_model(members)
        if self.data_type.is_list:
            return self._list_items_conversion()
        return next(map(self._member_conversion, members), None)

    def _list_items_conversion(self) -> _StructConversion | None:
        """Convert the default of a field typed as a list itself to the list, as its hint writes it.

        An item that is a Struct, or a type alias whose value names Structs, also at the end of nested lists, converts
        it; otherwise it converts as to an array that names Structs (``_written_conversion``).
        """
        items = self.data_type.data_types
        if items and (item_type := items[0]).is_list:
            while item_type.is_list and len(item_type.data_types) == 1:
                item_type = item_type.data_types[0]
            if item_type.reference and isinstance(item_type.reference.source, Struct):
                items = [item_type]
        for item in items:
            if (reference := item.reference) and self._converts_to_structs(reference):
                return _StructConversion(self._PARSE_METHOD, self.default, item, self.data_type)
        return self._written_conversion(self.data_type)

    def _member_conversion(self, member: DataType) -> _StructConversion | None:
        """Return the factory that converts the default to one member of the field's type, or None.

        A list of one Struct or type alias converts a list default to ``list[Item]`` (``_list_conversion``), a Struct
        or a type alias whose value names Structs to its name, and any other array or mapping as its hint writes it
        (``_written_conversion``).
        """
        if member.is_list and len(member.data_types) == 1:
            if (
                (child := member.data_types[0]).reference
                and isinstance(child.reference.source, (Struct, TypeAliasBase))
                and isinstance(self.default, list)
            ):
                return self._list_conversion(member, child)
        elif (reference := member.reference) is not None:
            if self._converts_to_structs(reference):
                return _StructConversion(self._PARSE_METHOD, self.default, member)
            return None
        return self._written_conversion(member)

    def _converts_to_structs(self, reference: Reference) -> bool:
        """Return whether the default converts to the Struct ``reference`` names, or to its alias of Structs."""
        return isinstance(source := reference.source, Struct) or (
            isinstance(source, TypeAliasBase) and self._type_alias_needs_struct_conversion(source)
        )

    def _written_conversion(self, data_type: DataType) -> _StructConversion | None:
        """Convert the default to an array or a mapping that names Structs, as its hint writes it, where it fits it.

        The default fits it as ``_takes_default`` reads it; an empty default names no Struct inside it. The default of a
        field typed as a tuple stays the value, as ``_takes_default`` and ``_attribute_default`` read its positions as
        alternatives.
        """
        if (
            (data_type in ARRAYS or data_type in MAPPINGS)
            and not data_type.is_tuple
            and _takes_default(data_type, default := self.default)
            and _names_structs(data_type, frozenset(), containers=bool(default))
        ):
            return _StructConversion(self._PARSE_METHOD, default, data_type, data_type)
        return None

    def _union_default_as_struct_model(self, members: Sequence[DataType]) -> _StructConversion | None:
        """Convert the default of a field of several types to the first written member it fits that names Structs.

        ``members`` are the members the field's type writes (``_non_none_members``), a container of several types being
        one. A member takes the default as ``_takes_default`` reads it as JSON, else, where none does, from attributes
        (``_default_takers``), and converts it as ``_member_conversion`` does. An object default beside a dict member
        converts only to a dict member.
        """
        beside_dict = isinstance(default := self.default, dict) and any(member.is_dict for member in members)
        takers = _default_takers([member for member in members if member.is_dict or not beside_dict], default)
        return next(filter(None, map(self._member_conversion, takers)), None)

    def _list_conversion(self, data_type: DataType, item: DataType) -> _StructConversion:
        """Return the factory that converts a list default to the list ``data_type`` of ``item``.

        It converts to ``list[Item]``, or to the list as the field writes it where an item of the default is None, which
        only an item type that admits None takes.
        """
        if item.is_optional and None in cast("list[object]", self.default):
            return _StructConversion(self._PARSE_METHOD, self.default, item, data_type)
        return _StructConversion(self._PARSE_METHOD, self.default, item, of_list=True)

    def _type_alias_needs_struct_conversion(self, source: TypeAliasBase) -> bool:
        """Return whether an array or object default converts to a type alias whose value names Structs.

        An empty array converts to none, and an empty object to one whose value names a Struct outside its containers.
        """
        if not source.fields or not isinstance(default := self.default, (dict, list)) or default == []:
            return False
        return _names_structs(source.fields[0].data_type, frozenset({id(source)}), containers=bool(default))

    def _uses_empty_builtin_container_factory(self) -> bool:
        """Return whether an empty collection can use its zero-cost builtin factory."""
        if self.default:
            return False

        match self.default:
            case list():
                container_attribute = "is_list"
            case dict():
                container_attribute = "is_dict"
            case set():
                container_attribute = "is_set"
            case _:
                return False

        if getattr(self.data_type, container_attribute):
            return True
        return any(getattr(data_type, container_attribute) for data_type in self.data_type.data_types)

    def _get_default_factory_for_optional_nested_model(self) -> _NestedModelFactory | None:
        """Get default_factory for optional nested Struct model fields.

        Returns the factory of the nested model if the field type references a Struct, otherwise None.
        """
        return _nested_model_factory(self, Struct)


class DataTypeManager(_DataTypeManager):
    """Type manager for msgspec Struct models."""

    SUPPORTS_ANNOTATED_CONSTRAINTS: ClassVar[bool] = True
    ANNOTATED_CONSTRAINTS_CONTEXT: ClassVar[object | None] = _ANNOTATED_CONSTRAINTS_CONTEXT
    CONSTRAINT_KEYWORDS: ClassVar[Mapping[str, frozenset[str]]] = _applicability.MSGSPEC_OUTPUT.type_keywords
    ANNOTATION_UNIONS_TAKE_CONSTRAINTS: ClassVar[bool] = _applicability.MSGSPEC_OUTPUT.annotation_unions_take
    FIXED_TUPLES_TAKE_LENGTH: ClassVar[bool] = _applicability.MSGSPEC_OUTPUT.fixed_tuples_take_length
    LITERALS_TAKE_CONSTRAINTS: ClassVar[bool] = _applicability.MSGSPEC_OUTPUT.literals_take
    REFUSED_LITERAL_TYPES: ClassVar[tuple[type, ...]] = _applicability.MSGSPEC_OUTPUT.refused_literal_types
    MAPPING_CLASS_PATHS: ClassVar[frozenset[str]] = _applicability.MSGSPEC_OUTPUT.mapping_classes
    REGEX_ENGINES: ClassVar[frozenset[str]] = _applicability.MSGSPEC_OUTPUT.regex_engines

    @property
    @override
    def checks_map_key_patterns(self) -> bool:
        """Whether the keys of a map are written as ``Annotated[str, Meta(...)]`` (--use-msgspec-meta-for-map-keys)."""
        return self.use_msgspec_meta_for_map_keys

    @override
    def get_map_key_str_type(self, **kwargs: Any) -> DataType:
        """Return ``Annotated[str, Meta(...)]`` under --use-msgspec-meta-for-map-keys, so msgspec checks the keys.

        Without it the key is ``str``, which accepts every key, as in 0.88.0.
        """
        if not self.use_msgspec_meta_for_map_keys or not (
            keywords := {meta: kwargs[key] for key, meta in _META_KEY_KEYWORDS.items() if kwargs.get(key) is not None}
        ):
            return super().get_map_key_str_type(**kwargs)
        return self._context_class("MsgspecMetaStrKeyType", _MetaType)(type="str", meta_keywords=keywords)


_rebuild_model_with_datamodel_namespace(Constraints)
_rebuild_model_with_datamodel_namespace(DataModelField)
