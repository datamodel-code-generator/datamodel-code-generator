from typing import Protocol

from datamodel_code_generator.types import DataType


class MapsHost(Protocol):
    @property
    def data_type(self) -> type[DataType]:
        """The data type class."""

    def _container_flags(self, obj: object, slot: str) -> dict[str, bool]:
        """The flags of a container slot."""


class MapParser:
    @property
    def data_type(self) -> type[DataType]:
        return DataType

    def _container_flags(self, obj: object, slot: str) -> dict[str, bool]:
        return {'is_dict': slot == 'dict'}


def build_map(parser: MapsHost) -> DataType:
    return part(parser)


def part(parser: MapsHost) -> DataType:
    return parser.data_type(is_dict=True)


def flags(parser: MapsHost) -> DataType:
    return parser.data_type(**{'is_dict': True})


def handed_on() -> dict[str, bool]:
    return {'is_dict': True, 'is_list': False}


def not_a_map(parser: MapsHost) -> DataType:
    return parser.data_type(is_dict=False)


class MapType(DataType):
    pass


def subclass_map() -> DataType:
    return MapType(is_dict=True)


def seeded_slot(parser: MapsHost, obj: object) -> DataType:
    return parser.data_type(**parser._container_flags(obj, 'dict'))


def other_slot(parser: MapsHost, obj: object) -> dict[str, bool]:
    return parser._container_flags(obj, 'list')
