"""A user formatter that raises an unexpected exception during generation."""

from __future__ import annotations

from datamodel_code_generator import InvalidClassNameError
from datamodel_code_generator.format import CustomCodeFormatter


class CodeFormatter(CustomCodeFormatter):
    """Fail when the generator applies this formatter."""

    def apply(self, code: str) -> str:
        """Pass models through and raise a formatter failure when formatting a target module."""
        if not code.lstrip().startswith('"""'):
            return code
        if class_name := self.formatter_kwargs.get("class_name"):
            raise InvalidClassNameError(class_name)
        msg = "The formatter stopped"
        raise RuntimeError(msg)
