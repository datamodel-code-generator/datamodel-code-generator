"""Custom formatter that adds a parenthesized module docstring."""

from __future__ import annotations

from datamodel_code_generator.format import CustomCodeFormatter


class CodeFormatter(CustomCodeFormatter):
    """Add a parenthesized, implicitly concatenated docstring."""

    def apply(self, code: str) -> str:
        """Prepend a parenthesized module docstring to generated code."""
        return f'("""Formatter docstring.""" """ Continued.""")\n{code}'
