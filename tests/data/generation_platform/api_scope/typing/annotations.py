"""Resolve new public annotations through a private import alias in another module."""

from __future__ import annotations
from datamodel_code_generator.parser.openapi_scope import ApiOpenAPIParser as _ApiParser


def identity(parser: _ApiParser | None) -> _ApiParser | None:
    """Keep the concrete parser type across a public annotation boundary."""
    return parser
