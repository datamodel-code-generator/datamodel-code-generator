"""Attempt-local identity ownership for optional final-type binding capture."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from typing import TYPE_CHECKING, Concatenate, Literal, ParamSpec, Protocol, TypeAlias, TypeVar

from datamodel_code_generator._generation_contract import AttemptId, BindingCaptureError, GraphObjectId
from datamodel_code_generator.model.base import DataModel
from datamodel_code_generator.parser.generation import GenerationStore

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from datamodel_code_generator._python_type_binding import BoundPythonType
    from datamodel_code_generator.enums import AllOfMergeMode
    from datamodel_code_generator.imports import Import
    from datamodel_code_generator.model.base import DataModelFieldBase
    from datamodel_code_generator.reference import Reference
    from datamodel_code_generator.types import DataType

    GraphNode: TypeAlias = DataModel | DataModelFieldBase | DataType | Reference


class BindingLedger:
    """Retain observed graph identities only for the lifetime of one attempt."""

    def __init__(self, attempt_id: AttemptId) -> None:
        """Allocate no graph state until the engine supplies an actual object."""
        self.attempt_id = attempt_id
        self._identities: dict[int, GraphObjectId] = {}
        self._anchors: list[GraphNode] = []
        self._closed = False
        self.failure: BindingCaptureError | None = None
        self.replacements: list[Replacement] = []
        self.copies: list[FieldCopy | TypeCopy] = []
        self.variants: list[Variant] = []
        self.collapses: list[RootCollapse] = []
        self.root_recipes: dict[GraphObjectId, TypeRecipe] = {}

    def identity(self, node: GraphNode) -> GraphObjectId:
        """Assign IDs by object identity, retaining anchors to prevent address reuse."""
        if self._closed:
            msg = "Binding ledger is closed"
            raise BindingCaptureError(msg)
        if (identity := self._identities.get(id(node))) is not None:
            return identity
        identity = GraphObjectId(len(self._anchors))
        self._identities[id(node)] = identity
        self._anchors.append(node)
        return identity

    def remember_failure(self, error: BindingCaptureError) -> None:
        """Latch the first recording failure for the shared retry driver's checks."""
        if self.failure is None:
            self.failure = error

    def close(self) -> None:
        """Release every strong anchor without modifying the model graph."""
        self._closed = True
        self._identities.clear()
        self._anchors.clear()
        self.replacements.clear()
        self.copies.clear()
        self.variants.clear()
        self.collapses.clear()
        self.root_recipes.clear()


class _CaptureOwner(Protocol):
    """Limit recording wrappers to their actual attempt's failure owner."""

    @property
    def binding_ledger(self) -> BindingLedger:
        """The attempt-local ledger owned before recording begins."""
        ...


_CaptureOwnerT = TypeVar("_CaptureOwnerT", bound=_CaptureOwner)
_ResultT = TypeVar("_ResultT")
_Parameters = ParamSpec("_Parameters")


def capture_errors(
    method: Callable[Concatenate[_CaptureOwnerT, _Parameters], _ResultT],
) -> Callable[Concatenate[_CaptureOwnerT, _Parameters], _ResultT]:
    """Latch only observer failures; never wrap an ordinary model-engine call."""

    @wraps(method)
    def record(self: _CaptureOwnerT, /, *args: _Parameters.args, **kwargs: _Parameters.kwargs) -> _ResultT:
        try:
            return method(self, *args, **kwargs)
        except BindingCaptureError as error:
            self.binding_ledger.remember_failure(error)
            raise
        except Exception as cause:
            failure = BindingCaptureError("Binding capture failed")
            self.binding_ledger.remember_failure(failure)
            raise failure from cause

    return record


@dataclass(frozen=True, slots=True)
class Replacement:
    """Keep a completed engine-selected edge within its actual ownership scope."""

    kind: Literal["reference", "scoped_reference", "type_reference", "field_type", "nested_type"]
    original: GraphObjectId | None
    replacement: GraphObjectId | None
    owner: GraphObjectId | None = None
    models: tuple[GraphObjectId, ...] = ()


@dataclass(frozen=True, slots=True)
class FieldCopy:
    """Retain both source fields when an inherited merge creates an actual field."""

    sources: tuple[GraphObjectId, ...]
    target: GraphObjectId
    merge_mode: AllOfMergeMode | None


@dataclass(frozen=True, slots=True)
class TypeCopy:
    """Connect an actual helper return to its original type without copying again."""

    source: GraphObjectId
    target: GraphObjectId


@dataclass(frozen=True, slots=True)
class Variant:
    """Preserve the directional reference actually selected by the parser."""

    base: GraphObjectId
    suffix: Literal["Request", "Response"]
    reference: GraphObjectId


@dataclass(frozen=True, slots=True)
class TypeRecipe:
    """Save a removed root's structural recipe without copying a model graph.

    Opaque constructor values are borrowed until projection. This attempt-local
    record must not escape into a frozen batch before value validation.
    """

    node: GraphObjectId
    reference: GraphObjectId | None
    atom: str | None
    alias: str | None
    import_: Import | None
    bound: BoundPythonType | None
    data_types: tuple[TypeRecipe, ...]
    dict_key: TypeRecipe | None
    literals: tuple[bool | int | str, ...]
    enum_members: tuple[tuple[str, str], ...]
    kwargs: tuple[tuple[str, object], ...]
    modifiers: tuple[str, ...]
    tuple_item_count: int | None
    discriminator: str | None


def _type_recipe(data_type: DataType, ledger: BindingLedger, active: set[int]) -> TypeRecipe:
    """Read only direct attributes of the actual root type before its mutation."""
    node = ledger.identity(data_type)
    if node in active:
        msg = "A root type recipe contains a structural cycle"
        raise BindingCaptureError(msg)
    active.add(node)
    result = TypeRecipe(
        node=node,
        reference=ledger.identity(reference) if (reference := data_type.reference) is not None else None,
        atom=data_type.type,
        alias=data_type.alias,
        import_=data_type.import_,
        bound=data_type.python_type,
        data_types=tuple(_type_recipe(child, ledger, active) for child in data_type.data_types),
        dict_key=_type_recipe(key, ledger, active) if (key := data_type.dict_key) is not None else None,
        literals=tuple(data_type.literals),
        enum_members=tuple(data_type.enum_member_literals),
        kwargs=tuple(data_type.kwargs.items()) if data_type.kwargs is not None else (),
        modifiers=tuple(
            name
            for name, enabled in (
                ("optional", data_type.is_optional),
                ("dict", data_type.is_dict),
                ("list", data_type.is_list),
                ("set", data_type.is_set),
                ("frozen_set", data_type.is_frozen_set),
                ("mapping", data_type.is_mapping),
                ("sequence", data_type.is_sequence),
                ("tuple", data_type.is_tuple),
                ("function", data_type.is_func),
                ("custom", data_type.is_custom_type),
                ("standard_collections", data_type.use_standard_collections),
                ("generic_container", data_type.use_generic_container),
                ("union_operator", data_type.use_union_operator),
                ("preserve_union_order", data_type.preserve_union_member_order),
                ("strict", data_type.strict),
                ("serialize_as_any", data_type.use_serialize_as_any),
            )
            if enabled
        ),
        tuple_item_count=data_type.tuple_item_count,
        discriminator=data_type.discriminator,
    )
    active.remove(node)
    return result


@dataclass(slots=True)
class RootCollapse:
    """Distinguish mutation at context entry from successful context completion."""

    reference: Reference
    root_field: DataModelFieldBase
    owner: GraphObjectId
    original: GraphObjectId
    replacement: GraphObjectId
    kind: Literal["field", "nested", "reference"]
    recipe: TypeRecipe
    completed: bool = False


class ContractGenerationStore(GenerationStore):
    """Record only capture-path replacement relations around original mutations."""

    def __init__(self, ledger: BindingLedger) -> None:
        """Own the ledger before the original store allocates its model list."""
        self.binding_ledger = ledger
        super().__init__()

    @capture_errors
    def _record_replacement(
        self,
        kind: Literal["reference", "scoped_reference", "type_reference", "field_type", "nested_type"],
        original: GraphNode | None,
        replacement: GraphNode | None,
        owner: GraphNode | None = None,
        models: tuple[DataModel, ...] = (),
    ) -> None:
        """Record completed mutation arguments without querying generation facts."""
        identity = self.binding_ledger.identity
        self.binding_ledger.replacements.append(
            Replacement(
                kind,
                identity(original) if original is not None else None,
                identity(replacement) if replacement is not None else None,
                identity(owner) if owner is not None else None,
                tuple(identity(model) for model in models),
            )
        )

    def redirect_reference_users(self, old_reference: Reference, new_reference: Reference) -> None:
        """Retain the actual canonical pair selected by existing deduplication."""
        super().redirect_reference_users(old_reference, new_reference)
        self._record_replacement("reference", old_reference, new_reference)

    def redirect_model_reference_users(
        self, model: DataModel, models: list[DataModel], new_reference: Reference
    ) -> None:
        """Retain a module-local redirect without promoting it to a global edge."""
        old_reference = model.reference
        super().redirect_model_reference_users(model, models, new_reference)
        self._record_replacement("scoped_reference", old_reference, new_reference, model, tuple(models))

    def replace_data_type_ref(self, data_type: DataType, new_reference: Reference | None) -> None:
        """Observe the original reference before one actual subtree mutation."""
        old_reference = data_type.reference
        super().replace_data_type_ref(data_type, new_reference)
        self._record_replacement("type_reference", old_reference, new_reference, data_type)

    def replace_field_type(self, field_: DataModelFieldBase, new_data_type: DataType) -> None:
        """Preserve the field owner and its previous type at replacement time."""
        old_data_type = field_.data_type
        super().replace_field_type(field_, new_data_type)
        self._record_replacement("field_type", old_data_type, new_data_type, field_)

    def replace_nested_data_type(
        self, parent_data_type: DataType, old_data_type: DataType, new_data_type: DataType
    ) -> None:
        """Observe only the selected nested type, including dictionary keys."""
        super().replace_nested_data_type(parent_data_type, old_data_type, new_data_type)
        self._record_replacement("nested_type", old_data_type, new_data_type, parent_data_type)

    @capture_errors
    def _begin_collapse(
        self,
        data_type: DataType,
        replacement: DataType | Reference,
        owner: DataType | DataModelFieldBase,
        kind: Literal["field", "nested", "reference"],
    ) -> RootCollapse:
        """Borrow the root's own field before the original replacement detaches it."""
        reference = data_type.reference
        if reference is None or not isinstance(model := reference.source, DataModel) or len(model.fields) != 1:
            msg = "Root collapse has no unique original root field"
            raise BindingCaptureError(msg)
        identity = self.binding_ledger.identity
        root_id = identity(reference)
        if (recipe := self.binding_ledger.root_recipes.get(root_id)) is None:
            recipe = _type_recipe(model.fields[0].data_type, self.binding_ledger, set())
            self.binding_ledger.root_recipes[root_id] = recipe
        return RootCollapse(
            reference,
            model.fields[0],
            identity(owner),
            identity(data_type),
            identity(replacement),
            kind,
            recipe,
        )

    @capture_errors
    def _record_collapse(self, collapse: RootCollapse) -> None:
        """Retain a root relation only after its actual mutation has entered."""
        self.binding_ledger.collapses.append(collapse)

    @contextmanager
    def _replace_data_type_and_detach_data_type_ref(
        self,
        data_type: DataType,
        new_data_type: DataType,
        *,
        owner: DataType | DataModelFieldBase,
        replacement_kind: Literal["field", "nested"],
    ) -> Generator[None, None, None]:
        """Observe enter and successful exit without suppressing original failures."""
        collapse = self._begin_collapse(data_type, new_data_type, owner, replacement_kind)
        with super()._replace_data_type_and_detach_data_type_ref(
            data_type, new_data_type, owner=owner, replacement_kind=replacement_kind
        ):
            self._record_collapse(collapse)
            yield
        collapse.completed = True

    def collapse_root_data_type(self, data_type: DataType, inner_reference: Reference) -> None:
        """Keep the original root value separate from a consumer's field metadata."""
        collapse = self._begin_collapse(data_type, inner_reference, data_type, "reference")
        super().collapse_root_data_type(data_type, inner_reference)
        collapse.completed = True
        self._record_collapse(collapse)
