# Protobuf option regression

from __future__ import annotations

from typing import Annotated

from msgspec import Meta, Struct, UnsetType


class Payload(Struct):
    text: Annotated[str, Meta(description='Docs [x(y)')] | UnsetType = 'ok'
    """
    Docs [x(y)
    """
    second: (
        Annotated[
            str,
            Meta(description='Docs [x(y)]\noption (unknown) = "keep this comment";'),
        ]
        | UnsetType
    ) = 'value'
    """
    Docs [x(y)]
    option (unknown) = "keep this comment";
    """
    count: (
        Annotated[int, Meta(description='Literal quotes \' " and /* [ ( , ]')]
        | UnsetType
    ) = 7
    """
    Literal quotes ' " and /* [ ( , ]
    """