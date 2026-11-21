"""A concern module: private members are reached through self, cls or a Host parameter that declares them."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Protocol

from ._passes._shared import GraphBuilderHost

if TYPE_CHECKING:
    from ._passes import _shared


class FixtureBaseHost(Protocol):
    _base_state: int


class FixtureHost(FixtureBaseHost, Protocol):
    _state: int
    _shared_state = 0

    def _method(self) -> None: ...


class FixtureView(Protocol):
    _state: int


class FixtureNotProtocolHost(FixtureBaseHost):
    _state: int


class FixtureParser:
    _state = 1

    def method(self, other: FixtureParser) -> int:
        return self._state + super()._state + type(self)._state + other._state

    @classmethod
    def build(cls) -> int:
        return cls._state


def declared(
    parser: FixtureHost,
    graph: GraphBuilderHost,
    dotted: _shared.GraphBuilderHost,
    quoted: "FixtureHost",
) -> int:
    parser._method()
    graph._graph_builder.close()
    dotted._graph_builder.close()

    def nested() -> int:
        return parser._state

    by_state = sorted([parser], key=lambda item: parser._state)
    return parser._base_state + parser._shared_state + quoted._state + nested() + len(by_state) + len(parser.__dict__)


def undeclared(
    parser: FixtureHost,
    graph: GraphBuilderHost,
    view: FixtureView,
    concrete: FixtureNotProtocolHost,
    plain,
) -> int:
    alias = parser
    parser._missing = 1
    return (
        alias._state
        + view._state
        + concrete._state
        + plain._state
        + graph._graph_builder._nodes
        + sys._getframe(0).f_lineno
    )


def shadowed(parser: FixtureHost) -> int:
    def rebinds() -> int:
        parser = FixtureParser()
        return parser._state

    by_parameter = lambda parser: parser._state
    return rebinds() + by_parameter(parser)



async def declared_async(parser: FixtureHost, fallback: FixtureHost | None = None) -> int:
    by_default = lambda state=parser._state: state
    return by_default() + (fallback or parser)._state


MODULE_STATE = FixtureParser()._state
