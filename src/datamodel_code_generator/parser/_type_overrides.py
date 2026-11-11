"""How the parser applies ``--type-overrides`` and binds ``x-python-type``: type paths, annotations and constraints.

It names a user-named type path for the target (an error for a type the target lacks, or once output imports it),
binds the names and imports of a Python type annotation, replaces the type of a field or of a model's references by the
type an override names, and decides which constraints the replaced type kept that the override type takes. Each
function takes the parser through ``TypeOverridesHost``, which ``Parser`` satisfies structurally, and the parser and the
passes call these functions; ``Parser._resolve_type_import_from_defs``, which ``JsonSchemaParser`` overrides, stays a
parser method.
"""

from __future__ import annotations

from functools import partial
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from typing_extensions import Protocol

from datamodel_code_generator import Error
from datamodel_code_generator._constraint_applicability import (
    COUNT_KEYWORDS,
    ITEM_COUNT_KEYWORDS,
    LENGTH_KEYWORDS,
    overridden_declared_constraints,
    resplit_overridden_constraints,
    untaken_minimums,
)
from datamodel_code_generator._container_kinds import ARRAYS, CONTAINER_FLAGS, MAPPINGS
from datamodel_code_generator._model_keys import model_key_of
from datamodel_code_generator._module_paths import dotted_name
from datamodel_code_generator._requirements import REQUIREMENTS, python_target_meets
from datamodel_code_generator._target_unicode import is_dotted_python_name
from datamodel_code_generator._user_keys import model_type_override
from datamodel_code_generator.enums import _pydantic_version_key
from datamodel_code_generator.imports import Import, _import_key, _import_path
from datamodel_code_generator.model.base import BaseClassDataType, ConstraintsBase
from datamodel_code_generator.parser._passes._keys import site_constraints
from datamodel_code_generator.parser._passes._shared import (
    _TYPE_DEPENDENT_CONSTRAINTS_KEY,
    TypeOverrideHost,
    enum_site_override_import,
)
from datamodel_code_generator.python_literal import message_repr
from datamodel_code_generator.types import _copy_data_type, _value_members

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from datamodel_code_generator._format_types import PythonVersion
    from datamodel_code_generator._python_type_annotation import PythonTypeExpr
    from datamodel_code_generator._python_type_binding import BoundPythonType
    from datamodel_code_generator.enums import TargetPydanticVersion
    from datamodel_code_generator.imports import Imports, _ImportKey
    from datamodel_code_generator.model.base import DataModelFieldBase
    from datamodel_code_generator.parser._passes._state import ModuleContextView
    from datamodel_code_generator.parser.generation import GraphBuilder
    from datamodel_code_generator.types import DataType, DataTypeManager

__all__ = [
    "TypeOverridesHost",
    "apply_override_to_data_type",
    "apply_override_to_field",
    "bind_python_type",
    "decide_overridden_constraints",
    "get_emitted_python_type_path",
    "get_target_python_type_path",
    "is_target_python_builtin_type",
    "mark_untaken_constraints",
    "override_annotation",
    "override_spells_builtin",
    "python_type_data_type",
    "record_overridden_constraints",
    "reject_unavailable_imports",
    "resolve_qualified_type_import",
    "resolve_type_import",
]


class TypeOverridesHost(TypeOverrideHost, Protocol):
    """The options, run state and hooks of the parser that the type overrides read."""

    @property
    def data_model_field_type(self) -> type[DataModelFieldBase]:
        """The field class of the output backend."""

    @property
    def data_type(self) -> type[DataType]:
        """The data type class of the type manager."""

    @property
    def data_type_manager(self) -> DataTypeManager:
        """The type manager of the output backend."""

    @property
    def imports(self) -> Imports:
        """The imports every module of the output takes."""

    @property
    def target_pydantic_version(self) -> TargetPydanticVersion | None:
        """The Pydantic version the output targets (``--target-pydantic-version``)."""

    @property
    def target_python_version(self) -> PythonVersion:
        """The Python version the output targets."""

    @property
    def type_overrides_replace_whole_type(self) -> bool:
        """Whether an override replaces a field's whole type and counts (``--type-overrides-replace-whole-type``)."""

    @property
    def _graph_builder(self) -> GraphBuilder:
        """The capability through which the parser changes the graph of the generation store."""

    @property
    def _unavailable_imports(self) -> dict[tuple[str | None, str], str]:
        """The imports of types the target lacks, with the error each raises once output imports it."""

    def _resolve_type_import_from_defs(self, type_name: str) -> Import | PythonTypeExpr | None:
        """Resolve a type name by the ``$defs`` of the document, which only a JSON Schema document has."""


_QUALIFIED_PYTHON_TYPE_IMPORT_ALIASES = {
    "pydantic.main.BaseModel": Import.from_full_path("pydantic.BaseModel"),
}


def _parse_override_annotation(value: str) -> PythonTypeExpr | None:
    """Return the annotation a ``--type-overrides`` value spells, or None when it is no annotation."""
    from datamodel_code_generator._python_type_annotation_codec import (  # noqa: PLC0415
        parse_python_type_annotation,
    )

    return parse_python_type_annotation(value)


def _qualified_python_type_import(qualified_name: str) -> Import:
    return _QUALIFIED_PYTHON_TYPE_IMPORT_ALIASES.get(qualified_name) or Import.from_full_path(qualified_name)


def get_target_python_type_path(parser: TypeOverridesHost, path: str) -> str:
    """Return a user-named type path for the target, naming a newer typing name by its backport.

    A standard-library type that the target lacks is an error, as for ``x-python-type``.
    """
    from datamodel_code_generator._python_type_import_registry import (  # noqa: PLC0415
        PythonTypeUnavailableError,
        get_qualified_python_type_import_path,
    )

    try:
        return get_qualified_python_type_import_path(path, parser.target_python_version.version_key)
    except PythonTypeUnavailableError as exc:
        raise Error(str(exc)) from exc


def get_emitted_python_type_path(parser: TypeOverridesHost, path: str) -> str:
    """Return a user-named type path for the target, rejecting a type the target lacks only once output imports it.

    A type that an override replaces or that no model uses emits no import, so it is left alone.
    """
    try:
        return get_target_python_type_path(parser, path)
    except Error as exc:
        import_ = Import.from_full_path(path)
        parser._unavailable_imports[_import_key(import_)] = str(exc)  # noqa: SLF001
        return path


def reject_unavailable_imports(parser: TypeOverridesHost, contexts: Sequence[ModuleContextView]) -> None:
    """Reject output that imports a type the target lacks, after overrides, unused models and import remapping.

    The first such type in schema order is reported, so the message does not depend on set iteration order.
    """
    all_imports = (parser.imports, *(ctx.imports for ctx in contexts))
    for (from_, name), message in parser._unavailable_imports.items():  # noqa: SLF001
        if any(name in imports.get(from_, ()) for imports in all_imports):
            raise Error(message)


def resolve_type_import(parser: TypeOverridesHost, type_name: str) -> Import | None:
    """Resolve a type through target-stable metadata, never host imports."""
    from datamodel_code_generator._python_type_import_registry import (  # noqa: PLC0415
        PythonTypeUnavailableError,
        get_python_type_import_path,
    )

    try:
        import_path = get_python_type_import_path(type_name, parser.target_python_version.version_key)
    except PythonTypeUnavailableError as exc:
        raise Error(str(exc)) from exc
    if import_path:
        return Import.from_full_path(import_path)
    return None


def is_target_python_builtin_type(parser: TypeOverridesHost, type_name: str) -> bool:
    """Recognize builtins against the target version, never the host runtime."""
    from datamodel_code_generator._python_type_import_registry import (  # noqa: PLC0415
        is_python_builtin_type_name,
    )

    return is_python_builtin_type_name(type_name, parser.target_python_version.version_key)


def resolve_qualified_type_import(parser: TypeOverridesHost, qualified_name: str) -> Import:
    """Resolve known stdlib paths against the target without host imports."""
    return _qualified_python_type_import(get_target_python_type_path(parser, qualified_name))


def _registry_import(binding: object, version_key: tuple[int, int]) -> bool:
    """Return whether what a bare name binds is the import of a type that the registry lists for the target."""
    from datamodel_code_generator._python_type_import_registry import is_known_python_type_path  # noqa: PLC0415

    return isinstance(binding, Import) and is_known_python_type_path(_import_path(binding), version_key)


def _write_refused_literal_values(parser: TypeOverridesHost, written: PythonTypeExpr) -> PythonTypeExpr:
    """Return the type with the values of its ``Literal`` that the output refuses written as their types, and warn.

    A msgspec ``Literal`` holds no float and no bytes (``REFUSED_LITERAL_TYPES``), so ``Literal[5.0]`` becomes
    ``float``, which msgspec does not check against the value.
    """
    if not (refused := parser.data_type_manager.REFUSED_LITERAL_TYPES):
        return written
    from datamodel_code_generator._message_text import for_count  # noqa: PLC0415
    from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
        render_python_type_expr,
        write_refused_literal_values,
    )
    from datamodel_code_generator.parser._passes._warnings import warn_as_parser_base  # noqa: PLC0415

    read, values = write_refused_literal_values(written, refused)
    if values:
        listed = tuple(dict.fromkeys(map(render_python_type_expr, values)))
        warn_as_parser_base(
            f"msgspec cannot decode {render_python_type_expr(written)}: a msgspec Literal holds only None, bool, "
            f"int and str values, not {', '.join(listed)}. It is written as {render_python_type_expr(read)}, so "
            f"msgspec does not check {for_count(len(listed), 'it', 'them')}.",
            UserWarning,
            stacklevel=2,
        )
    return read


def bind_python_type(parser: TypeOverridesHost, expression: PythonTypeExpr) -> BoundPythonType:
    """Bind names/imports without returning to runtime-dependent text parsing.

    ``expression`` was parsed only at the external JSON Schema boundary.
    Keep the IR through internal stages: rendering and reparsing here would
    incorrectly make the host AST/tokenizer an authority for target syntax.
    """
    # Binding is exclusive to x-python-type. Import it only after that
    # extension is present so ordinary schema generation keeps its fast path.
    from datamodel_code_generator._python_target_names import target_stdlib_module_names  # noqa: PLC0415
    from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
        PythonTypeBoundName,
        PythonTypeExpr,
        PythonTypeName,
        PythonTypeQualifiedName,
        PythonTypeRuntimeSymbol,
        read_literal_enum_members,
        rewrite_python_type_expr,
    )
    from datamodel_code_generator._python_type_binding import BoundPythonType  # noqa: PLC0415

    imports: dict[Import, None] = {}
    bound_imports: dict[_ImportKey, Import] = {}
    resolved_name_imports: dict[str, Import | PythonTypeExpr | None] = {}
    resolving_names: set[str] | None = None

    def bind_import(import_: Import) -> tuple[Import, str]:
        key = _import_key(import_)
        if (bound_import := bound_imports.get(key)) is not None:
            return bound_import, bound_import.binding_name

        name = import_.binding_name
        bound_imports[key] = import_
        imports.setdefault(import_, None)
        return import_, name

    def resolve_name(name: str) -> Import | PythonTypeExpr | None:
        """Return what a bare name binds: nothing for a builtin, else the registry's import or the schema's."""
        if name not in resolved_name_imports:
            try:
                is_builtin = is_target_python_builtin_type(parser, name)
                import_ = None if is_builtin else resolve_type_import(parser, name)
            except Error:
                if not (import_ := parser._resolve_type_import_from_defs(name)):  # noqa: SLF001
                    raise
                resolved_name_imports[name] = import_
            else:
                resolved_name_imports[name] = (
                    None if is_builtin else import_ or parser._resolve_type_import_from_defs(name)  # noqa: SLF001
                )
        return resolved_name_imports[name]

    def names_class(name: str) -> bool:
        """Return whether a bare name binds a class that it imports.

        A name of a standard-library module of the target stays that module, unless the schema imports a class by
        that name, which the registry does not list.
        """
        version_key = parser.target_python_version.version_key
        return isinstance(binding := resolve_name(name), Import | PythonTypeRuntimeSymbol) and (
            name not in target_stdlib_module_names(version_key) or not _registry_import(binding, version_key)
        )

    def bind_leaf(item: PythonTypeExpr) -> PythonTypeExpr:
        nonlocal resolving_names
        match item:
            case PythonTypeQualifiedName():
                import_, name = bind_import(resolve_qualified_type_import(parser, ".".join(item.parts)))
                return PythonTypeBoundName(name, import_.from_, import_.import_)
            case PythonTypeRuntimeSymbol(module=module) if module:
                import_, _ = bind_import(Import(import_=module))
                return PythonTypeRuntimeSymbol(import_.alias, item.qualname_parts) if import_.alias else item
            case PythonTypeName(value=name):
                if isinstance(resolved := resolve_name(name), PythonTypeExpr):
                    if resolving_names is None:
                        resolving_names = set()
                    if name in resolving_names:
                        msg = f"Cyclic x-python-import type expression involving {message_repr(name)}"
                        raise Error(msg)
                    resolving_names.add(name)
                    try:
                        return rewrite_python_type_expr(
                            read_literal_enum_members(_write_refused_literal_values(parser, resolved), names_class),
                            bind_leaf,
                        )
                    finally:
                        resolving_names.remove(name)
                if resolved:
                    import_, bound_name = bind_import(resolved)
                    return PythonTypeBoundName(bound_name, import_.from_, import_.import_)
        return item

    return BoundPythonType(
        rewrite_python_type_expr(
            read_literal_enum_members(_write_refused_literal_values(parser, expression), names_class), bind_leaf
        ),
        tuple(imports),
    )


def python_type_data_type(
    parser: TypeOverridesHost, expression: PythonTypeExpr, *, is_optional: bool = False
) -> DataType:
    """Return the type a Python type expression spells: its bound names and imports, written as the bound expression.

    ``x-python-type``, an ``x-python-import`` that names a runtime symbol and a ``--type-overrides`` annotation each
    become a type this way.
    """
    from datamodel_code_generator._python_type_annotation import render_python_type_expr  # noqa: PLC0415

    bound_type = bind_python_type(parser, expression)
    return parser.data_type(
        type=render_python_type_expr(bound_type.expression), python_type=bound_type, is_optional=is_optional
    )


def _override_expression(parser: TypeOverridesHost, path: str) -> PythonTypeExpr | None:
    """Return the annotation a ``--type-overrides`` value spells, or None when it names a class to import.

    A value that names no type, such as an empty one, is an error, and so is a value with brackets that is no
    annotation, as neither has an import to write. A value without brackets names a class to import when it is a
    dotted path, relative (``..types.T``) or not, or a bare name that no builtin and no standard-library type of the
    registry has; a bare name that one has, or a union of names such as ``str | None``, is an annotation. A name or a
    dotted path is read by the target's identifier characters, which the parser of the Python that runs the generator
    may not know; the parser reads the other values.
    """
    from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
        PythonTypeName,
        PythonTypeQualifiedName,
    )

    name = path.strip() if path.strip().startswith(".") else path
    if "[" not in name and is_dotted_python_name(name.lstrip("."), parser.target_python_version):
        if "." in name or not (is_target_python_builtin_type(parser, path) or resolve_type_import(parser, path)):
            return None
        return PythonTypeName(path)
    expression = _parse_override_annotation(path)
    if expression is None and ("[" in path or _parse_override_annotation(path.strip()) is None):
        msg = f"--type-overrides value {message_repr(path)} must be a valid Python type annotation"
        raise Error(msg)
    return None if expression is None or isinstance(expression, PythonTypeQualifiedName) else expression


def override_annotation(
    parser: TypeOverridesHost, override_import: Import, *, is_optional: bool = False
) -> DataType | None:
    """Return the type a ``--type-overrides`` value spells as an annotation, or None when it names a class.

    A value that is an annotation, such as ``typing.Literal[1, 2]``, ``str`` or ``str | None``, names no class to
    import: it is written as ``x-python-type`` writes it, with the imports of its names, none for a builtin, and the
    keywords that type takes.
    """
    if (expression := _override_expression(parser, _import_path(override_import))) is None:
        return None
    return python_type_data_type(parser, expression, is_optional=is_optional)


def override_spells_builtin(override_import: Import | None, names: frozenset[str]) -> bool:
    """Return whether a ``--type-overrides`` value spells one of the builtins ``names``, alone or unioned with ``None``.

    ``bytes``, ``builtins.bytes``, ``bytes | None``, ``None | bytes``, ``typing.Optional[bytes]`` and
    ``Union[bytes, None]`` spell ``bytes``. A union of two builtins, ``bytes | Decimal``, ``list[bytes]`` and a class
    path such as ``module.bytes`` spell none.
    """
    from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
        PythonTypeName,
        PythonTypeQualifiedName,
        is_union_python_type_expr,
        python_type_expr_arguments,
    )

    if override_import is None or (expression := _parse_override_annotation(_import_path(override_import))) is None:
        return False
    spelled: set[str] = set()
    pending = [expression]
    while pending:
        member = pending.pop()
        if is_union_python_type_expr(member):
            pending.extend(python_type_expr_arguments(member))
        elif isinstance(member, PythonTypeName):
            spelled.add(member.value)
        elif isinstance(member, PythonTypeQualifiedName) and member.parts[:-1] == ("builtins",):
            spelled.add(member.parts[-1])
        else:
            return False
    spelled.discard("None")
    return len(spelled) == 1 and spelled <= names


def apply_override_to_field(parser: TypeOverridesHost, field: DataModelFieldBase, override_import: Import) -> None:
    """Apply override to entire field's data_type, keeping type-dependent constraints that the new type takes.

    A value with brackets is an annotation: it drops the arguments of every ``con*`` call the field held and
    takes the keywords that annotation takes; the field keeps the None its schema admits.

    The arguments of a ``con*`` call stay on an override the generator does not know, which may be a factory such
    as ``pydantic.conint``; a known class takes none, so they are left out, and so are those of the nested types the
    override replaces. An output model that writes ``Field()`` arguments warns about the ones it leaves out.

    Row ``legacy-class-override-keeps-container``: without ``--type-overrides-replace-whole-type`` a class keeps the
    container of the field's own type node, so the override of an object map is written as its values
    (``dict[str, X]``, ``_override_node``), and every override keeps the item or property counts of the array or map
    it replaces (``_untaken_container_counts``).
    """
    if (data_type := override_annotation(parser, override_import, is_optional=field.data_type.is_optional)) is not None:
        record_overridden_constraints(parser, field, keeps_top_kwargs=False)
        decide_overridden_constraints(
            parser,
            field,
            partial(parser.data_type_manager.constraint_keywords, data_type, parser.target_python_version.version_key),
        )
        parser._graph_builder.replace_field_type(field, data_type)  # noqa: SLF001
        return
    from datamodel_code_generator._python_type_import_registry import (  # noqa: PLC0415
        is_known_python_type_path,
    )

    data_type = _override_node(field.data_type, whole_type=parser.type_overrides_replace_whole_type)
    _drop_site_constraints(parser, data_type)
    data_type.import_ = override_import
    data_type.alias = override_import.import_
    path = _import_path(override_import)
    if data_type.kwargs and is_known_python_type_path(path, parser.target_python_version.version_key):
        data_type.kwargs = None
    record_overridden_constraints(parser, field, keeps_top_kwargs=data_type.kwargs is not None)
    decide_overridden_constraints(
        parser,
        field,
        partial(parser.data_type_manager.type_constraint_keywords, path, parser.target_python_version.version_key),
    )
    parser._graph_builder.replace_field_type(field, data_type)  # noqa: SLF001
    parser._graph_builder.detach_data_type_ref(field.data_type)  # noqa: SLF001
    parser._graph_builder.set_nested_data_types(field.data_type, [])  # noqa: SLF001


_WITHOUT_CONTAINER: Final[Mapping[str, Any]] = MappingProxyType({
    **dict.fromkeys(CONTAINER_FLAGS.values(), False),
    "data_types": (),
    "dict_key": None,
    "tuple_item_count": None,
})
"""The values of a type node that holds no container: no container slot, member, dict key or tuple length."""


def _drop_site_constraints(parser: TypeOverridesHost, data_type: DataType) -> None:
    """Drop the constraints the allOf site of ``data_type`` adds to the target it named, which leave with it."""
    if site_constraints(data_type):
        parser._graph_builder.record_site_constraints(data_type, {})  # noqa: SLF001


def _override_node(data_type: DataType, *, whole_type: bool) -> DataType:
    """Return the copy of a field's own type node that a class override is written on, with its nullability.

    With ``whole_type`` the copy holds none of the container it replaces (``_WITHOUT_CONTAINER``), so an object map
    gives the class itself; the members of any other copy leave once the override replaces the type.
    """
    return _copy_data_type(data_type.model_copy(update=_WITHOUT_CONTAINER) if whole_type else data_type)


def _untaken_container_counts(
    field: DataModelFieldBase, keys: Iterable[str], keywords: Callable[..., frozenset[str]]
) -> list[str]:
    """Return the lengths among ``keys`` that count the map or the array an override replaces and it does not count.

    The lengths and item counts (``minItems``, as msgspec's constraints name a length) of a field whose every value is
    a map are its property counts, and of one whose every value is an array its item counts, as no string length is
    written beside them; the override counts the keys when it takes the lengths as a map's
    (``keywords(counts_keys=True)``: a mapping class, or a type the generator cannot classify) and the items when it
    takes the array's counts (a sequence). The lengths of any other field are not decided here (row
    ``legacy-override-keeps-string-keywords``).
    """
    if not (lengths := [key for key in keys if key in COUNT_KEYWORDS]) or not (
        members := [*_value_members(field.data_type)]
    ):
        return []
    if all(member in MAPPINGS for member in members):
        return [] if keywords(counts_keys=True) >= LENGTH_KEYWORDS else lengths
    if all(member in ARRAYS for member in members):
        return [] if keywords() >= ITEM_COUNT_KEYWORDS else lengths
    return []


def record_overridden_constraints(
    parser: TypeOverridesHost, field: DataModelFieldBase, *, keeps_top_kwargs: bool
) -> None:
    """Remember how many ``con*`` arguments the override of a field drops, to warn about them.

    The ``con*`` call of the field's own type stays where ``keeps_top_kwargs``; those of its nested types leave.
    """
    if parser.data_model_field_type.SUPPORTS_FIELD_CONSTRAINTS and (
        dropped := [
            name
            for nested in field.data_type.all_data_types
            if nested is not field.data_type or not keeps_top_kwargs
            for name in nested.kwargs or ()
            if name != "strict"
        ]
    ):
        field.record_overridden_constraints(field.data_type.type_hint, len(dropped))


def decide_overridden_constraints(
    parser: TypeOverridesHost, field: DataModelFieldBase, keywords: Callable[..., frozenset[str]]
) -> None:
    """Keep the constraints that the emitted type decides only when the override type takes them.

    ``keywords`` returns what that type takes, as ``DataTypeManager.type_constraint_keywords`` does for its path.

    An output model that writes constraints as ``Field()`` arguments also decides the number constraints of a
    declared number and the string constraints the replaced type left out this way, and remembers the ones it
    leaves out to warn about them. Under ``--type-overrides-replace-whole-type`` every output model leaves out the
    item or property counts of the array or map the override replaces that the override type does not count
    (``_untaken_container_counts``), which warn as the constraints that leave with the replaced type. The untaken
    constraints of the members, items and values that collapsing inlines into the field leave with the type the
    override replaces.
    """
    type_dependent = field.__dict__.get(_TYPE_DEPENDENT_CONSTRAINTS_KEY) or {}
    constraints = field.constraints
    is_model = isinstance(constraints, ConstraintsBase)
    values = (
        constraints.set_values()
        if is_model
        else {key: value for key, value in (constraints or {}).items() if value is not None}
    )
    declared = (
        overridden_declared_constraints(values, type_dependent=type_dependent, untaken=field.pop_untaken_constraints())
        if parser.data_model_field_type.SUPPORTS_FIELD_CONSTRAINTS
        else {}
    )
    counts = (
        _untaken_container_counts(field, (key for key in values if key not in type_dependent), keywords)
        if parser.type_overrides_replace_whole_type
        else []
    )
    if not type_dependent and not declared and not counts:
        return
    if counts:
        values = {key: value for key, value in values.items() if key not in counts}
        replaced, count = field.overridden_constraints or (field.data_type.type_hint, 0)
        field.record_overridden_constraints(replaced, count + len(counts))
    values, untaken = resplit_overridden_constraints(
        values,
        type_dependent=type_dependent,
        declared=declared,
        taken=keywords(),
        declared_taken=keywords(number_values=True, target_model_version=parser.target_pydantic_version),
    )
    mark_untaken_constraints(
        parser, field, untaken, lambda minimum: keywords(number_values=True, target_model_version=minimum)
    )
    parser._graph_builder.set_field_constraints(  # noqa: SLF001
        field, type(constraints).model_validate(values) if is_model else values
    )


def mark_untaken_constraints(
    parser: TypeOverridesHost,
    field: DataModelFieldBase,
    untaken: dict[str, Any],
    keywords_for: Callable[[str], frozenset[str]],
) -> None:
    """Remember the declared constraints a field leaves out, and the target version that would take each.

    ``keywords_for`` returns the number keywords the field's type takes for an explicit target model version.
    """
    if not untaken:
        return
    field.record_untaken_constraints(
        untaken,
        untaken_minimums(
            untaken,
            (
                (minimum, keywords_for(minimum))
                for minimum in sorted(
                    {
                        REQUIREMENTS[construct].minimum
                        for construct, _ in parser.data_type_manager.TARGET_NUMBER_KEYWORDS.values()
                    },
                    key=_pydantic_version_key,
                )
            ),
        ),
    )


def _class_path(annotation: DataType) -> str | None:
    """Return the path of the class an override annotation names as a bare class name, such as ``str`` or ``Decimal``.

    None for any other annotation, and for ``None``.
    """
    from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
        PythonTypeBoundName,
        PythonTypeName,
    )

    expression = None if annotation.python_type is None else annotation.python_type.expression
    if not isinstance(expression, PythonTypeName | PythonTypeBoundName) or expression.value == "None":
        return None
    return (
        dotted_name(expression.import_from, expression.import_name)
        if isinstance(expression, PythonTypeBoundName)
        else expression.value
    )


def _base_class_refusal(parser: TypeOverridesHost, override_import: Import, annotation: DataType | None) -> str | None:
    """Return why an override cannot replace a base class, or None when it names a class.

    A class path names a class, and so does an annotation that is a bare class name (``_class_path``);
    ``typing.Any``, by its path or as ``Any``, is a class only on a target that can subclass it.
    """
    path = _import_path(override_import)
    if annotation is not None and (path := _class_path(annotation)) is None:
        return "is no class"
    if path == "typing.Any" and not python_target_meets(
        "python.subclass_any", parser.target_python_version.version_key
    ):
        return f"is no class before Python {REQUIREMENTS['python.subclass_any'].minimum}"
    return None


def apply_override_to_data_type(parser: TypeOverridesHost, data_type: DataType) -> bool:
    """Recursively apply model-level overrides to a DataType; a stand-in takes the import it stands for first.

    A base class takes a class: a class path, or an annotation that is a bare class name such as ``str`` or
    ``Decimal``. Any other annotation, such as ``typing.Literal[1, 2]``, ``list[int]``, ``str | None`` or ``None``, is
    no class, and replacing a base class with it is an error.
    """
    overridden = False
    if (reference := data_type.reference) and (
        override_import := parser._base_stand_in_imports.get(model_key_of(reference))  # noqa: SLF001
        or model_type_override(parser._model_type_override_imports, reference, row="class-name")  # noqa: SLF001
        or enum_site_override_import(parser, reference)
    ):
        annotation = override_annotation(parser, override_import)
        if isinstance(data_type, BaseClassDataType) and (
            refusal := _base_class_refusal(parser, override_import, annotation)
        ):
            name, value = message_repr(reference.name), message_repr(_import_path(override_import))
            msg = f"--type-overrides {name}: {value} {refusal}, so it cannot replace {reference.name} as a base class"
            raise Error(msg)
        if annotation is None:
            parser._graph_builder.override_type_import(data_type, override_import)  # noqa: SLF001
            parser._graph_builder.name_reference(data_type, override_import.binding_name)  # noqa: SLF001
            parser._graph_builder.detach_data_type_ref(data_type)  # noqa: SLF001
        else:
            parser._graph_builder.name_reference(data_type, annotation.type_hint)  # noqa: SLF001
            parser._graph_builder.detach_data_type_ref(data_type)  # noqa: SLF001
            parser._graph_builder.set_type_expressions(  # noqa: SLF001
                data_type, type_=annotation.type, python_type=annotation.python_type
            )
        _drop_site_constraints(parser, data_type)
        overridden = True
    # Handle nested types (List[CustomType], Optional[CustomType], etc.)
    for nested in data_type.data_types:
        overridden = apply_override_to_data_type(parser, nested) or overridden
    if data_type.dict_key:
        overridden = apply_override_to_data_type(parser, data_type.dict_key) or overridden
    return overridden
