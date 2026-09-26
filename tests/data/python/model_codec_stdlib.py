"""Hand-written standard dataclasses that deliberately disagree with generated structural codec bindings."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated

import msgspec
from typing_extensions import NotRequired, Required, TypedDict


@dataclass
class InitTicket:
    """Accept the code in the constructor, although the binding cannot construct it."""

    code: str = ""
    label: str | int | None = None


@dataclass
class RequiredTicket:
    """Require the label, which the binding treats as optional."""

    label: str | int
    code: str = field(init=False)


@dataclass
class OwnedTicket:
    """Require an owner that no bound property supplies."""

    owner: str
    code: str = field(init=False)
    label: str | int | None = None


@dataclass
class UnresolvedTicket:
    """Annotate the label with a type that does not exist, so the final annotations cannot be read."""

    code: str = field(init=False)
    label: Missing | int | None = None  # noqa: F821


@dataclass
class ListTicket:
    """Annotate the label as a list, which no string leaf converts."""

    code: str = field(init=False)
    label: list[str] | int | None = None


@dataclass
class ChoiceTicket:
    """Widen the label to a union of three members."""

    code: str = field(init=False)
    label: str | int | bool | None = None


@dataclass
class CheckedTicket:
    """Refuse a label in __post_init__, which the codec reports as a native constructor failure."""

    code: str = field(init=False)
    label: str | int | None = None

    def __post_init__(self) -> None:
        if self.label == "bad":
            raise ValueError(self.label)


@dataclass
class SetRecord:
    """Collect the items in a set, although the binding plans a list."""

    name: str
    stamp: str
    items: set[str] | None = field(default_factory=set)
    note: str | None = field(init=False, default=None)
    hint: str | None = field(kw_only=True, default=None)


@dataclass
class NumberKeyed:
    """Key the colors by integers, which no member name converts to."""

    by_color: dict[int, int] | None = None
    by_code: dict[str, int] | None = None
    by_name: dict[str, SetRecord] | None = None


class OpenSealed(TypedDict):
    """Leave the record open, although its binding forbids unknown members."""

    a: str
    b: NotRequired[int]


class LooseSealed(TypedDict, closed=True):
    """Make the required key optional."""

    a: NotRequired[str]
    b: NotRequired[int]


class ShortSealed(TypedDict, closed=True):
    """Leave out a bound key."""

    a: str


class PlainCounts(TypedDict):
    """Declare no extra items, although the binding types them."""

    total: NotRequired[int]


class NumberShelf(TypedDict, extra_items=int):
    """Type the extra items as integers, although the binding binds a model."""

    label: NotRequired[str]


class MissingShelf(TypedDict, extra_items="Missing"):
    """Name extra items that do not exist."""

    label: NotRequired[str]


class HandAnimal(TypedDict):
    """Require the identifier, as the generated base does."""

    id: int
    name: NotRequired[str]


class HandKitten(HandAnimal, total=False):
    """Inherit the required identifier into a TypedDict that is not total."""

    meow: Required[bool]
    tail: str


class NamedLimits(msgspec.Struct):
    """Read the low bound under another wire name."""

    id: int
    low: int | msgspec.UnsetType = msgspec.field(name="LOW", default=msgspec.UNSET)
    name: str | msgspec.UnsetType = msgspec.UNSET


class RequiredLimits(msgspec.Struct):
    """Require the low bound, which the binding treats as optional."""

    id: int
    low: int
    name: str | msgspec.UnsetType = msgspec.UNSET


class TinySmall(msgspec.Struct, tag_field="size", tag="tiny"):
    """Tag the small variant with another value."""

    contact: str | msgspec.UnsetType = msgspec.UNSET


class LocalStamped(msgspec.Struct):
    """Refuse time zones through a Meta constraint the schema does not have."""

    when: Annotated[datetime, msgspec.Meta(tz=False)] | msgspec.UnsetType = msgspec.UNSET
    pair: tuple[int, str] | msgspec.UnsetType = msgspec.UNSET


class LookaheadStamped(msgspec.Struct):
    """Constrain the time with a pattern the builtin matcher cannot read."""

    when: Annotated[datetime, msgspec.Meta(pattern="(?=2)")] | msgspec.UnsetType = msgspec.UNSET
    pair: tuple[int, str] | msgspec.UnsetType = msgspec.UNSET


class SizedStamped(msgspec.Struct):
    """Constrain the fixed pair's length, which no structural check reads."""

    when: datetime | msgspec.UnsetType = msgspec.UNSET
    pair: Annotated[tuple[int, str], msgspec.Meta(min_length=2)] | msgspec.UnsetType = msgspec.UNSET
