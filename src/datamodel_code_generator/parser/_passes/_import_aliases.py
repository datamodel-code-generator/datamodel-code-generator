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

from datamodel_code_generator.imports import Import, _import_key
from datamodel_code_generator.parser._imports_plan import ImportProducer
from datamodel_code_generator.parser._passes._shared import iter_models_field_data_types, register_runtime_expression
from datamodel_code_generator.python_literal import rewrite_runtime_expressions, rewrite_runtime_imports

if TYPE_CHECKING:
    from collections.abc import Mapping

    from datamodel_code_generator.imports import Imports, _ImportKey
    from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
    from datamodel_code_generator.parser._passes._shared import RuntimeExpressionFlags
    from datamodel_code_generator.parser.generation import GraphBuilder
    from datamodel_code_generator.types import DataType

_SHADOWED_IMPORTS: Final = ImportProducer("shadowed-imports")


def _aliased_import(import_: Import | None, aliased_imports: Mapping[_ImportKey, Import]) -> Import | None:
    """Return the import that aliases ``import_``, or None when it has none other than itself."""
    if import_ is None or (aliased := aliased_imports.get(_import_key(import_))) is import_:
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
                if data_type.import_ and data_type.type in all_model_field_names:
                    key = _import_key(data_type.import_)
                    aliases.setdefault(
                        key,
                        Import(
                            from_=data_type.import_.from_,
                            import_=data_type.import_.import_,
                            alias=f"{data_type.type}_aliased",
                            reference_path=data_type.import_.reference_path,
                        ),
                    )
    return aliases, has_python_type, has_runtime_expressions


def _apply_structured_import_aliases(
    builder: GraphBuilder,
    models: list[DataModel],
    aliased_imports: dict[_ImportKey, Import],
) -> None:
    """Rewrite every identity-carrying structured consumer of selected imports."""
    for model in models:
        for field in model.fields:
            _alias_field_runtime_expressions(builder, field, aliased_imports)
            for data_type in field.data_type.all_data_types:
                _alias_data_type_structured_imports(builder, data_type, aliased_imports)
        _alias_additional_imports(model, aliased_imports)
    for model in models:
        _alias_base_class_imports(builder, model, aliased_imports)


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
    builder.set_field_extras(field, cast("dict[str, Any]", rewrite_runtime_expressions(field.extras, aliased_imports)))
    field._set_runtime_expression_imports(rewritten_imports)  # noqa: SLF001


def _alias_data_type_structured_imports(
    builder: GraphBuilder,
    data_type: DataType,
    aliased_imports: dict[_ImportKey, Import],
) -> None:
    """Apply aliases to type, annotation, and producer-registered kwargs expressions."""
    if (aliased_import := _aliased_import(data_type.import_, aliased_imports)) is not None:
        builder.set_type_expressions(data_type, type_=aliased_import.alias, import_=aliased_import)
    if data_type.python_type:
        alias_bound_python_type, render_python_type_expr = _python_type_import_alias_helpers()
        bound_type = alias_bound_python_type(data_type.python_type, aliased_imports)
        if bound_type is not data_type.python_type:
            builder.set_type_expressions(
                data_type, type_=render_python_type_expr(bound_type.expression), python_type=bound_type
            )
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
) -> None:
    """Apply aliased imports to a model's base classes."""
    for base_class in model.base_classes:
        if (aliased_import := _aliased_import(base_class.import_, aliased_imports)) is not None:
            builder.set_type_expressions(base_class, type_=aliased_import.alias, import_=aliased_import)


def _alias_additional_imports(
    model: DataModel,
    aliased_imports: dict[tuple[str | None, str], Import],
) -> None:
    """Replace every additional import with the module's canonical identity binding."""
    for index, import_ in enumerate(model._additional_imports):  # noqa: SLF001
        if (aliased_import := _aliased_import(import_, aliased_imports)) is not None:
            model._additional_imports[index] = aliased_import  # noqa: SLF001


def alias_shadowed_imports(
    host: RuntimeExpressionFlags,
    models: list[DataModel],
    all_model_field_names: set[str],
    *,
    module_imports: Imports | None = None,
) -> None:
    """Alias the imports whose names a field name of the models hides."""
    ordinary_aliases, has_python_type, has_runtime_expressions = _ordinary_field_shadow_aliases(
        models,
        all_model_field_names,
    )
    if not (has_python_type or has_runtime_expressions):
        if ordinary_aliases:
            _apply_structured_import_aliases(host._graph_builder, models, ordinary_aliases)  # noqa: SLF001
        return
    host._has_bound_python_types = host._has_bound_python_types or has_python_type  # noqa: SLF001
    if has_runtime_expressions:
        register_runtime_expression(host)
    # Keep structured import machinery lazy: ordinary schemas must not
    # pay its import, allocation, or module-scan cost.
    from datamodel_code_generator.parser._python_type_imports import (  # noqa: PLC0415
        resolve_structured_import_aliases,
    )

    data_types = (data_type for _, _, data_type in iter_models_field_data_types(models))
    aliased_imports = resolve_structured_import_aliases(
        data_types,
        models,
        all_model_field_names,
        (host.imports,) if module_imports is None else (host.imports, module_imports),
    )
    if not aliased_imports:
        return
    _apply_structured_import_aliases(host._graph_builder, models, aliased_imports)  # noqa: SLF001
    if module_imports is not None:
        for aliased_import in aliased_imports.values():
            host._import_ledger.apply_alias(module_imports, aliased_import, _SHADOWED_IMPORTS)  # noqa: SLF001
