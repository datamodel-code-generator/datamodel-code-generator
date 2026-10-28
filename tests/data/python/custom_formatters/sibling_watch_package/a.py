from datamodel_code_generator.format import CustomCodeFormatter


class CodeFormatter(CustomCodeFormatter):
    def apply(self, code: str) -> str:
        return '# formatter_revision = "sibling_initial"\n' + code
