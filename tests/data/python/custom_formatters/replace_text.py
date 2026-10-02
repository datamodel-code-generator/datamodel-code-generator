from __future__ import annotations

from datamodel_code_generator.format import CustomCodeFormatter


class CodeFormatter(CustomCodeFormatter):
    """Edit generated models as a user formatter may: replace `old` with `new`, then append `append`."""

    def apply(self, code: str) -> str:
        kwargs = self.formatter_kwargs
        return code.replace(kwargs.get("old", ""), kwargs.get("new", "")) + kwargs.get("append", "")
