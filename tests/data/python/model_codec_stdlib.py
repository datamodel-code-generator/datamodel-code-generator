"""Hand-written standard dataclasses that deliberately disagree with generated structural codec bindings."""

from __future__ import annotations

from dataclasses import dataclass, field


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
