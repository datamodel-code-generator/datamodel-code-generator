"""Cached properties of classes deriving from owners of fact rows, findings, and of a class the table does not
describe, none.

A cached property writes its own name outside the store, so the baseline holds the ones of today's cache rows
(``DataModel.path`` in ``model/base.py``, a finding here); ``DataType``, ``DataModelFieldBase`` and ``Reference`` own
rows of other kinds.
"""

from functools import cached_property

from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
from datamodel_code_generator.reference import Reference
from datamodel_code_generator.types import DataType


class Model(DataModel):
    @cached_property
    def label(self) -> str:
        return ""


class Type(DataType):
    @cached_property
    def label(self) -> str:
        return ""


class Field(DataModelFieldBase):
    @cached_property
    def label(self) -> str:
        return ""


class Link(Reference):
    @cached_property
    def label(self) -> str:
        return ""


class Helper:
    @cached_property
    def label(self) -> str:
        return ""
