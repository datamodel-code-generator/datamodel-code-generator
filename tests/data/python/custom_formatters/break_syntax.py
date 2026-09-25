from datamodel_code_generator.format import CustomCodeFormatter


class CodeFormatter(CustomCodeFormatter):
    """Invalid formatter output: appends an unfinished definition."""
    def apply(self, code: str) -> str:
        return f"{code}\ndef ("
