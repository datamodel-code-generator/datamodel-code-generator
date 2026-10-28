from datamodel_code_generator.format import CustomCodeFormatter


class CodeFormatter(CustomCodeFormatter):
    def apply(self, code: str) -> str:
        return '# formatter_revision = "refactored"\n' + code
