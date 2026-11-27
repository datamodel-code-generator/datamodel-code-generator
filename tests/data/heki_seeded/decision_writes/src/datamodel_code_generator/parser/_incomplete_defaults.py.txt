from datamodel_code_generator.parser._phases import _DecisionWriter


def own_write(writer: _DecisionWriter) -> None:
    writer.omit_default(1)
