from datamodel_code_generator.model.base import DataModelFieldBase
from datamodel_code_generator.parser.base import Parser


def parse_raw(parser: Parser, state: object) -> None:
    for field in parser.fields:
        field.read_only = True


def declared(parser: Parser, state: object) -> None:
    for field in parser.fields:
        field.read_only = True


def _flip(field: DataModelFieldBase) -> None:
    field.read_only = not field.read_only


def undeclared(parser: Parser) -> None:
    for field in parser.fields:
        _flip(field)


def keyed(parser: Parser, state: object) -> None:
    for field in parser.fields:
        parser.store.set_field_extra(field, "init", False)
        parser.store.set_field_extra(field, "kw_only", True)


def enters_phase(parser: Parser, state: object) -> None:
    parser.store.enter_phase(9)


def process_each_module(parser: Parser, state: object) -> None:
    parser._process_single_module()
