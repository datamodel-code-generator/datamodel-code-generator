"""How the parser applies ``--type-overrides`` and binds ``x-python-type``: type paths, annotations and constraints.

It names a user-named type path for the target (an error for a type the target lacks, or once output imports it),
binds the names and imports of a Python type annotation, replaces the type of a field or of a model's references by the
type an override names, and decides which constraints the replaced type kept that the override type takes. Each
function takes the parser through ``TypeOverridesHost``, which ``Parser`` satisfies structurally. The methods stay on
``Parser`` as delegators to these functions, and these functions call them through the parser, so subclasses still
override them; ``Parser._resolve_type_import_from_defs``, which ``JsonSchemaParser`` overrides, stays a parser method.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any

from typing_extensions import Protocol

from datamodel_code_generator import Error
from datamodel_code_generator._constraint_applicability import (
    overridden_declared_constraints,
    resplit_overridden_constraints,
    untaken_minimums,
)
from datamodel_code_generator._model_keys import model_key_of
from datamodel_code_generator._requirements import REQUIREMENTS
from datamodel_code_generator.enums import _pydantic_version_key
from datamodel_code_generator.imports import Import, _import_path
from datamodel_code_generator.model.base import ConstraintsBase
from datamodel_code_generator.parser._passes._shared import (
    _TYPE_DEPENDENT_CONSTRAINTS_KEY,
    TypeOverrideHost,
    enum_site_override_import,
)
from datamodel_code_generator.types import _copy_data_type

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from datamodel_code_generator._format_types import PythonVersion
    from datamodel_code_generator._python_type_annotation import PythonTypeExpr
    from datamodel_code_generator._python_type_binding import BoundPythonType
    from datamodel_code_generator.enums import TargetPydanticVersion
    from datamodel_code_generator.imports import Imports
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
    def _graph_builder(self) -> GraphBuilder:
        """The capability through which the parser changes the graph of the generation store."""

    @property
    def _unavailable_imports(self) -> dict[tuple[str | None, str], str]:
        """The imports of types the target lacks, with the error each raises once output imports it."""

    def _apply_override_to_data_type(self, data_type: DataType) -> bool:
        """Recursively apply model-level overrides to a DataType; a stand-in takes the import it stands for first."""

    def _bind_python_type(self, expression: PythonTypeExpr) -> BoundPythonType:
        """Bind names/imports without returning to runtime-dependent text parsing."""

    def _decide_overridden_constraints(
        self, field: DataModelFieldBase, keywords: Callable[..., frozenset[str]]
    ) -> None:
        """Keep the constraints that the emitted type decides only when the override type takes them."""

    def _get_target_python_type_path(self, path: str) -> str:
        """Return a user-named type path for the target, naming a newer typing name by its backport."""

    def _is_target_python_builtin_type(self, type_name: str) -> bool:
        """Recognize builtins against the target version, never the host runtime."""

    def _mark_untaken_constraints(
        self,
        field: DataModelFieldBase,
        untaken: dict[str, Any],
        keywords_for: Callable[[str], frozenset[str]],
    ) -> None:
        """Remember the declared constraints a field leaves out, and the target version that would take each."""

    def _override_annotation(self, override_import: Import, *, is_optional: bool = False) -> DataType | None:
        """Return the type a ``--type-overrides`` value spells as an annotation, or None when it names a class."""

    def _record_overridden_constraints(self, field: DataModelFieldBase, *, keeps_top_kwargs: bool) -> None:
        """Remember how many ``con*`` arguments the override of a field drops, to warn about them."""

    def _resolve_qualified_type_import(self, qualified_name: str) -> Import:
        """Resolve known stdlib paths against the target without host imports."""

    def _resolve_type_import(self, type_name: str) -> Import | None:
        """Resolve a type through target-stable metadata, never host imports."""

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
        return parser._get_target_python_type_path(path)  # noqa: SLF001
    except Error as exc:
        import_ = Import.from_full_path(path)
        parser._unavailable_imports[import_.from_, import_.import_] = str(exc)  # noqa: SLF001
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
    return _qualified_python_type_import(parser._get_target_python_type_path(qualified_name))  # noqa: SLF001


def bind_python_type(parser: TypeOverridesHost, expression: PythonTypeExpr) -> BoundPythonType:
    """Bind names/imports without returning to runtime-dependent text parsing.

    ``expression`` was parsed only at the external JSON Schema boundary.
    Keep the IR through internal stages: rendering and reparsing here would
    incorrectly make the host AST/tokenizer an authority for target syntax.
    """
    # Binding is exclusive to x-python-type. Import it only after that
    # extension is present so ordinary schema generation keeps its fast path.
    from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
        PythonTypeBoundName,
        PythonTypeExpr,
        PythonTypeName,
        PythonTypeQualifiedName,
        PythonTypeRuntimeSymbol,
        read_literal_enum_members,
        rewrite_python_type_expr,
    )
    from datamodel_code_generator._python_type_binding import (  # noqa: PLC0415
        BoundPythonType,
        python_type_import_key,
        python_type_import_name,
    )

    imports: dict[Import, None] = {}
    bound_imports: dict[tuple[str | None, str], Import] = {}
    resolved_name_imports: dict[str, Import | PythonTypeExpr | None] = {}
    resolving_names: set[str] | None = None

    def bind_import(import_: Import) -> tuple[Import, str]:
        key = python_type_import_key(import_)
        if (bound_import := bound_imports.get(key)) is not None:
            return bound_import, python_type_import_name(bound_import)

        name = python_type_import_name(import_)
        bound_imports[key] = import_
        imports.setdefault(import_, None)
        return import_, name

    def bind_leaf(item: PythonTypeExpr) -> PythonTypeExpr:
        nonlocal resolving_names
        match item:
            case PythonTypeQualifiedName():
                import_, name = bind_import(parser._resolve_qualified_type_import(".".join(item.parts)))  # noqa: SLF001
                return PythonTypeBoundName(name, import_.from_, import_.import_)
            case PythonTypeRuntimeSymbol(module=module) if module:
                import_, _ = bind_import(Import(import_=module))
                return PythonTypeRuntimeSymbol(import_.alias, item.qualname_parts) if import_.alias else item
            case PythonTypeName(value=name):
                if name not in resolved_name_imports:
                    try:
                        is_builtin = parser._is_target_python_builtin_type(name)  # noqa: SLF001
                        import_ = None if is_builtin else parser._resolve_type_import(name)  # noqa: SLF001
                    except Error:
                        if not (import_ := parser._resolve_type_import_from_defs(name)):  # noqa: SLF001
                            raise
                        resolved_name_imports[name] = import_
                    else:
                        resolved_name_imports[name] = (
                            None if is_builtin else import_ or parser._resolve_type_import_from_defs(name)  # noqa: SLF001
                        )
                if isinstance(resolved := resolved_name_imports[name], PythonTypeExpr):
                    if resolving_names is None:
                        resolving_names = set()
                    if name in resolving_names:
                        msg = f"Cyclic x-python-import type expression involving {name!r}"
                        raise Error(msg)
                    resolving_names.add(name)
                    try:
                        return rewrite_python_type_expr(read_literal_enum_members(resolved), bind_leaf)
                    finally:
                        resolving_names.remove(name)
                if resolved:
                    import_, bound_name = bind_import(resolved)
                    return PythonTypeBoundName(bound_name, import_.from_, import_.import_)
        return item

    return BoundPythonType(rewrite_python_type_expr(read_literal_enum_members(expression), bind_leaf), tuple(imports))


def override_annotation(
    parser: TypeOverridesHost, override_import: Import, *, is_optional: bool = False
) -> DataType | None:
    """Return the type a ``--type-overrides`` value spells as an annotation, or None when it names a class.

    A value with brackets, such as ``typing.Literal[1, 2]``, names no class to import: it is written as
    ``x-python-type`` writes it, with the imports of its names and the keywords that type takes. A value with
    brackets that is no annotation is an error, as it has no import to write either.
    """
    path = _import_path(override_import)
    if "[" not in path:
        return None
    if (expression := _parse_override_annotation(path)) is None:
        msg = f"--type-overrides value {path!r} must be a valid Python type annotation"
        raise Error(msg)
    from datamodel_code_generator._python_type_annotation import render_python_type_expr  # noqa: PLC0415

    bound_type = parser._bind_python_type(expression)  # noqa: SLF001
    return parser.data_type(
        type=render_python_type_expr(bound_type.expression), python_type=bound_type, is_optional=is_optional
    )


def apply_override_to_field(parser: TypeOverridesHost, field: DataModelFieldBase, override_import: Import) -> None:
    """Apply override to entire field's data_type, keeping type-dependent constraints that the new type takes.

    A value with brackets is an annotation: it drops the arguments of every ``con*`` call the field held and
    takes the keywords that annotation takes; the field keeps the None its schema admits.

    The arguments of a ``con*`` call stay on an override the generator does not know, which may be a factory such
    as ``pydantic.conint``; a known class takes none, so they are left out, and so are those of the nested types the
    override replaces. An output model that writes ``Field()`` arguments warns about the ones it leaves out.
    """
    if (
        data_type := parser._override_annotation(override_import, is_optional=field.data_type.is_optional)  # noqa: SLF001
    ) is not None:
        parser._record_overridden_constraints(field, keeps_top_kwargs=False)  # noqa: SLF001
        parser._decide_overridden_constraints(  # noqa: SLF001
            field,
            partial(parser.data_type_manager.constraint_keywords, data_type, parser.target_python_version.version_key),
        )
        parser._graph_builder.replace_field_type(field, data_type)  # noqa: SLF001
        return
    from datamodel_code_generator._python_type_import_registry import (  # noqa: PLC0415
        is_known_python_type_path,
    )

    data_type = _copy_data_type(field.data_type)
    data_type.import_ = override_import
    data_type.alias = override_import.import_
    path = _import_path(override_import)
    if data_type.kwargs and is_known_python_type_path(path, parser.target_python_version.version_key):
        data_type.kwargs = None
    parser._record_overridden_constraints(field, keeps_top_kwargs=data_type.kwargs is not None)  # noqa: SLF001
    parser._decide_overridden_constraints(  # noqa: SLF001
        field,
        partial(parser.data_type_manager.type_constraint_keywords, path, parser.target_python_version.version_key),
    )
    parser._graph_builder.replace_field_type(field, data_type)  # noqa: SLF001
    parser._graph_builder.detach_data_type_ref(field.data_type)  # noqa: SLF001
    parser._graph_builder.set_nested_data_types(field.data_type, [])  # noqa: SLF001


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
    leaves out to warn about them. The untaken constraints of the members, items and values that collapsing inlines
    into the field leave with the type the override replaces.
    """
    type_dependent = field.__dict__.get(_TYPE_DEPENDENT_CONSTRAINTS_KEY) or {}
    constraints = field.constraints
    is_model = isinstance(constraints, ConstraintsBase)
    values = {
        key: value
        for key, value in (constraints.model_dump(by_alias=True) if is_model else constraints or {}).items()
        if value is not None
    }
    declared = (
        overridden_declared_constraints(values, type_dependent=type_dependent, untaken=field.pop_untaken_constraints())
        if parser.data_model_field_type.SUPPORTS_FIELD_CONSTRAINTS
        else {}
    )
    if not type_dependent and not declared:
        return
    values, untaken = resplit_overridden_constraints(
        values,
        type_dependent=type_dependent,
        declared=declared,
        taken=keywords(),
        declared_taken=keywords(number_values=True, target_model_version=parser.target_pydantic_version),
    )
    parser._mark_untaken_constraints(  # noqa: SLF001
        field, untaken, lambda minimum: keywords(number_values=True, target_model_version=minimum)
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


def apply_override_to_data_type(parser: TypeOverridesHost, data_type: DataType) -> bool:
    """Recursively apply model-level overrides to a DataType; a stand-in takes the import it stands for first."""
    overridden = False
    if (reference := data_type.reference) and (
        override_import := parser._base_stand_in_imports.get(model_key_of(reference))  # noqa: SLF001
        or parser._model_type_override_imports.get(reference.name)  # noqa: SLF001
        or enum_site_override_import(parser, reference)
    ):
        if (annotation := parser._override_annotation(override_import)) is None:  # noqa: SLF001
            parser._graph_builder.override_type_import(data_type, override_import)  # noqa: SLF001
            parser._graph_builder.name_reference(data_type, override_import.binding_name)  # noqa: SLF001
            parser._graph_builder.detach_data_type_ref(data_type)  # noqa: SLF001
        else:
            parser._graph_builder.name_reference(data_type, annotation.type_hint)  # noqa: SLF001
            parser._graph_builder.detach_data_type_ref(data_type)  # noqa: SLF001
            parser._graph_builder.set_type_expressions(  # noqa: SLF001
                data_type, type_=annotation.type, python_type=annotation.python_type
            )
        overridden = True
    # Handle nested types (List[CustomType], Optional[CustomType], etc.)
    for nested in data_type.data_types:
        overridden = parser._apply_override_to_data_type(nested) or overridden  # noqa: SLF001
    if data_type.dict_key:
        overridden = parser._apply_override_to_data_type(data_type.dict_key) or overridden  # noqa: SLF001
    return overridden
