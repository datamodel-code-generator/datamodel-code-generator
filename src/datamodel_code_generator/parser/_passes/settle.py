"""The settle passes (L2): what the parsed models settle into before they are grouped into output modules.

Each pass is a function of the parser and the run, called from the pipeline tables of ``parser/_passes/_tables.py``;
the parser comes in through ``SettleHost``, which ``Parser`` satisfies structurally. The passes of the parse segment
before them, which run L0 and L1, take it through ``ParseHost``.
"""

from __future__ import annotations

import operator
import sys
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from typing_extensions import Protocol, TypeVar

from datamodel_code_generator import Error
from datamodel_code_generator._model_keys import ModelKey, model_key_of
from datamodel_code_generator._url_redaction import redact_reference
from datamodel_code_generator.model.base import DataModel, sort_data_models_for_mro
from datamodel_code_generator.parser._graph_ids import keyed_models
from datamodel_code_generator.parser._passes._shared import (
    _is_pydantic_v2_root_model,
    _runtime_validation_reference_keys,
    _sort_models_by_class_body_references,
)
from datamodel_code_generator.parser._passes._state import ParseConfigView, RecursionFacts
from datamodel_code_generator.parser._passes._warnings import warn_as_parser_base
from datamodel_code_generator.parser.generation import set_model_base_classes
from datamodel_code_generator.reference import _UNMATCHED_MODEL_NAME_KEYS_REPORTED, location_of

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from datamodel_code_generator import (
        AllExportsCollisionStrategy,
        AllExportsScope,
        ModuleSplitMode,
        ReadOnlyWriteOnlyModelType,
    )
    from datamodel_code_generator.parser._graph_ids import KeyedModels
    from datamodel_code_generator.parser._passes._state import ConfigT, SettleRun, SortedDataModels, _ConstNarrowing
    from datamodel_code_generator.parser.generation import GenerationIndex, GenerationStore, GraphBuilder
    from datamodel_code_generator.reference import ModelResolver, Reference
    from datamodel_code_generator.types import DataType

__all__ = [
    "MAX_RECURSION_COUNT",
    "ParseHost",
    "SettleHost",
    "index_const_narrowed_values",
    "parse_raw",
    "prepare_run_parse_config",
    "record_source_reference_paths",
    "report_parse_diagnostics",
    "settle_yielding_names",
    "sort_base_classes_for_mro",
    "sort_data_models",
    "sort_parsed_models",
    "warn_about_unmatched_model_name_keys",
]


_ConfigT_co = TypeVar("_ConfigT_co", bound=ParseConfigView, covariant=True)


class SettleHost(Protocol[_ConfigT_co]):
    """The options, run state and hooks of the parser that the settle passes read and write."""

    _recursion_facts: RecursionFacts
    _typed_map_value_reference_ids: set[int]
    yielding_names_to_assign: tuple[tuple[str, tuple[str, str | None]], ...]

    @property
    def _graph_builder(self) -> GraphBuilder:
        """The builder that changes the model graph through the generation store."""

    @property
    def dedupe_request_response_variants(self) -> bool:
        """Whether the read and write variants are decided from the schema graph."""

    @property
    def generation_store(self) -> GenerationStore:
        """The generation store of the parse."""

    @property
    def keep_model_order(self) -> bool:
        """Whether models keep the order of the schema."""

    @property
    def model_resolver(self) -> ModelResolver:
        """The resolver of references and class names."""

    @property
    def pydantic_v2_root_model_type(self) -> type[DataModel] | None:
        """The root model type whose class body evaluates its root type, if the output has one."""

    @property
    def read_only_write_only_model_type(self) -> ReadOnlyWriteOnlyModelType | None:
        """The read and write variants the output generates, if any."""

    @property
    def results(self) -> list[DataModel]:
        """The parsed models."""

    def _names_have_class_keyed_options(self, names: set[str]) -> bool:
        """Return whether an option keyed by class name, or by a class-scoped field, names one of ``names``."""

    def _prepare_parse_config(
        self,
        with_import: bool | None,  # noqa: FBT001
        disable_future_imports: bool,  # noqa: FBT001
        all_exports_scope: AllExportsScope | None,
        all_exports_collision_strategy: AllExportsCollisionStrategy | None,
        module_split_mode: ModuleSplitMode | None,
    ) -> _ConfigT_co:
        """Return the configuration of output modules."""

    def _report_parse_diagnostics(self) -> None:
        """Report dangling references and other parse diagnostics."""

    def _run_with_suppressed_warnings(self, callback: Callable[[], None]) -> None:
        """Run ``callback`` with warnings suppressed."""


class ParseHost(Protocol):
    """The hooks and run state of the parser that the passes of the parse segment read and write."""

    _const_narrowed_values: dict[int, tuple[DataType, _ConstNarrowing]]

    @property
    def _const_narrowed_references(self) -> list[tuple[Reference, _ConstNarrowing]]:
        """The references of the enums a const narrows, with that const, as L1 marked them."""

    @property
    def generation_store(self) -> GenerationStore:
        """The generation store of the parse."""

    def _close_http_fetch_session(self) -> None:
        """Close and discard the parser-scoped HTTP session."""

    def _run_with_suppressed_warnings(self, callback: Callable[[], None]) -> None:
        """Run ``callback`` with warnings suppressed."""

    def parse_raw(self) -> None:
        """Load the sources and parse them into models (L0 and L1)."""


def _runtime_validation_names_later_model(
    models: list[DataModel],
    runtime_validation_references: Mapping[ModelKey, frozenset[ModelKey]],
) -> bool:
    """Return whether a runtime rule names a model that ``models`` list after the rule's model."""
    positions = {model_key_of(model.reference): index for index, model in enumerate(models)}
    return any(
        positions.get(reference_key, -1) > index
        for index, model in enumerate(models)
        for reference_key in runtime_validation_references.get(model_key_of(model.reference), ())
    )


MAX_RECURSION_COUNT: int = sys.getrecursionlimit()


def _named_model_path(path: str, models: Mapping[str, DataModel]) -> str:
    """Return how a message names the model at a reference path: its schema location, or the path without one."""
    location = None if (model := models.get(path)) is None else location_of(model.reference)
    return redact_reference(path if location is None else str(location))


def sort_data_models(  # noqa: PLR0912, PLR0913, PLR0914, PLR0915
    unsorted_data_models: list[DataModel],
    sorted_data_models: SortedDataModels | None = None,
    require_update_action_models: list[ModelKey] | None = None,
    recursion_count: int = MAX_RECURSION_COUNT,
    generation_index: GenerationIndex | None = None,
    *,
    pydantic_v2_root_model_type: type[DataModel] | None = None,
    runtime_validation_references: Mapping[ModelKey, frozenset[ModelKey]] | None = None,
    keyed: KeyedModels | None = None,
    unresolved_hint: str = "",
) -> tuple[list[DataModel], SortedDataModels, list[ModelKey]]:
    """Sort data models by dependency order for correct forward references.

    Models, their references and the models needing a reference update action are keyed by model key; ``keyed``
    holds the models of the outermost call, which the paths that models list themselves resolve against.
    ``unresolved_hint`` ends the message of the error for models that cannot be ordered.
    """
    if sorted_data_models is None:
        sorted_data_models = OrderedDict()

    runtime_validation_references = runtime_validation_references or {}

    if require_update_action_models is None:
        require_update_action_models = []
    require_update_action_model_keys = set(require_update_action_models)
    universe = (
        keyed_models((*sorted_data_models.values(), *unsorted_data_models), "sort_data_models")
        if keyed is None
        else keyed
    )

    def add_require_update_action_model(model: DataModel, key: ModelKey) -> None:
        if model.is_alias or key in require_update_action_model_keys:
            return
        require_update_action_models.append(key)
        require_update_action_model_keys.add(key)

    def get_reference_keys(model: DataModel, key: ModelKey) -> frozenset[ModelKey]:
        reference_keys = (
            universe.resolve(model.reference_classes)
            if generation_index is None
            else generation_index.referenced_keys_for_model(model, universe)
        )
        if model_runtime_validation_references := runtime_validation_references.get(key):
            return reference_keys | model_runtime_validation_references
        return reference_keys

    sorted_model_count: int = len(sorted_data_models)
    sorted_model_keys = set(sorted_data_models)

    unresolved_references: list[DataModel] = []
    for model in unsorted_data_models:
        key = model_key_of(model.reference)
        reference_keys = get_reference_keys(model, key)
        if not reference_keys:
            sorted_data_models[key] = model
            sorted_model_keys.add(key)
        elif key in reference_keys and len(reference_keys) == 1:  # only self-referencing
            sorted_data_models[key] = model
            sorted_model_keys.add(key)
            add_require_update_action_model(model, key)
        elif not reference_keys - {key} - sorted_model_keys:  # reference classes have been resolved
            sorted_data_models[key] = model
            sorted_model_keys.add(key)
            if key in reference_keys:
                add_require_update_action_model(model, key)
        else:
            unresolved_references.append(model)

    if unresolved_references:
        if sorted_model_count != len(sorted_data_models) and recursion_count:
            try:
                return sort_data_models(
                    unresolved_references,
                    sorted_data_models,
                    require_update_action_models,
                    recursion_count - 1,
                    generation_index,
                    pydantic_v2_root_model_type=pydantic_v2_root_model_type,
                    runtime_validation_references=runtime_validation_references,
                    keyed=universe,
                    unresolved_hint=unresolved_hint,
                )
            except RecursionError:  # pragma: no cover
                pass

        # sort on base_class dependency
        seen_orderings: set[tuple[ModelKey, ...]] = set()
        while True:
            ordered_models: list[tuple[int, DataModel]] = []
            # Build lookup dict for O(1) index access instead of O(n) list.index()
            key_to_index = {model_key_of(m.reference): idx for idx, m in enumerate(unresolved_references)}
            for model in unresolved_references:
                if _is_pydantic_v2_root_model(model, pydantic_v2_root_model_type):
                    indexes = [
                        key_to_index[reference_key]
                        for f in model.fields
                        for t in f.data_type.all_data_types
                        if t.reference and (reference_key := universe.key_of(t.reference)) in key_to_index
                    ]
                else:
                    indexes = [
                        key_to_index[reference_key]
                        for b in model.base_classes
                        if b.reference and (reference_key := universe.key_of(b.reference)) in key_to_index
                    ]

                if indexes:
                    ordered_models.append((
                        max(indexes),
                        model,
                    ))
                else:
                    ordered_models.append((
                        -1,
                        model,
                    ))

            sorted_unresolved_models = [m[1] for m in sorted(ordered_models, key=operator.itemgetter(0))]
            if sorted_unresolved_models == unresolved_references:
                break

            sig = tuple(model_key_of(m.reference) for m in sorted_unresolved_models)
            if sig in seen_orderings:
                # Base-class dependency order has no fixed point (e.g. cyclic inheritance with
                # discriminators). Further iterations only permute the list; use stable order.
                unresolved_references.sort(key=lambda m: m.path)
                break

            seen_orderings.add(sig)
            unresolved_references = sorted_unresolved_models

        if runtime_validation_references and _runtime_validation_names_later_model(
            unresolved_references, runtime_validation_references
        ):
            unresolved_references = _sort_models_by_class_body_references(
                unresolved_references, pydantic_v2_root_model_type, runtime_validation_references
            )

        # circular reference
        unsorted_data_model_keys = set(key_to_index)
        for model in unresolved_references:
            key = model_key_of(model.reference)
            reference_keys = get_reference_keys(model, key)
            unresolved_model = reference_keys - {key} - sorted_model_keys
            base_model_keys = {universe.key_of(s.reference) for s in model.base_classes if s.reference}
            update_action_parent = require_update_action_model_keys.intersection(base_model_keys)
            if not unresolved_model:
                sorted_data_models[key] = model
                sorted_model_keys.add(key)
                if update_action_parent:
                    add_require_update_action_model(model, key)
                continue

            if not unresolved_model - unsorted_data_model_keys:
                sorted_data_models[key] = model
                sorted_model_keys.add(key)
                add_require_update_action_model(model, key)
                continue

            # unresolved
            models_by_path = {
                **{item.path: item for item in sorted_data_models.values()},
                **{item.path: item for item in unresolved_references},
            }

            def reference_paths(item: DataModel) -> frozenset[str]:
                reference_classes = (
                    item.reference_classes
                    if generation_index is None
                    else generation_index.reference_classes_for_model(item)
                )
                return reference_classes | {
                    universe.models[reference_key].path
                    for reference_key in runtime_validation_references.get(model_key_of(item.reference), ())
                }

            unresolved_classes = ", ".join(
                f"[class: {_named_model_path(item.path, models_by_path)} references: "
                f"{frozenset(_named_model_path(path, models_by_path) for path in reference_paths(item))}]"
                for item in unresolved_references
            )
            msg = f"A Parser can not resolve classes: {unresolved_classes}.{unresolved_hint}"
            raise Exception(msg)  # noqa: TRY002

    return unresolved_references, sorted_data_models, require_update_action_models


def sort_base_classes_for_mro(
    sorted_data_models: SortedDataModels,
    generation_store: GenerationStore | None = None,
) -> None:
    """Sort base classes in each model to ensure valid Python MRO.

    When a class inherits from multiple base classes where some bases inherit
    from others, Python's C3 linearization requires that child classes appear
    before their parent classes in the inheritance list.

    For example, if B inherits from A, then class C(A, B) is invalid but
    class C(B, A) is valid.
    """
    sorted_keyed = keyed_models(sorted_data_models.values(), "base class sources")
    for model in sorted_data_models.values():
        base_classes = model.base_classes
        if len(base_classes) <= 1:
            continue

        source_models = [
            source_model
            for base_class in base_classes
            if base_class.reference
            and (
                source_model := (
                    base_class.reference.source
                    if isinstance(base_class.reference.source, DataModel)
                    else sorted_data_models.get(sorted_keyed.key_of(base_class.reference))
                )
            )
            is not None
        ]
        ordered_models = sort_data_models_for_mro(source_models)
        ordered = keyed_models(ordered_models, "base class order")
        model_order = {model_key_of(source_model.reference): index for index, source_model in enumerate(ordered_models)}
        sorted_base_classes = sorted(
            base_classes,
            key=lambda base_class: (
                model_order.get(ordered.key_of(base_class.reference), 0) if base_class.reference else 0
            ),
        )
        if all(
            sorted_base_class is base_class
            for sorted_base_class, base_class in zip(sorted_base_classes, base_classes, strict=True)
        ):
            continue
        set_model_base_classes(model, sorted_base_classes, generation_store)


def warn_about_unmatched_model_name_keys(parser: SettleHost[Any]) -> None:
    """Warn about the ``--model-name-map`` keys that matched no model, after parsing has named every model."""
    if _UNMATCHED_MODEL_NAME_KEYS_REPORTED.get() and (
        unmatched_keys := sorted({redact_reference(key) for key in parser.model_resolver.unmatched_model_name_keys()})
    ):
        listed = ", ".join(map(repr, unmatched_keys))
        warn_as_parser_base(
            f"--model-name-map: {len(unmatched_keys)} key{'' if len(unmatched_keys) == 1 else 's'} "
            f"matched no generated model: {listed}",
            UserWarning,
            stacklevel=2,
        )


def settle_yielding_names(parser: SettleHost[Any], _run: SettleRun[Any]) -> None:
    """Give the names that yield to ``--model-name-map`` their final form."""
    parser._typed_map_value_reference_ids = parser.model_resolver.settle_yielding_names()  # noqa: SLF001
    if (changes := parser.model_resolver.yielding_name_changes) and parser._names_have_class_keyed_options(changes):  # noqa: SLF001
        parser.yielding_names_to_assign = tuple(parser.model_resolver.final_yielding_names.items())


def report_parse_diagnostics(parser: SettleHost[Any], run: SettleRun[Any]) -> None:
    """Report the parse diagnostics, without repeating them on a retry."""
    if run.suppress_parse_warnings:
        parser._run_with_suppressed_warnings(parser._report_parse_diagnostics)  # noqa: SLF001
    else:
        parser._report_parse_diagnostics()  # noqa: SLF001


def prepare_run_parse_config(parser: SettleHost[ConfigT], run: SettleRun[ConfigT]) -> None:
    """Prepare the configuration of output modules from the arguments of ``parse()``."""
    run.config = parser._prepare_parse_config(  # noqa: SLF001
        run.args.with_import,
        run.args.disable_future_imports,
        run.args.all_exports_scope,
        run.args.all_exports_collision_strategy,
        run.args.module_split_mode,
    )


def sort_parsed_models(parser: SettleHost[Any], run: SettleRun[Any]) -> None:
    """Sort the parsed models so that each follows the models it needs.

    The sort keeps one model per path; the others leave the output.
    """
    _, run.sorted_data_models, require_update = sort_data_models(
        parser.results,
        generation_index=parser.generation_store.index,
        pydantic_v2_root_model_type=parser.pydantic_v2_root_model_type,
        runtime_validation_references=(
            None if parser.keep_model_order else _runtime_validation_reference_keys(parser.results, same_module=True)
        ),
        unresolved_hint=(
            " --dedupe-request-response-variants decides the read/write variants from the schema graph and can"
            " generate such schemas."
            if parser.read_only_write_only_model_type is not None and not parser.dedupe_request_response_variants
            else ""
        ),
    )
    parser._recursion_facts = RecursionFacts(require_update=frozenset(require_update), circular_roots=frozenset())  # noqa: SLF001
    sorted_model_ids = {id(model) for model in run.sorted_data_models.values()}
    for model in parser.results:
        if id(model) not in sorted_model_ids:
            parser._graph_builder.retire_model(model)  # noqa: SLF001


def record_source_reference_paths(run: SettleRun[Any]) -> None:
    """Record the reference path each model was parsed from, for the model metadata."""
    run.model_identities = {model: model.reference.path for model in run.sorted_data_models.values()}


def parse_raw(parser: ParseHost, run: SettleRun[Any]) -> None:
    """Run L0 and L1, without repeating warnings on a retry; the HTTP session closes either way.

    A parse that exceeds the recursion limit on deeply nested schemas stops with an error, not a partial result.
    """
    try:
        if run.suppress_parse_warnings:
            parser._run_with_suppressed_warnings(parser.parse_raw)  # noqa: SLF001
        else:
            parser.parse_raw()
    except RecursionError:
        msg = (
            "Schema nesting is too deep to generate: parsing the nested schemas exceeded the Python recursion "
            f"limit ({sys.getrecursionlimit()})."
        )
        raise Error(msg) from None
    finally:
        parser._close_http_fetch_session()  # noqa: SLF001


def index_const_narrowed_values(parser: ParseHost, _run: SettleRun[Any]) -> None:
    """Index the data types whose value a const narrows, by data type."""
    parser._const_narrowed_values = {  # noqa: SLF001
        id(fact.data_type): (fact.data_type, narrowing)
        for reference, narrowing in parser._const_narrowed_references  # noqa: SLF001
        for fact in parser.generation_store.index.data_type_facts_for_reference(reference)
        if fact.role != "base"
    }
