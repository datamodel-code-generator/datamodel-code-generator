"""Review R30, N6: string arguments of TypeVar(bound=...), TypeVar constraints, cast and a TypeAlias are type positions."""

from typing import TypeAlias, TypeVar, cast

T = TypeVar("T", bound="Later")
S = TypeVar("S", "Later", "int")
Alias: TypeAlias = "list[Later]"
Forward = "list[Later]"


def f(x: object):
    return cast("Later", x)


class Later: ...
