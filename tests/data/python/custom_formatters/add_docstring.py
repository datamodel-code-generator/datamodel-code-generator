"""Custom formatter that adds a module docstring."""

from __future__ import annotations

from datamodel_code_generator.format import CustomCodeFormatter


class CodeFormatter(CustomCodeFormatter):
    """Add a module docstring before generated code."""

    def apply(self, code: str) -> str:
        """Prepend a module docstring to generated code."""
        return f'"""Formatter docstring."""\n{code}'
