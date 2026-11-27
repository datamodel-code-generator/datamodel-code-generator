from functools import partial
from typing import Protocol

from datamodel_code_generator.parser._schema_dispatch import Default, DispatchTable, Row


def _shared(parser: 'Parser', site: object) -> object:
    return parser.parse_object(site)


def _then(build: object) -> object:
    return build


class Parser:
    def _build_array(self, site: object) -> object:
        return self._build_enum(site) or self._build_default(site)

    def _build_enum(self, site: object) -> object:
        return site

    def _build_default(self, site: object) -> object:
        return self._build_array(site)

    def parse_object(self, site: object) -> object:
        return _shared(self, site)

    _ROWS = (Row('enum', None, _then(_build_enum)),)
    _TABLE = DispatchTable(
        (Row('array', None, build=partial(_build_array)), *_ROWS, Row('shared', None, _shared)),
        Default('default', _build_default),
    )

    def parse_site(self, site: object) -> object:
        return self._TABLE.choose(self, site)

    def shortcut(self, site: object) -> object:
        return self._TABLE.choose(self, site)

    def parameter(self, _build_enum: object) -> object:
        return _build_enum

    def alias(self, site: object) -> object:
        table = self._TABLE
        return table.choose(self, site)


class KindsHost(Protocol):
    _TABLE: DispatchTable

    def _build_enum(self, site: object) -> object:
        """The parse method of the enum row."""


def via_host(parser: KindsHost, site: object) -> object:
    return parser._build_enum(parser._TABLE.choose(parser, site))


def typed(parser: KindsHost, site: object) -> object:
    return parser._TABLE.typed_children(parser, site)
