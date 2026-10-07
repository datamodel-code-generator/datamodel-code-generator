from __future__ import annotations

from datamodel_code_generator.format import CustomCodeFormatter


class CodeFormatter(CustomCodeFormatter):
    """Edit generated models as a user formatter may: replace `old` with `new`, then append `append`.

    Target modules, which open with a docstring, stay as rendered, so the edits reach only the models.
    """

    def apply(self, code: str) -> str:
        if code.lstrip().startswith('"""'):
            return code
        kwargs = self.formatter_kwargs
        return code.replace(kwargs.get("old", ""), kwargs.get("new", "")) + kwargs.get("append", "")
