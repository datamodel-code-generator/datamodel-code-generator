# Protobuf option regression

from __future__ import annotations

from typing_extensions import NotRequired, TypedDict


class Payload(TypedDict, closed=True):
    text: NotRequired[str]
    """
    Docs [x(y)
    """
    second: NotRequired[str]
    """
    Docs [x(y)]
    option (unknown) = "keep this comment";
    """
    count: NotRequired[int]
    """
    Literal quotes ' " and /* [ ( , ]
    """
