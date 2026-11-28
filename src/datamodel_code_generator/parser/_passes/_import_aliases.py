"""How the imports of a module take aliases where a field name of the module hides the names they bind.

``alias_shadowed_imports`` gives such an import an alias: the plain ``<name>_aliased`` of the fast path for ordinary
types (``_ordinary_field_shadow_aliases``), or the aliases the lazy ``parser/_python_type_imports.py`` resolves when
the models hold structured imports (bound Python types, runtime expressions). ``_apply_structured_import_aliases``
writes the aliases into every consumer that carries the import's identity: the types, annotations and runtime
expressions of the fields, a model's additional imports and its base classes, through the generation store.
"""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING, Any, Final, cast

from typing_extensions import Protocol

from datamodel_code_generator.imports import Import, _aliased_as, _aliased_name, _import_key
from datamodel_code_generator.model.base import _COMPILED_PATTERN_KEY
from datamodel_code_generator.parser._imports_plan import ImportProducer
from datamodel_code_generator.parser._passes._shared import (
    GenerationStoreHost,
    iter_models_field_data_types,
    register_runtime_expression,
)
from datamodel_code_generator.python_literal import rewrite_runtime_expressions, rewrite_runtime_imports

if TYPE_CHECKING:
    from collections.abc import Mapping

    from datamodel_code_generator.imports import Imports, _ImportKey
    from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
    from datamodel_code_generator.parser._passes._shared import RuntimeExpressionFlags
    from datamodel_code_generator.parser._passes._state import (
        DecidedImportAliases,
        ParseConfigView,
        _ModuleState,
        _PreparedModule,
    )
    from datamodel_code_generator.parser.generation import GraphBuilder
    from datamodel_code_generator.types import DataType

_SHADOWED_IMPORTS: Final = ImportProducer("shadowed-imports")


def _aliased_import(import_: Import | None, aliased_imports: Mapping[_ImportKey, Import]) -> Import | None:
    """Return the import the module binds for the identity of ``import_``, or None when ``import_`` binds it already.

    The binding is the module's alias, or the bare import of an identity another module aliased and this module did
    not, which leaves a bare ``import_`` alone.
    """
    if (
        import_ is None
        or (aliased := aliased_imports.get(_import_key(import_))) is None
        or aliased is import_
        or (aliased.alias is None and import_.alias is None)
    ):
        return None
    return aliased


def _ordinary_field_shadow_aliases(
    models: list[DataModel],
    all_model_field_names: set[str],
) -> tuple[dict[_ImportKey, Import], bool, bool]:
    """Run the narrow fast path and report whether structured imports exist."""
    aliases: dict[_ImportKey, Import] = {}
    has_python_type = False
    has_runtime_expressions = False
    for model in models:
        for field in model.fields:
            if not has_runtime_expressions and field.runtime_expression_imports:
                has_runtime_expressions = True
            for data_type in field.data_type.all_data_types:
                if data_type.python_type:
                    has_python_type = True
                if not has_runtime_expressions and data_type.runtime_expression_imports:
                    has_runtime_expressions = True
                if (
                    data_type.import_
                    and data_type.type in all_model_field_names
                    and (key := _import_key(data_type.import_)) not in aliases
                ):
                    aliases[key] = _aliased_as(data_type.import_, _aliased_name(data_type.type))
    return aliases, has_python_type, has_runtime_expressions


def _apply_structured_import_aliases(
    builder: GraphBuilder,
    models: list[DataModel],
    aliased_imports: dict[_ImportKey, Import],
    kept_base_bindings: frozenset[tuple[_ImportKey, str]] = frozenset(),
) -> None:
    """Rewrite every identity-carrying structured consumer of selected imports, the kept base bindings aside."""
    for model in models:
        for field in model.fields:
            _alias_field_runtime_expressions(builder, field, aliased_imports)
            for data_type in field.data_type.all_data_types:
                _alias_data_type_structured_imports(builder, data_type, aliased_imports)
        _alias_additional_imports(model, aliased_imports)
    for model in models:
        _alias_base_class_imports(builder, model, aliased_imports, kept_base_bindings)


def _alias_field_runtime_expressions(
    builder: GraphBuilder,
    field: DataModelFieldBase,
    aliased_imports: dict[_ImportKey, Import],
) -> None:
    """Rewrite a field only when its producer registered a runtime expression."""
    imports = field.runtime_expression_imports
    if not imports or (rewritten_imports := rewrite_runtime_imports(imports, aliased_imports)) is imports:
        return
    builder.set_field_default(field, rewrite_runtime_expressions(field.default, aliased_imports))
    if _COMPILED_PATTERN_KEY in field.extras:
        pattern = rewrite_runtime_expressions(field.extras[_COMPILED_PATTERN_KEY], aliased_imports)
        builder.set_field_extra(field, _COMPILED_PATTERN_KEY, pattern)
    field._set_runtime_expression_imports(rewritten_imports)


def _alias_data_type_structured_imports(
    builder: GraphBuilder,
    data_type: DataType,
    aliased_imports: dict[_ImportKey, Import],
) -> None:
    """Apply aliases to type, annotation, and producer-registered kwargs expressions.

    A type renders by its ``alias`` first, so an alias that repeats the spelling being rewritten (the bare name a class
    type override writes, the annotation text an annotation override writes) takes the aliased spelling too; any other
    alias (a quoted forward reference, a module-qualified reference) stays.
    """
    alias = data_type.alias
    if (import_ := data_type.import_) is not None and (
        aliased_import := _aliased_import(import_, aliased_imports)
    ) is not None:
        if alias == import_.binding_name:
            builder.name_reference(data_type, aliased_import.binding_name)
        builder.set_type_expressions(data_type, type_=aliased_import.binding_name, import_=aliased_import)
    if python_type := data_type.python_type:
        alias_bound_python_type, render_python_type_expr = _python_type_import_alias_helpers()
        bound_type = alias_bound_python_type(python_type, aliased_imports)
        if bound_type is not python_type:
            type_text = render_python_type_expr(bound_type.expression)
            if alias is not None and alias == render_python_type_expr(python_type.expression):
                builder.name_reference(data_type, type_text)
            builder.set_type_expressions(data_type, type_=type_text, python_type=bound_type)
    if (
        not (imports := data_type.runtime_expression_imports)
        or (rewritten_imports := rewrite_runtime_imports(imports, aliased_imports)) is imports
    ):
        return
    builder.set_type_expressions(
        data_type,
        kwargs=cast("dict[str, Any]", rewrite_runtime_expressions(data_type.kwargs, aliased_imports)),
        runtime_expression_imports=rewritten_imports,
    )


@cache
def _python_type_import_alias_helpers() -> tuple[Any, Any]:
    """Load annotation alias helpers only for the structured annotation path."""
    from datamodel_code_generator._python_type_annotation import render_python_type_expr  # noqa: PLC0415
    from datamodel_code_generator._python_type_binding import alias_bound_python_type  # noqa: PLC0415

    return alias_bound_python_type, render_python_type_expr


def _alias_base_class_imports(
    builder: GraphBuilder,
    model: DataModel,
    aliased_imports: dict[tuple[str | None, str], Import],
    kept_base_bindings: frozenset[tuple[_ImportKey, str]] = frozenset(),
) -> None:
    """Apply aliased imports to a model's base classes, those whose identity and alias are kept aside."""
    for base_class in model.base_classes:
        if (base_import := base_class.import_) is not None and (
            _import_key(base_import),
            base_import.alias,
        ) in kept_base_bindings:
            continue
        if (aliased_import := _aliased_import(base_import, aliased_imports)) is not None:
            builder.set_type_expressions(base_class, type_=aliased_import.binding_name, import_=aliased_import)


def _alias_additional_imports(
    model: DataModel,
    aliased_imports: dict[tuple[str | None, str], Import],
) -> None:
    """Replace every additional import with the module's canonical identity binding."""
    for index, import_ in enumerate(model._additional_imports):
        if (aliased_import := _aliased_import(import_, aliased_imports)) is not None:
            model._additional_imports[index] = aliased_import


def alias_module_shadowed_imports(host: RuntimeExpressionFlags, state: _ModuleState[ParseConfigView]) -> None:
    """Alias the imports a field name of the module hides and keep the aliases for the types later passes write."""
    state.decided_import_aliases = alias_shadowed_imports(host, state.model_list, state.all_module_fields)


class DecidedImportAliasesHost(GenerationStoreHost, Protocol):
    """The output modules whose decided import aliases the render preparation writes again."""

    @property
    def _prepared_modules(self) -> list[_PreparedModule[ParseConfigView]]:
        """The output modules as ``_process_single_module`` left them, with the aliases each decided."""


def apply_decided_import_aliases(host: DecidedImportAliasesHost, _: object) -> None:
    """Write each module's binding of an aliased identity into the types passes after the decisions wrote, once final.

    Every consumer in a module spells that module's decision, whichever module its node came from: its own alias, or,
    for an identity whose alias another module's decision made and its own did not, the bare import (a root another
    module aliased that ``--collapse-root-models`` copies in). A consumer the decision already reached keeps its
    spelling: ``_aliased_import`` returns None for the module's own aliased import and for a bare import the module
    binds bare, and an alias that no longer repeats the spelling being rewritten stays.
    """
    prepared_modules = host._prepared_modules
    if not (
        decisions := [
            decided for prepared in prepared_modules if (decided := prepared.decided_import_aliases) is not None
        ]
    ):
        return
    bare_imports = {
        key: Import(from_=key[0], import_=key[1], reference_path=aliased_imports[key].reference_path)
        for aliased_imports, _, made in decisions
        for key in made
    }
    builder = host.generation_store.builder()
    for prepared in prepared_modules:
        aliased_imports, kept_base_bindings, _ = prepared.decided_import_aliases or ({}, frozenset(), frozenset())
        _apply_structured_import_aliases(
            builder, prepared.model_list, {**bare_imports, **aliased_imports}, kept_base_bindings
        )


def alias_shadowed_imports(
    host: RuntimeExpressionFlags,
    models: list[DataModel],
    all_model_field_names: set[str],
    *,
    module_imports: Imports | None = None,
) -> DecidedImportAliases | None:
    """Alias the imports whose names a field name of the models hides; return the aliases written, if any."""
    ordinary_aliases, has_python_type, has_runtime_expressions = _ordinary_field_shadow_aliases(
        models,
        all_model_field_names,
    )
    if not (has_python_type or has_runtime_expressions):
        if not ordinary_aliases:
            return None
        _apply_structured_import_aliases(host._graph_builder, models, ordinary_aliases)
        return ordinary_aliases, frozenset(), frozenset(ordinary_aliases)
    host._has_bound_python_types = host._has_bound_python_types or has_python_type
    if has_runtime_expressions:
        register_runtime_expression(host)
    # Keep structured import machinery lazy: ordinary schemas must not
    # pay its import, allocation, or module-scan cost.
    from datamodel_code_generator.parser._python_type_imports import (  # noqa: PLC0415
        resolve_structured_import_aliases,
    )

    data_types = (data_type for _, _, data_type in iter_models_field_data_types(models))
    aliases = resolve_structured_import_aliases(
        data_types,
        models,
        all_model_field_names,
        (host.imports,) if module_imports is None else (host.imports, module_imports),
    )
    if not aliases.imports:
        return None
    _apply_structured_import_aliases(host._graph_builder, models, aliases.imports, aliases.kept_base_bindings)
    if module_imports is not None:
        for aliased_import in aliases.imports.values():
            host._import_ledger.apply_alias(module_imports, aliased_import, _SHADOWED_IMPORTS)
    return aliases.imports, aliases.kept_base_bindings, frozenset(aliases.imports.keys() - aliases.established)
