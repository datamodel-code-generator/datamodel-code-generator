"""Forward keywords that a method does not take; checkers may treat unpacked keywords differently on some lines."""

from __future__ import annotations

from typing import Protocol

from typing_extensions import NotRequired, TypedDict

from pets import Client
from pets.options import RequestOptions
from pets.types.pets import ListPetsResponse
from pets_models import FieldPetsGetHeaderXTraceParameter


class ListArguments(TypedDict):
    """Keywords of list_pets that a caller declares for itself."""

    x_trace: FieldPetsGetHeaderXTraceParameter
    options: NotRequired[RequestOptions | None]


class ColoredArguments(ListArguments):
    """The same keywords and one more, which list_pets does not take."""

    color: str


class Lister(Protocol):
    """A callable that takes any keywords."""

    def __call__(self, **kwargs: object) -> ListPetsResponse: ...


class TracedLister(Protocol):
    """A callable that takes only the trace."""

    def __call__(self, *, x_trace: FieldPetsGetHeaderXTraceParameter) -> ListPetsResponse: ...


def forward(client: Client, arguments: ColoredArguments) -> None:
    client.pets.list_pets(**arguments)  # error
    anything: Lister = client.pets.list_pets  # error
    traced: TracedLister = client.pets.list_pets
    del anything, traced
