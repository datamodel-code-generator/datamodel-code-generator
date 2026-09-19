"""Attempt-local identity ownership for optional final-type binding capture."""

from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING, Concatenate, ParamSpec, Protocol, TypeAlias, TypeVar

from datamodel_code_generator._generation_contract import AttemptId, BindingCaptureError, GraphObjectId

if TYPE_CHECKING:
    from collections.abc import Callable

    from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
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
