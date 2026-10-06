"""Positive native model codec typing cases, checked with the existing strict checkers."""

from __future__ import annotations

from dataclasses import dataclass

from msgspec import Struct
from pydantic import BaseModel
from typing_extensions import TypedDict, assert_type

from datamodel_code_generator._runtime.model_codecs.native import (
    MsgspecCodec,
    PydanticCodec,
    PydanticDataclassCodec,
)
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
    owner = codec.decode(b'{"name":"a"}')
    assert_type(owner, Owner)
    assert_type(codec.convert({"name": "a"}), Owner)
    assert_type(codec.assemble({"name": "a"}), Owner)
    assert_type(codec.encode(owner), bytes)
    assert_type(codec.dump(owner), object)


def exercise_dataclass(codec: PydanticDataclassCodec[Tag]) -> None:
    tag = codec.decode(b'{"name":"a"}')
    assert_type(tag, Tag)
    assert_type(codec.encode(tag), bytes)
    assert_type(codec.dump(tag), object)


def exercise_stdlib(codec: StdlibCodec[Tag], record: StdlibCodec[TagRecord]) -> None:
    tag = codec.decode(b'{"name":"a"}')
    assert_type(tag, Tag)
    assert_type(codec.convert({"name": "a"}), Tag)
    assert_type(codec.encode(tag), bytes)
    assert_type(codec.dump(tag), object)
    assert_type(record.decode(b'{"name":"a"}'), TagRecord)
    assert_type(record.encode({"name": "a"}), bytes)


def exercise_msgspec(codec: MsgspecCodec[TagStruct]) -> None:
    tag = codec.decode(b'{"name":"a"}')
    assert_type(tag, TagStruct)
    assert_type(codec.convert({"name": "a"}), TagStruct)
    assert_type(codec.assemble({"name": "a"}), TagStruct)
    assert_type(codec.encode(tag), bytes)
    assert_type(codec.dump(tag), object)
