"""Every binding form hides a Host parameter of the scopes around it: its private reads are not the Host's."""

from __future__ import annotations

from typing import Protocol


class FixtureHost(Protocol):
    _state: int


class FixtureParser:
    _state = 1


def assignment(parser: FixtureHost, models: list[FixtureParser]) -> int:
    def rebinds() -> int:
        parser = models[0]
        return parser._state

    def unpacks() -> int:
        first, *parser = models
        return parser[0]._state + first._state

    return parser._state + rebinds() + unpacks()


def augmented_assignment(parser: FixtureHost) -> int:
    def rebinds() -> int:
        parser += 1
        return parser._state

    return parser._state + rebinds()


def annotated_assignment(parser: FixtureHost, model: FixtureParser) -> int:
    def rebinds() -> int:
        parser: FixtureParser = model
        return parser._state

    def declares() -> int:
        parser: FixtureParser
        return parser._state

    return parser._state + rebinds() + declares()


def for_target(parser: FixtureHost, models: list[FixtureParser]) -> int:
    def rebinds() -> int:
        for parser in models:
            return parser._state
        return 0

    return parser._state + rebinds()


def comprehension_targets(parser: FixtureHost, models: list[FixtureParser]) -> int:
    states = [parser._state for parser in models]
    unique = {parser._state for parser in models}
    by_name = {parser._state: parser for parser in models}
    total = sum(parser._state for parser in models)
    nested = [[parser._state for parser in models] for _ in parser._state]
    first_iterable = [state for parser in parser._state for state in parser._state]
    return parser._state + len(states) + len(unique) + len(by_name) + total + len(nested) + len(first_iterable)


def with_target(parser: FixtureHost, model: FixtureParser) -> int:
    def rebinds() -> int:
        with model as parser:
            return parser._state

    return parser._state + rebinds()


def except_name(parser: FixtureHost, error: type[Exception]) -> int:
    def rebinds() -> int:
        try:
            return 0
        except error as parser:
            return parser._state

    return parser._state + rebinds()


def import_names(parser: FixtureHost) -> int:
    def imports() -> int:
        import parser

        return parser._state

    def imports_a_package() -> int:
        import parser.sub

        return parser._state

    def imports_as() -> int:
        import sys as parser

        return parser._state

    def imports_from() -> int:
        from .models import parser

        return parser._state

    def imports_from_as() -> int:
        from .models import model as parser

        return parser._state

    return parser._state + imports() + imports_a_package() + imports_as() + imports_from() + imports_from_as()


def def_and_class_names(parser: FixtureHost) -> int:
    def defines() -> int:
        def parser() -> None: ...

        return parser._state

    async def defines_async() -> int:
        async def parser() -> None: ...

        return parser._state

    def declares_a_class() -> int:
        class parser:
            _state = 1

        return parser._state

    return parser._state + defines() + declares_a_class()


def match_captures(parser: FixtureHost, value: object) -> int:
    def captures() -> int:
        match value:
            case FixtureParser() as parser:
                return parser._state
            case [*parser]:
                return parser._state
            case {"state": 1, **parser}:
                return parser._state
            case parser:
                return parser._state

    def ignores_wildcards() -> int:
        match value:
            case [*_]:
                return parser._state
            case _:
                return parser._state

    return parser._state + captures() + ignores_wildcards()


def walrus_targets(parser: FixtureHost, models: list[FixtureParser]) -> int:
    def rebinds() -> int:
        if parser := models[0]:
            return parser._state
        return 0

    def rebinds_from_a_comprehension() -> int:
        states = [(parser := model)._state for model in models]
        return parser._state + len(states)

    def rebinds_in_a_lambda() -> int:
        read = lambda: (parser := models[0])._state + parser._state
        return read()

    return parser._state + rebinds() + rebinds_from_a_comprehension() + rebinds_in_a_lambda()


def delete_target(parser: FixtureHost) -> int:
    def deletes() -> int:
        state = parser._state
        del parser
        return state

    return parser._state + deletes()


def global_and_nonlocal(parser: FixtureHost, other: FixtureHost) -> int:
    def declares_global() -> int:
        global parser
        return parser._state

    def declares_nonlocal() -> int:
        nonlocal parser
        parser = other
        return parser._state

    def rebinds() -> int:
        parser = FixtureParser()

        def declares_nonlocal_of_the_rebound_name() -> int:
            nonlocal parser
            parser = FixtureParser()
            return parser._state

        return declares_nonlocal_of_the_rebound_name()

    return parser._state + declares_global() + declares_nonlocal() + rebinds()


def class_bodies(parser: FixtureHost, models: list[FixtureParser]) -> int:
    class Rebinds:
        parser = models[0]
        state = parser._state
        states = [parser._state for _ in models]

        def method(self) -> int:
            return parser._state

    return parser._state + Rebinds().method()
