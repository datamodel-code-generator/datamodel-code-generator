"""Only a method's self or cls reaches every member; elsewhere the names are ordinary parameters."""

from __future__ import annotations

from typing import Protocol


class FixtureHost(Protocol):
    _state: int


class FixtureParser:
    _state = 1

    def method(self) -> int:
        def closure() -> int:
            return self._state

        by_lambda = lambda: self._state
        return self._state + closure() + by_lambda()

    @classmethod
    def build(cls) -> int:
        return cls._state

    def __init_subclass__(cls) -> None:
        cls._state = 2

    @staticmethod
    def static(self: FixtureParser) -> int:
        return self._state

    def rebinds(self, models: list[FixtureParser]) -> int:
        def takes_its_own(self: FixtureParser) -> int:
            return self._state

        return sum(self._state for self in models) + takes_its_own(models[0])

    def second(other, self: FixtureParser) -> int:
        return other._state + self._state


def module_function(self: FixtureParser) -> int:
    return self._state


def module_function_on_a_host(self: FixtureHost, cls: type[FixtureHost]) -> int:
    return self._state + cls._state + cls._missing


TABLE = {"state": lambda self: self._state}
