"""Module imports collected through one ledger.

Every write to the imports of a generated module (``ModuleContext.imports``) and to the imports that every module shares
(``Parser.imports``) goes through ``ModuleImportLedger``. The ledger applies the operation to the live ``Imports`` at
once, so a pass reads what it read before, and keeps it as an entry together with its producer: the pass that asked
for it, and the model and field it was asked for. The live ``Imports`` is the materialization of its entries, which
``replay`` rebuilds. The imports a module writes can be fewer than the names it binds: ``ModuleImportLedger.rendered``
subtracts from a copy of the live ``Imports`` what the entries wrote of their models' unrendered imports
(``DataModel._unrendered_imports``), counted as the difference between the replay of the entries and their replay
without those imports. The copy keeps the order of the module lines and the imports written outside the ledger, which a
replay of some entries only would reorder or lose.

Code that the parser hands the live ``Imports`` (``DataModel.resolve_module_import_conflicts``) runs inside
``ModuleImportLedger.external``, which records what it changed as removals, additions and aliases of import keys.
The ``DATAMODEL_CODE_GENERATOR_CHECK_MODULE_IMPORTS`` check of the test suite (``tests/checks/module_imports.py``)
compares each module's imports with the replay of their entries when the module's import text is written, so a write
that bypasses the ledger fails it. The re-exports of package ``__init__`` modules and the imports of forwarder modules
are built and written at once by one function each and are not module imports in this sense.

The names a module's ``ModelResolver`` gives its imports are asked for through the ledger too (``add_name``,
``release_names``), so that the check sees, in order, every name a module asked for and released.

Named legacy rows, imports decided where the ledger does not see them:

* ``legacy.enum-member-import-bindings``: an Enum's ``imports`` keep the imports of its member declarations (pydantic's
  ``Field`` for described members), which the class never renders. The module binds their names, so class names
  (``Parser.__replace_duplicate_name_in_module``, ``Parser._flatten_invalid_modules``) and reuse keys
  (``DataModel._render_dedup_key``, ``Parser._stdout_model_fingerprint``) stay as in 0.86.0, and ``rendered`` leaves
  them out of the import block. It goes when an option lets the module bind only what it writes (G09's naming
  rules, #4802).
* ``legacy.alias-order``: a module's import names are unique among the names taken when each was asked for, in the
  order L5 and the L6 repairs asked. Unifying the alias schemes changes names (an option candidate).
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from itertools import chain, repeat
from typing import TYPE_CHECKING, Literal, TypeAlias

from typing_extensions import final

from datamodel_code_generator._records import record
from datamodel_code_generator.imports import Import, Imports

if TYPE_CHECKING:
    from collections.abc import Collection, Generator, Iterable, Iterator, Mapping, Sequence
    from collections.abc import Set as AbstractSet

    from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
    from datamodel_code_generator.reference import ModelResolver, Reference

__all__ = [
    "Added",
    "Aliased",
    "FutureExtracted",
    "ImportOperation",
    "ImportPass",
    "ImportProducer",
    "LedgerEntry",
    "ModuleImportLedger",
    "Pruned",
    "ReferenceDropped",
    "Remapped",
    "Removed",
    "replay",
]

ImportPass: TypeAlias = Literal[
    "additional-imports",
    "external-ref-mapping",
    "x-python-import",
    "deferred-annotations",
    "change-from-import",
    "collapse-root-models",
    "discriminator",
    "const-narrowed-values",
    "type-dict-keys-by-root-types",
    "import-removed-reuse-canonicals",
    "import-renamed-types",
    "import-enum-member-literal-classes",
    "tag-fields-of-tagged-bases",
    "generic-base-class",
    "inherited-keyword-only-copies",
    "type-alias-fields",
    "model-imports-snapshot",
    "unused-models",
    "schema-validators",
    "runtime-expressions",
    "remove-unused",
    "import-conflicts",
    "builtin-module-bindings",
    "builtin-submodule-bindings",
    "field-name-bindings",
    "reused-variant-literal",
    "shadowed-imports",
    "import-overrides",
    "future-imports",
]
"""The passes that write imports, named after the function that writes (``parser/base.py:Parser`` unless named).

* ``additional-imports``: ``_append_additional_imports``, ``--additional-imports`` into the shared imports.
* ``external-ref-mapping``, ``x-python-import``: ``parser/jsonschema.py:JsonSchemaParser._check_external_ref_mapping``
  (``--external-ref-mapping``) and ``get_ref_data_type`` (``x-python-import``), the Python type a reference names,
  into the shared imports (L1).
* ``deferred-annotations``: ``from __future__ import annotations``, shared by ``_prepare_parse_config`` and per module
  by ``_get_module_future_imports`` (L7).
* ``change-from-import``: ``change_from_import`` (L5 prepare), the imports of each model and the models of other
  modules that it references.
* ``collapse-root-models``: ``collapse_root_models_in_scope`` and ``import_collapsed_inner_models`` (L5), the
  imports of inlined root types and of the inner models that holders are redirected to, without those of the roots.
* ``discriminator``: ``apply_discriminator_type`` (L5), the imports of tag fields.
* ``const-narrowed-values``: ``__type_const_narrowed_values`` (L5), the imports of the types a const gives its values.
* ``type-dict-keys-by-root-types``: ``__type_dict_keys_by_root_types`` (L5), the imports of the root types that
  dict keys take instead of root models that cannot be dict keys.
* ``import-removed-reuse-canonicals``, ``import-renamed-types``, ``import-enum-member-literal-classes``: the L6
  repairs of the same names.
* ``tag-fields-of-tagged-bases``: ``tag_fields_of_tagged_bases`` (L6), the models that the fields a reused msgspec
  struct copies from its bases name from the module of that struct.
* ``generic-base-class``: ``apply_generic_base_class`` (L6), the generated base model instead of the output base class.
* ``inherited-keyword-only-copies``: ``fix_constructor_field_ordering`` (L6), the models that the keyword-only copies of
  inherited fields name from the module of the model that takes them.
* ``type-alias-fields``: ``prepare_type_alias_fields``, drops the imports a prepared type-alias body no longer uses.
* ``model-imports-snapshot``: ``_finalize_modules``, the imports of each model once more, read after the generic base
  class.
* ``unused-models``: ``_finalize_modules``, drops the imports of the models that collapse left unused.
* ``schema-validators``, ``runtime-expressions``: ``_sync_schema_runtime_validation_module_imports`` and
  ``_merge_runtime_expression_imports``, imports that module code and runtime default expressions add.
* ``remove-unused``: ``_finalize_modules``, drops the imports whose name no model uses.
* ``import-conflicts``: the output model's ``resolve_module_import_conflicts``, recorded through ``external``.
* ``builtin-module-bindings``: ``alias_builtin_shadowing_module_imports``, aliases of module imports named like a
  builtin that their module also writes bare.
* ``builtin-submodule-bindings``: ``alias_builtins_hidden_by_submodules``, aliases of builtins that a package's own
  submodules hide and that the package writes bare.
* ``field-name-bindings``: ``_bind_module_field_names``, aliases of imports that a field name hides.
* ``reused-variant-literal``: ``alias_reused_variant_literal``, the alias of ``typing.Literal`` beside a model named
  ``Literal``.
* ``shadowed-imports``: ``alias_shadowed_imports`` (L6), aliases of structured imports that a field name hides.
* ``import-overrides``: ``_remap_imports``, ``--import-overrides`` moving names to other modules.
* ``future-imports``: ``render_modules`` and ``_get_module_future_imports`` (L7), moving the ``__future__`` imports
  out to write them first.
"""


@final
@record
class ImportProducer:
    """The pass that writes imports, and the model and the field it writes them for."""

    pass_: ImportPass
    model: DataModel | None = None
    field: DataModelFieldBase | None = None


@final
@record
class Added:
    """``Imports.append`` of the imports."""

    imports: tuple[Import, ...]

    def apply_to(self, imports: Imports) -> None:
        """Write the operation into the imports."""
        imports.append(self.imports)


@final
@record
class Removed:
    """``Imports.remove`` of the imports."""

    imports: tuple[Import, ...]

    def apply_to(self, imports: Imports) -> None:
        """Write the operation into the imports."""
        imports.remove(self.imports)


@final
@record
class Aliased:
    """``Imports.apply_alias``: the import's alias for the import of the same name, which must be counted."""

    import_: Import

    def apply_to(self, imports: Imports) -> None:
        """Write the operation into the imports."""
        imports.apply_alias(self.import_)


@final
@record
class Pruned:
    """``Imports.remove_unused``: drops every import whose bound name is not one of the used names."""

    used_names: frozenset[str]

    def apply_to(self, imports: Imports) -> None:
        """Write the operation into the imports."""
        imports.remove_unused(set(self.used_names))


@final
@record
class Remapped:
    """``Imports.remap_modules`` with the overrides, unless no import is counted (reference paths stay then too)."""

    overrides: tuple[tuple[str, str], ...]

    def apply_to(self, imports: Imports) -> None:
        """Write the operation into the imports."""
        if imports.counter:
            imports.remap_modules(dict(self.overrides))


@final
@record
class ReferenceDropped:
    """``Imports.remove_referenced_imports``: drops the import added for the reference path."""

    reference_path: str

    def apply_to(self, imports: Imports) -> None:
        """Write the operation into the imports."""
        imports.remove_referenced_imports(self.reference_path)


@final
@record
class FutureExtracted:
    """``Imports.extract_future``: moves the ``__future__`` imports out, to be written first."""

    def apply_to(self, imports: Imports) -> None:  # ruff: ignore[no-self-use]
        """Write the operation into the imports."""
        imports.extract_future()


ImportOperation: TypeAlias = Added | Removed | Aliased | Pruned | Remapped | ReferenceDropped | FutureExtracted
"""One write to an ``Imports``: each calls the ``Imports`` method its docstring names."""


@final
@record
class LedgerEntry:
    """One write to an ``Imports`` and the producer that asked for it."""

    operation: ImportOperation
    producer: ImportProducer


def replay(entries: Iterable[LedgerEntry], *, use_exact: bool) -> Imports:
    """Return the imports that the entries write, applied in order to empty imports."""
    imports = Imports(use_exact)
    for entry in entries:
        entry.operation.apply_to(imports)
    return imports


def _as_imports(items: Import | Iterable[Import]) -> tuple[Import, ...]:
    return (items,) if isinstance(items, Import) else tuple(items)


_ImportKey: TypeAlias = tuple[str | None, str]


def _key_import(key: _ImportKey, alias: str | None = None) -> Import:
    """Return an import stored under the key: a name ``X as Y`` is an alias kept apart from the ordinary import."""
    module, name = key
    import_, _, kept_alias = name.partition(" as ")
    return Import(from_=module, import_=import_, alias=kept_alias or alias, keep_unaliased=bool(kept_alias))


@final
@record
class _KeyState:
    """How often each import key is counted and the alias it is bound to."""

    counts: Mapping[_ImportKey, int]
    aliases: Mapping[_ImportKey, str]

    @classmethod
    def of(cls, imports: Imports) -> _KeyState:
        return cls(
            dict(imports.counter),
            {(module, name): alias for module, aliases in imports.alias.items() for name, alias in aliases.items()},
        )

    def operations_to(self, after: _KeyState) -> Iterator[ImportOperation]:
        """Yield the removals, additions and aliases that turn this state into the state after.

        Reference paths are not compared: a write that changes them is left to the check.
        """
        changes = {key: after.counts.get(key, 0) - self.counts.get(key, 0) for key in {**self.counts, **after.counts}}
        removed = Removed(
            tuple(chain.from_iterable(repeat(_key_import(key), -n) for key, n in changes.items() if n < 0))
        )
        added = Added(tuple(chain.from_iterable(repeat(_key_import(key), n) for key, n in changes.items() if n > 0)))
        yield from (operation for operation in (removed, added) if operation.imports)
        yield from (
            Aliased(_key_import(key, alias)) for key, alias in after.aliases.items() if self.aliases.get(key) != alias
        )


_storage_key = Imports._storage_key  # ruff: ignore[private-member-access]


def _without_unrendered(entry: LedgerEntry) -> LedgerEntry:
    """Return the entry without the unrendered imports of its model, or the entry itself when its model has none."""
    operation, model = entry.operation, entry.producer.model
    if model is None or not isinstance(operation, Added | Removed):
        return entry
    if not (unrendered := model._unrendered_imports()):  # ruff: ignore[private-member-access]
        return entry
    left = Counter(map(_storage_key, unrendered))
    kept: list[Import] = []
    for import_ in operation.imports:
        if left[key := _storage_key(import_)] > 0:
            left[key] -= 1
        else:
            kept.append(import_)
    return LedgerEntry(type(operation)(tuple(kept)), entry.producer)


def _copy(imports: Imports) -> Imports:
    """Return a copy of the imports that writes the same lines in the same order."""
    copy = Imports(imports.use_exact)
    copy.update((module, set(names)) for module, names in imports.items())
    copy.counter.update(imports.counter)
    copy.alias.update((module, dict(aliases)) for module, aliases in imports.alias.items())
    copy.reference_paths.update(imports.reference_paths)
    return copy


@final
class ModuleImportLedger:
    """The writes to every module's imports and to the shared imports, kept per ``Imports`` in the order made."""

    __slots__ = ("_accounts",)

    def __init__(self) -> None:
        """Start without entries."""
        self._accounts: dict[int, tuple[Imports, list[LedgerEntry]]] = {}

    def _write(self, imports: Imports, operation: ImportOperation, producer: ImportProducer) -> None:
        operation.apply_to(imports)
        self._entries_of(imports).append(LedgerEntry(operation, producer))

    def _entries_of(self, imports: Imports) -> list[LedgerEntry]:
        if (account := self._accounts.get(id(imports))) is None:
            account = self._accounts[id(imports)] = (imports, [])
        return account[1]

    def entries(self, imports: Imports) -> tuple[LedgerEntry, ...]:
        """Return the entries written to the imports, in order."""
        return tuple(self._entries_of(imports))

    def append(self, imports: Imports, items: Import | Iterable[Import], producer: ImportProducer) -> None:
        """Add the imports (``Imports.append``)."""
        if added := _as_imports(items):
            self._write(imports, Added(added), producer)

    def remove(self, imports: Imports, items: Import | Iterable[Import], producer: ImportProducer) -> None:
        """Remove the imports (``Imports.remove``)."""
        if removed := _as_imports(items):
            self._write(imports, Removed(removed), producer)

    def apply_alias(self, imports: Imports, import_: Import, producer: ImportProducer) -> None:
        """Bind the counted import of the same name to the import's alias (``Imports.apply_alias``)."""
        self._write(imports, Aliased(import_), producer)

    def remove_unused(self, imports: Imports, used_names: Collection[str], producer: ImportProducer) -> None:
        """Drop the imports whose bound name is not used (``Imports.remove_unused``)."""
        self._write(imports, Pruned(frozenset(used_names)), producer)

    def remap_modules(self, imports: Imports, overrides: Mapping[str, str], producer: ImportProducer) -> None:
        """Move the overridden names to their modules (``Imports.remap_modules``), unless no import is counted.

        Raises ``ValueError`` when two names would bind the same name, as ``Imports.remap_modules`` does.
        """
        self._write(imports, Remapped(tuple(overrides.items())), producer)

    def remove_referenced_imports(self, imports: Imports, reference_path: str, producer: ImportProducer) -> None:
        """Drop the import added for the reference path (``Imports.remove_referenced_imports``)."""
        self._write(imports, ReferenceDropped(reference_path), producer)

    def extract_future(self, imports: Imports, producer: ImportProducer) -> Imports:
        """Move the ``__future__`` imports out and return them (``Imports.extract_future``)."""
        future = imports.extract_future()
        self._entries_of(imports).append(LedgerEntry(FutureExtracted(), producer))
        return future

    @contextmanager
    def external(self, imports: Imports, producer: ImportProducer) -> Generator[None, None, None]:
        """Record what code the block hands the live imports to changes in them, as removals, additions and aliases."""
        before = _KeyState.of(imports)
        try:
            yield
        finally:
            self._entries_of(imports).extend(
                LedgerEntry(operation, producer) for operation in before.operations_to(_KeyState.of(imports))
            )

    def rendered(self, imports: Imports) -> Imports:
        """Return the imports the module writes: the live imports without what the entries wrote of unrendered imports.

        Without such entries this is the live ``Imports`` itself.
        """
        entries = self._entries_of(imports)
        reduced = [_without_unrendered(entry) for entry in entries]
        if all(entry is reduced_entry for entry, reduced_entry in zip(entries, reduced, strict=True)):
            return imports
        written = replay(entries, use_exact=imports.use_exact).counter
        kept = replay(reduced, use_exact=imports.use_exact).counter
        view = _copy(imports)
        for key, count in written.items():
            view.remove(repeat(_key_import(key), min(count, imports.counter.get(key, 0)) - kept.get(key, 0)))
        return view

    def add_name(  # noqa: PLR0913, PLR6301
        self,
        imports: Imports,  # noqa: ARG002
        resolver: ModelResolver,
        path: Sequence[str],
        original_name: str,
        *,
        class_name: bool = False,
        unique: bool = True,
    ) -> Reference:
        """Ask the resolver of the module that ``imports`` belongs to for a name (``ModelResolver.add``)."""
        return resolver.add(path, original_name, class_name=class_name, unique=unique)

    def release_names(self, imports: Imports, resolver: ModelResolver, names: AbstractSet[str]) -> None:  # noqa: ARG002, PLR6301
        """Release names in the resolver of the module that ``imports`` belongs to (``ModelResolver.delete_names``)."""
        resolver.delete_names(names)
