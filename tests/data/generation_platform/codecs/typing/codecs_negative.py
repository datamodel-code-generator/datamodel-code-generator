"""Negative native model codec typing cases; each marked misuse belongs to the typed native interface."""

from __future__ import annotations

from dataclasses import dataclass

from msgspec import Struct
from pydantic import BaseModel
from typing_extensions import TypedDict

from datamodel_code_generator._runtime.model_codecs.native import MsgspecCodec, PydanticCodec
from datamodel_code_generator._runtime.model_codecs.stdlib import StdlibCodec


class Owner(BaseModel):
    name: str


@dataclass
class Tag:
    name: str


class TagRecord(TypedDict):
    name: str


class TagStruct(Struct):
    name: str


def exercise_pydantic(codec: PydanticCodec[Owner]) -> None:
    _widened: PydanticCodec[object] = codec  # error
    codec.decode({"name": "a"})  # error
    codec.encode(Tag("a"))  # error
    _dumped: Owner = codec.dump(Owner(name="a"))  # error


def exercise_stdlib(codec: StdlibCodec[Tag], record: StdlibCodec[TagRecord]) -> None:
    _widened: StdlibCodec[object] = codec  # error
    codec.encode(Owner(name="a"))  # error
    record.encode(Tag("a"))  # error
    _tag: Tag = record.decode(b'{"name":"a"}')  # error


def exercise_msgspec(codec: MsgspecCodec[TagStruct]) -> None:
    _widened: MsgspecCodec[object] = codec  # error
    codec.encode(Tag("a"))  # error
    _tag: Tag = codec.decode(b'{"name":"a"}')  # error
