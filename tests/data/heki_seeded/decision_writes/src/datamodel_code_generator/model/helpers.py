from datamodel_code_generator.parser._phases import _DecisionWriter


def omit_through_a_helper(writer: _DecisionWriter) -> None:
    writer.omit_default(1)
