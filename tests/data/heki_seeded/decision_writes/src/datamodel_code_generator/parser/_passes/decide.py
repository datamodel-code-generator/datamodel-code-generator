from datamodel_code_generator.parser._phases import _DecisionWriter


def decide(writer: _DecisionWriter) -> object:
    return writer.outcome()
