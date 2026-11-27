from datamodel_code_generator.model.helpers import omit_through_a_helper
from datamodel_code_generator.parser._phases import _DecisionWriter


def own_write(writer: _DecisionWriter) -> None:
    writer.drop_held_null(1)


def other_write(writer: _DecisionWriter) -> None:
    writer.omit_default(1)


def helper_write(writer: _DecisionWriter) -> None:
    omit_through_a_helper(writer)


def untyped_write(writer) -> None:
    writer.omit_default(1)


def phase_read(writer: _DecisionWriter) -> object:
    return writer.outcome()
