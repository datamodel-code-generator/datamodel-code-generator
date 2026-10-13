"""Resolve module-wide import collisions for structured Python consumers."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from datamodel_code_generator._python_type_annotation import PythonTypeName, rewrite_python_type_expr
from datamodel_code_generator._python_type_binding import python_type_import_key, python_type_import_name
from datamodel_code_generator.imports import Import

if TYPE_CHECKING:
    from collections.abc import Iterable

    from datamodel_code_generator._python_type_annotation import PythonTypeExpr
    from datamodel_code_generator.imports import Imports
    from datamodel_code_generator.model.base import DataModel
    from datamodel_code_generator.types import DataType

_ImportKey = tuple[str | None, str]
_ImportsByName = dict[str, dict[_ImportKey, Import]]


@dataclass(slots=True)
class _StructuredImportAliasState:
    """Parser-level identities needed to alias type and runtime expression imports."""

    ordinary_imports_by_name: _ImportsByName
    ordinary_imports_by_key: dict[_ImportKey, Import]
    structured_imports_by_name: _ImportsByName
    ordinary_field_alias_candidates: dict[_ImportKey, Import]
    reserved_names: set[str]
    used_names: set[str]
    aliased_imports: dict[_ImportKey, Import]
    template_import_keys: set[_ImportKey]

    def collect_import(self, imports_by_name: _ImportsByName, import_: Import) -> None:
        """Record an exact import identity under its effective binding name."""
        name = python_type_import_name(import_)
        imports_by_name.setdefault(name, {}).setdefault(python_type_import_key(import_), import_)
        self.used_names.add(name)

    def collect_ordinary_import(self, import_: Import) -> None:
        """Record the canonical binding already established for one identity."""
        self.collect_import(self.ordinary_imports_by_name, import_)
        key = python_type_import_key(import_)
        if key not in self.ordinary_imports_by_key or import_.alias:
            self.ordinary_imports_by_key[key] = import_

    def reserve_unbound_name(self, expression: PythonTypeExpr) -> PythonTypeExpr:
        """Reserve plain leaves while preserving the immutable expression tree."""
        if isinstance(expression, PythonTypeName):
            self.reserved_names.add(expression.value)
            self.used_names.add(expression.value)
        return expression

    def unique_alias(self, name: str, *, field_collision: bool = False) -> str:
        """Allocate the deterministic spelling used by existing collision policy."""
        if field_collision:
            alias = f"{name}_aliased"
            while alias in self.used_names:
                alias += "_"
            return alias
        suffix = 1
        while (alias := f"{name}_{suffix}") in self.used_names:
            suffix += 1
        return alias

    def alias_import(self, import_: Import, alias: str) -> None:
        """Bind one exact import identity to an alias."""
        self.aliased_imports[python_type_import_key(import_)] = Import(
            from_=import_.from_,
            import_=import_.import_,
            alias=alias,
            reference_path=import_.reference_path,
        )
        self.used_names.add(alias)


def _collect_data_type_imports(
    state: _StructuredImportAliasState,
    data_types: Iterable[DataType],
    all_model_field_names: set[str],
) -> None:
    """Collect ordinary and structured identities from field DataTypes."""
    for data_type in data_types:
        if data_type.import_:
            state.collect_ordinary_import(data_type.import_)
            if data_type.type in all_model_field_names:
                state.ordinary_field_alias_candidates.setdefault(
                    python_type_import_key(data_type.import_), data_type.import_
                )
        elif not data_type.python_type and (name := data_type.alias or data_type.type):
            state.reserved_names.add(name.partition(".")[0])
            state.used_names.add(name.partition(".")[0])
        if data_type.python_type:
            for import_ in data_type.python_type.imports:
                state.collect_import(state.structured_imports_by_name, import_)
            rewrite_python_type_expr(data_type.python_type.expression, state.reserve_unbound_name)
        for import_ in data_type.runtime_expression_imports:
            state.collect_import(state.structured_imports_by_name, import_)


def _structured_import_keys(state: _StructuredImportAliasState) -> set[_ImportKey]:
    return {
        python_type_import_key(import_)
        for imports_by_key in state.structured_imports_by_name.values()
        for import_ in imports_by_key.values()
    }


def _collect_field_runtime_imports(state: _StructuredImportAliasState, models: Iterable[DataModel]) -> None:
    """Collect runtime expressions stored in defaults outside DataType kwargs."""
    for model in models:
        for field in model.fields:
            imports = field.runtime_expression_imports
            for import_ in imports:
                state.collect_import(state.structured_imports_by_name, import_)


def _alias_following_import_counts(model: DataModel) -> Counter[_ImportKey]:
    """Count the imports a model holds for consumers that follow an alias.

    Each further import of the same identity is named by template text such as ``Field(...)``,
    which may sit in the same model, so its binding stays.
    """
    counts = Counter(
        python_type_import_key(base_class.import_) for base_class in model.base_classes if base_class.import_
    )
    for field in model.fields:
        counts.update(map(python_type_import_key, field.runtime_expression_imports))
        for data_type in field.data_type.all_data_types:
            if data_type.import_:
                counts[python_type_import_key(data_type.import_)] += 1
            if data_type.python_type:
                counts.update(map(python_type_import_key, data_type.python_type.imports))
            counts.update(map(python_type_import_key, data_type.runtime_expression_imports))
    return counts


def _collect_model_context_imports(
    state: _StructuredImportAliasState,
    models: Iterable[DataModel],
    import_aggregates: Iterable[Imports],
) -> None:
    """Collect parser/model bindings without reaching into model internals."""
    structured_import_keys = _structured_import_keys(state)
    for model in models:
        for base_class in model.base_classes:
            if not base_class.import_ and (base_name := base_class.type_hint):
                state.reserved_names.add(base_name.partition(".")[0])
                state.used_names.add(base_name.partition(".")[0])
        # This public aggregate contains field, base, default, backend, and
        # additional imports. Unaliased structured identities are classified
        # above; an established alias remains the canonical module binding.
        alias_following_counts: Counter[_ImportKey] | None = None
        for import_ in model.imports:
            if (key := python_type_import_key(import_)) in structured_import_keys and not import_.alias:
                if alias_following_counts is None:
                    alias_following_counts = _alias_following_import_counts(model)
                if alias_following_counts[key]:
                    alias_following_counts[key] -= 1
                    continue
                state.template_import_keys.add(key)
            state.collect_ordinary_import(import_)
    for import_aggregate in import_aggregates:
        for from_, imported_names in import_aggregate.items():
            for import_name in imported_names:
                key = (from_, import_name)
                alias = import_aggregate.alias.get(from_, {}).get(import_name)
                if key in structured_import_keys and not alias:
                    continue
                state.collect_ordinary_import(
                    Import(
                        from_=key[0],
                        import_=key[1],
                        alias=alias,
                    ),
                )


def resolve_structured_import_aliases(
    data_types: Iterable[DataType],
    models: list[DataModel],
    all_model_field_names: set[str],
    import_aggregates: Iterable[Imports] = (),
) -> dict[_ImportKey, Import]:
    """Prefer established module bindings and alias structured imports around them."""
    reserved_names = set(all_model_field_names)
    reserved_names.update(model.class_name for model in models)
    state = _StructuredImportAliasState({}, {}, {}, {}, reserved_names, set(reserved_names), {}, set())
    _collect_data_type_imports(state, data_types, all_model_field_names)
    _collect_field_runtime_imports(state, models)
    _collect_model_context_imports(state, models, import_aggregates)

    # An existing alias is the canonical binding for its exact import identity.
    # Propagate it to every structured consumer before allocating new aliases;
    # otherwise the import statement and the annotation can name different objects.
    for key in _structured_import_keys(state) & state.ordinary_imports_by_key.keys():
        ordinary_import = state.ordinary_imports_by_key[key]
        if ordinary_import.alias:
            if ordinary_import.binding_name in state.reserved_names:
                state.alias_import(
                    ordinary_import,
                    state.unique_alias(ordinary_import.binding_name, field_collision=True),
                )
            else:
                state.aliased_imports[key] = ordinary_import
        elif (name := ordinary_import.binding_name) in state.reserved_names and key not in state.template_import_keys:
            state.alias_import(ordinary_import, state.unique_alias(name, field_collision=True))

    for import_ in state.ordinary_field_alias_candidates.values():
        if python_type_import_key(import_) in state.aliased_imports:
            continue
        state.alias_import(
            import_,
            state.unique_alias(python_type_import_name(import_), field_collision=True),
        )

    for name, imports_by_key in state.structured_imports_by_name.items():
        ordinary_keys = state.ordinary_imports_by_name.get(name, {})
        kept_unaliased = False
        for key, import_ in imports_by_key.items():
            if key in state.aliased_imports:
                continue
            if key in ordinary_keys:
                kept_unaliased = True
                continue
            if not kept_unaliased and name not in state.reserved_names and not ordinary_keys:
                kept_unaliased = True
                continue
            state.alias_import(
                import_,
                state.unique_alias(
                    name,
                    field_collision=name in state.reserved_names and not kept_unaliased,
                ),
            )
    return state.aliased_imports


resolve_python_type_import_aliases = resolve_structured_import_aliases


__all__ = ["resolve_python_type_import_aliases", "resolve_structured_import_aliases"]
